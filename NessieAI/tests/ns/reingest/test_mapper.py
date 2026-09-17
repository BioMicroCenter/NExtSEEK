from NessieAI.ns.reingest import manifest, mapper, maps


def _run(metrics=None, derived=None, params=None, software_versions=None,
         parent_sample_type="D.SEQ"):
    return manifest.RunManifest(
        run_dir="/net/cluster/runs/r",
        params=params or {"genome": "GRCm39", "aligner": "star_salmon"},
        software_versions=software_versions or {},
        pipeline=manifest.PipelineInfo(name="nf-core/rnaseq", version="3.18.0"),
        samples=[manifest.SampleRecord(
            nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-EXAMPLE-1",
            uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
            parent_sample_type=parent_sample_type,
            metrics=metrics or {}, derived=derived or {})],
        sources={"metrics": "multiqc/star_salmon/multiqc_data/multiqc_general_stats.txt"})


def test_a_committed_rule_maps_and_is_marked_origin_map():
    result = mapper.apply(_run(metrics={"star-uniquely_mapped_percent": 91.4}),
                          maps.load("rnaseq"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["MappedPercent"].value == 91.4
    assert row.attributes["MappedPercent"].origin == mapper.ORIGIN_MAP
    assert row.attributes["MappedPercent"].raw_key == "star-uniquely_mapped_percent"


def test_the_alternate_is_used_when_the_primary_key_is_absent():
    result = mapper.apply(_run(metrics={"samtools_stats-reads_mapped_percent": 88.0}),
                          maps.load("rnaseq"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["MappedPercent"].value == 88.0
    assert row.attributes["MappedPercent"].raw_key == "samtools_stats-reads_mapped_percent"


def test_no_source_at_all_yields_no_attribute_rather_than_a_zero():
    result = mapper.apply(_run(metrics={}), maps.load("rnaseq"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert "MappedPercent" not in row.attributes


def test_an_unknown_metric_key_lands_in_unmapped_with_its_evidence():
    result = mapper.apply(_run(metrics={"Kraken2_bracken_fraction": 3.2}),
                          maps.load("rnaseq"))
    entry = next(u for u in result.unmapped if u["raw_key"] == "Kraken2_bracken_fraction")
    assert entry["example_value"] == 3.2
    assert entry["source_file"].endswith("multiqc_general_stats.txt")


def test_a_deliberately_unmapped_key_is_never_proposed():
    pipeline_map = maps.load("rnaseq")
    assert "Picard_PERCENT_DUPLICATION" in pipeline_map.ruled_out()
    result = mapper.apply(
        _run(metrics={"Picard_PERCENT_DUPLICATION": 18.4,
                      "Some_Definitely_Unknown_Metric_XYZ": 1.0}),
        pipeline_map)
    # The ruling specifically excludes its own key...
    assert not any(u["raw_key"] == "Picard_PERCENT_DUPLICATION" for u in result.unmapped)
    # ...but unmapped is genuinely populated: a second, genuinely unknown key
    # on the same sample still surfaces as evidence.
    assert any(u["raw_key"] == "Some_Definitely_Unknown_Metric_XYZ" for u in result.unmapped)


def test_an_approved_rule_applies_and_is_marked_origin_approved():
    # approved_rules is keyed BY attribute, so the rule carries only its source.
    # The map's own ContamPercent rule reads "kraken2-pct_unclassified", which is
    # absent here, so the committed rule finds nothing and the approved rule applies.
    approved = {"ContamPercent": maps.AttributeRule(
        **{"from": "Kraken2_bracken_fraction", "target": "D.SEQ",
           "datatype": "number", "provenance": "approved 2026-09-15 by tester"})}
    result = mapper.apply(_run(metrics={"Kraken2_bracken_fraction": 3.2}),
                          maps.load("rnaseq"), approved_rules=approved)
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["ContamPercent"].origin == mapper.ORIGIN_APPROVED
    assert not any(u["raw_key"] == "Kraken2_bracken_fraction" for u in result.unmapped)


def test_provenance_attributes_are_applied_to_an_opted_in_output_row():
    # A.GEX sets include_provenance: true in the committed rnaseq map, so its
    # row receives provenance_attributes (e.g. ReferenceGenome).
    result = mapper.apply(_run(), maps.load("rnaseq"))
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert gex.attributes["ReferenceGenome"].value == "GRCm39"


def test_provenance_attributes_are_not_applied_to_an_opted_out_output_row():
    # A.ALN does NOT set include_provenance in the committed rnaseq map --
    # deliberately, since A.ALN's sample type does not have most of the
    # provenance attributes (e.g. DESeqFile) at all. Its row must receive
    # only its own rule's attributes, never a spillover from
    # provenance_attributes.
    result = mapper.apply(_run(), maps.load("rnaseq"))
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    assert "ReferenceGenome" not in aln.attributes
    assert "DESeqFile" not in aln.attributes
    assert "Pipeline" not in aln.attributes


def test_a_multirun_sample_with_nothing_resolved_produces_no_d_seq_row_but_ships_a_parentless_child():
    # A multi-run sample none of whose contributing rows resolved
    # (`d_seq_uid_multirun` empty, matching a fully-unresolved multi-run
    # sample) behaves like an unresolved single-run sample: no D.SEQ
    # backfill row, but its A.ALN child still SHIPS -- with no Parent key --
    # rather than silently dropping the output file it would otherwise
    # register. This is the corrected behaviour; see
    # test_a_multirun_sample_whose_rows_resolve_ships_a_child_with_the_joined_parent
    # for the case some or all of its rows DID resolve.
    run = _run(metrics={"star-uniquely_mapped_percent": 91.4,
                        "Kraken2_bracken_fraction": 3.2})
    run.samples[0].uid_resolution = manifest.RESOLUTION_MULTIRUN
    run.samples[0].d_seq_uid = None
    result = mapper.apply(run, maps.load("rnaseq"))
    # No D.SEQ backfill row for the multirun sample -- this half of the old
    # behaviour is still correct and must survive: MultiQC's one figure for
    # the concatenated sample cannot honestly be attributed to any one
    # contributing D.SEQ, and that measurement limit is untouched by the fix.
    assert not any(r.sample_type == "D.SEQ" for r in result.rows)
    # ...but the per_sample A.ALN row now SHIPS -- this is the half of the
    # old behaviour that changes: the output file a multi-run sample's
    # analysis produced (the BAM, its index) is real primary data and must
    # be registered, even with no parent to name yet.
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    assert "Parent" not in aln.attributes
    # The per_run A.GEX rule still emits its one row: its literal attributes
    # (Matrix, MatrixDataType, DataType) do not depend on any sample
    # resolving...
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    # ...but the multirun sample contributes nothing to the join (it has no
    # resolved parents of its own), and since no other sample resolved
    # either, Parent is omitted rather than set to an empty or fabricated
    # value.
    assert "Parent" not in gex.attributes
    # ...and the sample's own metrics are still walked: an unknown key on it
    # still reaches unmapped rather than being silently swallowed along with
    # the D.SEQ row.
    assert any(u["raw_key"] == "Kraken2_bracken_fraction" for u in result.unmapped)


def test_a_multirun_sample_whose_rows_resolve_ships_a_child_with_the_joined_parent():
    # The defect this fix closes: a multi-run sample whose contributing rows
    # DID resolve to real D.SEQ parents must ship an A.ALN child carrying
    # them, `;`-joined, first-occurrence de-duplicated, in manifest order --
    # and, since that lineage is now real and honest (not a fabricated
    # measurement), the per_run A.GEX join gets it too.
    run = _run()
    run.samples[0].uid_resolution = manifest.RESOLUTION_MULTIRUN
    run.samples[0].d_seq_uid = None
    run.samples[0].d_seq_uid_multirun = ["D.SEQ-LANE-1", "D.SEQ-LANE-2", "D.SEQ-LANE-1"]
    result = mapper.apply(run, maps.load("rnaseq"))
    # Still no D.SEQ backfill row -- the QC-attribution limit is unchanged.
    assert not any(r.sample_type == "D.SEQ" for r in result.rows)
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    assert aln.attributes["Parent"].value == "D.SEQ-LANE-1;D.SEQ-LANE-2"
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert gex.attributes["Parent"].value == "D.SEQ-LANE-1;D.SEQ-LANE-2"


def test_a_multirun_samples_partial_parent_list_ships_as_is():
    # Only one of two contributing rows resolved (see uid_resolve's own
    # partial-resolution tests) -- the child ships with exactly that partial
    # list, not padded, not withheld.
    run = _run()
    run.samples[0].uid_resolution = manifest.RESOLUTION_MULTIRUN
    run.samples[0].d_seq_uid = None
    run.samples[0].d_seq_uid_multirun = ["D.SEQ-LANE-1"]
    result = mapper.apply(run, maps.load("rnaseq"))
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    assert aln.attributes["Parent"].value == "D.SEQ-LANE-1"


# --- Resolution 2: an output rule's own attribute must win over a
# provenance attribute of the same name (A.ALN's "Software" is the STAR
# version string; provenance_attributes' "Software" is the whole
# software_versions dict, which must not clobber it). ---

def test_an_output_rules_own_attribute_wins_over_a_same_named_provenance_attribute():
    run = _run(software_versions={"STAR": "2.7.11a"})
    result = mapper.apply(run, maps.load("rnaseq"))
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    assert aln.attributes["Software"].value == "2.7.11a"
    assert isinstance(aln.attributes["Software"].value, str)


# --- Resolution 3: when a committed map rule and an approved rule both
# produce a value for the same attribute, the committed map rule must win. ---

def test_a_committed_rule_wins_over_a_contested_approved_rule():
    approved = {"ContamPercent": maps.AttributeRule(
        **{"from": "Kraken2_bracken_fraction", "target": "D.SEQ",
           "datatype": "number", "provenance": "approved 2026-09-15 by tester"})}
    result = mapper.apply(
        _run(metrics={"kraken2-pct_unclassified": 4.5, "Kraken2_bracken_fraction": 3.2}),
        maps.load("rnaseq"), approved_rules=approved)
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["ContamPercent"].value == 4.5
    assert row.attributes["ContamPercent"].origin == mapper.ORIGIN_MAP


# --- The "raw_key in claimed or raw_key in ruled_out" skip is meant to be
# order-independent: either reason alone must be enough to keep a key out of
# unmapped. Nothing demonstrated that until now. ---

def test_a_ruled_out_key_claimed_by_an_approved_rule_still_stays_out_of_unmapped():
    # "Picard_PERCENT_DUPLICATION" is a real deliberately_unmapped key in the
    # committed rnaseq map. No committed rule claims it as a "from" or
    # alternate, so we give it to an approved rule instead -- a realistic way
    # a key ends up BOTH ruled_out (committed map) AND claimed (approved
    # rule) at once.
    pipeline_map = maps.load("rnaseq")
    assert "Picard_PERCENT_DUPLICATION" in pipeline_map.ruled_out()
    approved = {"PercentDuplication": maps.AttributeRule(
        **{"from": "Picard_PERCENT_DUPLICATION", "target": "D.SEQ",
           "datatype": "number", "provenance": "approved 2026-09-15 by tester"})}
    result = mapper.apply(
        _run(metrics={"Picard_PERCENT_DUPLICATION": 18.4}),
        pipeline_map, approved_rules=approved)
    assert not any(u["raw_key"] == "Picard_PERCENT_DUPLICATION" for u in result.unmapped)
    # And the approved rule genuinely fired -- this isn't passing merely
    # because the key was skipped for an unrelated reason.
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["PercentDuplication"].value == 18.4
    assert row.attributes["PercentDuplication"].origin == mapper.ORIGIN_APPROVED


def _multi_sample_run(*samples):
    return manifest.RunManifest(
        run_dir="/net/cluster/runs/r",
        params={"genome": "GRCm39", "aligner": "star_salmon"},
        pipeline=manifest.PipelineInfo(name="nf-core/rnaseq", version="3.18.0"),
        samples=list(samples),
        sources={"metrics": "multiqc/star_salmon/multiqc_data/multiqc_general_stats.txt"})


# --- The fan-out: a per_sample output rule must emit one row per sample
# that resolved to a d_seq_uid, not one row for the whole rule. ---

def test_a_per_sample_rule_emits_one_row_per_resolved_sample_with_parent_and_nfcore_sample():
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD),
        manifest.SampleRecord(nfcore_sample="CONTROL_REP2", d_seq_uid="D.SEQ-2",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    result = mapper.apply(run, maps.load("rnaseq"))
    aln_rows = [r for r in result.rows if r.sample_type == "A.ALN"]
    assert len(aln_rows) == 2
    by_sample = {r.nfcore_sample: r for r in aln_rows}
    assert set(by_sample) == {"CONTROL_REP1", "CONTROL_REP2"}
    assert by_sample["CONTROL_REP1"].attributes["Parent"].value == "D.SEQ-1"
    assert by_sample["CONTROL_REP2"].attributes["Parent"].value == "D.SEQ-2"
    # The analysis record does not exist yet -- this row is what creates it.
    assert by_sample["CONTROL_REP1"].uid is None
    assert by_sample["CONTROL_REP2"].uid is None


def test_a_per_run_rule_still_emits_exactly_one_row_with_the_joined_parent():
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD),
        manifest.SampleRecord(nfcore_sample="CONTROL_REP2", d_seq_uid="D.SEQ-2",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    result = mapper.apply(run, maps.load("rnaseq"))
    gex_rows = [r for r in result.rows if r.sample_type == "A.GEX"]
    assert len(gex_rows) == 1
    # Join order follows the manifest's own (samplesheet) sample order.
    assert gex_rows[0].attributes["Parent"].value == "D.SEQ-1;D.SEQ-2"


def test_the_per_run_join_deduplicates_a_repeated_d_seq_uid():
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="S1_LANE1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD),
        manifest.SampleRecord(nfcore_sample="S1_LANE2", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    result = mapper.apply(run, maps.load("rnaseq"))
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert gex.attributes["Parent"].value == "D.SEQ-1"


# --- The UID-resolution table in
# docs/superpowers/specs/2026-09-15-nfcore-reingest-design.md (Section 11)
# is the authority for what an unresolved vs. ambiguous sample's child does.
# An earlier addendum instruction over-generalised "no d_seq_uid -> no row"
# from the multirun case to every unresolved sample; these two tests pin the
# corrected, per-resolution behaviour. ---

def test_an_unresolved_sample_still_ships_its_child_with_no_parent():
    # "Matches none" is a hard reject "on the backfill only" -- the
    # per_sample child still ships, just with no Parent to name (see
    # mapper._per_sample_rows).
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD),
        manifest.SampleRecord(nfcore_sample="CONTROL_REP2", d_seq_uid=None,
                              uid_resolution=manifest.RESOLUTION_UNRESOLVED))
    result = mapper.apply(run, maps.load("rnaseq"))
    aln_rows = [r for r in result.rows if r.sample_type == "A.ALN"]
    by_sample = {r.nfcore_sample: r for r in aln_rows}
    # Both samples ship a child row...
    assert set(by_sample) == {"CONTROL_REP1", "CONTROL_REP2"}
    # ...the resolved one keeps its Parent...
    assert by_sample["CONTROL_REP1"].attributes["Parent"].value == "D.SEQ-1"
    # ...but the unresolved one's row has no Parent key at all -- not an
    # empty string, not a fabricated value.
    #
    # THIS DISTINCTION IS LOAD-BEARING ACROSS A LAYER BOUNDARY, and the other
    # half of it is NOT in this worktree yet. reingest_qa.py HERE is two-state:
    # collect_parent_tokens() in nextseek_api/batch_upload/helpers.py skips
    # falsy values, so it returns [] for both "no parent-ish key" and "key
    # present but blank", and the gate hard-rejects either. The three-state
    # rule -- real value passes, blank hard-rejects, ABSENT soft-flags and
    # ships -- lives on feat/nfcore-reingest-workbooks and arrives at merge.
    # A gate can only tell absent from blank because the mapper never emits an
    # empty Parent: it sets a real UID or omits the key. Start emitting
    # `Parent: ""` here and every unresolved sample's child becomes a hard
    # reject downstream, which is exactly the spec guarantee (lines 375-383,
    # "children still ship") that this test exists to protect.
    assert "Parent" not in by_sample["CONTROL_REP2"].attributes
    # The unresolved sample contributes nothing to the per_run join either.
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert gex.attributes["Parent"].value == "D.SEQ-1"


def test_an_ambiguous_sample_produces_no_child_row():
    # "Matches more than one D.SEQ" is a hard reject -- never guess between
    # candidate parents. Unlike unresolved, this resolution gets no row at
    # all: no backfill, no per_sample child, and it contributes nothing to
    # the per_run join.
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid=None,
                              uid_resolution=manifest.RESOLUTION_AMBIGUOUS))
    result = mapper.apply(run, maps.load("rnaseq"))
    assert not any(r.sample_type == "D.SEQ" for r in result.rows)
    assert not any(r.sample_type == "A.ALN" for r in result.rows)
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert "Parent" not in gex.attributes


# --- The QC backfill row must be built for the parent's REAL sample type,
# never a hardcoded "D.SEQ" -- a resolved parent can now legitimately be an
# already-analysed A.* sample (see maps.PipelineMap.accepts_parent_types).
# `sample.parent_sample_type` (filled by harvest.py from the database, never
# parsed off the UID) is the only source of truth for which type's
# qc_attributes rules apply. ---

def test_an_aln_parent_gets_a_row_typed_for_its_real_type_and_only_its_own_rules():
    # A synthetic map with one rule per target, exactly the mechanism under
    # test: a rule targeting the sample's actual parent type (A.ALN) must
    # apply; a rule targeting a DIFFERENT type (D.SEQ) must not -- even
    # though its own metric genuinely measured something on this sample.
    pipeline_map = maps.PipelineMap(
        pipeline="nf-core/fake-for-test",
        qc_attributes={
            "MappedPercent": maps.AttributeRule(
                **{"from": "star-uniquely_mapped_percent", "target": "D.SEQ",
                   "datatype": "number", "provenance": "seed"}),
            "AlnQualityScore": maps.AttributeRule(
                **{"from": "some-aln-metric", "target": "A.ALN",
                   "datatype": "number", "provenance": "seed"}),
        })
    run = _run(
        metrics={"star-uniquely_mapped_percent": 91.4, "some-aln-metric": 7.0},
        parent_sample_type="A.ALN")

    result = mapper.apply(run, pipeline_map)

    # The backfill row itself -- distinguished from any analysis-child row
    # by carrying the parent's own uid (a child row's uid is always None,
    # see _per_sample_rows) -- is typed A.ALN, not D.SEQ.
    row = next(r for r in result.rows if r.uid == "D.SEQ-EXAMPLE-1")
    assert row.sample_type == "A.ALN"
    # Only the A.ALN-targeted rule's attribute made it onto the row...
    assert set(row.attributes) == {"AlnQualityScore"}
    assert row.attributes["AlnQualityScore"].value == 7.0
    # ...the D.SEQ-targeted rule found a real, measured value
    # (star-uniquely_mapped_percent=91.4 is genuinely present) but has no
    # home on an A.ALN row, so it must NOT silently vanish: it is named in a
    # manifest warning, naming the sample, the real parent type, and how
    # many measured attributes had no matching rule.
    assert "MappedPercent" not in row.attributes
    warning = next(w for w in result.warnings if "CONTROL_REP1" in w)
    assert "A.ALN" in warning
    assert "1" in warning


def test_an_unknown_parent_type_skips_the_backfill_row_and_warns_rather_than_guess():
    # The lookup could not determine the parent's real type (see harvest.py's
    # sample_type_lookup and reingest_lookups.sample_types_for_uids -- either
    # was unreachable, or the UID was absent from its result). Guessing
    # "D.SEQ" here is exactly the failure mode this change exists to stop, so
    # no backfill row is written at all -- but the sample is not silently
    # dropped: a warning names it.
    run = _run(metrics={"star-uniquely_mapped_percent": 91.4},
               parent_sample_type="")

    result = mapper.apply(run, maps.load("rnaseq"))

    assert not any(r.uid == "D.SEQ-EXAMPLE-1" for r in result.rows)
    warning = next(w for w in result.warnings if "CONTROL_REP1" in w)
    assert "D.SEQ-EXAMPLE-1" in warning
    # The rest of the sample's evidence gathering is unaffected: an unknown
    # metric key on the very same sample still reaches unmapped, proving the
    # unknown-type branch does not short-circuit the per-sample metrics scan.
    run2 = _run(metrics={"Some_Definitely_Unknown_Metric_XYZ": 1.0},
                parent_sample_type="")
    result2 = mapper.apply(run2, maps.load("rnaseq"))
    assert any(u["raw_key"] == "Some_Definitely_Unknown_Metric_XYZ"
               for u in result2.unmapped)


def test_a_d_seq_parent_produces_no_warnings():
    # The regression guard from the other direction: the ordinary, unchanged
    # case -- every rnaseq qc_attributes rule targets D.SEQ, and the parent
    # really is D.SEQ -- must not emit any of the new warnings.
    result = mapper.apply(
        _run(metrics={"star-uniquely_mapped_percent": 91.4,
                      "kraken2-pct_unclassified": 4.5}),
        maps.load("rnaseq"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["MappedPercent"].value == 91.4
    assert row.attributes["ContamPercent"].value == 4.5
    assert result.warnings == []


def test_a_ruled_out_key_on_two_different_samples_stays_out_of_unmapped():
    # "MaxReads" is a real deliberately_unmapped key in the committed rnaseq
    # map. Put it on two different samples in the same run and confirm
    # neither occurrence is proposed -- the per-sample dedup-by-raw_key guard
    # must not be the only thing keeping it out.
    pipeline_map = maps.load("rnaseq")
    assert "MaxReads" in pipeline_map.ruled_out()
    run = manifest.RunManifest(
        run_dir="/net/cluster/runs/r",
        params={"genome": "GRCm39", "aligner": "star_salmon"},
        pipeline=manifest.PipelineInfo(name="nf-core/rnaseq", version="3.18.0"),
        samples=[
            manifest.SampleRecord(
                nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-EXAMPLE-1",
                uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                metrics={"MaxReads": 40000000.0}),
            manifest.SampleRecord(
                nfcore_sample="CONTROL_REP2", d_seq_uid="D.SEQ-EXAMPLE-2",
                uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                metrics={"MaxReads": 40000000.0}),
        ],
        sources={"metrics": "multiqc/star_salmon/multiqc_data/multiqc_general_stats.txt"})
    result = mapper.apply(run, pipeline_map)
    assert not any(u["raw_key"] == "MaxReads" for u in result.unmapped)


# ---------------------------------------------------------------------------
# Checksum_PrimaryData: reaches a row via the output rule's own `glob` +
# `primary_data`, matched against RunManifest.outputs/.checksums -- not a
# static map $-ref (the harvested path is a run-time value; see mapper.py's
# _primary_output docstring for why a committed map rule cannot name it).
# ---------------------------------------------------------------------------

def test_a_per_sample_rules_checksum_attaches_to_the_matching_samples_own_file():
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD),
        manifest.SampleRecord(nfcore_sample="CONTROL_REP2", d_seq_uid="D.SEQ-2",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.outputs = [
        manifest.OutputRecord(path="star_salmon/CONTROL_REP1.markdup.sorted.bam",
                              bytes=100, sample="CONTROL_REP1"),
        manifest.OutputRecord(path="star_salmon/CONTROL_REP2.markdup.sorted.bam",
                              bytes=100, sample="CONTROL_REP2"),
    ]
    run.checksums = {
        "star_salmon/CONTROL_REP1.markdup.sorted.bam": "aaa111",
        "star_salmon/CONTROL_REP2.markdup.sorted.bam": "bbb222",
    }
    result = mapper.apply(run, maps.load("rnaseq"))
    aln_by_sample = {r.nfcore_sample: r for r in result.rows if r.sample_type == "A.ALN"}
    assert aln_by_sample["CONTROL_REP1"].attributes["Checksum_PrimaryData"].value == "aaa111"
    assert aln_by_sample["CONTROL_REP2"].attributes["Checksum_PrimaryData"].value == "bbb222"
    # Never each other's -- a per-sample checksum must not leak across rows.
    assert aln_by_sample["CONTROL_REP1"].attributes["Checksum_PrimaryData"].value != "bbb222"


def test_a_per_run_rules_checksum_attaches_from_the_single_run_wide_file():
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.outputs = [
        manifest.OutputRecord(path="star_salmon/salmon.merged.gene_counts.tsv", bytes=50),
    ]
    run.checksums = {"star_salmon/salmon.merged.gene_counts.tsv": "ccc333"}
    result = mapper.apply(run, maps.load("rnaseq"))
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert gex.attributes["Checksum_PrimaryData"].value == "ccc333"


def test_no_checksum_at_all_is_the_advisory_path_row_still_renders():
    # A manifest with no checksums (the default -- `outputs` and `checksums`
    # both empty) must not be a new hard dependency: rows ship exactly as
    # before, simply without Checksum_PrimaryData.
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    result = mapper.apply(run, maps.load("rnaseq"))
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert "Checksum_PrimaryData" not in aln.attributes
    assert "Checksum_PrimaryData" not in gex.attributes


def test_a_checksum_for_a_path_the_rule_does_not_match_is_not_attached():
    # An inventoried, checksummed file that is not this rule's primary output
    # (e.g. the .bai index, or an unrelated file) must not leak in.
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.outputs = [
        manifest.OutputRecord(path="star_salmon/CONTROL_REP1.markdup.sorted.bam.bai",
                              bytes=10, sample="CONTROL_REP1"),
    ]
    run.checksums = {"star_salmon/CONTROL_REP1.markdup.sorted.bam.bai": "deadbeef"}
    result = mapper.apply(run, maps.load("rnaseq"))
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    assert "Checksum_PrimaryData" not in aln.attributes


def test_the_brace_glob_matches_whichever_aligner_directory_was_actually_used():
    # rule.glob is "{star_salmon,star_rsem,hisat2}/*.markdup.sorted.bam" --
    # every alternative must match, not only the first.
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.params["aligner"] = "hisat2"
    run.outputs = [
        manifest.OutputRecord(path="hisat2/CONTROL_REP1.markdup.sorted.bam",
                              bytes=100, sample="CONTROL_REP1"),
    ]
    run.checksums = {"hisat2/CONTROL_REP1.markdup.sorted.bam": "hisat2sum"}
    result = mapper.apply(run, maps.load("rnaseq"))
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    assert aln.attributes["Checksum_PrimaryData"].value == "hisat2sum"


def test_expand_braces_handles_a_pattern_with_no_braces():
    assert mapper._expand_braces("plain/*.bam") == ["plain/*.bam"]


def test_expand_braces_expands_one_group():
    assert mapper._expand_braces("{a,b,c}/*.bam") == ["a/*.bam", "b/*.bam", "c/*.bam"]


def test_expand_braces_cross_multiplies_two_groups():
    # "harvest_globs" in the same committed map file (rnaseq.outputs.json)
    # already uses two groups in one pattern
    # ("{star_salmon,star_rsem,hisat2}/samtools_stats/*.{flagstat,idxstats,stats}"),
    # so an output rule glob adopting the same two-group style must not
    # silently degrade to matching nothing (Important 4, 2026-09-17 review).
    assert mapper._expand_braces("a/{x,y}/*.{p,q}") == [
        "a/x/*.p", "a/x/*.q", "a/y/*.p", "a/y/*.q"]


# ---------------------------------------------------------------------------
# File_PrimaryData: the same fix that unblocks the render on a populated
# catalog (Critical 1, 2026-09-17 review) -- without it, neither
# File_PrimaryData nor Link_PrimaryData was ever produced, so A.ALN/A.GEX
# HARD_REJECT on the catalog's own ALTERNATIVE_REQUIRED_GROUPS gate
# (reingest_qa.py) on any populated catalog, and granular.py never calls
# render_upload_workbook for them.
# ---------------------------------------------------------------------------

def test_file_primary_data_is_set_from_the_same_primary_output_as_the_checksum():
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.outputs = [
        manifest.OutputRecord(path="star_salmon/CONTROL_REP1.markdup.sorted.bam",
                              bytes=100, sample="CONTROL_REP1"),
    ]
    run.checksums = {"star_salmon/CONTROL_REP1.markdup.sorted.bam": "aaa111"}
    result = mapper.apply(run, maps.load("rnaseq"))
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    # Basename, not the harvested run-relative path -- see _attach_checksum's
    # docstring for the seed-data evidence (real File_PrimaryData values are
    # overwhelmingly bare filenames, never a path with a directory).
    assert aln.attributes["File_PrimaryData"].value == "CONTROL_REP1.markdup.sorted.bam"
    assert aln.attributes["Checksum_PrimaryData"].value == "aaa111"


def test_file_primary_data_is_set_even_when_nothing_has_been_checksummed_yet():
    # The harvest-only case: run-harvest has populated `outputs`, but
    # run-checksum was never called (or skipped, per the agent recipe's own
    # advice). File_PrimaryData must still be set from the inventory alone
    # -- it does not depend on a checksum having been computed.
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.outputs = [
        manifest.OutputRecord(path="star_salmon/CONTROL_REP1.markdup.sorted.bam",
                              bytes=100, sample="CONTROL_REP1"),
        manifest.OutputRecord(path="star_salmon/all.merged.gene_counts.tsv", bytes=50),
    ]
    result = mapper.apply(run, maps.load("rnaseq"))
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert aln.attributes["File_PrimaryData"].value == "CONTROL_REP1.markdup.sorted.bam"
    assert gex.attributes["File_PrimaryData"].value == "all.merged.gene_counts.tsv"
    assert "Checksum_PrimaryData" not in aln.attributes
    assert "Checksum_PrimaryData" not in gex.attributes


def test_no_output_inventory_at_all_still_sets_no_file_primary_data():
    # Negative control, symmetric with test_no_checksum_at_all_is_the_
    # advisory_path_row_still_renders above: with no `outputs` at all
    # (never a real run-harvest shape, but must not crash), neither
    # File_PrimaryData nor Checksum_PrimaryData is set -- exactly the
    # pre-fix behaviour, not a new failure mode.
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    result = mapper.apply(run, maps.load("rnaseq"))
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    assert "File_PrimaryData" not in aln.attributes
    assert "Checksum_PrimaryData" not in aln.attributes


# ---------------------------------------------------------------------------
# _primary_output must prefer a checksummed candidate over a merely
# alphabetically-first one (Important 3, 2026-09-17 review) -- otherwise an
# agent that paid for an SSH hash of the aligner the run actually used
# (e.g. star_salmon) can have that checksum silently discarded because a
# DIFFERENT, unhashed candidate (e.g. salmon/) sorts first.
# ---------------------------------------------------------------------------

def test_primary_output_prefers_the_checksummed_candidate_over_sorted_first():
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    # nf-core/rnaseq --aligner star_salmon --pseudo_aligner salmon publishes
    # the SAME-named gene-counts matrix under both directories; "salmon/"
    # sorts before "star_salmon/" alphabetically, but only "star_salmon/"
    # (the aligner the run actually used) was hashed.
    run.outputs = [
        manifest.OutputRecord(path="salmon/all.merged.gene_counts.tsv", bytes=50),
        manifest.OutputRecord(path="star_salmon/all.merged.gene_counts.tsv", bytes=50),
    ]
    run.checksums = {"star_salmon/all.merged.gene_counts.tsv": "star0salmon0checksum"}
    result = mapper.apply(run, maps.load("rnaseq"))
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert gex.attributes["Checksum_PrimaryData"].value == "star0salmon0checksum"
    assert gex.attributes["File_PrimaryData"].value == "all.merged.gene_counts.tsv"


def test_primary_output_still_breaks_ties_by_sorted_path_when_none_is_checksummed():
    # Same two candidates, neither hashed -- sorted-first ("salmon/") stays
    # the deterministic tie-break, unchanged from before Important 3's fix.
    #
    # Selects the A.GEX rule by `sample_type`, not `outputs[1]`: a positional
    # index silently retargets to a different rule if `rnaseq.outputs.json`
    # is ever reordered (Minor 6, 2026-09-17 review). The old
    # `File_PrimaryData.value == "all.merged.gene_counts.tsv"` assertion is
    # dropped rather than kept alongside: both candidates share that
    # basename by construction, so it passed no matter which one won and
    # exercised nothing that `_primary_output(...).path` below does not
    # already cover for real.
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.outputs = [
        manifest.OutputRecord(path="star_salmon/all.merged.gene_counts.tsv", bytes=50),
        manifest.OutputRecord(path="salmon/all.merged.gene_counts.tsv", bytes=50),
    ]
    gex_rule = next(o for o in maps.load("rnaseq").outputs if o.sample_type == "A.GEX")
    primary = mapper._primary_output(gex_rule, run, None)
    assert primary.path == "salmon/all.merged.gene_counts.tsv"


def test_ambiguous_primary_candidates_are_reported_but_still_set_a_value():
    # Minor 1 (2026-09-17 review): the checksummed-preference fix only helps
    # when the agent actually hashed something -- the recipe usually says to
    # SKIP the checksum step, so the common path is exactly this one, with
    # `checksums == {}` and sorted-first the only tie-break. Because the
    # rendered value is a basename, the two candidates are textually
    # identical on the Samples sheet, so the silent pick must be reported
    # somewhere a curator can see it: on the attribute's own `candidates`
    # list (see mapper.py's `MappedAttribute.candidates` docstring), never
    # dropped even though a value is still set so the render stays unblocked.
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.outputs = [
        manifest.OutputRecord(path="star_salmon/all.merged.gene_counts.tsv", bytes=50),
        manifest.OutputRecord(path="salmon/all.merged.gene_counts.tsv", bytes=50),
    ]
    result = mapper.apply(run, maps.load("rnaseq"))
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    attr = gex.attributes["File_PrimaryData"]
    # A value is still set -- the render stays unblocked.
    assert attr.value == "all.merged.gene_counts.tsv"
    # ...but the ambiguity is reported: both candidates are named, and the
    # winner is identifiable by its full, directory-qualified path (unlike
    # `value`, which cannot show the difference).
    assert set(attr.candidates) == {
        "star_salmon/all.merged.gene_counts.tsv", "salmon/all.merged.gene_counts.tsv"}
    assert attr.source_file == "salmon/all.merged.gene_counts.tsv"


def test_a_single_checksummed_candidate_is_not_reported_as_ambiguous():
    # The counterpart to the test above: when a checksum genuinely singles
    # out one candidate (Important 3's own fix), that is a real, evidenced
    # pick, not a silent one -- `candidates` must stay empty.
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.outputs = [
        manifest.OutputRecord(path="salmon/all.merged.gene_counts.tsv", bytes=50),
        manifest.OutputRecord(path="star_salmon/all.merged.gene_counts.tsv", bytes=50),
    ]
    run.checksums = {"star_salmon/all.merged.gene_counts.tsv": "star0salmon0checksum"}
    result = mapper.apply(run, maps.load("rnaseq"))
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert gex.attributes["File_PrimaryData"].candidates == []

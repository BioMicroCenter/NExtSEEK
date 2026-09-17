from NessieAI.ns.reingest import manifest, mapper, maps


def _run(metrics=None, derived=None, params=None, software_versions=None):
    return manifest.RunManifest(
        run_dir="/net/cluster/runs/r",
        params=params or {"genome": "GRCm39", "aligner": "star_salmon"},
        software_versions=software_versions or {},
        pipeline=manifest.PipelineInfo(name="nf-core/rnaseq", version="3.18.0"),
        samples=[manifest.SampleRecord(
            nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-EXAMPLE-1",
            uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
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


def test_provenance_attributes_are_applied_to_the_analysis_rows():
    result = mapper.apply(_run(), maps.load("rnaseq"))
    analysis = [r for r in result.rows if r.sample_type.startswith("A.")]
    assert analysis, "expected at least one analysis row"
    assert analysis[0].attributes["ReferenceGenome"].value == "GRCm39"


def test_a_multirun_sample_produces_no_d_seq_row_no_per_sample_row_and_no_parent_in_the_join():
    # uid_resolve.resolve() discards a multi-run sample's source UIDs
    # entirely (d_seq_uid=None), so there is nowhere in the manifest to
    # carry the list the spec would otherwise want -- this pins that as a
    # real limitation, not an oversight (see mapper._per_sample_rows).
    run = _run(metrics={"star-uniquely_mapped_percent": 91.4,
                        "Kraken2_bracken_fraction": 3.2})
    run.samples[0].uid_resolution = manifest.RESOLUTION_MULTIRUN
    run.samples[0].d_seq_uid = None
    result = mapper.apply(run, maps.load("rnaseq"))
    # No D.SEQ backfill row for the multirun sample...
    assert not any(r.sample_type == "D.SEQ" for r in result.rows)
    # ...and no per_sample A.ALN row either. Unlike an unresolved sample
    # (which ships its child with no Parent -- see
    # test_an_unresolved_sample_still_ships_its_child_with_no_parent), a
    # multi-run sample gets no row at all: the spec table does want its
    # child to carry a `;`-joined Parent across every contributing D.SEQ,
    # but uid_resolve.resolve() discards those source UIDs entirely and
    # SampleRecord cannot carry a list, so there is nothing here that could
    # honestly be set. This is the module-level gap `_per_sample_rows`
    # documents as tracked separately, not implemented here.
    assert not any(r.sample_type == "A.ALN" for r in result.rows)
    # The per_run A.GEX rule still emits its one row: its literal attributes
    # (Matrix, MatrixDataType, DataType) do not depend on any sample
    # resolving...
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    # ...but the multirun sample contributes nothing to the join, and since
    # no other sample resolved either, Parent is omitted rather than set to
    # an empty or fabricated value.
    assert "Parent" not in gex.attributes
    # ...and the sample's own metrics are still walked: an unknown key on it
    # still reaches unmapped rather than being silently swallowed along with
    # the D.SEQ row.
    assert any(u["raw_key"] == "Kraken2_bracken_fraction" for u in result.unmapped)


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

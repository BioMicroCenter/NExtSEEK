import pytest

from NessieAI.ns.reingest import manifest, mapper, maps
from NessieAI.ns.reingest.pipelines import common


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


def test_5_3utr_reads_multiqc_1_22s_general_stats_column_name():
    # MultiQC 1.22 and earlier name the general-stats column <namespace>_<rid>,
    # so the same Qualimap 5'-3' bias arrives under a different key.
    old_key = "QualiMap_mqc-generalstats-qualimap-5_3_bias"
    result = mapper.apply(_run(metrics={old_key: 0.62}), maps.load("rnaseq"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["5_3UTR"].value == 0.62
    assert row.attributes["5_3UTR"].raw_key == old_key
    assert not any(u["raw_key"] == old_key for u in result.unmapped)


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
    assert "MaxReads" in pipeline_map.ruled_out()
    result = mapper.apply(
        _run(metrics={"MaxReads": 18.4,
                      "Some_Definitely_Unknown_Metric_XYZ": 1.0}),
        pipeline_map)
    # The ruling specifically excludes its own key...
    assert not any(u["raw_key"] == "MaxReads" for u in result.unmapped)
    # ...but unmapped is genuinely populated: a second, genuinely unknown key
    # on the same sample still surfaces as evidence.
    assert any(u["raw_key"] == "Some_Definitely_Unknown_Metric_XYZ" for u in result.unmapped)


# MultiQC general-stats keys whose quantity D.SEQ already records another way.
COVERED_RNASEQ_KEYS = (
    "qualimap_rnaseq-reads_aligned",
    "qualimap_rnaseq-reads_aligned_exonic",
    "qualimap_rnaseq-reads_aligned_intronic",
    "qualimap_rnaseq-reads_aligned_intergenic",
    "qualimap_rnaseq-reads_aligned_overlapping_exon",
    "star-mapped",
    "salmon-strand_mapping_bias",
)

# Keys judged not worth recording on D.SEQ: sums of recorded values, counts
# in millions or of BAM records, near-constant shares, and Salmon diagnostics.
LOW_VALUE_RNASEQ_KEYS = (
    "star-mapped_percent",
    "salmon-num_mapped",
    "salmon-compatible_fragment_ratio",
    "salmon-library_types",
    "samtools_flagstat-flagstat_total",
    "samtools_flagstat-mapped_passed",
    "samtools_flagstat-mapped_passed_pct",
    "samtools_stats-reads_mapped",
    "samtools_stats-non_primary_alignments",
    "samtools_stats-reads_properly_paired_percent",
    "rseqc_bam_stat-proper_pairs_percent",
    "samtools_stats-reads_MQ0_percent",
    "samtools_stats-insert_size_average",
)


@pytest.mark.parametrize("raw_key", COVERED_RNASEQ_KEYS + LOW_VALUE_RNASEQ_KEYS)
def test_a_key_d_seq_already_covers_is_ruled_out_and_never_queued(raw_key):
    pipeline_map = maps.load("rnaseq")
    assert raw_key in pipeline_map.ruled_out()
    result = mapper.apply(_run(metrics={raw_key: 1.0}), pipeline_map)
    assert not any(u["raw_key"] == raw_key for u in result.unmapped)


@pytest.mark.parametrize("attribute, metrics, derived, expected, raw_key", [
    ("DuplicationPercent", {"picard_mark_duplicates-PERCENT_DUPLICATION": 76.4293}, {},
     76.4293, "picard_mark_duplicates-PERCENT_DUPLICATION"),
    ("MismatchPercent", {"samtools_stats-error_rate": 0.5994653}, {},
     0.5994653, "samtools_stats-error_rate"),
    ("DupRadarIntercept", {"custom_content_dupradar-dupRadar_intercept": 0.653985557}, {},
     0.653985557, "custom_content_dupradar-dupRadar_intercept"),
    ("MultimappedPercent", {}, {"multimapped_pct": 9.3038}, 9.3038, "$derived.multimapped_pct"),
    ("Strandedness", {}, {"strandedness": "reverse"}, "reverse", "$derived.strandedness"),
])
def test_the_new_d_seq_qc_attributes_are_mapped(attribute, metrics, derived, expected, raw_key):
    result = mapper.apply(_run(metrics=metrics, derived=derived), maps.load("rnaseq"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes[attribute].value == expected
    assert row.attributes[attribute].raw_key == raw_key
    assert not result.unmapped


def test_star_multimapped_millions_stay_out_of_multimapped_percent():
    # MultiQC's star-multimapped is a count in millions; the percentage comes
    # from the STAR log, so the count alone must not fill the attribute.
    result = mapper.apply(_run(metrics={"star-multimapped": 0.427914}), maps.load("rnaseq"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert "MultimappedPercent" not in row.attributes
    assert not any(u["raw_key"] == "star-multimapped" for u in result.unmapped)


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
    # "MaxReads" is a real deliberately_unmapped key in the committed rnaseq
    # map. No committed rule claims it as a "from" or alternate, so we give it
    # to an approved rule instead -- a realistic way a key ends up BOTH
    # ruled_out (committed map) AND claimed (approved rule) at once.
    pipeline_map = maps.load("rnaseq")
    assert "MaxReads" in pipeline_map.ruled_out()
    approved = {"MaxReads": maps.AttributeRule(
        **{"from": "MaxReads", "target": "D.SEQ",
           "datatype": "number", "provenance": "approved 2026-09-15 by tester"})}
    result = mapper.apply(
        _run(metrics={"MaxReads": 18.4}),
        pipeline_map, approved_rules=approved)
    assert not any(u["raw_key"] == "MaxReads" for u in result.unmapped)
    # And the approved rule genuinely fired -- this isn't passing merely
    # because the key was skipped for an unrelated reason.
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["MaxReads"].value == 18.4
    assert row.attributes["MaxReads"].origin == mapper.ORIGIN_APPROVED


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
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                              parent_sample_type="D.SEQ"),
        manifest.SampleRecord(nfcore_sample="CONTROL_REP2", d_seq_uid="D.SEQ-2",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                              parent_sample_type="D.SEQ"))
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
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                              parent_sample_type="D.SEQ"),
        manifest.SampleRecord(nfcore_sample="CONTROL_REP2", d_seq_uid="D.SEQ-2",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                              parent_sample_type="D.SEQ"))
    result = mapper.apply(run, maps.load("rnaseq"))
    gex_rows = [r for r in result.rows if r.sample_type == "A.GEX"]
    assert len(gex_rows) == 1
    # Join order follows the manifest's own (samplesheet) sample order.
    assert gex_rows[0].attributes["Parent"].value == "D.SEQ-1;D.SEQ-2"


def test_the_per_run_join_deduplicates_a_repeated_d_seq_uid():
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="S1_LANE1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                              parent_sample_type="D.SEQ"),
        manifest.SampleRecord(nfcore_sample="S1_LANE2", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                              parent_sample_type="D.SEQ"))
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
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                              parent_sample_type="D.SEQ"),
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


# Every SampleRecord in this file is hand-built, so a new field on the model
# arrives here as a DEFAULT nobody chose. That is not hypothetical: when
# `parent_sample_type` was added, fixtures that never set it silently stopped
# producing a backfill row, and the tests that assert a row is ABSENT kept
# passing -- for the wrong reason. This fails by name the next time the model
# grows a field, so whoever adds it decides what these fixtures should claim
# rather than inheriting a default by accident.
_SAMPLE_RECORD_FIELDS_KNOWN_HERE = {
    "nfcore_sample", "fastq_1", "fastq_2", "d_seq_uid", "d_seq_uid_multirun",
    "uid_resolution", "parent_sample_type", "strandedness_declared",
    "strandedness_inferred", "metrics", "derived",
}


def test_sample_record_has_not_grown_a_field_these_fixtures_ignore():
    actual = set(manifest.SampleRecord.model_fields)
    new = actual - _SAMPLE_RECORD_FIELDS_KNOWN_HERE
    gone = _SAMPLE_RECORD_FIELDS_KNOWN_HERE - actual
    assert not new, (
        f"SampleRecord gained {sorted(new)}. Decide what the hand-built "
        "fixtures in this file should set for it -- a default nobody chose "
        "can make an absence assertion pass for the wrong reason -- then add "
        "it to _SAMPLE_RECORD_FIELDS_KNOWN_HERE.")
    assert not gone, (
        f"SampleRecord no longer has {sorted(gone)}; drop it from "
        "_SAMPLE_RECORD_FIELDS_KNOWN_HERE and from any fixture that sets it.")


def test_an_ambiguous_sample_is_refused_on_its_resolution_alone():
    """The existing ambiguous test below cannot prove WHY the row is absent.

    Its fixture has `d_seq_uid=None` and no `parent_sample_type`, and the
    backfill is guarded on all three -- resolution, uid, and parent type -- so
    that assertion passes if ANY one of them fires. Delete the
    `_NO_BACKFILL` membership check entirely and it still passes, on the uid
    guard alone.

    So this one hands the sample a uid AND a parent type it has no business
    having, leaving its AMBIGUOUS resolution as the only thing that can
    refuse it. That is also the defence-in-depth case that matters: a stray
    uid on an ambiguous sample must not become a Parent, because "matches
    more than one D.SEQ" is a hard reject and never a guess.
    """
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1",
                              d_seq_uid="D.SEQ-STRAY",
                              uid_resolution=manifest.RESOLUTION_AMBIGUOUS,
                              parent_sample_type="D.SEQ"))
    result = mapper.apply(run, maps.load("rnaseq"))
    assert not any(r.sample_type == "D.SEQ" for r in result.rows)
    assert not any(r.sample_type == "A.ALN" for r in result.rows)
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert "Parent" not in gex.attributes


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
    # really is D.SEQ -- must not emit any of the QC-backfill warnings. This
    # manifest has no outputs, so the output rules' "matched no file" warnings
    # are expected and are not what this guards.
    result = mapper.apply(
        _run(metrics={"star-uniquely_mapped_percent": 91.4,
                      "kraken2-pct_unclassified": 4.5}),
        maps.load("rnaseq"))
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert row.attributes["MappedPercent"].value == 91.4
    assert row.attributes["ContamPercent"].value == 4.5
    assert [w for w in result.warnings if "matched no file" not in w] == []


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


def test_differentialabundance_1x_registers_its_normalised_counts_as_one_run_level_gex():
    # The 1.4/1.5 layout is flat: DESEQ2_NORM publishes all.<kind>.tsv straight
    # into tables/processed_abundance/. Only the normalised counts become a
    # row; the per-contrast results tables and the vst/rlog matrix do not.
    run = manifest.RunManifest(
        run_dir="/net/cluster/runs/240404Kel_diffabun",
        params={},
        pipeline=manifest.PipelineInfo(name="nf-core/differentialabundance", version="1.4.0"),
        samples=[
            manifest.SampleRecord(nfcore_sample="WT_1", d_seq_uid="D.SEQ-1",
                                  uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                                  parent_sample_type="D.SEQ"),
            manifest.SampleRecord(nfcore_sample="WT_2", d_seq_uid="D.SEQ-2",
                                  uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                                  parent_sample_type="D.SEQ")])
    run.outputs = [
        manifest.OutputRecord(path="tables/processed_abundance/all.normalised_counts.tsv", bytes=50),
        manifest.OutputRecord(path="tables/processed_abundance/all.vst.tsv", bytes=50),
        manifest.OutputRecord(path="other/deseq2/WT_vs_KO.dds.rld.rds", bytes=50),
    ]
    run.named_outputs = {"differentialabundance_report_html": "report/study.html"}
    result = mapper.apply(run, maps.load("differentialabundance"))

    gex = [r for r in result.rows if r.sample_type == "A.GEX"]
    assert len(gex) == 1
    attrs = {k: v.value for k, v in gex[0].attributes.items()}
    assert attrs["File_PrimaryData"] == "all.normalised_counts.tsv"
    assert attrs["Matrix"] == "normalised_counts"
    assert attrs["DataType"] == "TSV"
    assert attrs["Pipeline"] == "nf-core/differentialabundance"
    assert attrs["PipelineVersion"] == "1.4.0"
    # The pipeline has no MultiQC; its own HTML report is the QC link.
    assert attrs["Link_QualityControl"] == "report/study.html"
    assert attrs["Parent"] == "D.SEQ-1;D.SEQ-2"


def _differentialabundance_run(*paths):
    run = manifest.RunManifest(
        run_dir="/net/cluster/runs/250818Rib_diffabun",
        params={},
        pipeline=manifest.PipelineInfo(name="nf-core/differentialabundance", version="v1.5.0"),
        samples=[
            manifest.SampleRecord(nfcore_sample="d11311.1.1", d_seq_uid="D.SEQ-1",
                                  uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                                  parent_sample_type="D.SEQ"),
            manifest.SampleRecord(nfcore_sample="d11312.1.2", d_seq_uid="D.SEQ-2",
                                  uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                                  parent_sample_type="D.SEQ")])
    run.outputs = [manifest.OutputRecord(path=p, bytes=50) for p in paths]
    return run


def test_differentialabundance_registers_one_gex_per_contrast_named_by_its_contrast():
    run = _differentialabundance_run(
        "tables/differential/LPS_MUC2_vs_Ctrl.deseq2.results.tsv",
        "tables/differential/LPS_MUC2_vs_Ctrl.deseq2.results_filtered.tsv",
        "tables/differential/LPS_vs_Ctrl.deseq2.results.tsv",
        "tables/differential/LPS_vs_Ctrl.deseq2.results_filtered.tsv",
        "tables/differential/MUC2_vs_Ctrl.deseq2.results.tsv",
        "tables/differential/MUC2_vs_Ctrl.deseq2.results_filtered.tsv",
    )
    run.checksums = {"tables/differential/LPS_vs_Ctrl.deseq2.results.tsv": "abc123"}
    result = mapper.apply(run, maps.load("differentialabundance"))

    rows = [r for r in result.rows if r.sample_type == "A.GEX"
            and r.attributes["Matrix"].value == "deseq2_results"]
    by_contrast = {r.attributes["Contrast"].value: r for r in rows}
    # One row per unfiltered table; the 1.5 *_filtered.tsv subsets are not rows.
    assert sorted(by_contrast) == ["LPS_MUC2_vs_Ctrl", "LPS_vs_Ctrl", "MUC2_vs_Ctrl"]
    lps = {k: v.value for k, v in by_contrast["LPS_vs_Ctrl"].attributes.items()}
    assert lps["File_PrimaryData"] == "LPS_vs_Ctrl.deseq2.results.tsv"
    assert lps["Checksum_PrimaryData"] == "abc123"
    assert lps["Matrix"] == "deseq2_results"
    assert lps["Parent"] == "D.SEQ-1;D.SEQ-2"
    # Each row names only its own file: no other contrast's checksum or candidates.
    muc2 = by_contrast["MUC2_vs_Ctrl"].attributes
    assert muc2["File_PrimaryData"].value == "MUC2_vs_Ctrl.deseq2.results.tsv"
    assert muc2["File_PrimaryData"].candidates == []
    assert "Checksum_PrimaryData" not in muc2


def test_a_limma_run_registers_its_contrasts_with_the_limma_matrix_label():
    run = _differentialabundance_run("tables/differential/KO_vs_WT.limma.results.tsv")
    result = mapper.apply(run, maps.load("differentialabundance"))
    (row,) = [r for r in result.rows if r.sample_type == "A.GEX"
              and r.attributes["Matrix"].value == "limma_results"]
    assert row.attributes["Contrast"].value == "KO_vs_WT"
    assert row.attributes["Matrix"].value == "limma_results"


def test_rows_built_together_get_distinct_name_ordinals():
    # next_name_ordinal reads the saved catalog, which cannot see rows built in
    # the same call: every A.GEX row would otherwise ask, get the same "next"
    # number, and share a Name.
    run = _differentialabundance_run(
        "tables/processed_abundance/all.normalised_counts.tsv",
        "tables/differential/LPS_vs_Ctrl.deseq2.results.tsv",
        "tables/differential/MUC2_vs_Ctrl.deseq2.results.tsv",
    )
    run.params = {"trace_report_suffix": "2025-09-16_11-59-28"}
    calls = []

    def catalog_next(sample_type, prefix):
        calls.append((sample_type, prefix))
        return 5

    result = mapper.apply(run, maps.load("differentialabundance"),
                          context={"next_name_ordinal": catalog_next})
    names = sorted(r.attributes["Name"].value for r in result.rows
                   if r.sample_type == "A.GEX" and "Name" in r.attributes)
    assert len(names) == 3
    assert len(set(names)) == 3
    assert [n.rsplit("_", 1)[1] for n in names] == ["5", "6", "7"]
    # The catalog is asked once per prefix, not once per row.
    assert len(calls) == 1


def test_file_stem_is_blank_outside_a_per_file_row():
    # @file_stem names the file a per_file row was built from; any other row
    # has no single file to name and gets a blank, never a guess.
    run = _differentialabundance_run("tables/processed_abundance/all.normalised_counts.tsv")
    assert mapper._resolve_value("@file_stem", run, None, {}) is None


def _differentialabundance_2_run(*paths):
    run = _differentialabundance_run(*paths)
    run.pipeline = manifest.PipelineInfo(name="nf-core/differentialabundance", version="2.0.0")
    run.params = {"study_name": "SRP254919"}
    return run


def test_differentialabundance_2_registers_every_paramset_and_method():
    # Paths as nf-core/differentialabundance 2.0.0's own test snapshots publish
    # them: <folder>/<paramset>/, and results named <contrast>_<study>.<method>.
    run = _differentialabundance_2_run(
        "tables/processed_abundance/rnaseq_deseq2/all.normalised_counts.tsv",
        "tables/processed_abundance/rnaseq_dream/all.normalised_counts.tsv",
        "tables/differential/rnaseq_deseq2/interaction_genotype_treatment_SRP254919.deseq2.results.tsv",
        "tables/differential/rnaseq_deseq2/interaction_genotype_treatment_SRP254919_deseq2.annotated.tsv",
        "tables/differential/rnaseq_dream/interaction_genotype_treatment_SRP254919.dream.results.tsv",
        "tables/differential/rnaseq_dream/full_model_with_interactions_SRP254919.dream.results.tsv",
        "tables/differential/rnaseq_dream/full_model_with_interactions_SRP254919_dream.annotated.tsv",
    )
    result = mapper.apply(run, maps.load("differentialabundance"))
    gex = [r for r in result.rows if r.sample_type == "A.GEX"]

    by_matrix = {}
    for row in gex:
        by_matrix.setdefault(row.attributes["Matrix"].value, []).append(row)
    # One normalised matrix per paramset, not only the first.
    assert len(by_matrix["normalised_counts"]) == 2
    # The annotated joins are not results tables and get no row.
    assert len(by_matrix["deseq2_results"]) == 1
    dream = {r.attributes["Contrast"].value: r for r in by_matrix["dream_results"]}
    # The study name 2.0.0 appends to the contrast id is not part of the contrast.
    assert sorted(dream) == ["full_model_with_interactions", "interaction_genotype_treatment"]
    assert by_matrix["deseq2_results"][0].attributes["Contrast"].value == "interaction_genotype_treatment"


def test_a_file_name_shared_across_paramsets_keeps_its_folder():
    # 2.0.0 can run one contrast under two paramsets of the same method; both
    # tables then share a file name, and File_PrimaryData must still tell them apart.
    run = _differentialabundance_2_run(
        "tables/differential/generic_matrix_dream/full_model_with_interactions_SRP254919.dream.results.tsv",
        "tables/differential/rnaseq_dream/full_model_with_interactions_SRP254919.dream.results.tsv",
        "tables/differential/rnaseq_dream/interaction_genotype_treatment_SRP254919.dream.results.tsv",
    )
    result = mapper.apply(run, maps.load("differentialabundance"))
    files = sorted(r.attributes["File_PrimaryData"].value for r in result.rows
                   if r.sample_type == "A.GEX" and r.attributes["Matrix"].value == "dream_results")
    assert files == [
        "generic_matrix_dream/full_model_with_interactions_SRP254919.dream.results.tsv",
        "interaction_genotype_treatment_SRP254919.dream.results.tsv",
        "rnaseq_dream/full_model_with_interactions_SRP254919.dream.results.tsv",
    ]


def test_a_1x_contrast_ending_in_the_study_name_is_left_whole():
    # Only 2.x appends the study name; a 1.x contrast that happens to end with
    # it is the whole contrast id.
    run = _differentialabundance_run("tables/differential/LPS_vs_Ctrl.deseq2.results.tsv")
    run.params = {"study_name": "Ctrl"}
    (row,) = [r for r in mapper.apply(run, maps.load("differentialabundance")).rows
              if r.sample_type == "A.GEX" and "Contrast" in r.attributes]
    assert row.attributes["Contrast"].value == "LPS_vs_Ctrl"


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
    # rule.candidates[0] is "{star_salmon,star_rsem,hisat2}/*.markdup.sorted.bam" --
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


def test_the_aligners_matrix_wins_over_the_pseudo_aligners_when_none_is_checksummed():
    # nf-core/rnaseq 3.22 runs Salmon beside STAR, so star_salmon/ and salmon/ both
    # publish a merged gene-counts matrix. The aligner's is the run's result: the rule
    # lists star_salmon/ (and star_rsem/) ahead of salmon/ and kallisto/, so the pick is
    # by candidate order, not by an alphabetical tie-break that chose salmon/ (seen live).
    #
    # Selects the A.GEX rule by `sample_type`, not `outputs[1]`: a positional
    # index silently retargets to a different rule if `rnaseq.outputs.json`
    # is ever reordered (Minor 6, 2026-09-17 review).
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.outputs = [
        manifest.OutputRecord(path="salmon/all.merged.gene_counts.tsv", bytes=50),
        manifest.OutputRecord(path="star_salmon/all.merged.gene_counts.tsv", bytes=50),
    ]
    gex_rule = next(o for o in maps.load("rnaseq").outputs if o.sample_type == "A.GEX")
    primary = mapper._primary_output(gex_rule, run, None)
    assert primary.path == "star_salmon/all.merged.gene_counts.tsv"
    gex = next(r for r in mapper.apply(run, maps.load("rnaseq")).rows if r.sample_type == "A.GEX")
    assert gex.attributes["File_PrimaryData"].candidates == []   # nothing left to confirm


def test_a_salmon_only_run_still_takes_the_salmon_matrix():
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.outputs = [manifest.OutputRecord(path="salmon/all.merged.gene_counts.tsv", bytes=50)]
    gex_rule = next(o for o in maps.load("rnaseq").outputs if o.sample_type == "A.GEX")
    assert mapper._primary_output(gex_rule, run, None).path == "salmon/all.merged.gene_counts.tsv"


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
        manifest.OutputRecord(path="star_rsem/all.merged.gene_counts.tsv", bytes=50),
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
        "star_salmon/all.merged.gene_counts.tsv", "star_rsem/all.merged.gene_counts.tsv"}
    assert attr.source_file == "star_rsem/all.merged.gene_counts.tsv"


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


# ── secondary data ─────────────────────────────────────────────────────────
# secondary_data_glob sat in the map schema and in the rnaseq map, read by
# nothing: every .bam.bai was inventoried and then dropped, leaving four A.ALN
# attributes blank for no reason other than a missing reader.

def _run_with_alignment_outputs(checksums=None, extra=()):
    run = _run()
    paths = ["star_salmon/CONTROL_REP1.markdup.sorted.bam",
             "star_salmon/CONTROL_REP1.markdup.sorted.bam.bai", *extra]
    run.outputs = [manifest.OutputRecord(path=p, bytes=10, sample="CONTROL_REP1")
                   for p in paths]
    run.checksums = dict(checksums or {})
    return run


def _aln_row(run):
    result = mapper.apply(run, maps.load("rnaseq"))
    return next(r for r in result.rows if r.sample_type == "A.ALN")


def test_the_bai_beside_the_bam_becomes_file_secondarydata():
    row = _aln_row(_run_with_alignment_outputs())
    assert row.attributes["File_PrimaryData"].value == "CONTROL_REP1.markdup.sorted.bam"
    assert row.attributes["File_SecondaryData"].value == "CONTROL_REP1.markdup.sorted.bam.bai"


def test_secondary_data_is_a_basename_never_a_path():
    """Same reason as File_PrimaryData: the server resolves it per sample."""
    row = _aln_row(_run_with_alignment_outputs())
    assert "/" not in row.attributes["File_SecondaryData"].value


def test_the_secondary_checksum_comes_from_the_bai_not_the_bam():
    run = _run_with_alignment_outputs(checksums={
        "star_salmon/CONTROL_REP1.markdup.sorted.bam": "aaa",
        "star_salmon/CONTROL_REP1.markdup.sorted.bam.bai": "bbb"})
    row = _aln_row(run)
    assert row.attributes["Checksum_PrimaryData"].value == "aaa"
    assert row.attributes["Checksum_SecondaryData"].value == "bbb"


def test_an_unhashed_secondary_still_gets_its_filename():
    """No checksum is not a reason to withhold the name we do know."""
    row = _aln_row(_run_with_alignment_outputs())
    assert row.attributes["File_SecondaryData"].value
    assert "Checksum_SecondaryData" not in row.attributes


def test_no_bai_in_the_inventory_means_no_secondary_attributes():
    run = _run()
    run.outputs = [manifest.OutputRecord(
        path="star_salmon/CONTROL_REP1.markdup.sorted.bam", bytes=10,
        sample="CONTROL_REP1")]
    row = _aln_row(run)
    assert "File_SecondaryData" not in row.attributes
    assert "Checksum_SecondaryData" not in row.attributes


def test_another_samples_bai_is_never_attached_to_this_row():
    """The per-sample filter is OutputRecord.sample, set once at harvest."""
    run = _run_with_alignment_outputs()
    run.outputs.append(manifest.OutputRecord(
        path="star_salmon/OTHER.markdup.sorted.bam.bai", bytes=10, sample="OTHER"))
    row = _aln_row(run)
    assert row.attributes["File_SecondaryData"].value == "CONTROL_REP1.markdup.sorted.bam.bai"


def test_several_matching_secondaries_are_kept_not_silently_dropped():
    """Both paths match the companion glob (`*.markdup.sorted.bam.bai`) for the
    same sample. The sorted-first fills File_SecondaryData; the other is named
    in Notes (spec G-3), so neither is silently lost."""
    run = _run_with_alignment_outputs(
        extra=["star_salmon/CONTROL_REP1.extra.markdup.sorted.bam.bai"])
    row = _aln_row(run)
    attr = row.attributes["File_SecondaryData"]
    assert attr.value == "CONTROL_REP1.extra.markdup.sorted.bam.bai", "sorted-first"
    assert row.attributes["Notes"].value == (
        "Also in run: star_salmon/CONTROL_REP1.markdup.sorted.bam.bai")


# ── computed "@name" values ────────────────────────────────────────────────
# A $ref resolves; these transform. Names and bodies in
# the pipelines package, and nothing may resolve to a placeholder.

def test_an_unknown_computed_name_fails_map_load_not_silently():
    with pytest.raises(Exception) as exc:
        maps.PipelineMap(pipeline="x", outputs=[
            {"glob": "a/*.bam", "sample_type": "A.ALN",
             "attributes": {"Name": "@no_such_thing"}}])
    assert "no_such_thing" in str(exc.value)


def _computed(name, run, sample=None, context=None):
    from NessieAI.ns.reingest import pipelines
    return pipelines.tokens_for("differentialabundance")[name](run, sample, context)


@pytest.mark.parametrize("fastq_1, expected", [
    ("/net/run/fastq/SAMPLE01_R1.fastq.gz", "SAMPLE01_R1"),
    ("SAMPLE01_R1.fq.gz",                   "SAMPLE01_R1"),
    ("/x/SAMPLE01_R1.fastq",                "SAMPLE01_R1"),
    ("/x/SAMPLE01_R1.FASTQ.GZ",             "SAMPLE01_R1"),
    ("",                                None),
])
def test_fastq_stem_strips_the_suffix_and_the_directory(fastq_1, expected):
    run = _run()
    run.samples[0].fastq_1 = fastq_1
    assert _computed("fastq_stem", run, run.samples[0]) == expected


def test_run_date_takes_the_date_half_of_the_trace_suffix():
    run = _run(params={"trace_report_suffix": "2026-01-26_13-53-17"})
    assert _computed("run_date", run) == "2026-01-26"


@pytest.mark.parametrize("suffix", ["", "not-a-date", "26-01-2026_13-53-17", "2026-1-6_1-2-3"])
def test_an_unexpected_trace_suffix_yields_no_date_rather_than_a_wrong_one(suffix):
    run = _run(params={"trace_report_suffix": suffix})
    assert _computed("run_date", run) is None


def test_gex_name_is_the_date_plus_a_running_counter():
    run = _run(params={"trace_report_suffix": "2026-01-26_13-53-17"})
    ctx = {"next_name_ordinal": lambda st, prefix: 1}
    assert _computed("gex_name", run, None, ctx) == "gex_2026-01-26_1"
    ctx3 = {"next_name_ordinal": lambda st, prefix: 3}
    assert _computed("gex_name", run, None, ctx3) == "gex_2026-01-26_3"
    assert _computed("gex_name", _run(params={}), None, ctx) is None


def test_the_counter_is_asked_for_the_right_sample_type_and_prefix():
    run = _run(params={"trace_report_suffix": "2026-01-26_13-53-17"})
    seen = []
    _computed("gex_name", run, None,
              {"next_name_ordinal": lambda st, prefix: seen.append((st, prefix)) or 1})
    assert seen == [("A.GEX", "gex_2026-01-26")]


def test_without_the_counter_there_is_no_name_rather_than_a_collidable_one():
    """An unnumbered "gex_<date>" is exactly what a second run that day makes."""
    run = _run(params={"trace_report_suffix": "2026-01-26_13-53-17"})
    assert _computed("gex_name", run, None, {}) is None
    assert _computed("gex_name", run, None, None) is None


def test_the_scientist_is_the_session_user_and_blank_without_one():
    run = _run()
    assert _computed("nextseek_user", run, None,
                     {"user_full_name": "Forrest Hopkins"}) == "Forrest Hopkins"
    assert _computed("nextseek_user", run, None, {}) is None
    assert _computed("nextseek_user", run, None, None) is None


def test_the_computed_attributes_reach_the_rows_end_to_end():
    run = _run(params={"aligner": "star_salmon",
                       "trace_report_suffix": "2026-01-26_13-53-17"})
    run.samples[0].fastq_1 = "/net/run/fastq/SAMPLE01_R1.fastq.gz"
    result = mapper.apply(run, maps.load("rnaseq"), context={
        "user_full_name": "Forrest Hopkins",
        "next_name_ordinal": lambda st, prefix: 1})
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert aln.attributes["Name"].value == "SAMPLE01_R1"
    assert gex.attributes["Name"].value == "gex_2026-01-26_1"
    for row in (aln, gex):
        assert row.attributes["Scientist"].value == "Forrest Hopkins"
        assert row.attributes["SampleCreationDate"].value == "2026-01-26"


def test_a_missing_input_leaves_the_cell_absent_never_a_placeholder():
    """Blank a curator can fill beats a fabricated value they must catch."""
    run = _run(params={"aligner": "star_salmon"})   # no trace_report_suffix
    run.samples[0].fastq_1 = ""
    result = mapper.apply(run, maps.load("rnaseq"))  # and no context
    aln = next(r for r in result.rows if r.sample_type == "A.ALN")
    for absent in ("Name", "Scientist", "SampleCreationDate"):
        assert absent not in aln.attributes


# ── Lab, from the parent UID's lab segment ─────────────────────────────────
# Lab is not an attribute on D.SEQ, so the UID is the only carrier. The value
# is an EXISTING database string, never a composed one: the catalog spells the
# same lab several ways and inventing "<Surname> Lab" would add another.

def _lab_ctx(table, seen=None):
    from nextseek_api.services.reingest_lookups import lab_code_from_uid
    def lookup(code):
        if seen is not None:
            seen.append(code)
        return table.get(code)
    return {"lab_code_from_uid": lab_code_from_uid, "lab_for_code": lookup}


@pytest.mark.parametrize("uid, code", [
    ("D.SEQ-230512ABC-287-PUB", "ABC"),
    ("A.ALN-230303DEF-1-PUB",   "DEF"),
    ("D.SEQ-240710GHI-12-PUB",  "GHI"),
    ("a free-text CEL title",   None),
    ("D.SEQ-NOTADATEFOR-1-PUB", None),
    ("",                        None),
])
def test_the_lab_code_is_read_off_the_uid_or_not_at_all(uid, code):
    from nextseek_api.services.reingest_lookups import lab_code_from_uid
    assert lab_code_from_uid(uid) == code


def test_lab_resolves_through_the_parent_uid():
    run = _run()
    run.samples[0].d_seq_uid = "D.SEQ-230512ABC-287-PUB"
    seen = []
    assert _computed("lab", run, run.samples[0],
                     _lab_ctx({"ABC": "Abbott"}, seen)) == "Abbott"
    assert seen == ["ABC"], "looked up by code, not by UID"


def test_a_lab_code_with_no_recorded_name_leaves_the_cell_blank():
    """Real lab codes exist for which no sample carries a Lab value at all."""
    run = _run()
    run.samples[0].d_seq_uid = "D.SEQ-230512XYZ-1-PUB"
    assert _computed("lab", run, run.samples[0], _lab_ctx({"ABC": "Abbott"})) is None


def test_parents_from_two_labs_give_no_lab_rather_than_the_majority():
    run = _run()
    run.samples = [
        manifest.SampleRecord(nfcore_sample="a", d_seq_uid="D.SEQ-230512ABC-1-PUB"),
        manifest.SampleRecord(nfcore_sample="b", d_seq_uid="D.SEQ-230512ABC-2-PUB"),
        manifest.SampleRecord(nfcore_sample="c", d_seq_uid="D.SEQ-230512ESS-3-PUB"),
    ]
    ctx = _lab_ctx({"ABC": "Abbott", "DEF": "Danforth"})
    assert _computed("lab", run, None, ctx) is None, "a mixed run has no single Lab"


def test_a_per_run_row_agrees_across_every_parent():
    run = _run()
    run.samples = [
        manifest.SampleRecord(nfcore_sample=n, d_seq_uid=f"D.SEQ-230512ABC-{i}-PUB")
        for i, n in enumerate(("a", "b", "c"))]
    assert _computed("lab", run, None, _lab_ctx({"ABC": "Abbott"})) == "Abbott"


def test_unresolved_parents_contribute_no_code():
    """An unresolved sample has no UID, so it cannot veto the others' agreement."""
    run = _run()
    run.samples = [
        manifest.SampleRecord(nfcore_sample="a", d_seq_uid="D.SEQ-230512ABC-1-PUB"),
        manifest.SampleRecord(nfcore_sample="b", d_seq_uid=None),
    ]
    assert _computed("lab", run, None, _lab_ctx({"ABC": "Abbott"})) == "Abbott"


def test_without_the_injected_lookups_lab_is_blank():
    run = _run()
    run.samples[0].d_seq_uid = "D.SEQ-230512ABC-287-PUB"
    assert _computed("lab", run, run.samples[0], {}) is None


# ── reference paths are redacted ───────────────────────────────────────────
# A reference path names the assembly (useful) via a mount point, group
# directory and the home directory of whoever built the index (identifying).
# Everything left of "Genomes" goes.

@pytest.mark.parametrize("raw, expected", [
    # the house layout: truncate at the Genomes component
    ("/net/somewhere/data/grp/alice/Genomes/mm39_ens113/x.fa",
     "Genomes/mm39_ens113/x.fa"),
    # case-insensitive, because the component is a directory name not a keyword
    ("/n/d/alice/genomes/mm39/x.gtf", "genomes/mm39/x.gtf"),
    # NO Genomes component: keep the last two, so the redaction still holds
    ("/scratch/refs/private/mm39_ens113/x.gtf", "mm39_ens113/x.gtf"),
    # already relative, nothing identifying to remove
    ("Genomes/mm39/x.fa", "Genomes/mm39/x.fa"),
    ("x.fa", "x.fa"),
    ("", None),
    (None, None),
])
def test_a_reference_path_keeps_the_build_and_drops_the_rest(raw, expected):
    assert common._redact_reference_path(raw) == expected


def test_no_identifying_component_survives_redaction():
    """The property that matters, stated as a property rather than a string."""
    raw = "/net/ostrich/data/bcc/someuser/Genomes/mm39_ens113/ref.fa"
    got = common._redact_reference_path(raw)
    for identifying in ("net", "ostrich", "bcc", "someuser"):
        assert identifying not in got.split("/")
    assert got.startswith("Genomes/"), "the build directory is kept"


def test_the_genome_and_gtf_attributes_are_redacted_end_to_end():
    run = _run(params={
        "aligner": "star_salmon",
        "fasta": "/net/x/data/grp/someuser/Genomes/mm39_ens113/ref.fa",
        "gtf": "/net/x/data/grp/someuser/Genomes/mm39_ens113/ann.gtf"})
    result = mapper.apply(run, maps.load("rnaseq"))
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    assert gex.attributes["Link_ReferenceGenome"].value == "Genomes/mm39_ens113/ref.fa"
    assert gex.attributes["AnnotationGTF"].value == "Genomes/mm39_ens113/ann.gtf"
    assert gex.attributes["Link_GTF"].value == "Genomes/mm39_ens113/ann.gtf"
    for attr in ("Link_ReferenceGenome", "AnnotationGTF", "Link_GTF"):
        assert "someuser" not in gex.attributes[attr].value


def test_an_absent_reference_param_leaves_the_cell_blank():
    run = _run(params={"aligner": "star_salmon"})
    result = mapper.apply(run, maps.load("rnaseq"))
    gex = next(r for r in result.rows if r.sample_type == "A.GEX")
    for attr in ("Link_ReferenceGenome", "AnnotationGTF", "Link_GTF"):
        assert attr not in gex.attributes


def test_denovotranscripts_quant_sf_rows_each_carry_their_own_samples_file():
    """Every sample's Salmon output is named `quant.sf`; only the folder says
    whose it is. Attributed the way harvest attributes it, the committed map
    must give each sample its own file, never one sample's file twice.

    File_PrimaryData is a basename by convention (see _attach_checksum),
    but a bare "quant.sf" on every row would make the rows indistinguishable
    on the Samples sheet, so a basename two samples share keeps its folder."""
    from NessieAI.ns.reingest import harvest

    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD),
        manifest.SampleRecord(nfcore_sample="CONTROL_REP2", d_seq_uid="D.SEQ-2",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    names = ["CONTROL_REP1", "CONTROL_REP2"]
    run.outputs = [
        manifest.OutputRecord(path=p, bytes=100,
                              sample=harvest._sample_for_output_path(p, names))
        for p in ("salmon/CONTROL_REP1/quant.sf", "salmon/CONTROL_REP2/quant.sf")]

    result = mapper.apply(run, maps.load("denovotranscript"))

    gex = {r.nfcore_sample: r for r in result.rows if r.sample_type == "A.GEX"}
    assert {s: r.attributes["File_PrimaryData"].value for s, r in gex.items()} == {
        "CONTROL_REP1": "CONTROL_REP1/quant.sf",
        "CONTROL_REP2": "CONTROL_REP2/quant.sf",
    }
    assert {s: r.attributes["File_PrimaryData"].source_file for s, r in gex.items()} == {
        "CONTROL_REP1": "salmon/CONTROL_REP1/quant.sf",
        "CONTROL_REP2": "salmon/CONTROL_REP2/quant.sf",
    }
    assert gex["CONTROL_REP2"].attributes["Parent"].value == "D.SEQ-2"


def test_a_basename_unique_to_its_sample_stays_bare():
    """The folder is added only on a collision between samples: a BAM that
    already names its sample keeps the house convention, a bare filename."""
    run = _multi_sample_run(
        manifest.SampleRecord(nfcore_sample="CONTROL_REP1", d_seq_uid="D.SEQ-1",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD),
        manifest.SampleRecord(nfcore_sample="CONTROL_REP2", d_seq_uid="D.SEQ-2",
                              uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD))
    run.outputs = [
        manifest.OutputRecord(path=f"star_salmon/{s}.markdup.sorted.bam", bytes=100, sample=s)
        for s in ("CONTROL_REP1", "CONTROL_REP2")]
    result = mapper.apply(run, maps.load("rnaseq"))
    assert sorted(r.attributes["File_PrimaryData"].value
                  for r in result.rows if r.sample_type == "A.ALN") == [
        "CONTROL_REP1.markdup.sorted.bam", "CONTROL_REP2.markdup.sorted.bam"]


# --- differentialabundance: direction, paramset, settings, report, model object,
# study type, on each result row ---

def _row(result, matrix, contrast=None):
    rows = [r for r in result.rows if r.sample_type == "A.GEX"
            and r.attributes["Matrix"].value == matrix
            and (contrast is None or r.attributes["Contrast"].value == contrast)]
    assert len(rows) == 1, rows
    return {k: v.value for k, v in rows[0].attributes.items()}


def test_a_1x_result_row_carries_direction_settings_report_and_model_object():
    run = _differentialabundance_run(
        "tables/differential/LPS_vs_Ctrl.deseq2.results.tsv",
        "other/deseq2/LPS_vs_Ctrl.dds.rld.rds",
        "report/250818Rib_diffabun.html")
    run.params = {"study_type": "rnaseq", "deseq2_test": "Wald", "deseq2_shrink_lfc": True,
                  "deseq2_cores": 1, "limma_trend": False, "filtering_min_abundance": 1.0,
                  "differential_max_qval": 0.05, "differential_min_fold_change": 2.0,
                  "differential_palette_name": "Set1", "filtering_grouping_var": None}
    run.contrasts = {"LPS_vs_Ctrl": {"variable": "Condition", "reference": "Ctrl",
                                     "target": "LPS", "blocking": "Donor"}}
    run.named_outputs = {"differentialabundance_report_html": "report/250818Rib_diffabun.html"}
    attrs = _row(mapper.apply(run, maps.load("differentialabundance")), "deseq2_results")

    assert attrs["ContrastVariable"] == "Condition"
    assert attrs["ContrastReference"] == "Ctrl"
    assert attrs["ContrastTarget"] == "LPS"
    # Only what shaped this result: its own method's settings (not limma's, not
    # the core count), filtering and the significance thresholds; unset ones skipped.
    assert attrs["MethodSettings"] == (
        "deseq2_shrink_lfc=true; deseq2_test=Wald; differential_max_qval=0.05; "
        "differential_min_fold_change=2.0; filtering_min_abundance=1.0")
    assert attrs["Link_QualityControl"] == "report/250818Rib_diffabun.html"
    assert attrs["DESeqFile"] == "LPS_vs_Ctrl.dds.rld.rds"
    # 1.x has no paramsets, and an RNA-seq study needs no note.
    assert "Paramset" not in attrs
    assert "Notes" not in attrs


def test_a_contrast_missing_from_the_contrasts_file_gets_no_direction():
    run = _differentialabundance_run("tables/differential/LPS_vs_Ctrl.deseq2.results.tsv")
    attrs = _row(mapper.apply(run, maps.load("differentialabundance")), "deseq2_results")
    for field in ("ContrastVariable", "ContrastReference", "ContrastTarget"):
        assert field not in attrs


def test_a_2x_row_names_its_paramset_its_own_report_and_its_own_model_object():
    run = _differentialabundance_2_run(
        "tables/differential/rnaseq_limma/treatment_SRP254919.limma.results.tsv",
        "other/limma/rnaseq_limma/treatment_SRP254919.MArrayLM.limma.rds",
        "other/limma/rnaseq_other/treatment_SRP254919.MArrayLM.limma.rds",
        "report/rnaseq_deseq2/SRP254919_differentialabundance_report.html",
        "report/rnaseq_limma/SRP254919_differentialabundance_report.html")
    run.params = {"study_name": "SRP254919", "limma_trend": True}
    run.named_outputs = {"differentialabundance_report_html":
                         "report/rnaseq_deseq2/SRP254919_differentialabundance_report.html"}
    attrs = _row(mapper.apply(run, maps.load("differentialabundance")), "limma_results")

    assert attrs["Paramset"] == "rnaseq_limma"
    # Its own paramset's report, not the run's first.
    assert attrs["Link_QualityControl"] == "report/rnaseq_limma/SRP254919_differentialabundance_report.html"
    # Two paramsets wrote an object of this name, so the folder stays.
    assert attrs["DESeqFile"] == "rnaseq_limma/treatment_SRP254919.MArrayLM.limma.rds"
    # 2.0.0 settings are per paramset and not in params: blank, never the wrong set.
    assert "MethodSettings" not in attrs


def test_a_non_rnaseq_study_is_named_in_notes():
    run = _differentialabundance_run("tables/differential/A_vs_B.limma.results.tsv")
    run.params = {"study_type": "maxquant"}
    attrs = _row(mapper.apply(run, maps.load("differentialabundance")), "limma_results")
    assert attrs["Notes"].startswith("study_type: maxquant")
    assert "protein" in attrs["Notes"]


# ── v2: candidates, companions, row defaults, misses ─────────────────────────

def _v2map(outputs, **extra):
    return maps.PipelineMap.model_validate(
        {"map_version": 2, "pipeline": "nf-core/x", "applies_to_versions": ">=1,<2",
         "outputs": outputs, **extra})


def _run_with(paths, version="1.0.0", samples=("S1",)):
    return manifest.RunManifest(
        run_dir="/net/cluster/runs/r",
        pipeline=manifest.PipelineInfo(name="nf-core/x", version=version),
        samples=[manifest.SampleRecord(nfcore_sample=s, d_seq_uid=f"D.SEQ-X-{i}",
                                       uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                                       parent_sample_type="D.SEQ")
                 for i, s in enumerate(samples)],
        outputs=[manifest.OutputRecord(path=p, sample=next((s for s in samples if f"/{s}" in p
                                                            or p.startswith(f"{s}.")), None))
                 for p in paths])


VCF_RULE = {"id": "vcf", "sample_type": "A.VCF", "primary_data": True,
            "candidates": ["ann/*/*.ann.vcf.gz", "vc/*/*.filtered.vcf.gz", "vc/*/*.raw.vcf.gz"]}


def _out_rows(result, rule_id):
    return [r for r in result.rows if r.rule_id == rule_id]


def test_the_first_candidate_present_wins_and_later_ones_become_companions():
    run = _run_with(["vc/S1/S1.filtered.vcf.gz", "vc/S1/S1.raw.vcf.gz"])
    result = mapper.apply(run, _v2map([VCF_RULE]))
    (row,) = _out_rows(result, "vcf")
    assert row.attributes["File_PrimaryData"].value == "S1.filtered.vcf.gz"
    assert row.attributes["File_SecondaryData"].value == "S1.raw.vcf.gz"


def test_the_most_processed_candidate_wins_when_all_are_present():
    run = _run_with(["ann/S1/S1.ann.vcf.gz", "vc/S1/S1.filtered.vcf.gz"])
    (row,) = _out_rows(mapper.apply(run, _v2map([VCF_RULE])), "vcf")
    assert row.attributes["File_PrimaryData"].value == "S1.ann.vcf.gz"


def test_companions_fill_the_named_slot_then_secondary_then_notes():
    rule = {"id": "aln", "sample_type": "A.ALN", "primary_data": True,
            "candidates": ["a/*.bam"],
            "companions": [{"glob": "a/*.bam.bai"},
                           {"glob": "a/*.tab", "slot": "Link_QC"},
                           {"glob": "a/*.junction"}]}
    run = _run_with(["a/S1.bam", "a/S1.bam.bai", "a/S1.tab", "a/S1.junction"])
    (row,) = _out_rows(mapper.apply(run, _v2map([rule])), "aln")
    assert row.attributes["File_SecondaryData"].value == "S1.bam.bai"
    assert row.attributes["Link_QC"].value == "S1.tab"
    assert row.attributes["Notes"].value == "Also in run: a/S1.junction"


def test_a_slot_the_type_lacks_falls_through_to_secondary_then_notes():
    rule = {"id": "vcf", "sample_type": "A.VCF", "primary_data": True,
            "candidates": ["v/*.filtered.vcf.gz"],
            "companions": [{"glob": "v/*.g.vcf.gz", "slot": "NoSuchField"}]}
    run = _run_with(["v/S1.filtered.vcf.gz", "v/S1.g.vcf.gz"])
    has = lambda st, attr: attr not in {"NoSuchField", "File_SecondaryData"}
    result = mapper.apply(run, _v2map([rule]), context={"has_attribute": has})
    (row,) = _out_rows(result, "vcf")
    assert "NoSuchField" not in row.attributes
    assert "File_SecondaryData" not in row.attributes
    assert row.attributes["Notes"].value == "Also in run: v/S1.g.vcf.gz"


def test_overflow_appends_after_an_existing_note():
    rule = {"id": "g", "sample_type": "A.GEX", "primary_data": True, "cardinality": "per_run",
            "candidates": ["m/counts.tsv"], "attributes": {"Notes": "study_type: x"},
            "companions": [{"glob": "m/tpm.tsv"}, {"glob": "m/extra.tsv"}]}
    run = _run_with(["m/counts.tsv", "m/tpm.tsv", "m/extra.tsv"])
    (row,) = _out_rows(mapper.apply(run, _v2map([rule])), "g")
    assert row.attributes["File_SecondaryData"].value == "tpm.tsv"
    assert row.attributes["Notes"].value == "study_type: x; Also in run: m/extra.tsv"


def test_overflow_on_a_type_without_notes_goes_to_the_warnings():
    rule = {"id": "aln", "sample_type": "A.ALN", "primary_data": True,
            "candidates": ["a/*.bam"],
            "companions": [{"glob": "a/*.bam.bai"}, {"glob": "a/*.junction"}]}
    run = _run_with(["a/S1.bam", "a/S1.bam.bai", "a/S1.junction"])
    has = lambda st, attr: attr != "Notes"
    result = mapper.apply(run, _v2map([rule]), context={"has_attribute": has})
    (row,) = _out_rows(result, "aln")
    assert "Notes" not in row.attributes
    assert "S1: no Notes on A.ALN; also in run: a/S1.junction" in result.warnings


def test_per_file_companions_pair_by_stem_and_paramset():
    rule = {"id": "de", "sample_type": "A.GEX", "primary_data": True, "cardinality": "per_file",
            "candidates": ["tables/differential/*/*.deseq2.results.tsv"],
            "companions": [{"glob": "tables/differential/*/*.deseq2.results_filtered.tsv"}]}
    run = _run_with([
        "tables/differential/p1/A_vs_B.deseq2.results.tsv",
        "tables/differential/p2/A_vs_B.deseq2.results.tsv",
        "tables/differential/p1/A_vs_B.deseq2.results_filtered.tsv",
        "tables/differential/p2/A_vs_B.deseq2.results_filtered.tsv",
        "tables/differential/p1/C_vs_D.deseq2.results_filtered.tsv"])
    rows = _out_rows(mapper.apply(run, _v2map([rule])), "de")
    secondary = sorted(r.attributes["File_SecondaryData"].source_file for r in rows)
    assert secondary == ["tables/differential/p1/A_vs_B.deseq2.results_filtered.tsv",
                         "tables/differential/p2/A_vs_B.deseq2.results_filtered.tsv"]


def test_row_defaults_fill_every_rule_and_the_rule_wins():
    rules = [{"id": "aln", "sample_type": "A.ALN", "primary_data": True,
              "candidates": ["a/*.bam"], "attributes": {"Scientist": "Rule Wins"}},
             {"id": "vcf", "sample_type": "A.VCF", "primary_data": True,
              "candidates": ["v/*.vcf.gz"]}]
    run = _run_with(["a/S1.bam", "v/S1.vcf.gz"])
    result = mapper.apply(run, _v2map(rules, row_defaults={"Scientist": "@nextseek_user",
                                                           "Name": "@fastq_stem"}),
                          context={"user_full_name": "Ada Lovelace"})
    (aln,) = _out_rows(result, "aln")
    (vcf,) = _out_rows(result, "vcf")
    assert aln.attributes["Scientist"].value == "Rule Wins"
    assert vcf.attributes["Scientist"].value == "Ada Lovelace"


def test_a_row_default_the_type_lacks_is_dropped():
    rule = {"id": "vcf", "sample_type": "A.VCF", "primary_data": True,
            "candidates": ["v/*.vcf.gz"]}
    run = _run_with(["v/S1.vcf.gz"])
    has = lambda st, attr: attr != "Lab"
    result = mapper.apply(run, _v2map([rule], row_defaults={"Lab": "Fixed Lab"}),
                          context={"has_attribute": has})
    (row,) = _out_rows(result, "vcf")
    assert "Lab" not in row.attributes


def test_a_non_optional_rule_that_matches_nothing_is_reported_and_optional_is_not():
    rules = [{"id": "vcf", "sample_type": "A.VCF", "primary_data": True,
              "candidates": ["v/*.vcf.gz"]},
             {"id": "cram", "sample_type": "A.ALN", "primary_data": True,
              "candidates": ["a/*.cram"], "optional": True}]
    result = mapper.apply(_run_with([]), _v2map(rules))
    assert 'rule "vcf" (A.VCF) matched no file' in result.warnings
    assert not any('"cram"' in w for w in result.warnings)
    # The same miss, structured, for the report's blocker wording.
    assert result.missed_rules == [{"rule_id": "vcf", "sample_type": "A.VCF"}]


@pytest.mark.parametrize("cardinality", ["per_sample", "per_run"])
def test_an_optional_rule_that_matches_nothing_builds_no_rows(cardinality):
    # The output was not produced (its flag was off), so there is nothing to
    # register: a row would carry no data file and block the whole upload.
    rule = {"id": "hla", "sample_type": "A.HLA", "primary_data": True,
            "cardinality": cardinality, "candidates": ["optitype/*/*_result.tsv"],
            "optional": True, "attributes": {"Software": "OptiType"}}
    result = mapper.apply(_run_with([], samples=("S1", "S2")), _v2map([rule]))
    assert _out_rows(result, "hla") == []
    assert result.missed_rules == []


def _scrnaseq_run(matrices, version="v4.1.0-gf7bf36d"):
    """One-sample nf-core/scrnaseq run shaped like a real cellranger run."""
    return manifest.RunManifest(
        run_dir="/net/cluster/runs/scrnaseq_M188_run",
        params={"aligner": "cellranger", "genome": "GRCh38",
                "trace_report_suffix": "2026-06-03_14-38-55"},
        pipeline=manifest.PipelineInfo(name="nf-core/scrnaseq", version=version),
        samples=[manifest.SampleRecord(nfcore_sample="M188", d_seq_uid="D.SEQ-X-1",
                                       uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD,
                                       parent_sample_type="D.SEQ")],
        outputs=[manifest.OutputRecord(path=f"cellranger/mtx_conversions/{m}")
                 for m in matrices])


_SCRNASEQ_CTX = {"user_full_name": "Ada Lovelace",
                 "next_name_ordinal": lambda sample_type, prefix: 1}


@pytest.mark.parametrize("release, filtered", [
    ("v4.1.0-gf7bf36d", "cellbender_filter"),
    ("3.0.0", "emptydrops_filter"),
    ("2.7.1", "custom_emptydrops_filter"),
])
def test_the_scrnaseq_map_finds_each_releases_combined_matrix(release, filtered):
    # Every real release names the combined matrix after its input type
    # (combined_<type>_matrix.h5ad); only a -stub run writes combined_matrix.h5ad.
    run = _scrnaseq_run([f"combined_{t}_matrix.h5ad" for t in (filtered, "filtered", "raw")],
                        version=release)
    result = mapper.apply(run, maps.load("scrnaseq"), context=_SCRNASEQ_CTX)
    (row,) = _out_rows(result, "combined_matrix")
    assert row.attributes["File_PrimaryData"].value == f"combined_{filtered}_matrix.h5ad"
    assert result.missed_rules == []
    assert not any("matched no file" in w or "no usable pipeline version" in w
                   for w in result.warnings)


def test_the_scrnaseq_map_falls_back_to_the_aligner_filtered_matrix():
    # CellBender is skippable (--skip_cellbender), so the aligner's filtered
    # matrix must win when it is the most processed one present.
    run = _scrnaseq_run(["combined_filtered_matrix.h5ad", "combined_raw_matrix.h5ad"])
    (row,) = _out_rows(mapper.apply(run, maps.load("scrnaseq"), context=_SCRNASEQ_CTX),
                       "combined_matrix")
    assert row.attributes["File_PrimaryData"].value == "combined_filtered_matrix.h5ad"


def test_the_scrnaseq_row_gets_the_row_defaults():
    run = _scrnaseq_run(["combined_cellbender_filter_matrix.h5ad"])
    (row,) = _out_rows(mapper.apply(run, maps.load("scrnaseq"), context=_SCRNASEQ_CTX),
                       "combined_matrix")
    assert row.attributes["Scientist"].value == "Ada Lovelace"
    assert row.attributes["SampleCreationDate"].value == "2026-06-03"
    assert row.attributes["Name"].value == "scxp_2026-06-03_1"


def test_apply_uses_the_version_override_and_carries_its_warning():
    m = _v2map([{"id": "aln", "sample_type": "A.ALN", "primary_data": True,
                 "candidates": ["old/*.bam"]}],
               versions=[{"range": ">=2,<3", "outputs": [
                   {"id": "aln", "sample_type": "A.ALN", "primary_data": True,
                    "candidates": ["new/*.bam"]}]}])
    (row,) = _out_rows(mapper.apply(_run_with(["new/S1.bam"], version="2.1.0"), m), "aln")
    assert row.attributes["File_PrimaryData"].value == "S1.bam"
    result = mapper.apply(_run_with(["old/S1.bam"], version="9.0.0"), m)
    assert any("outside the checked range" in w for w in result.warnings)


def test_every_output_row_names_its_rule():
    result = mapper.apply(_run_with(["v/S1.vcf.gz"]), _v2map(
        [{"id": "vcf", "sample_type": "A.VCF", "primary_data": True,
          "candidates": ["v/*.vcf.gz"]}]))
    assert {r.rule_id for r in result.rows if r.sample_type == "A.VCF"} == {"vcf"}


def test_tokens_resolve_by_the_maps_pipeline_not_the_manifests():
    """A manifest with no pipeline name, mapped with the differentialabundance
    map, still resolves that pipeline's own @contrast token."""
    run = manifest.RunManifest(
        run_dir="/net/cluster/runs/r",
        pipeline=manifest.PipelineInfo(name="", version="1.5.0"),
        outputs=[manifest.OutputRecord(
            path="tables/differential/A_vs_B.deseq2.results.tsv")])
    result = mapper.apply(run, maps.load("differentialabundance"))
    (row,) = [r for r in result.rows
              if r.attributes.get("Matrix") and r.attributes["Matrix"].value == "deseq2_results"]
    assert row.attributes["Contrast"].value == "A_vs_B"


def test_per_file_companions_pair_by_paramset_under_the_real_brace_glob():
    """The committed differentialabundance glob expands flat-first, and the flat
    "*" also matches a nested 2.0.0 path; the paramset filter must still hold."""
    rule = {"id": "de", "sample_type": "A.GEX", "primary_data": True, "cardinality": "per_file",
            "candidates": ["tables/differential/{,*/}*.deseq2.results.tsv"],
            "companions": [{"glob": "tables/differential/{,*/}*.deseq2.results_filtered.tsv"}]}
    run = _run_with([
        "tables/differential/p1/A_vs_B.deseq2.results.tsv",
        "tables/differential/p2/A_vs_B.deseq2.results.tsv",
        "tables/differential/p1/A_vs_B.deseq2.results_filtered.tsv",
        "tables/differential/p2/A_vs_B.deseq2.results_filtered.tsv"])
    rows = _out_rows(mapper.apply(run, _v2map([rule])), "de")
    assert len(rows) == 2
    for row in rows:
        paramset = row.attributes["File_PrimaryData"].source_file.split("/")[2]
        assert row.attributes["File_SecondaryData"].source_file == (
            f"tables/differential/{paramset}/A_vs_B.deseq2.results_filtered.tsv")
        other = "p2" if paramset == "p1" else "p1"
        notes = row.attributes.get("Notes")
        assert notes is None or f"/{other}/" not in notes.value


def test_per_file_companions_never_pair_a_contrast_with_a_longer_one():
    rule = {"id": "de", "sample_type": "A.GEX", "primary_data": True, "cardinality": "per_file",
            "candidates": ["tables/differential/*.deseq2.results.tsv"],
            "companions": [{"glob": "tables/differential/*.deseq2.results_filtered.tsv"}]}
    run = _run_with([
        "tables/differential/A_vs_B.deseq2.results.tsv",
        "tables/differential/A_vs_B_batch.deseq2.results.tsv",
        "tables/differential/A_vs_B.deseq2.results_filtered.tsv",
        "tables/differential/A_vs_B_batch.deseq2.results_filtered.tsv"])
    rows = {r.attributes["File_PrimaryData"].value: r
            for r in _out_rows(mapper.apply(run, _v2map([rule])), "de")}
    plain = rows["A_vs_B.deseq2.results.tsv"]
    batch = rows["A_vs_B_batch.deseq2.results.tsv"]
    assert plain.attributes["File_SecondaryData"].value == "A_vs_B.deseq2.results_filtered.tsv"
    assert batch.attributes["File_SecondaryData"].value == (
        "A_vs_B_batch.deseq2.results_filtered.tsv")
    assert "Notes" not in plain.attributes
    assert "Notes" not in batch.attributes


def _qc_run(pipeline, version, metrics, derived=None, outputs=()):
    return manifest.RunManifest(
        run_dir="/net/cluster/runs/r",
        pipeline=manifest.PipelineInfo(name=f"nf-core/{pipeline}", version=version),
        samples=[manifest.SampleRecord(
            nfcore_sample="S1", d_seq_uid="D.SEQ-X-1",
            uid_resolution=manifest.RESOLUTION_LAUNCH_RECORD, parent_sample_type="D.SEQ",
            metrics=metrics, derived=derived or {})],
        outputs=[manifest.OutputRecord(path=p, sample="S1") for p in outputs])



@pytest.mark.parametrize("pipeline, version, metrics, derived, expected", [
    ("denovotranscript", "1.2.1",
     {"fastqc_raw-percent_gc@R1": 48.0, "fastqc_raw-avg_sequence_length@R1": 101.0,
      "fastqc_raw-avg_sequence_length@R2": 100.0, "sortmerna-rRNA_pct": 2.5,
      "salmon-percent_mapped": 85.0},
     {"strandedness": "reverse (ISR)"},
     {"GCPercent": 48.0, "F_bp": 101.0, "R_bp": 100.0, "rRNAPercent": 2.5,
      "PseudoalignedPercent": 85.0, "Strandedness": "reverse (ISR)"}),
    ("hlatyping", "2.2.0",
     {"fastqc-percent_gc@R1": 47.0, "fastqc-avg_sequence_length@R1": 150.0,
      "fastqc-avg_sequence_length@R2": 150.0},
     {}, {"GCPercent": 47.0, "F_bp": 150.0, "R_bp": 150.0}),
    ("riboseq", "1.2.0",
     # single-end: FastQC's row is the sample's own row, no @R1
     {"fastqc_raw-percent_gc": 52.0, "fastqc_raw-avg_sequence_length": 51.0,
      "fastp-after_filtering_q30_rate": 95.1, "fastp-pct_surviving": 97.0,
      "fastp-pct_adapter": 60.0, "sortmerna-rRNA_pct": 40.0,
      "star-uniquely_mapped_percent": 70.0, "samtools_stats-error_rate": 0.4},
     {}, {"GCPercent": 52.0, "F_bp": 51.0, "Q30Percent": 95.1, "PassedFilterPercent": 97.0,
          "AdapterPercent": 60.0, "rRNAPercent": 40.0, "MappedPercent": 70.0,
          "MismatchPercent": 0.4}),
    ("rnafusion", "4.1.3",
     {"fastqc_raw-percent_gc@R1": 49.0, "fastp-after_filtering_q30_rate": 93.0,
      "fastp-pct_surviving": 98.0, "fastp-pct_adapter": 12.0,
      "fastp-before_filtering_read1_mean_length": 101.0,
      "fastp-before_filtering_read2_mean_length": 101.0,
      "star-uniquely_mapped_percent": 88.0,
      "picard_mark_duplicates-PERCENT_DUPLICATION": 31.0,
      "picard_rnaseqmetrics-PCT_RIBOSOMAL_BASES": 1.2,
      "picard_rnaseqmetrics-PCT_MRNA_BASES": 81.0},
     {}, {"GCPercent": 49.0, "Q30Percent": 93.0, "PassedFilterPercent": 98.0,
          "AdapterPercent": 12.0, "F_bp": 101.0, "R_bp": 101.0, "MappedPercent": 88.0,
          "DuplicationPercent": 31.0, "rRNAPercent": 1.2, "MRNAPercent": 81.0}),
    ("rnasplice", "1.0.4",
     {"FastQC_mqc-generalstats-fastqc-percent_gc@R1": 50.0,
      "FastQC_mqc-generalstats-fastqc-avg_sequence_length@R1": 101.0,
      "STAR_mqc-generalstats-star-uniquely_mapped_percent": 90.0,
      "STAR_mqc-generalstats-star-uniquely_mapped": 24155641.0,
      "featureCounts_mqc-generalstats-featurecounts-percent_assigned": 71.0,
      "Samtools_mqc-generalstats-samtools-error_rate": 0.006},
     {"mismatch_pct": 0.6},
     {"GCPercent": 50.0, "F_bp": 101.0, "MappedPercent": 90.0, "NumAligned": 24155641.0,
      "AssignedPercent": 71.0, "MismatchPercent": 0.6}),
    ("rnavar", "1.3.0",
     {"read_alignment_star-uniquely_mapped_percent": 91.0,
      "gatk4_markduplicates_mark_duplicates-PERCENT_DUPLICATION": 22.0},
     {}, {"MappedPercent": 91.0, "DuplicationPercent": 22.0}),
    ("scrnaseq", "4.1.0",
     # 10x: read 1 is the barcode read, read 2 the cDNA, so GC comes from R2
     {"cell_ranger_count-count_estimated_cells": 13330.0,
      "cell_ranger_count-count_avg_reads_cell": 12627.0,
      "cell_ranger_count-count_reads_in_cells": 75.8,
      "cell_ranger_count-count_valid_bc": 96.8,
      "cell_ranger_count-count_q30_read": 94.3,
      # harvest folds only claimed per-read keys, so there is no GC @R1 here
      "fastqc-percent_gc@R2": 47.0,
      "fastqc-avg_sequence_length@R1": 28.0, "fastqc-avg_sequence_length@R2": 90.0},
     {}, {"EstimatedCells": 13330.0, "MeanReadsPerCell": 12627.0, "ReadsInCellsPercent": 75.8,
          "ValidBarcodesPercent": 96.8, "Q30Percent": 94.3, "GCPercent": 47.0,
          "F_bp": 28.0, "R_bp": 90.0}),
    ("smrnaseq", "2.4.1",
     {"fastp-after_filtering_gc_content": 51.0, "fastp-after_filtering_q30_rate": 97.0,
      "fastp-pct_surviving": 88.0, "fastp-pct_adapter": 95.0,
      "fastp-before_filtering_read1_mean_length": 75.0,
      "samtools_stats-error_rate": 0.3, "mirtop-isomiR_perc": 38.0,
      "umicollapse-dedup_percent_passing": 64.0},
     {}, {"GCPercent": 51.0, "Q30Percent": 97.0, "PassedFilterPercent": 88.0,
          "AdapterPercent": 95.0, "F_bp": 75.0, "MismatchPercent": 0.3,
          "IsomiRPercent": 38.0, "UMIUniquePercent": 64.0}),
])
def test_each_pipelines_qc_metrics_map_to_d_seq(pipeline, version, metrics, derived, expected):
    pipeline_map = maps.load(pipeline)
    result = mapper.apply(_qc_run(pipeline, version, metrics, derived), pipeline_map)
    row = next(r for r in result.rows if r.sample_type == "D.SEQ")
    assert {a: row.attributes[a].value for a in expected if a in row.attributes} == expected
    assert [u["raw_key"] for u in result.unmapped] == []
    if pipeline_map.applies_to_versions:  # hlatyping's map declares no range yet
        assert not [w for w in result.warnings if "outside the checked range" in w]


def test_rnavar_writes_snpeffs_ts_tv_ratio_on_the_vcf_row():
    run = _qc_run("rnavar", "1.3.0", {"snpeff-Ts_Tv_ratio": 2.1},
                  outputs=["variant_calling/S1/S1.haplotypecaller.filtered.vcf.gz"])
    result = mapper.apply(run, maps.load("rnavar"))
    (vcf,) = [r for r in result.rows if r.sample_type == "A.VCF"]
    assert vcf.attributes["TsTvRatio"].value == 2.1
    # A raw key an output rule reads is claimed, so it is never queued.
    assert [u["raw_key"] for u in result.unmapped] == []


@pytest.mark.parametrize("pipeline, version, raw_key", [
    ("denovotranscript", "1.2.1", "salmon-num_mapped"),
    ("hlatyping", "2.2.0", "fastqc-percent_fails"),
    ("riboseq", "1.2.0", "samtools_flagstat-flagstat_total"),
    ("rnafusion", "4.1.3", "picard_insertsizemetrics-summed_median"),
    ("rnasplice", "1.0.4", "Samtools_mqc-generalstats-samtools-raw_total_sequences"),
    ("rnavar", "1.3.0", "snpeff-Change_rate"),
    ("scrnaseq", "4.1.0", "cell_ranger_count-count_q30_bc"),
    ("smrnaseq", "2.4.1", "mirtop-read_count"),
])
def test_a_low_value_key_is_ruled_out_and_never_queued(pipeline, version, raw_key):
    pipeline_map = maps.load(pipeline)
    assert raw_key in pipeline_map.ruled_out()
    result = mapper.apply(_qc_run(pipeline, version, {raw_key: 1.0}), pipeline_map)
    assert not any(u["raw_key"] == raw_key for u in result.unmapped)


def test_hlatyping_writes_optitypes_alleles_on_an_a_hla_row():
    metrics = {"optitype-A1": "A*02:01", "optitype-A2": "A*24:02", "optitype-B1": "B*07:02",
               "optitype-B2": "B*44:02", "optitype-C1": "C*07:02", "optitype-C2": "C*05:01",
               "optitype-Reads": 1532.0, "optitype-Objective": 1480.2}
    run = _qc_run("hlatyping", "2.2.0", metrics, outputs=[
        "optitype/S1/S1_result.tsv", "optitype/S1/S1_coverage_plot.pdf"])
    result = mapper.apply(run, maps.load("hlatyping"))
    (row,) = [r for r in result.rows if r.sample_type == "A.HLA"]
    values = {k: v.value for k, v in row.attributes.items()}
    assert values["File_PrimaryData"] == "S1_result.tsv"
    assert values["File_SecondaryData"] == "S1_coverage_plot.pdf"
    assert {values[f"HLA_{locus}{n}"] for locus in "ABC" for n in (1, 2)} == {
        "A*02:01", "A*24:02", "B*07:02", "B*44:02", "C*07:02", "C*05:01"}
    assert values["HLAReads"] == 1532.0 and values["HLATypingObjective"] == 1480.2
    assert values["Software"] == "OptiType"
    assert result.unmapped == []


# --- Link_PrimaryData / Link_SecondaryData: where the run left the files -----------------------
# Interim, ruled 2026-10-21: an uploaded A.HLA row named its file but not where to find it.
# Until the data has a permanent home, the link is the file's path in the cluster run folder.

_HLA_RULE = {"id": "hla", "sample_type": "A.HLA", "primary_data": True,
             "candidates": ["optitype/*/*_result.tsv"],
             "companions": [{"glob": "optitype/*/*_coverage_plot.pdf"}]}


def test_the_primary_and_secondary_files_get_their_run_folder_paths_as_links():
    run = _run_with(["optitype/S1/S1_result.tsv", "optitype/S1/S1_coverage_plot.pdf"])
    (row,) = _out_rows(mapper.apply(run, _v2map([_HLA_RULE])), "hla")
    assert row.attributes["File_PrimaryData"].value == "S1_result.tsv"
    assert row.attributes["Link_PrimaryData"].value == "/net/cluster/runs/r/optitype/S1/S1_result.tsv"
    assert row.attributes["Link_SecondaryData"].value == \
        "/net/cluster/runs/r/optitype/S1/S1_coverage_plot.pdf"


def test_no_link_is_written_on_a_type_without_the_attribute():
    run = _run_with(["optitype/S1/S1_result.tsv", "optitype/S1/S1_coverage_plot.pdf"])
    has = lambda st, attr: attr != "Link_SecondaryData"
    (row,) = _out_rows(mapper.apply(run, _v2map([_HLA_RULE]), context={"has_attribute": has}),
                       "hla")
    assert "Link_PrimaryData" in row.attributes
    assert "Link_SecondaryData" not in row.attributes


def test_a_link_the_map_sets_wins():
    rule = {**_HLA_RULE, "attributes": {"Link_PrimaryData": "https://example.org/hla"}}
    run = _run_with(["optitype/S1/S1_result.tsv"])
    (row,) = _out_rows(mapper.apply(run, _v2map([rule])), "hla")
    assert row.attributes["Link_PrimaryData"].value == "https://example.org/hla"


# --- unmapped_files: a file the map rules out is never matched by a rule. ---

_MANTA_RULE = {
    "id": "vcf_manta", "candidates": ["variant_calling/manta/*/*.manta.diploid_sv.vcf.gz"],
    "sample_type": "A.VCF", "cardinality": "per_sample", "primary_data": True, "optional": True,
    "companions": [{"glob": "variant_calling/manta/*/*.manta.diploid_sv.vcf.gz.tbi"}]}


def _pair_run():
    # The harvest attributes a pair file by its basename's prefix, so the
    # tumor T gets T_vs_N's VCF and the normal N gets N_vs_T's index.
    outputs = {
        "variant_calling/manta/T/T.manta.diploid_sv.vcf.gz": "T",
        "variant_calling/manta/T_vs_N/T_vs_N.manta.diploid_sv.vcf.gz": "T",
        "variant_calling/manta/N/N.manta.diploid_sv.vcf.gz": "N",
        "variant_calling/manta/N/N.manta.diploid_sv.vcf.gz.tbi": "N",
        "variant_calling/manta/N_vs_T/N_vs_T.manta.diploid_sv.vcf.gz.tbi": "N",
    }
    run = _run_with([], samples=("T", "N"))
    run.outputs = [manifest.OutputRecord(path=p, sample=s) for p, s in outputs.items()]
    return run


def _manta_rows(result):
    return {r.nfcore_sample: r for r in _out_rows(result, "vcf_manta")}


def test_without_a_ruling_a_pair_file_is_matched_by_the_rule():
    rows = _manta_rows(mapper.apply(_pair_run(), _v2map([_MANTA_RULE])))
    assert "N_vs_T" in rows["N"].attributes["Notes"].value


def test_a_file_the_map_rules_out_is_never_a_rows_primary_file_or_companion():
    ruled = _v2map([_MANTA_RULE], unmapped_files=[
        {"glob": "variant_calling/*/*_vs_*/*", "why": "somatic pair outputs, pending a pair rule"}])
    run = _pair_run()
    run.outputs = [o for o in run.outputs if o.path != "variant_calling/manta/T/T.manta.diploid_sv.vcf.gz"]
    result = mapper.apply(run, ruled)
    rows = _manta_rows(result)
    # T's only match is the pair's VCF, which is ruled out, so the optional rule
    # has no file for T and writes no row for it.
    assert "T" not in rows
    assert "T: no file for optional rule vcf_manta; no row written" in result.warnings
    # N keeps its own VCF and its own index, never the pair's index.
    assert rows["N"].attributes["File_PrimaryData"].value == "N.manta.diploid_sv.vcf.gz"
    assert "N_vs_T" not in str({k: v.value for k, v in rows["N"].attributes.items()})


def test_a_ruled_out_file_does_not_displace_the_samples_own_file():
    ruled = _v2map([_MANTA_RULE], unmapped_files=[
        {"glob": "variant_calling/*/*_vs_*/*", "why": "somatic pair outputs, pending a pair rule"}])
    rows = _manta_rows(mapper.apply(_pair_run(), ruled))
    assert rows["T"].attributes["File_PrimaryData"].value == "T.manta.diploid_sv.vcf.gz"
    assert "T_vs_N" not in str({k: v.value for k, v in rows["T"].attributes.items()})


@pytest.mark.parametrize("path", [
    "variant_calling/strelka/T_vs_N/T_vs_N.strelka.somatic_snvs.vcf.gz",
    "variant_calling/manta/T_vs_N/T_vs_N.manta.diploid_sv.vcf.gz",
    "annotation/mutect2/T_vs_N/T_vs_N.mutect2.filtered_VEP.ann.vcf.gz",
    "reports/SnpEff/T_vs_N/strelka/T_vs_N.strelka_snpEff.csv",
    "variant_calling/controlfreec/T_vs_N/x/y.txt",
    "T_vs_N/top_level.vcf.gz",
])
def test_sareks_pair_ruling_covers_a_pair_folder_at_any_depth(path):
    import fnmatch
    globs = [g for u in maps.load("sarek").unmapped_files for g in maps.expand_braces(u.glob)
             if "_vs_" in u.glob]
    assert any(fnmatch.fnmatch(path, g) for g in globs), path


@pytest.mark.parametrize("path", [
    "variant_calling/manta/NA12878/NA12878.manta.diploid_sv.vcf.gz",
    "preprocessing/recalibrated/S1/S1.recal.cram",
])
def test_sareks_pair_ruling_leaves_single_sample_files_alone(path):
    import fnmatch
    globs = [g for u in maps.load("sarek").unmapped_files for g in maps.expand_braces(u.glob)
             if "_vs_" in u.glob]
    assert not any(fnmatch.fnmatch(path, g) for g in globs), path


# --- An optional per_sample rule writes no row for a sample it has no file for. ---

def _two_sample_vcf_run():
    run = _run_with([], samples=("N", "T"))
    run.outputs = [manifest.OutputRecord(path="variant_calling/manta/N/N.manta.diploid_sv.vcf.gz",
                                         sample="N")]
    return run


def test_an_optional_per_sample_rule_skips_a_sample_with_no_file_and_says_so():
    # A tumour in a tumour/normal run gets no germline call set; a fileless
    # A.VCF row would be a hard MISSING_REQUIRED that blocks the whole reingest.
    result = mapper.apply(_two_sample_vcf_run(), _v2map([_MANTA_RULE]))
    assert sorted(_manta_rows(result)) == ["N"]
    assert "T: no file for optional rule vcf_manta; no row written" in result.warnings
    assert not any(w.startswith("N:") and "vcf_manta" in w for w in result.warnings)


def test_a_required_per_sample_rule_still_writes_a_row_for_a_sample_with_no_file():
    required = {**_MANTA_RULE, "optional": False}
    result = mapper.apply(_two_sample_vcf_run(), _v2map([required]))
    rows = _manta_rows(result)
    assert sorted(rows) == ["N", "T"]
    assert "File_PrimaryData" not in rows["T"].attributes
    assert not any("no row written" in w for w in result.warnings)


def test_an_optional_rule_that_matches_nothing_in_the_run_warns_for_no_sample():
    run = _two_sample_vcf_run()
    run.outputs = []
    result = mapper.apply(run, _v2map([_MANTA_RULE]))
    assert _manta_rows(result) == {}
    assert not any("vcf_manta" in w for w in result.warnings)

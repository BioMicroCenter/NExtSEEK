import pytest

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
    result = mapper.apply(_run(metrics={"Picard_PERCENT_DUPLICATION": 18.4}), pipeline_map)
    assert not any(u["raw_key"] == "Picard_PERCENT_DUPLICATION" for u in result.unmapped)


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


def test_a_multirun_sample_gets_no_d_seq_row():
    run = _run(metrics={"star-uniquely_mapped_percent": 91.4})
    run.samples[0].uid_resolution = manifest.RESOLUTION_MULTIRUN
    run.samples[0].d_seq_uid = None
    result = mapper.apply(run, maps.load("rnaseq"))
    assert not any(r.sample_type == "D.SEQ" for r in result.rows)


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

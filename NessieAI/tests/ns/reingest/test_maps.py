import pytest

from NessieAI.ns.reingest import maps


def test_load_reads_the_rnaseq_map_by_pipeline_name():
    pipeline_map = maps.load("nf-core/rnaseq")
    assert pipeline_map.pipeline == "nf-core/rnaseq"
    assert pipeline_map.qc_attributes["MappedPercent"].target == "D.SEQ"


def test_load_accepts_a_bare_pipeline_name():
    assert maps.load("rnaseq").pipeline == "nf-core/rnaseq"


def test_load_raises_for_an_unknown_pipeline():
    with pytest.raises(maps.UnknownPipelineMap):
        maps.load("nf-core/not-a-pipeline")


def test_resolve_ref_reads_a_params_field():
    class _M:
        params = {"aligner": "star_salmon"}
    assert maps.resolve_ref("$params.aligner", _M()) == "star_salmon"


def test_resolve_ref_returns_none_for_a_missing_field():
    class _M:
        params = {}
    assert maps.resolve_ref("$params.nope", _M()) is None


def test_resolve_ref_passes_a_literal_through_unchanged():
    assert maps.resolve_ref("BAM", object()) == "BAM"


def test_resolve_ref_reads_a_derived_metric_off_the_sample():
    class _S:
        derived = {"exon_intron_ratio": 9.81}
        metrics = {}
    assert maps.resolve_ref("$derived.exon_intron_ratio", object(), _S()) == 9.81


def test_a_map_cannot_smuggle_an_expression():
    # $-refs are lookups. Anything that is not a known section is not resolved.
    assert maps.resolve_ref("$os.system", object()) is None


def test_resolve_ref_reads_a_named_output():
    class _M:
        named_outputs = {"multiqc_report_html": "multiqc/multiqc_report.html"}
    assert maps.resolve_ref("$outputs.multiqc_report_html", _M()) == "multiqc/multiqc_report.html"

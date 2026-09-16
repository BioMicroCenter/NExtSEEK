import pydantic
import pytest

from NessieAI.ns.reingest import maps
from NessieAI.ns.reingest.manifest import PipelineInfo, RunManifest


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


def test_resolve_ref_reads_the_whole_named_outputs_bag():
    # Bare "$outputs" must resolve to named_outputs (the dict), never to the
    # `outputs` inventory list -- that distinction is the point of the map.
    class _M:
        named_outputs = {"multiqc_report_html": "multiqc/multiqc_report.html"}
        outputs = ["should not be reachable"]
    assert maps.resolve_ref("$outputs", _M()) == {
        "multiqc_report_html": "multiqc/multiqc_report.html",
    }


# --- cardinality is constrained to a Literal so a typo in a map file fails
# loudly at load time, rather than silently falling through to one branch
# (this repo has already been bitten by exactly this class of bug: an
# unrecognised status value read as "complete"). ---

def test_a_bogus_cardinality_fails_map_validation():
    with pytest.raises(pydantic.ValidationError):
        maps.PipelineMap.model_validate({
            "pipeline": "nf-core/rnaseq",
            "outputs": [{"glob": "*.bam", "sample_type": "A.ALN",
                        "cardinality": "per_lane"}],
        })


def test_the_committed_rnaseq_map_still_loads_with_the_constrained_cardinality():
    pipeline_map = maps.load("rnaseq")
    assert pipeline_map.outputs, "expected at least one output rule"
    assert {rule.cardinality for rule in pipeline_map.outputs} <= {"per_sample", "per_run"}


def test_resolve_ref_cannot_reach_a_dunder_or_bound_method_on_a_model():
    # The escape vector finding 1 fixed: a non-dict run-section bag is a real
    # pydantic model (RunManifest.pipeline is a PipelineInfo), and the old
    # fallback `getattr(bag, key, None)` happily returned `__class__` (the
    # class object) or `model_dump` (a bound method) for any key at all. Only
    # a field the model actually declares may resolve.
    run_manifest = RunManifest(run_dir="run", pipeline=PipelineInfo(name="nf-core/rnaseq"))
    assert maps.resolve_ref("$pipeline.__class__", run_manifest) is None
    assert maps.resolve_ref("$pipeline.model_dump", run_manifest) is None
    # A genuine declared field on the same section must still resolve, so the
    # fix is a restriction to known fields, not a blanket rejection.
    assert maps.resolve_ref("$pipeline.name", run_manifest) == "nf-core/rnaseq"

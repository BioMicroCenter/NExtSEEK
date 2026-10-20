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

def test_resolve_ref_reads_a_checksum_by_path():
    class _M:
        checksums = {"star_salmon/CONTROL_REP1.markdup.sorted.bam": "abc123"}
    assert maps.resolve_ref(
        "$checksums.star_salmon/CONTROL_REP1.markdup.sorted.bam", _M()) == "abc123"


def test_resolve_ref_returns_none_for_a_checksum_not_yet_computed():
    class _M:
        checksums = {}
    assert maps.resolve_ref("$checksums.not/hashed/yet.bam", _M()) is None


def test_resolve_ref_still_returns_none_for_an_unknown_section():
    # Adding "checksums" must not loosen the allowlist itself -- an
    # unrecognised section name is still an ordinary miss, not a lookup.
    assert maps.resolve_ref("$bogus_section.key", object()) is None


def test_a_bogus_cardinality_fails_map_validation():
    with pytest.raises(pydantic.ValidationError):
        maps.PipelineMap.model_validate({
            "pipeline": "nf-core/rnaseq",
            "outputs": [{"glob": "*.bam", "sample_type": "A.ALN",
                        "cardinality": "per_lane"}],
        })


def test_per_file_is_a_valid_cardinality():
    pipeline_map = maps.PipelineMap.model_validate({
        "pipeline": "nf-core/differentialabundance",
        "outputs": [{"glob": "tables/differential/*.deseq2.results.tsv",
                     "sample_type": "A.GEX", "cardinality": "per_file",
                     "primary_data": True}],
    })
    assert pipeline_map.outputs[0].cardinality == "per_file"


def test_a_per_file_rule_must_declare_primary_data():
    # Its rows are counted by the files its glob matches; without primary data
    # no file is matched and the rule could only ever emit nothing.
    with pytest.raises(pydantic.ValidationError):
        maps.PipelineMap.model_validate({
            "pipeline": "nf-core/differentialabundance",
            "outputs": [{"glob": "*.results.tsv", "sample_type": "A.GEX",
                         "cardinality": "per_file"}],
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


# ── schema v2 ────────────────────────────────────────────────────────────────

def _v2(**overrides):
    base = {"map_version": 2, "pipeline": "nf-core/x", "applies_to_versions": ">=1,<2",
            "outputs": [{"id": "aln", "candidates": ["a/*.bam"], "sample_type": "A.ALN",
                         "primary_data": True}]}
    base.update(overrides)
    return maps.PipelineMap.model_validate(base)


def test_a_v1_glob_reads_as_one_candidate_and_secondary_as_one_companion():
    m = maps.PipelineMap.model_validate({
        "pipeline": "nf-core/x",
        "outputs": [{"glob": "a/*.bam", "secondary_data_glob": "a/*.bam.bai",
                     "sample_type": "A.ALN", "primary_data": True}]})
    rule = m.outputs[0]
    assert rule.candidates == ["a/*.bam"]
    assert [(c.glob, c.slot) for c in rule.companions] == [("a/*.bam.bai", None)]
    assert rule.id == "rule0"


def test_glob_and_candidates_together_are_refused():
    with pytest.raises(ValueError, match="glob or candidates"):
        maps.PipelineMap.model_validate({"pipeline": "nf-core/x", "outputs": [
            {"glob": "a", "candidates": ["b"], "sample_type": "A.ALN"}]})


def test_a_v2_rule_must_have_an_id():
    with pytest.raises(ValueError, match="needs an id"):
        _v2(outputs=[{"candidates": ["a/*.bam"], "sample_type": "A.ALN"}])


def test_rule_ids_must_be_unique_and_lowercase_words():
    with pytest.raises(ValueError, match="duplicate"):
        _v2(outputs=[{"id": "x", "candidates": ["a"], "sample_type": "A.ALN"},
                     {"id": "x", "candidates": ["b"], "sample_type": "A.ALN"}])
    with pytest.raises(ValueError, match="rule id"):
        _v2(outputs=[{"id": "Bad-Id", "candidates": ["a"], "sample_type": "A.ALN"}])


def test_an_empty_candidate_list_is_refused():
    with pytest.raises(ValueError):
        _v2(outputs=[{"id": "x", "candidates": [], "sample_type": "A.ALN"}])


def test_a_per_file_rule_may_now_declare_companions():
    m = _v2(outputs=[{"id": "de", "candidates": ["t/*.deseq2.results.tsv"],
                      "sample_type": "A.GEX", "cardinality": "per_file",
                      "primary_data": True,
                      "companions": [{"glob": "t/*.results_filtered.tsv"}]}])
    assert m.outputs[0].companions[0].glob == "t/*.results_filtered.tsv"


def test_a_per_file_rule_takes_exactly_one_candidate():
    with pytest.raises(ValueError, match="one candidate"):
        _v2(outputs=[{"id": "de", "candidates": ["a/*.tsv", "b/*.tsv"],
                      "sample_type": "A.GEX", "cardinality": "per_file",
                      "primary_data": True}])


def test_an_override_may_only_drop_rules_that_exist():
    with pytest.raises(ValueError, match="drops unknown rule"):
        _v2(versions=[{"range": ">=2", "drop": ["nope"]}])


def test_an_override_may_not_drop_and_replace_the_same_rule():
    with pytest.raises(ValueError, match="both drops and replaces"):
        _v2(versions=[{"range": ">=2", "drop": ["aln"],
                       "outputs": [{"id": "aln", "candidates": ["b/*.bam"],
                                    "sample_type": "A.ALN"}]}])


def test_a_bad_version_range_fails_at_load():
    with pytest.raises(ValueError):
        _v2(applies_to_versions="not a range")
    with pytest.raises(ValueError):
        _v2(versions=[{"range": "~~~"}])


def test_expand_braces_moved_to_maps_and_mapper_keeps_the_alias():
    from NessieAI.ns.reingest import mapper
    assert maps.expand_braces("{a,b}/x.{c,d}") == ["a/x.c", "a/x.d", "b/x.c", "b/x.d"]
    assert mapper._expand_braces is maps.expand_braces


# ── Task 1 extra: override output id validation ────────────────────────────────

def test_override_output_ids_must_match_rule_id_pattern():
    with pytest.raises(ValueError, match="rule id must be lowercase word characters"):
        _v2(versions=[{"range": ">=2", "outputs": [
            {"id": "Bad-Id", "candidates": ["b/*.bam"], "sample_type": "A.ALN"}]}])


def test_override_outputs_must_not_have_duplicate_ids():
    with pytest.raises(ValueError, match="duplicate rule id"):
        _v2(versions=[{"range": ">=2", "outputs": [
            {"id": "x", "candidates": ["a/*.bam"], "sample_type": "A.ALN"},
            {"id": "x", "candidates": ["b/*.bam"], "sample_type": "A.ALN"}]}])


# ── Task 2: Version resolution ───────────────────────────────────────────────────

def _versioned():
    return _v2(
        applies_to_versions=">=1.0,<2",
        outputs=[{"id": "aln", "candidates": ["old/*.bam"], "sample_type": "A.ALN",
                  "primary_data": True},
                 {"id": "vcf", "candidates": ["v/*.vcf.gz"], "sample_type": "A.VCF",
                  "primary_data": True}],
        versions=[{"range": ">=2,<3",
                   "outputs": [{"id": "aln", "candidates": ["new/*.bam"],
                                "sample_type": "A.ALN", "primary_data": True},
                               {"id": "cram", "candidates": ["new/*.cram"],
                                "sample_type": "A.ALN", "primary_data": True,
                                "optional": True}],
                   "drop": ["vcf"]}])


def test_effective_in_range_returns_the_base_rules_and_no_warning():
    m, warnings = maps.effective(_versioned(), "1.4.0")
    assert [r.id for r in m.outputs] == ["aln", "vcf"]
    assert m.outputs[0].candidates == ["old/*.bam"]
    assert warnings == []


def test_effective_applies_the_matching_override_replace_add_and_drop():
    m, warnings = maps.effective(_versioned(), "v2.0.1")
    assert [r.id for r in m.outputs] == ["aln", "cram"]
    assert m.outputs[0].candidates == ["new/*.bam"]
    assert warnings == []


def test_effective_out_of_range_applies_base_rules_with_a_warning():
    m, warnings = maps.effective(_versioned(), "3.0.2")
    assert [r.id for r in m.outputs] == ["aln", "vcf"]
    assert warnings == ["nf-core/x 3.0.2 is outside the checked range >=1.0,<2; "
                        "rules applied unchecked"]


def test_effective_with_no_usable_version_warns_and_applies_base_rules():
    for bad in ("", "not-a-version"):
        m, warnings = maps.effective(_versioned(), bad)
        assert [r.id for r in m.outputs] == ["aln", "vcf"]
        assert warnings and "no usable pipeline version" in warnings[0]


def test_effective_reads_the_release_out_of_an_nf_core_commit_suffix():
    # nf-core's version string appends "-g<short commit>" when the run came
    # from a git checkout (nf-core/scrnaseq 4.1.0 reports "v4.1.0-gf7bf36d").
    m, warnings = maps.effective(_versioned(), "v1.4.0-gf7bf36d")
    assert [r.id for r in m.outputs] == ["aln", "vcf"]
    assert warnings == []
    m, warnings = maps.effective(_versioned(), "v2.0.1-gabc1234")
    assert [r.id for r in m.outputs] == ["aln", "cram"]
    assert warnings == []


def test_effective_accepts_a_prerelease_inside_the_range():
    _, warnings = maps.effective(_versioned(), "1.5.0dev")
    assert warnings == []


def test_effective_leaves_the_loaded_map_untouched():
    base = _versioned()
    maps.effective(base, "2.0.0")
    assert [r.id for r in base.outputs] == ["aln", "vcf"]


def test_hlatyping_2_2_is_a_checked_release():
    # Checked against a real nf-core/hlatyping 2.2.0 run (OptiType file names and
    # MultiQC's multiqc_optitype.txt layout); nf-core reports it as v2.2.0-g<commit>.
    _, warnings = maps.effective(maps.load("hlatyping"), "v2.2.0-g3237d20")
    assert warnings == []

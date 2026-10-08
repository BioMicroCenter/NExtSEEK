import pytest

from NessieAI.ns.reingest import maps, pipelines


def test_names_and_resolvers_agree_for_every_pipeline_with_a_map():
    for name in maps.available():
        assert set(pipelines.token_names_for(name)) == set(pipelines.tokens_for(name)), name


def test_common_tokens_are_available_to_every_pipeline():
    for name in ("rnaseq", "nf-core/rnavar", "something-new"):
        assert set(pipelines.COMMON_TOKENS) <= set(pipelines.token_names_for(name))


def test_a_pipeline_cannot_use_another_pipelines_tokens():
    assert "contrast" in pipelines.token_names_for("nf-core/differentialabundance")
    assert "contrast" not in pipelines.token_names_for("nf-core/rnaseq")


def test_a_map_using_a_foreign_token_fails_to_load():
    with pytest.raises(ValueError, match="@contrast"):
        maps.PipelineMap.model_validate({
            "map_version": 2, "pipeline": "nf-core/rnaseq",
            "outputs": [{"id": "x", "candidates": ["a"], "sample_type": "A.GEX",
                         "attributes": {"Contrast": "@contrast"}}]})


def test_row_defaults_and_override_outputs_are_token_checked_too():
    with pytest.raises(ValueError, match="@nope"):
        maps.PipelineMap.model_validate({
            "map_version": 2, "pipeline": "nf-core/x", "row_defaults": {"Name": "@nope"}})
    with pytest.raises(ValueError, match="@nope"):
        maps.PipelineMap.model_validate({
            "map_version": 2, "pipeline": "nf-core/x",
            "outputs": [{"id": "a", "candidates": ["a"], "sample_type": "A.ALN"}],
            "versions": [{"range": ">=2", "outputs": [
                {"id": "a", "candidates": ["b"], "sample_type": "A.ALN",
                 "attributes": {"Name": "@nope"}}]}]})


def test_upstream_parent_is_gone():
    assert "upstream_parent" not in pipelines.token_names_for("differentialabundance")


def test_row_name_numbers_by_the_rows_own_sample_type():
    from NessieAI.ns.reingest import manifest
    run = manifest.RunManifest(run_dir="/r", params={"trace_report_suffix": "2026-01-26_13-53-17"})
    seen = []

    def next_ordinal(sample_type, prefix):
        seen.append((sample_type, prefix))
        return 3

    value = pipelines.tokens_for("x")["row_name"](
        run, None, {"next_name_ordinal": next_ordinal, "row_sample_type": "A.SCXP"})
    assert value == "scxp_2026-01-26_3"
    assert seen == [("A.SCXP", "scxp_2026-01-26")]

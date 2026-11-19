"""nf-core/raredisease 3.1.2, wired end to end: catalog, pedigree columns, template, atlases.

The pedigree columns are data: sex and phenotype are derived from each sample's metadata by
the param atlas (corroborated, asked when absent), and case_id, paternal_id and maternal_id,
which no marker can derive, are checked for a consistent pedigree before the sheet is written.
"""
import json
from pathlib import Path

import pytest

from NessieAI.paths import CHAT_NEXTSEEK_DIR
from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.param_atlas import data_driven_params, evaluate_data_driven_param

FIXTURES = Path(__file__).parent / "fixtures" / "nfcore"
TEMPLATES = CHAT_NEXTSEEK_DIR / "src/chat_nextseek/reports/templates/nfcore"


def _template():
    return json.loads((TEMPLATES / "raredisease.json").read_text())


def _schema():
    return json.loads((FIXTURES / "raredisease.nextflow_schema.json").read_text())


def _input_schema():
    return json.loads((FIXTURES / "raredisease.schema_input.json").read_text())["items"]


# --- catalog and columns ------------------------------------------------------------------------

def test_the_catalog_pins_3_1_2_for_fastq_from_d_seq():
    entry = NFCORE_PIPELINE_CATALOG["raredisease"]
    assert entry["default_revision"] == "3.1.2"
    assert entry["samplesheet_input_kind"] == "fastq"
    assert entry["accepted_leaf_sample_types"] == ["D.SEQ"]
    assert entry["reference_cli_flags"] == ["genome", "fasta"]


def test_the_required_columns_cover_the_input_schema_and_name_only_its_columns():
    items = _input_schema()
    catalog = NFCORE_PIPELINE_CATALOG["raredisease"]["required_columns"]
    template = _template()["pipeline"]
    assert set(items["required"]) <= set(catalog)
    assert catalog == template["required_columns"]
    columns = set(template["required_columns"]) | set(template["optional_columns"])
    assert columns <= set(items["properties"]), columns - set(items["properties"])
    for row in _template()["samplesheet_template"]:
        assert set(row) <= set(items["properties"])


def test_the_five_upstream_files_are_committed():
    for suffix in ("nextflow_schema.json", "schema_input.json", "output.md", "readme.md", "usage.md"):
        assert (FIXTURES / f"raredisease.{suffix}").is_file(), suffix


# --- param atlas: sex and phenotype from the metadata, never guessed ------------------------------

def _rule(name):
    return data_driven_params("raredisease")[name]


@pytest.mark.parametrize("signals,verdict,value", [
    ({"Sex": "Female"}, "derived_uncorroborated", "2"),
    ({"Sex": "male"}, "derived_uncorroborated", "1"),
    ({"Sex": "F"}, "derived_uncorroborated", "2"),
    ({"Sex": "M"}, "derived_uncorroborated", "1"),
    ({"Sex": "Unknown"}, "derived_uncorroborated", "0"),
    ({"Sex": "Female", "Name": "proband female"}, "corroborated", "2"),
    ({"Sex": "Female", "Name": "XY sample"}, "conflict", None),
    ({}, "absent", None),
])
def test_sex_is_derived_with_male_kept_out_of_female(signals, verdict, value):
    out = evaluate_data_driven_param(_rule("sex"), signals)
    assert out["verdict"] == verdict
    if verdict != "conflict":
        assert out["value"] == value


@pytest.mark.parametrize("signals,verdict,value", [
    ({"Phenotype": "Affected"}, "derived_uncorroborated", "2"),
    ({"Phenotype": "unaffected"}, "derived_uncorroborated", "1"),
    ({"Phenotype": "proband"}, "derived_uncorroborated", "2"),
    ({"Phenotype": "healthy control"}, "derived_uncorroborated", "1"),
    ({"Phenotype": "Affected", "Disease": "affected, Duchenne"}, "corroborated", "2"),
    ({"Phenotype": "unaffected carrier"}, "conflict", None),
    ({"Phenotype": "affected", "Disease": "none"}, "conflict", None),
    ({}, "absent", None),
])
def test_phenotype_is_derived_and_asked_when_absent_or_unclear(signals, verdict, value):
    out = evaluate_data_driven_param(_rule("phenotype"), signals)
    assert out["verdict"] == verdict
    if verdict != "conflict":
        assert out["value"] == value


def test_sex_and_phenotype_are_asked_at_write_samplesheet_not_defaulted():
    for name in ("sex", "phenotype"):
        rule = _rule(name)
        assert rule["target"] == "row_column" and rule.get("on_absent") != "use_default"
        assert rule["ask"]["on_absent"] and rule["ask"]["on_conflict"]
        assert set(rule["allowed"]) <= {"0", "1", "2"}


@pytest.mark.parametrize("strategy,verdict,value", [
    ("WGS", "derived_uncorroborated", "wgs"),
    ("WXS", "derived_uncorroborated", "wes"),
    ("Targeted-Capture", "derived_uncorroborated", "wes"),
    (None, "absent", None),
])
def test_analysis_type_follows_the_library_strategy(strategy, verdict, value):
    signals = {"LibraryStrategy": strategy} if strategy else {}
    out = evaluate_data_driven_param(_rule("analysis_type"), signals)
    assert out["verdict"] == verdict and out["value"] == value


def test_an_exome_cohort_is_asked_for_its_target_bed():
    out = evaluate_data_driven_param(_rule("target_bed"), {"LibraryStrategy": "WXS"})
    assert out["verdict"] == "needs_value"
    out = evaluate_data_driven_param(_rule("target_bed"), {"LibraryStrategy": "WGS"})
    assert out["verdict"] == "not_detected"


# --- the pedigree check -------------------------------------------------------------------------

TRIO = [
    {"sample": "D.SEQ-P", "lane": "1", "sex": "1", "phenotype": "2", "paternal_id": "D.SEQ-F",
     "maternal_id": "D.SEQ-M", "case_id": "FAM1"},
    {"sample": "D.SEQ-F", "lane": "1", "sex": "1", "phenotype": "1", "paternal_id": "",
     "maternal_id": "", "case_id": "FAM1"},
    {"sample": "D.SEQ-M", "lane": "1", "sex": "2", "phenotype": "1", "paternal_id": "0",
     "maternal_id": "0", "case_id": "FAM1"},
]


def _errors(rows):
    from chat_nextseek.seqera.pipeline_checks import row_errors
    return row_errors("raredisease", rows, {})


def test_a_consistent_trio_passes():
    assert _errors(TRIO) == []


def test_a_singleton_passes():
    assert _errors([{"sample": "D.SEQ-S", "lane": "1", "sex": "2", "phenotype": "2",
                     "case_id": "S1"}]) == []


def test_a_blank_case_id_is_refused_and_says_to_ask():
    errors = _errors([{**TRIO[1], "case_id": ""}])
    assert errors and "case_id" in errors[0] and "ask" in errors[0].lower()


@pytest.mark.parametrize("change,needle", [
    ({"paternal_id": "D.SEQ-ELSEWHERE"}, "not a sample"),
    ({"paternal_id": "D.SEQ-M"}, "sex"),
    ({"maternal_id": "D.SEQ-F"}, "sex"),
    ({"paternal_id": "D.SEQ-P"}, "own"),
    ({"case_id": "FAM2"}, "case"),
    ({"sex": "3"}, "sex"),
    ({"phenotype": "affected"}, "phenotype"),
    ({"case_id": "FAM 1"}, "space"),
])
def test_an_inconsistent_pedigree_is_refused(change, needle):
    rows = [{**TRIO[0], **change}] + TRIO[1:]
    errors = _errors(rows)
    assert errors and any(needle in e for e in errors), errors


def test_lanes_of_one_sample_must_agree_on_its_pedigree():
    rows = TRIO + [{**TRIO[0], "lane": "2", "phenotype": "1"}]
    assert any("disagree" in e for e in _errors(rows))


# --- template -----------------------------------------------------------------------------------

UNPROVISIONED_SUBWORKFLOWS = {"repeat_calling", "repeat_annotation", "snv_annotation",
                              "sv_annotation", "mt_annotation", "me_calling", "me_annotation",
                              "generate_clinical_set"}
UNPROVISIONED_TOOLS = {"gens", "germlinecnvcaller"}


def test_curated_defaults_pass_the_pinned_schema():
    from chat_nextseek.seqera.pipeline_params import build_run_params
    from chat_nextseek.seqera.schema_check import check_params
    merged, errors, _ = build_run_params("raredisease", {}, "GRCh38")
    assert errors == []
    errors, skipped = check_params("raredisease", "3.1.2", merged, required=False,
                                   schema_getter=lambda _p, _r: {"nextflow_schema": _schema()})
    assert skipped is None and errors == []


def test_every_menu_param_is_one_the_pinned_schema_declares():
    from chat_nextseek.seqera.schema_check import check_params
    errors, skipped = check_params("raredisease", "3.1.2", {n: None for n in _template()["params"]},
                                   required=False,
                                   schema_getter=lambda _p, _r: {"nextflow_schema": _schema()})
    assert skipped is None and errors == []


def test_the_subworkflows_and_tools_whose_references_are_not_staged_are_skipped():
    from chat_nextseek.seqera.pipeline_params import build_run_params
    merged, _, _ = build_run_params("raredisease", {}, "GRCh38")
    assert set(merged["skip_subworkflows"].split(",")) == UNPROVISIONED_SUBWORKFLOWS
    assert set(merged["skip_tools"].split(",")) == UNPROVISIONED_TOOLS


@pytest.mark.parametrize("params,needle", [
    ({"skip_subworkflows": "repeat_calling"}, "snv_annotation"),
    ({"skip_tools": "gens"}, "germlinecnvcaller"),
    ({"analysis_type": "wes"}, "target_bed"),
])
def test_a_launch_that_would_need_unstaged_references_is_refused(params, needle):
    from chat_nextseek.seqera.pipeline_params import build_run_params
    _, errors, _ = build_run_params("raredisease", params, "GRCh38")
    assert errors and any(needle in e for e in errors), errors


def test_more_skips_and_an_exome_with_its_bed_still_build():
    from chat_nextseek.seqera.pipeline_params import build_run_params
    skips = ",".join(sorted(UNPROVISIONED_SUBWORKFLOWS | {"mt_subsample"}))
    _, errors, _ = build_run_params(
        "raredisease", {"skip_subworkflows": skips, "analysis_type": "wes",
                        "target_bed": "/net/bmc-pub/refs/kit.bed"}, "GRCh38")
    assert errors == []


def test_the_template_says_what_is_not_provisioned():
    text = json.dumps(_template())
    for name in ("vep_cache", "gnomad_af", "variant_catalog", "intervals_wgs", "intervals_y"):
        assert name in text, name


def test_every_optional_param_says_what_question_or_data_calls_for_it():
    for name, spec in _template()["params"].items():
        text = spec.get("help") or ""
        assert len(text) > 40, name
        assert "unless asked" not in text and "old behaviour" not in text, name


# --- atlas --------------------------------------------------------------------------------------

def test_raredisease_is_a_human_only_dna_atlas_pipeline():
    from chat_nextseek.seqera.nfcore_atlas import DNA_ATLAS_PATH, load_atlas
    entry = load_atlas(DNA_ATLAS_PATH)["pipelines"]["raredisease"]
    assert entry["revision"] == "3.1.2"
    assert "HUMAN ONLY" in entry["species_constraint"]


def test_raredisease_and_sarek_point_at_each_other_with_a_question():
    from chat_nextseek.seqera.nfcore_atlas import load_atlas
    pipelines = load_atlas()["pipelines"]
    assert pipelines["raredisease"]["versus"]["sarek"]["ask_user"]
    assert pipelines["sarek"]["versus"]["raredisease"]["ask_user"]


def test_the_map_covers_the_pinned_release():
    from NessieAI.ns.reingest import maps
    assert maps.load("raredisease").covers_revision("3.1.2")

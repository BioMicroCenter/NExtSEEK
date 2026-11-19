"""nf-core/scdownstream at dev commit d9264b35: its catalog entry, matrix samplesheet, species
and ambient-correction checks, launch, template, atlas and reingest map.

The pipeline has no release. The pinned upstream files are the five
fixtures/nfcore/scdownstream.* copies (byte-identical to the repository at that commit)."""
import csv
import json
from pathlib import Path

import pytest

from NessieAI.ns.reingest import maps
from NessieAI.paths import CHAT_NEXTSEEK_DIR
from chat_nextseek.pipeline.agent_tools import tool_configure_run
from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.emitter import emit_nfcore_artifacts
from chat_nextseek.seqera.pipeline_params import build_run_params
from chat_nextseek.seqera.schema_check import check_params

KEY = "scdownstream"
SHA = "d9264b35ed88b648baf157d76bd9b261344fb585"
FIXTURES = Path(__file__).parent / "fixtures" / "nfcore"
TEMPLATE = json.loads((CHAT_NEXTSEEK_DIR / "src/chat_nextseek/reports/templates/nfcore"
                       / f"{KEY}.json").read_text())
SCHEMA = json.loads((FIXTURES / f"{KEY}.nextflow_schema.json").read_text())
INPUT = json.loads((FIXTURES / f"{KEY}.schema_input.json").read_text())["items"]

A, B = "A.SCXP-260711ENG-1-PUB", "A.SCXP-260711ENG-2-PUB"
META = {
    A: {"File_PrimaryData": "/net/bmc-pub10/x/alevin/mtx_conversions/combined_raw_matrix.h5ad",
        "File_SecondaryData": "/net/bmc-pub10/x/alevin/mtx_conversions/combined_filtered_matrix.h5ad"},
    B: {"Link_PrimaryData": "/net/bmc-pub10/y/sample_filtered.h5ad"},
}


class _Cfg:
    pass


@pytest.fixture(autouse=True)
def _pinned_schemas(monkeypatch):
    from chat_nextseek.seqera import schema_check

    monkeypatch.setattr(schema_check, "get_schema", lambda p, r: {
        "nextflow_schema": json.loads((FIXTURES / f"{p}.nextflow_schema.json").read_text())})


def _emit(tmp_path, rows, meta=META):
    result = emit_nfcore_artifacts(tmp_path, pipeline=KEY, samplesheet_rows=rows, resolutions=[],
                                   launch_plan=None, tower_env=None, accession_metadata=meta)
    return result, list(csv.DictReader((tmp_path / "samplesheet.csv").open(newline="")))


# --- catalog -------------------------------------------------------------------------------

def test_the_catalog_pins_the_commit_and_the_nextflow_it_declares():
    entry = NFCORE_PIPELINE_CATALOG[KEY]
    assert entry["default_revision"] == SHA
    assert entry["nextflow_version"] == "26.04.6"
    assert entry["samplesheet_input_kind"] == "matrix"
    assert entry["accepted_leaf_sample_types"] == ["A.SCXP"]
    assert entry["reference_cli_flags"] == []
    # nextflow.config at the commit: nextflowVersion = '!>=26.04.6'
    assert set(INPUT["required"]) <= set(entry["required_columns"]) <= set(INPUT["properties"])


# --- the matrix sheet ----------------------------------------------------------------------

def test_a_raw_matrix_goes_to_unfiltered_and_the_filtered_one_to_filtered(tmp_path):
    result, rows = _emit(tmp_path, [{"sample": A}, {"sample": B}])
    assert result.missing_input_files == []
    a = next(r for r in rows if r["sample"] == A)
    assert a["filtered"].endswith("combined_filtered_matrix.h5ad")
    assert a["unfiltered"].endswith("combined_raw_matrix.h5ad")
    b = next(r for r in rows if r["sample"] == B)
    assert b["filtered"].endswith("sample_filtered.h5ad") and not b["unfiltered"]
    for row in rows:
        assert set(k for k, v in row.items() if v) <= set(INPUT["properties"])


def test_a_sample_with_only_a_raw_matrix_is_not_a_missing_file(tmp_path):
    raw_only = {A: {"File_PrimaryData": "/net/bmc-pub10/x/s_raw_matrix.h5ad"}}
    result, rows = _emit(tmp_path, [{"sample": A}], raw_only)
    assert result.missing_input_files == []
    assert rows[0]["unfiltered"].endswith("s_raw_matrix.h5ad") and not rows[0]["filtered"]


def test_a_nextseek_uid_satisfies_the_sample_pattern():
    import re
    assert re.fullmatch(INPUT["properties"]["sample"]["pattern"], A)


# --- configure_run: species and ambient correction ----------------------------------------

def _state(tmp_path, rows, bundle="GRCh38"):
    sheet = tmp_path / "samplesheet.csv"
    with sheet.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["sample", "filtered", "unfiltered"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return {"artifacts": {"samplesheet": str(sheet)}, "bundle_key": bundle,
            "messages": [{"role": "user", "content": "go"}]}


BOTH = [{"sample": A, "filtered": "/x/f.h5ad", "unfiltered": "/x/r.h5ad"}]
FILTERED_ONLY = [{"sample": B, "filtered": "/y/f.h5ad", "unfiltered": ""}]


def _configure(state, tmp_path, params=None):
    return json.loads(tool_configure_run(_Cfg(), state, {"pipeline_key": KEY,
                                                         "params": params or {}}, str(tmp_path)))


def test_soupx_with_both_matrices_builds_with_the_human_species(tmp_path):
    out = _configure(_state(tmp_path, BOTH), tmp_path)
    assert out["ok"] is True, out
    assert out["resolved_params"]["species"] == "human"
    assert out["resolved_params"]["ambient_correction"] == "soupx"


def test_soupx_without_an_unfiltered_matrix_is_refused(tmp_path):
    out = _configure(_state(tmp_path, FILTERED_ONLY), tmp_path)
    assert out["ok"] is False
    assert B in out["errors"][0] and "decontx" in out["errors"][0]
    assert _configure(_state(tmp_path, FILTERED_ONLY), tmp_path,
                      {"ambient_correction": "decontx"})["ok"] is True


def test_a_mouse_cohort_runs_with_the_mouse_species(tmp_path):
    out = _configure(_state(tmp_path, BOTH, bundle="GRCm39"), tmp_path)
    assert out["ok"] is True and out["resolved_params"]["species"] == "mouse"


def test_a_species_with_no_cell_cycle_lists_needs_cell_cycle_scoring_off(tmp_path):
    out = _configure(_state(tmp_path, BOTH, bundle="Mmul_10"), tmp_path)
    assert out["ok"] is False and "cell_cycle_scoring" in json.dumps(out)
    out = _configure(_state(tmp_path, BOTH, bundle="Mmul_10"), tmp_path,
                     {"species": "macaque", "cell_cycle_scoring": False})
    assert out["ok"] is True, out


def test_an_unknown_species_is_asked_not_defaulted_to_human(tmp_path):
    out = _configure(_state(tmp_path, BOTH, bundle=None), tmp_path)
    assert out["ok"] is False and out["needs_user_input"] == ["species"]


def test_a_species_the_cohort_contradicts_is_refused(tmp_path):
    out = _configure(_state(tmp_path, BOTH, bundle="GRCm39"), tmp_path, {"species": "human"})
    assert out["ok"] is False and "mouse" in json.dumps(out)


# --- launch on Luria -------------------------------------------------------------------------

def test_run_sh_pins_the_commit_the_nextflow_release_and_the_strict_parser():
    from chat_nextseek.luria.run_script import luria_params_overrides, render_run_script
    sh = render_run_script(job_name="j", pipeline="nf-core/scdownstream", revision=SHA,
                           run_dir="/w/runs/r", work_dir="/w/work/r",
                           singularity_cache="/w/singularity_cache", genome="GRCh38",
                           resources=None, refs_root="/w/refs", reference_cli_flags=[],
                           nextflow_version="26.04.6")
    assert f"-r {SHA}" in sh and "NXF_VER=26.04.6 nextflow run" in sh
    assert "export NXF_SYNTAX_PARSER=v2" in sh
    assert "--genome" not in sh and "--fasta" not in sh
    # No reference is passed, so nothing replaces iGenomes: igenomes_ignore is not a
    # parameter of this pipeline and stays out of params.yml.
    assert luria_params_overrides("nf-core/scdownstream", "GRCh38") == {}


def test_rnavar_still_gets_igenomes_ignore_under_v2():
    from chat_nextseek.luria.run_script import luria_params_overrides
    assert luria_params_overrides("nf-core/rnavar", "GRCh38") == {"igenomes_ignore": True}


# --- template ------------------------------------------------------------------------------

def _schema(_p, _r):
    return {"nextflow_schema": SCHEMA}


def test_curated_defaults_pass_the_pinned_schema():
    merged, errors, _ = build_run_params(KEY, {}, None)
    assert errors == []
    errs, skipped = check_params(KEY, SHA, merged, schema_getter=_schema)
    assert skipped is None and errs == []


def test_every_menu_param_is_one_the_pinned_schema_declares():
    errs, skipped = check_params(KEY, SHA, {n: None for n in TEMPLATE["params"]},
                                 required=False, schema_getter=_schema)
    assert skipped is None and errs == []


def test_each_curated_default_is_upstreams():
    props = {k: v for d in SCHEMA["$defs"].values() for k, v in d["properties"].items()}
    for name, spec in TEMPLATE["params"].items():
        if spec.get("default") is not None:
            assert spec["default"] == props[name].get("default"), name


def test_every_optional_param_says_what_calls_for_it():
    for name, spec in TEMPLATE["params"].items():
        help_text = spec.get("help", "")
        assert len(help_text) > 40, name
        assert "unless asked" not in help_text and "old behaviour" not in help_text, name


def test_template_columns_are_input_schema_columns():
    pipeline = TEMPLATE["pipeline"]
    cols = set(pipeline["required_columns"]) | set(pipeline["optional_columns"])
    assert cols <= set(INPUT["properties"])
    for row in TEMPLATE["samplesheet_template"]:
        assert set(row) <= set(INPUT["properties"])


# --- atlas ---------------------------------------------------------------------------------

def test_the_rna_atlas_pins_the_commit_and_points_at_scrnaseq_both_ways():
    from chat_nextseek.seqera.nfcore_atlas import ATLAS_PATH, load_atlas
    rna = load_atlas(ATLAS_PATH)["pipelines"]
    assert rna[KEY]["revision"] == SHA
    assert rna[KEY]["versus"]["scrnaseq"]["ask_user"]
    assert rna["scrnaseq"]["versus"][KEY]["ask_user"]


# --- reingest map --------------------------------------------------------------------------

def test_the_map_is_read_at_the_pinned_commit_and_takes_matrices_as_parents():
    m = maps.load(KEY)
    assert m.applies_to_commits == [SHA] and m.covers_revision(SHA)
    assert m.accepts_parent_types == ["A.SCXP"]
    _, warnings = maps.effective(m, "v0.0.1dev-gd9264b3")
    assert warnings == []
    _, warnings = maps.effective(m, "v0.0.1dev-g0123456")
    assert warnings


def test_the_final_object_is_one_clustering_row_for_the_run():
    rule = next(r for r in maps.load(KEY).outputs if r.id == "merged")
    assert rule.sample_type == "A.SCCL" and rule.cardinality == "per_run"
    assert rule.candidates == ["09_finalized/merged.h5ad"]

"""nf-core/proteinfold 2.0.0 and nf-core/pangenome 1.1.3: the two pipelines of the other atlas
whose input is not reads. proteinfold folds one protein FASTA per D.PSEQ sample; pangenome builds
one graph from ONE combined, bgzipped multi-genome FASTA (an A.ASM sample) with no samplesheet.

Every curated value is read against the pinned upstream files committed under fixtures/nfcore/
(byte-identical copies of the release tag's nextflow_schema.json, assets/schema_input.json where
the release has one, docs/output.md, docs/usage.md and README.md)."""
import csv
import json
from pathlib import Path

import pytest
import yaml

import chat_nextseek.luria.submitter as sub
from NessieAI.paths import CHAT_NEXTSEEK_DIR
from chat_nextseek.seqera import emitter
from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.input_kinds import input_extensions, input_kind, read_samplesheet
from chat_nextseek.seqera.nfcore_atlas import load_atlas
from chat_nextseek.seqera.param_atlas import (
    ParamAtlasError, evaluate_data_driven_param, load_param_atlas,
)
from chat_nextseek.seqera.pipeline_params import build_run_params
from chat_nextseek.seqera.schema_check import check_params, missing_required
from chat_nextseek.seqera.user_params import missing_user_params, validate_user_params

TEMPLATES = CHAT_NEXTSEEK_DIR / "src/chat_nextseek/reports/templates/nfcore"
FIXTURES = Path(__file__).parent / "fixtures" / "nfcore"
PINS = {"proteinfold": "2.0.0", "pangenome": "1.1.3"}


def _schema(key):
    return json.loads((FIXTURES / f"{key}.nextflow_schema.json").read_text())


def _schema_property(key, name):
    for group in (_schema(key).get("$defs") or _schema(key).get("definitions") or {}).values():
        if name in (group.get("properties") or {}):
            return group["properties"][name]
    raise KeyError(name)


def _template(key):
    return json.loads((TEMPLATES / f"{key}.json").read_text())


def _getter(key):
    schema = _schema(key)
    return lambda _p, _r: {"nextflow_schema": schema}


# --- catalog ---------------------------------------------------------------------------------

@pytest.mark.parametrize("key", sorted(PINS))
def test_the_catalog_pins_the_release_the_fixtures_were_read_at(key):
    assert NFCORE_PIPELINE_CATALOG[key]["default_revision"] == PINS[key]
    assert load_atlas()["pipelines"][key]["revision"] == PINS[key]


def test_proteinfold_takes_one_protein_fasta_per_d_pseq_and_never_a_gzipped_one():
    entry = NFCORE_PIPELINE_CATALOG["proteinfold"]
    assert input_kind(entry).name == "protein_fasta"
    assert entry["accepted_leaf_sample_types"] == ["D.PSEQ"]
    # assets/schema_input.json 2.0.0: fasta must match ^\S+\.(fa(sta)?|faa|yaml|yml|json)$.
    assert set(input_extensions(entry)) == {".fasta", ".fa", ".faa"}
    # nextflow.config manifest: nextflowVersion = '!>=25.10.2' (a hard minimum).
    major, minor, _ = (int(x) for x in entry["nextflow_version"].split("."))
    assert (major, minor) >= (25, 10)


def test_pangenome_takes_one_bgzipped_combined_fasta_from_an_a_asm_sample():
    entry = NFCORE_PIPELINE_CATALOG["pangenome"]
    kind = input_kind(entry)
    assert kind.name == "combined_fasta" and kind.samplesheet is False
    assert entry["accepted_leaf_sample_types"] == ["A.ASM"]
    # docs/usage.md 1.1.3: "You will need to bgzip your input FASTA"; the schema pattern is
    # ^\S+\.fn?a(sta)?(\.gz)?$ (no .fas), and input_check.nf's uncompressed branch calls
    # String.endswith, which does not exist, for a .fasta or .fna name.
    assert set(input_extensions(entry)) == {".fa.gz", ".fna.gz", ".fasta.gz"}
    assert "nextflow_version" not in entry  # !>=24.04.2: the launcher's own release runs it


# --- emitter, write_samplesheet and the submitter ------------------------------------------

def test_a_proteinfold_row_is_id_and_fasta(tmp_path):
    meta = {"D.PSEQ-1": {"Link_PrimaryData": "/net/bmc/lab/antibody_hc_lc.fasta"}}
    res = emitter.emit_nfcore_artifacts(
        tmp_path, pipeline="proteinfold", samplesheet_rows=[{"sample": "D.PSEQ-1"}],
        resolutions=[], accession_metadata=meta, launch_plan=None, tower_env={})
    path = Path(res.saved_files["samplesheet"])
    assert path.name == "samplesheet.csv"  # --input must match ^\S+\.csv$
    rows = read_samplesheet(path)
    assert rows[0]["id"] == "D.PSEQ-1" and rows[0]["fasta"] == "/net/bmc/lab/antibody_hc_lc.fasta"
    assert "sample" not in rows[0] and "fastq_1" not in rows[0]
    items = json.loads((FIXTURES / "proteinfold.schema_input.json").read_text())["items"]
    assert set(rows[0]) <= set(items["properties"]) | set(emitter.PIPELINE_COLUMN_ALIASES.get("proteinfold", {}).values())
    assert res.missing_input_files == []


def test_a_gzipped_protein_fasta_is_reported_missing_not_passed_on(tmp_path):
    res = emitter.emit_nfcore_artifacts(
        tmp_path, pipeline="proteinfold", samplesheet_rows=[{"sample": "D.PSEQ-2"}],
        resolutions=[], accession_metadata={"D.PSEQ-2": {"Link_PrimaryData": "/net/bmc/p.fasta.gz"}},
        launch_plan=None, tower_env={})
    assert res.missing_input_files == ["D.PSEQ-2"]


def _write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _capture(monkeypatch):
    seen = {"scp": []}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/key")
    monkeypatch.setattr(sub, "ssh_run", lambda env, cmd, **kw: "Submitted batch job 9")
    monkeypatch.setattr(sub, "scp_file", lambda env, src, dst, **kw: seen["scp"].append((str(src), dst)))
    real = sub.render_run_script
    monkeypatch.setattr(sub, "render_run_script", lambda **kw: seen.update(kw) or real(**kw))
    return seen


def _launch(tmp_path, key):
    path = tmp_path / "launch.yml"
    path.write_text(yaml.safe_dump({"launch": [{"name": "r", "pipeline": f"nf-core/{key}",
                                                "revision": PINS[key]}]}))
    return path


LE = {"user": "u", "host": "luria.mit.edu", "key": "/k", "working_path": "/work"}


def test_pangenome_passes_the_one_combined_fasta_as_input(tmp_path, monkeypatch):
    sheet = tmp_path / "samplesheet.csv"
    _write_csv(sheet, [{"sample": "A.ASM-1", "fasta": "/net/bmc/lab/ecoli_12.fa.gz"}])
    seen = _capture(monkeypatch)
    runs = sub.submit_luria(_launch(tmp_path, "pangenome"), luria_env=LE, samplesheet_local=str(sheet))
    assert runs and seen["input_arg"] == "/net/bmc/lab/ecoli_12.fa.gz"
    assert seen["needs_fetch"] is False


def test_proteinfold_runs_on_the_nextflow_its_manifest_requires(tmp_path, monkeypatch):
    sheet = tmp_path / "samplesheet.csv"
    _write_csv(sheet, [{"id": "D.PSEQ-1", "fasta": "/net/bmc/lab/p.fasta"}])
    seen = _capture(monkeypatch)
    sub.submit_luria(_launch(tmp_path, "proteinfold"), luria_env=LE, samplesheet_local=str(sheet))
    assert seen["input_arg"] == "samplesheet.csv"
    assert seen["nextflow_version"] == NFCORE_PIPELINE_CATALOG["proteinfold"]["nextflow_version"]


def test_write_samplesheet_refuses_two_assemblies_for_pangenome(tmp_path):
    from chat_nextseek.pipeline.agent_tools import tool_write_samplesheet

    state = {"resolved": {"uids": ["A.ASM-1", "A.ASM-2"], "accessions": []},
             "accession_file_paths": {"A.ASM-1": {"Link_PrimaryData": "/net/bmc/a.fa.gz"},
                                      "A.ASM-2": {"Link_PrimaryData": "/net/bmc/b.fa.gz"}}}
    out = json.loads(tool_write_samplesheet(object(), state, {
        "pipeline_key": "pangenome",
        "cohorts": [{"label": "c", "rows": [{"sample": "A.ASM-1"}, {"sample": "A.ASM-2"}]}]},
        str(tmp_path)))
    assert out["ok"] is False and "exactly one" in out["errors"][0]


# --- templates against the pinned schemas ----------------------------------------------------

def _launched(key, extra=None):
    user = {s["name"]: (s.get("example") or "x").split()[0]
            for s in __import__("chat_nextseek.seqera.user_params", fromlist=["x"]).required_user_params(key)}
    merged, errors, _ = build_run_params(key, {**user, **(extra or {})}, None)
    assert errors == [], errors
    return merged


@pytest.mark.parametrize("key", sorted(PINS))
def test_every_menu_param_is_one_the_pinned_schema_declares(key):
    menu = _template(key)["params"]
    errors, skipped = check_params(key, PINS[key], {name: None for name in menu}, required=False,
                                   schema_getter=_getter(key))
    assert skipped is None and errors == []


def test_proteinfolds_catalog_template_and_input_schema_name_the_same_required_columns():
    items = json.loads((FIXTURES / "proteinfold.schema_input.json").read_text())["items"]
    template = _template("proteinfold")
    assert (set(NFCORE_PIPELINE_CATALOG["proteinfold"]["required_columns"])
            == set(template["pipeline"]["required_columns"]) == set(items["required"]))
    for row in template["samplesheet_template"]:
        assert set(row) <= set(items["properties"])


@pytest.mark.parametrize("key", sorted(PINS))
def test_every_curated_default_passes_the_pinned_schema(key):
    # pangenome's n_haplotypes is required and has no default: the param atlas sets it at
    # configure_run from the cohort (or asks), so it is given here as that step would.
    extra = {"n_haplotypes": 12} if key == "pangenome" else None
    errors, skipped = check_params(key, PINS[key], _launched(key, extra), required=True,
                                   schema_getter=_getter(key))
    assert skipped is None and errors == []


def test_pangenome_without_n_haplotypes_is_missing_exactly_that():
    names, _ = missing_required("pangenome", "1.1.3", _launched("pangenome"),
                                schema_getter=_getter("pangenome"))
    assert names == ["n_haplotypes"]


@pytest.mark.parametrize("key", sorted(PINS))
def test_every_curated_default_matches_upstreams_or_says_why_not(key):
    """The owner's rule: upstream's default is the fallback. A menu default that differs must
    say so in its help (proteinfold's mode, whose upstream default needs ~2 TB of databases)."""
    for name, spec in _template(key)["params"].items():
        upstream = _schema_property(key, name).get("default")
        ours = spec.get("default")
        if ours is None or ours == upstream:
            continue
        assert "upstream" in spec["help"].lower() and str(upstream) in spec["help"], (key, name, ours, upstream)


@pytest.mark.parametrize("key", sorted(PINS))
def test_no_help_text_switches_an_analysis_off_unless_asked(key):
    for name, spec in _template(key)["params"].items():
        text = (spec.get("help") or "").lower()
        assert "unless asked" not in text and "old behaviour" not in text, (key, name)
        assert text, (key, name)


def test_proteinfolds_fallback_mode_is_esmfold_and_its_help_says_upstream_is_alphafold2():
    spec = _template("proteinfold")["params"]["mode"]
    assert spec["default"] == "esmfold"
    assert _schema_property("proteinfold", "mode")["default"] == "alphafold2"
    for word in ("single chain", "complex", "ligand", "accura"):
        assert word in spec["help"].lower(), word


@pytest.mark.parametrize("mode, db", [
    ("colabfold", "colabfold_db"), ("alphafold2", "alphafold2_db"), ("boltz", "boltz_db"),
    ("rosettafold_all_atom", "rosettafold_all_atom_db"), ("helixfold3", "helixfold3_db"),
    ("rosettafold2na", "rosettafold2na_db"), ("alphafold3", "alphafold3_db"),
])
def test_a_mode_whose_databases_are_not_staged_needs_their_path_before_launch(mode, db):
    """Without --<mode>_db proteinfold downloads the databases into the run folder (hundreds of
    GB to over 2 TB). None is staged on Luria, so the path is asked for, never left to download."""
    asked = {s["name"] for s in missing_user_params("proteinfold", {"mode": mode})}
    assert db in asked
    assert missing_user_params("proteinfold", {"mode": mode, db: "/net/bmc/db/x",
                                               **({"alphafold3_params_path": "/net/bmc/af3/af3.bin"}
                                                  if mode == "alphafold3" else {})}) == []


def test_esmfold_needs_no_staged_path():
    assert missing_user_params("proteinfold", {"mode": "esmfold"}) == []
    assert missing_user_params("proteinfold", {}) == []


def test_a_database_path_must_be_an_absolute_cluster_path():
    assert validate_user_params("proteinfold", {"colabfold_db": "relative/db"})
    assert validate_user_params("proteinfold", {"colabfold_db": "/net/bmc/db/colabfold"}) == []


def test_the_msa_server_is_never_switched_on_by_the_menu():
    """use_msa_server sends the sequences to a public server outside MIT: not the agent's call."""
    menu = _template("proteinfold")["params"]
    for name in ("use_msa_server", "use_gpu"):
        # Unset: upstream's own default (off) applies.
        assert menu[name]["default"] is None and menu[name]["steerable"] is False, name
        assert _schema_property("proteinfold", name).get("default") in (None, False), name


def test_the_proteinfold_template_says_gpu_and_databases_are_unconfirmed_on_luria():
    text = json.dumps(_template("proteinfold")).lower()
    assert "gpu" in text and "not confirmed" in text


def test_pangenome_template_states_the_one_combined_fasta_limit():
    text = json.dumps(_template("pangenome")).lower()
    assert "one" in text and "combined" in text and "bgzip" in text and "pansn" in text


# --- the param atlas -------------------------------------------------------------------------

def test_n_haplotypes_comes_from_the_assemblys_genome_count_and_is_asked_when_absent():
    spec = load_param_atlas()["pipelines"]["pangenome"]["params"]["n_haplotypes"]
    assert spec["target"] == "run_param" and spec["value_type"] == "integer"
    assert spec.get("on_absent") != "use_default"
    got = evaluate_data_driven_param(spec, {"GenomeCount": "12"})
    assert got["value"] == "12" and got["verdict"] in ("derived_uncorroborated", "corroborated")
    assert evaluate_data_driven_param(spec, {})["verdict"] == "absent"
    # One haplotype is not a pangenome.
    assert evaluate_data_driven_param(spec, {"GenomeCount": "1"})["verdict"] == "absent"
    assert "pansn" in spec["ask"]["definition"].lower()


def _band_spec(**over):
    spec = {"target": "run_param", "scope": "run", "value_type": "string",
            "allowed": ["monomer", "multimer"], "on_absent": "use_default", "default": "monomer",
            "derive_from": {"signal": "ChainCount", "number": "bands",
                            "bands": [[1, "monomer"], [2, "multimer"]]},
            "ask": {"definition": "d", "on_conflict": "c", "on_absent": "a"}}
    spec.update(over)
    return spec


@pytest.mark.parametrize("count, value", [("1", "monomer"), ("2", "multimer"), ("6", "multimer"),
                                          (3, "multimer"), ("1.0", "monomer")])
def test_a_banded_number_votes_the_band_it_falls_in(count, value):
    got = evaluate_data_driven_param(_band_spec(), {"ChainCount": count})
    assert got["value"] == value and got["verdict"] == "derived_uncorroborated"


@pytest.mark.parametrize("count", ["0", "", "two", None])
def test_a_number_below_every_band_or_unreadable_falls_back_to_the_default(count):
    got = evaluate_data_driven_param(_band_spec(), {"ChainCount": count})
    assert got["verdict"] == "defaulted" and got["value"] == "monomer"


def _atlas_with(spec, tmp_path):
    path = tmp_path / "atlas.json"
    path.write_text(json.dumps({"pipelines": {"proteinfold": {"params": {"esmfold_model_preset": spec}}}}))
    return path


@pytest.mark.parametrize("bad", [
    {"bands": [[2, "multimer"], [1, "monomer"]]},     # not ascending
    {"bands": [[1, "monomer"], [2, "trimer"]]},       # value not allowed
    {"bands": []},
    {"bands": [[1.5, "monomer"]]},
])
def test_a_malformed_band_list_is_refused(bad, tmp_path):
    spec = _band_spec()
    spec["derive_from"] = {**spec["derive_from"], **bad}
    with pytest.raises(ParamAtlasError, match="band"):
        load_param_atlas(_atlas_with(spec, tmp_path))


@pytest.mark.parametrize("param, single, complex_", [
    ("esmfold_model_preset", "monomer", "multimer"),
    ("colabfold_model_preset", "alphafold2_ptm", "alphafold2_multimer_v3"),
    ("alphafold2_model_preset", "monomer_ptm", "multimer"),
])
def test_proteinfold_reads_single_chain_or_complex_off_the_chain_count(param, single, complex_):
    spec = load_param_atlas()["pipelines"]["proteinfold"]["params"][param]
    assert evaluate_data_driven_param(spec, {"ChainCount": "1"})["value"] == single
    assert evaluate_data_driven_param(spec, {"ChainCount": "2"})["value"] == complex_
    # The fallback is upstream's own default, and every value is one the schema allows.
    assert spec["default"] == _schema_property("proteinfold", param)["default"] == single
    assert set(spec["allowed"]) <= set(_schema_property("proteinfold", param)["enum"])
    assert _template("proteinfold")["params"][param]["default"] == single


# --- atlas -------------------------------------------------------------------------------------

def test_both_are_in_the_other_atlas_with_the_fields_the_selector_reads():
    from chat_nextseek.seqera.nfcore_atlas import OTHER_ATLAS_PATH
    other = json.loads(OTHER_ATLAS_PATH.read_text())["pipelines"]
    for key in PINS:
        entry = other[key]
        for field in ("answers", "assumes_prep", "input_you_must_have", "data_signals",
                      "species_constraint", "versus"):
            assert field in entry, (key, field)


def test_the_pangenome_entry_states_the_single_combined_fasta_limit_and_its_cohort():
    entry = load_atlas()["pipelines"]["pangenome"]
    text = (entry["input_you_must_have"] + " " + entry["assumes_prep"]).lower()
    assert "one" in text and "combined" in text and "not supported yet" in text
    assert "pansn" in text and "bgzip" in text
    assert entry["versus"] == {}
    assert "species" in entry["answers"].lower()


def test_the_proteinfold_entry_says_plainly_what_luria_lacks():
    entry = load_atlas()["pipelines"]["proteinfold"]
    text = json.dumps(entry).lower()
    assert "gpu" in text and "not confirmed" in text
    assert "ligand" in text and "yaml" in text
    assert entry["versus"] == {}

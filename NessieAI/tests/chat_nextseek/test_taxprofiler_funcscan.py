"""nf-core/taxprofiler 2.0.1 and nf-core/funcscan 4.0.0, wired end to end.

taxprofiler profiles shotgun metagenomic reads (D.SEQ) against classification databases it
neither ships nor builds; Nessie writes its `--databases` sheet into the run folder, as it
writes rnasplice's contrasts. funcscan screens assemblies (A.ASM FASTA) for antimicrobial
peptides, resistance genes, biosynthetic gene clusters and CAZymes. Every fact below is read
off the pinned files in fixtures/nfcore/ (`<key>.nextflow_schema.json`, `.schema_input.json`,
`.usage.md`, `.output.md`, `.readme.md`).
"""
import csv
import json
from pathlib import Path

import pytest

from chat_nextseek.pipeline import agent_tools as at
from chat_nextseek.seqera import taxprofiler_databases as tdb
from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.nfcore_atlas import OTHER_ATLAS_PATH, load_atlas
from chat_nextseek.seqera.param_atlas import evaluate_data_driven_param, load_param_atlas
from chat_nextseek.seqera.pipeline_params import build_run_params
from chat_nextseek.seqera.schema_check import check_params

FIXTURES = Path(__file__).parent / "fixtures" / "nfcore"
PINS = {"taxprofiler": "2.0.1", "funcscan": "4.0.0"}


def _schema(key):
    return json.loads((FIXTURES / f"{key}.nextflow_schema.json").read_text())


def _schema_props(key):
    doc = _schema(key)
    out = {}
    for section in (doc.get("$defs") or doc.get("definitions") or {}).values():
        out.update(section.get("properties") or {})
    return out


@pytest.fixture(autouse=True)
def _pinned_schemas(monkeypatch):
    """configure_run checks params against the pinned schema; serve it from the fixtures."""
    from chat_nextseek.seqera import schema_check

    def get(pipeline, revision):
        return {"nextflow_schema": _schema(pipeline)}
    monkeypatch.setattr(schema_check, "get_schema", get)
    monkeypatch.setattr(at, "lookup_run_sizes", lambda accs: {})


class _Cfg:
    LOG_DIR = "/tmp"
    TOWER_ENV = {}


# --- catalog and atlas -------------------------------------------------------------------

@pytest.mark.parametrize("key", sorted(PINS))
def test_the_catalog_pins_the_release_the_fixtures_were_read_at(key):
    entry = NFCORE_PIPELINE_CATALOG[key]
    assert entry["default_revision"] == PINS[key]
    assert _schema(key)["$id"].endswith("/nextflow_schema.json")


def test_taxprofiler_takes_reads_and_funcscan_takes_assemblies():
    tax, fun = NFCORE_PIPELINE_CATALOG["taxprofiler"], NFCORE_PIPELINE_CATALOG["funcscan"]
    assert (tax["samplesheet_input_kind"], tax["accepted_leaf_sample_types"]) == ("fastq", ["D.SEQ"])
    assert (fun["samplesheet_input_kind"], fun["accepted_leaf_sample_types"]) == ("fasta", ["A.ASM"])
    # taxprofiler 2.0.1's schema_input.json: FASTQ must be gzipped.
    assert set(tax["input_extensions"]) == {".fastq.gz", ".fq.gz"}
    # funcscan's sample/fasta columns are the generic FASTA path column, so no alias.
    assert fun["required_columns"] == ["sample", "fasta"]
    required = json.loads((FIXTURES / "taxprofiler.schema_input.json").read_text())["items"]["required"]
    assert set(required) <= set(tax["required_columns"])


@pytest.mark.parametrize("key", sorted(PINS))
def test_both_are_in_the_other_atlas_at_their_catalog_pin_with_every_field(key):
    other = json.loads(OTHER_ATLAS_PATH.read_text())["pipelines"]
    entry = other[key]
    assert entry["revision"] == PINS[key]
    for field in ("answers", "assumes_prep", "input_you_must_have", "data_signals",
                  "species_constraint", "versus"):
        assert entry.get(field), (key, field)
    assert key in load_atlas()["pipelines"]


def test_taxprofiler_and_funcscan_point_at_each_other_with_a_question():
    pipelines = load_atlas()["pipelines"]
    assert pipelines["taxprofiler"]["versus"]["funcscan"]["ask_user"]
    assert pipelines["funcscan"]["versus"]["taxprofiler"]["ask_user"]


def test_taxprofiler_explains_mag_and_ampliseq_in_its_text():
    """mag and ampliseq are catalog pipelines outside the atlas, so `versus` cannot name
    them (load_atlas refuses an unknown key); the entry's own text has to."""
    entry = load_atlas()["pipelines"]["taxprofiler"]
    text = " ".join(str(entry[f]) for f in ("answers", "assumes_prep", "input_you_must_have"))
    assert "mag" in text and "ampliseq" in text
    assert "16S" in text
    assert "none is provisioned on Luria" in entry["input_you_must_have"]


def test_funcscan_says_a_reads_only_cohort_needs_assembling_first():
    entry = load_atlas()["pipelines"]["funcscan"]
    text = " ".join(str(entry[f]) for f in ("answers", "assumes_prep", "input_you_must_have"))
    assert "assembl" in text.lower()
    assert "mag" in text and "bacass" in text
    assert "A.ASM" in text


# --- curated menus against the pinned schemas ---------------------------------------------

@pytest.mark.parametrize("key", sorted(PINS))
def test_the_curated_menu_names_only_real_params_with_real_defaults(key):
    from chat_nextseek.seqera.pipeline_params import load_pipeline_context
    props = _schema_props(key)
    menu = load_pipeline_context(key)["params"]
    for name, spec in menu.items():
        assert name in props, f"{key}: {name} is not in the pinned schema"
        assert spec.get("help"), f"{key}: {name} has no help saying what calls for it"
        allowed = spec.get("allowed")
        if allowed:
            assert set(allowed) <= set(props[name].get("enum") or allowed), (key, name)
    merged, errors, _ = build_run_params(key, {}, None)
    assert errors == []
    schema_errors, skip = check_params(key, PINS[key], merged, required=False)
    assert (schema_errors, skip) == ([], None)


@pytest.mark.parametrize("key", sorted(PINS))
def test_no_help_text_defaults_by_habit(key):
    blob = (Path(at.__file__).resolve().parents[1] / "reports" / "templates" / "nfcore"
            / f"{key}.json").read_text().lower()
    for phrase in ("off unless asked", "keep old behaviour", "keep old behavior", "off by default"):
        assert phrase not in blob, (key, phrase)


def test_every_classifier_nessie_knows_has_a_run_flag_in_the_pinned_schema():
    props = _schema_props("taxprofiler")
    assert {t for t in tdb.TOOLS} == {n[len("run_"):] for n in props
                                      if n.startswith("run_") and n[len("run_"):] in tdb.TOOLS}
    for tool in tdb.TOOLS:
        assert f"run_{tool}" in props, tool
    # schema_database.json at 2.0.1 names these fourteen tools.
    assert set(tdb.TOOLS) == {"bracken", "centrifuge", "diamond", "ganon", "kaiju", "kmcp",
                              "kraken2", "krakenuniq", "malt", "metaphlan", "motus", "sylph",
                              "melon", "metacache"}


# --- the database sheet ---------------------------------------------------------------------

def test_a_line_per_database_becomes_a_sheet_row():
    rows, errors = tdb.parse_databases("kraken2 /net/dbs/k2_standard_08gb.tar.gz\n"
                                       "metaphlan /net/dbs/mpa_vJan25/ --index mpa_vJan25")
    assert errors == []
    assert rows == [
        {"tool": "kraken2", "db_name": "k2_standard_08gb", "db_params": "", "db_path": "/net/dbs/k2_standard_08gb.tar.gz"},
        {"tool": "metaphlan", "db_name": "mpa_vJan25", "db_params": "--index mpa_vJan25", "db_path": "/net/dbs/mpa_vJan25/"},
    ]


def test_a_pasted_sheet_and_a_list_of_entries_are_read_too():
    rows, errors = tdb.parse_databases(
        "tool,db_name,db_params,db_path\nbracken,std8,;-r 150,/net/dbs/k2_std8/\n")
    assert errors == [] and rows[0]["db_params"] == ";-r 150"
    rows, errors = tdb.parse_databases([{"tool": "motus", "db_path": "/net/dbs/motus_db"}])
    assert errors == [] and rows[0]["db_name"] == "motus_db"


@pytest.mark.parametrize("answer,fragment", [
    ("kraken3 /net/dbs/k2", "not a classifier"),
    ("kraken2 net/dbs/k2", "absolute"),
    ("kraken2 /net/dbs/k2 --confidence '0.1'", "quotes"),
    ("kraken2 /net/dbs/k2 --quick; rm -rf /", "characters"),
    ("kraken2 /net/dbs/k2\nbracken /net/dbs/k2", "unique"),
    ("", "no database"),
])
def test_a_sheet_taxprofiler_would_refuse_is_refused_here(answer, fragment):
    rows, errors = tdb.parse_databases(answer)
    assert rows == [] and any(fragment in e for e in errors), errors


def test_a_two_step_classifiers_semicolon_is_allowed():
    rows, errors = tdb.parse_databases("bracken /net/dbs/k2std --quick;-r 150")
    assert errors == [] and rows[0]["db_params"] == "--quick;-r 150"


def test_the_written_sheet_is_what_schema_database_reads(tmp_path):
    rows, _ = tdb.parse_databases("kraken2 /net/dbs/k2 --quick\nmetaphlan /net/dbs/mpa")
    path = tdb.write_databases_csv(rows, tmp_path)
    assert path.name == "databases.csv"
    got = list(csv.DictReader(path.open()))
    assert list(got[0]) == ["tool", "db_name", "db_params", "db_path"]
    assert got[1] == {"tool": "metaphlan", "db_name": "mpa", "db_params": "", "db_path": "/net/dbs/mpa"}


def test_every_classifier_with_a_database_runs_unless_the_question_names_some():
    rows, _ = tdb.parse_databases("bracken /net/dbs/k2std\nmetaphlan /net/dbs/mpa")
    flags, errors = tdb.classifier_flags(rows, {})
    assert errors == []
    # bracken needs kraken2 (taxprofiler 2.0.1 error()s on run_bracken without run_kraken2).
    assert flags == {"run_bracken": True, "run_kraken2": True, "run_metaphlan": True}
    flags, errors = tdb.classifier_flags(rows, {"run_metaphlan": True})
    assert errors == [] and flags == {"run_metaphlan": True}


def test_a_named_classifier_with_no_database_is_refused():
    rows, _ = tdb.parse_databases("metaphlan /net/dbs/mpa")
    _, errors = tdb.classifier_flags(rows, {"run_kraken2": True})
    assert errors and "kraken2" in errors[0]
    _, errors = tdb.classifier_flags(rows, {"run_metaphlan": False})
    assert errors and "no classifier" in errors[0]


def test_run_merging_follows_the_rows():
    assert tdb.needs_run_merging([{"sample": "A", "fastq_1": "/x/a.fq.gz"},
                                  {"sample": "A", "fastq_1": "/x/b.fq.gz"}])
    # An experiment accession may download as several runs of one sample.
    assert tdb.needs_run_merging([{"sample": "A", "fastq_1": "", "accession": "SRX123"}])
    assert not tdb.needs_run_merging([{"sample": "A", "fastq_1": "", "accession": "SRR123"},
                                      {"sample": "B", "fastq_1": "/x/b.fq.gz"}])


# --- configure_run ---------------------------------------------------------------------------

def _tax_state(tmp_path, rows=None):
    sheet = tmp_path / "samplesheet.csv"
    rows = rows or [("D.SEQ-1", "D.SEQ-1", "ILLUMINA", "/net/fq/a_R1.fastq.gz", "/net/fq/a_R2.fastq.gz")]
    sheet.write_text("sample,run_accession,instrument_platform,fastq_1,fastq_2,fasta\n"
                     + "".join(",".join(r) + ",\n" for r in rows))
    return {"artifacts": {"samplesheet": str(sheet), "base_dir": str(tmp_path)},
            "bundle_key": None, "data_driven_evidence": {}}


def _configure(tmp_path, state, key, params):
    return json.loads(at.tool_configure_run(
        _Cfg(), state, {"pipeline_key": key, "params": params}, str(tmp_path)))


def test_with_no_database_configured_the_user_is_asked_and_told_none_is_on_luria(tmp_path, monkeypatch):
    monkeypatch.setattr(tdb, "curated_databases", lambda: [])
    out = _configure(tmp_path, _tax_state(tmp_path), "taxprofiler", {})
    assert out["ok"] is False and out["needs_user_input"] == ["databases"]
    assert "none is provisioned on Luria" in out["ask_the_user"]


def test_named_databases_are_written_into_the_run_and_set_the_classifiers(tmp_path, monkeypatch):
    import yaml
    monkeypatch.setattr(tdb, "curated_databases", lambda: [])
    state = _tax_state(tmp_path)
    out = _configure(tmp_path, state, "taxprofiler",
                     {"databases": "kraken2 /net/dbs/k2std\nmetaphlan /net/dbs/mpa"})
    assert out["ok"] is True, out
    assert out["resolved_params"]["databases"] == "databases.csv"
    assert out["resolved_params"]["run_kraken2"] is True
    assert out["resolved_params"]["run_metaphlan"] is True
    assert "run_bracken" not in out["resolved_params"]
    assert out["databases_written"][0]["tool"] == "kraken2"
    assert (tmp_path / "databases.csv").exists()
    assert at._staged_extra_files(state["artifacts"]) == {"databases.csv": str(tmp_path / "databases.csv")}
    params = yaml.safe_load(open(out["params_yml"]))
    assert params["databases"] == "databases.csv" and params["run_kraken2"] is True
    assert out["schema_check"]["status"] == "ok", out["schema_check"]
    # One run per sample: nothing to merge, so the release's default stands.
    assert "perform_runmerging" not in out["resolved_params"]


def test_the_curated_list_is_used_without_asking(tmp_path, monkeypatch):
    monkeypatch.setattr(tdb, "curated_databases", lambda: [
        {"tool": "kraken2", "db_name": "k2std", "db_params": "", "db_path": "/net/refs/k2std"}])
    state = _tax_state(tmp_path)
    out = _configure(tmp_path, state, "taxprofiler", {})
    assert out["ok"] is True, out
    assert out["resolved_params"]["run_kraken2"] is True
    assert "databases.csv" in at._staged_extra_files(state["artifacts"])


def test_a_users_own_sheet_passes_through_but_must_name_its_classifiers(tmp_path, monkeypatch):
    monkeypatch.setattr(tdb, "curated_databases", lambda: [])
    state = _tax_state(tmp_path)
    out = _configure(tmp_path, state, "taxprofiler", {"databases": "/net/bmc-lab/dbs.csv"})
    assert out["ok"] is False and "run_" in json.dumps(out)
    out = _configure(tmp_path, state, "taxprofiler",
                     {"databases": "/net/bmc-lab/dbs.csv", "run_kraken2": True})
    assert out["ok"] is True, out
    assert out["resolved_params"]["databases"] == "/net/bmc-lab/dbs.csv"
    assert at._staged_extra_files(state["artifacts"]) == {}


def test_a_refused_sheet_leaves_no_launch_and_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tdb, "curated_databases", lambda: [])
    state = _tax_state(tmp_path)
    assert _configure(tmp_path, state, "taxprofiler", {"databases": "kraken2 /net/dbs/k2"})["ok"] is True
    out = _configure(tmp_path, state, "taxprofiler", {"databases": "kraken9 /net/dbs/k2"})
    assert out["ok"] is False and out["invalid_user_params"]
    assert "databases" not in state["artifacts"] and "launch" not in state["artifacts"]


def test_a_sample_with_several_runs_turns_run_merging_on(tmp_path, monkeypatch):
    monkeypatch.setattr(tdb, "curated_databases", lambda: [])
    state = _tax_state(tmp_path, rows=[("D.SEQ-1", "SRX9", "ILLUMINA", "", "")])
    out = _configure(tmp_path, state, "taxprofiler", {"databases": "kraken2 /net/dbs/k2"})
    assert out["ok"] is True, out
    assert out["resolved_params"]["perform_runmerging"] is True


def test_rebuilding_the_samplesheet_drops_the_written_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(tdb, "curated_databases", lambda: [])
    state = _tax_state(tmp_path)
    assert _configure(tmp_path, state, "taxprofiler", {"databases": "kraken2 /net/dbs/k2"})["ok"] is True
    state["resolved"] = {"uids": ["D.SEQ-1"], "accessions": []}
    state["accession_file_paths"] = {"D.SEQ-1": {"Link_PrimaryData": "/net/fq/a_R1.fastq.gz"}}
    out = json.loads(at.tool_write_samplesheet(_Cfg(), state, {
        "pipeline_key": "taxprofiler",
        "cohorts": [{"label": "c", "rows": [{"sample": "D.SEQ-1", "instrument_platform": "ILLUMINA"}]}]},
        str(tmp_path)))
    assert out["ok"] is True, out
    assert "databases" not in state["artifacts"]


def _fun_state(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fasta\nA.ASM-1,/net/asm/a.fna.gz\n")
    return {"artifacts": {"samplesheet": str(sheet), "base_dir": str(tmp_path)},
            "bundle_key": None, "data_driven_evidence": {}}


def test_funcscan_with_no_screen_is_refused(tmp_path):
    out = _configure(tmp_path, _fun_state(tmp_path), "funcscan", {})
    assert out["ok"] is False
    assert "run_arg_screening" in json.dumps(out)


def test_funcscan_runs_the_screens_the_question_names(tmp_path):
    out = _configure(tmp_path, _fun_state(tmp_path), "funcscan",
                     {"run_arg_screening": True, "run_bgc_screening": True})
    assert out["ok"] is True, out
    assert out["resolved_params"]["run_arg_screening"] is True
    assert "run_amp_screening" not in out["resolved_params"]
    assert out["schema_check"]["status"] == "ok", out["schema_check"]


# --- instrument_platform from the sequencing metadata ---------------------------------------

def _platform_spec():
    return load_param_atlas()["pipelines"]["taxprofiler"]["params"]["instrument_platform"]


def test_the_platform_rule_offers_exactly_the_schemas_platforms():
    enum = json.loads((FIXTURES / "taxprofiler.schema_input.json").read_text())[
        "items"]["properties"]["instrument_platform"]["enum"]
    spec = _platform_spec()
    assert spec["target"] == "row_column" and sorted(spec["allowed"]) == sorted(enum)


@pytest.mark.parametrize("signals,value,verdict", [
    ({"Sequencer": "Illumina NovaSeq 6000"}, "ILLUMINA", "derived_uncorroborated"),
    ({"Sequencer": "NextSeq 500", "Protocol": "Illumina DNA Prep"}, "ILLUMINA", "corroborated"),
    ({"Sequencer": "PromethION", "FlowCellType": "FLO-PRO114M"}, "OXFORD_NANOPORE", "corroborated"),
    ({"Sequencer": "Sequel IIe"}, "PACBIO_SMRT", "derived_uncorroborated"),
    ({"Sequencer": "BGISEQ-500"}, "BGISEQ", "derived_uncorroborated"),
    ({"Sequencer": "Illumina MiSeq", "FlowCellType": "FLO-MIN106"}, "ILLUMINA", "conflict"),
    ({"Sequencer": "Element AVITI"}, None, "absent"),
    ({}, None, "absent"),
])
def test_the_platform_is_derived_corroborated_or_asked(signals, value, verdict):
    got = evaluate_data_driven_param(_platform_spec(), signals)
    assert got["verdict"] == verdict, got
    if verdict != "conflict":
        assert got["value"] == value


def test_a_row_without_the_derived_platform_is_corrected(tmp_path):
    state = {"resolved": {"uids": ["D.SEQ-1"], "accessions": []},
             "accession_file_paths": {"D.SEQ-1": {"Link_PrimaryData": "/net/fq/a_R1.fastq.gz"}},
             "data_driven_evidence": {"D.SEQ-1": {"instrument_platform": {
                 "value": "ILLUMINA", "verdict": "corroborated", "evidence": "x"}}}}
    out = json.loads(at.tool_write_samplesheet(_Cfg(), state, {
        "pipeline_key": "taxprofiler", "cohorts": [{"label": "c", "rows": [{"sample": "D.SEQ-1"}]}]},
        str(tmp_path)))
    assert out["ok"] is False and "ILLUMINA" in out["errors"][0]


# --- the samplesheet ---------------------------------------------------------------------------

def test_the_taxprofiler_sheet_opens_with_its_six_columns_and_names_each_run(tmp_path):
    from chat_nextseek.seqera.emitter import emit_nfcore_artifacts
    rows = [{"sample": "D.SEQ-1", "instrument_platform": "ILLUMINA"},
            {"sample": "D.SEQ-2", "instrument_platform": "ILLUMINA", "accession": "SRR42"}]
    emit_nfcore_artifacts(tmp_path, pipeline="taxprofiler", samplesheet_rows=rows, resolutions=[],
                          launch_plan=None, tower_env=None,
                          accession_metadata={"D.SEQ-1": {"Link_PrimaryData": "/net/fq/a_R1.fastq.gz"}})
    with open(tmp_path / "samplesheet.csv", newline="") as fh:
        reader = csv.DictReader(fh)
        got = list(reader)
    assert reader.fieldnames[:6] == ["sample", "run_accession", "instrument_platform",
                                     "fastq_1", "fastq_2", "fasta"]
    # A run accession when the record has one, else the sample's own id (usage.md: "Can
    # also supply sample name again here, if only a single run was generated").
    assert [r["run_accession"] for r in got] == ["D.SEQ-1", "SRR42"]


def test_an_experiment_that_downloads_as_several_runs_names_each_run(tmp_path):
    from chat_nextseek.luria.fetchngs_helpers import fill_rows
    fq = tmp_path / "fastq"
    fq.mkdir()
    for run in ("SRR1", "SRR2"):
        for read in ("1", "2"):
            (fq / f"SRX9_{run}_{read}.fastq.gz").write_text("")
    rows, missing = fill_rows([{"sample": "D.SEQ-1", "run_accession": "SRX9", "accession": "SRX9",
                                "fastq_1": "", "fastq_2": ""}], str(tmp_path))
    assert missing == []
    assert [r["run_accession"] for r in rows] == ["SRR1", "SRR2"]
    # A sheet with no run_accession column (every other pipeline) is unchanged.
    rows, _ = fill_rows([{"sample": "S", "accession": "SRX9", "fastq_1": ""}], str(tmp_path))
    assert all("run_accession" not in r for r in rows)


def test_a_funcscan_row_takes_its_assemblys_fasta(tmp_path):
    from chat_nextseek.seqera.emitter import emit_nfcore_artifacts
    result = emit_nfcore_artifacts(
        tmp_path, pipeline="funcscan", samplesheet_rows=[{"sample": "A.ASM-1"}], resolutions=[],
        launch_plan=None, tower_env=None,
        accession_metadata={"A.ASM-1": {"Link_PrimaryData": "/net/asm/a.contigs.fna.gz"}})
    assert result.missing_input_files == []
    row = next(csv.DictReader(open(tmp_path / "samplesheet.csv", newline="")))
    assert row["fasta"] == "/net/asm/a.contigs.fna.gz" and "fastq_1" not in row

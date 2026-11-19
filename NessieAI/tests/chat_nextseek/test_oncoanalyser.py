"""nf-core/oncoanalyser 3.0.0: its catalog entry, long-format samplesheet, tumour/normal
confirmation, launch, template, atlases and reingest map.

The pinned upstream files are the five fixtures/nfcore/oncoanalyser.* copies (byte-identical
to nf-core/oncoanalyser at the 3.0.0 tag)."""
import csv
import fnmatch
import json
from pathlib import Path

import pytest

from NessieAI.ns.reingest import maps
from NessieAI.paths import CHAT_NEXTSEEK_DIR
from chat_nextseek.pipeline import agent_tools
from chat_nextseek.pipeline.agent_tools import tool_configure_run, tool_write_samplesheet
from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.emitter import emit_nfcore_artifacts
from chat_nextseek.seqera.pairing import OK, REFUSAL, TABLE, group_pairing_question
from chat_nextseek.seqera.param_atlas import evaluate_leaf
from chat_nextseek.seqera.pipeline_params import build_run_params
from chat_nextseek.seqera.schema_check import check_params

KEY = "oncoanalyser"
FIXTURES = Path(__file__).parent / "fixtures" / "nfcore"
TEMPLATE = json.loads((CHAT_NEXTSEEK_DIR / "src/chat_nextseek/reports/templates/nfcore"
                       / f"{KEY}.json").read_text())
SCHEMA = json.loads((FIXTURES / f"{KEY}.nextflow_schema.json").read_text())
INPUT = json.loads((FIXTURES / f"{KEY}.schema_input.json").read_text())["items"]
HEADER = ["group_id", "subject_id", "sample_id", "sample_type", "sequence_type", "filetype",
          "info", "filepath"]

T, N, R = "D.SEQ-260711ENG-1-PUB", "D.SEQ-260711ENG-2-PUB", "D.SEQ-260711ENG-3-PUB"
META = {
    T: {"Link_PrimaryData": "/net/bmc-pub10/x/T_S1_L002_R1_001.fastq.gz",
        "Link_SecondaryData": "/net/bmc-pub10/x/T_S1_L002_R2_001.fastq.gz"},
    N: {"Link_PrimaryData": "/net/bmc-pub10/x/N_R1.fastq.gz",
        "Link_SecondaryData": "/net/bmc-pub10/x/N_R2.fastq.gz"},
    R: {"Link_PrimaryData": "/net/bmc-pub10/x/R_R1.fastq.gz"},          # single-end
}


class _Cfg:
    pass


@pytest.fixture(autouse=True)
def _pinned_schemas(monkeypatch):
    """configure_run checks params against the pinned schema fixtures, never GitHub."""
    from chat_nextseek.seqera import schema_check

    monkeypatch.setattr(schema_check, "get_schema", lambda p, r: {
        "nextflow_schema": json.loads((FIXTURES / f"{p}.nextflow_schema.json").read_text())})


def _row(uid, sample_type, sequence_type="dna", group="PAT-260711ENG-9-PUB"):
    return {"sample": uid, "group_id": group, "sample_type": sample_type,
            "sequence_type": sequence_type}


def _emit(tmp_path, rows, meta=META):
    result = emit_nfcore_artifacts(tmp_path, pipeline=KEY, samplesheet_rows=rows, resolutions=[],
                                   launch_plan=None, tower_env=None, accession_metadata=meta)
    return result, (tmp_path / "samplesheet.csv").read_text().splitlines()


# --- catalog -------------------------------------------------------------------------------

def test_the_catalog_pins_3_0_0_and_names_the_schemas_required_columns():
    entry = NFCORE_PIPELINE_CATALOG[KEY]
    assert entry["default_revision"] == "3.0.0"
    assert entry["samplesheet_input_kind"] == "fastq"
    assert entry["accepted_leaf_sample_types"] == ["D.SEQ"]
    assert set(entry["required_columns"]) == set(INPUT["required"])
    assert set(TEMPLATE["pipeline"]["required_columns"]) == set(INPUT["required"])
    assert entry["reference_cli_flags"] == ["genome"]
    assert "genome" in json.dumps(SCHEMA) and '"fasta"' not in json.dumps(SCHEMA["$defs"]["reference_data_options"])


# --- the long-format sheet -----------------------------------------------------------------

def test_each_sample_becomes_one_long_format_row_with_its_fastq_pair(tmp_path):
    result, lines = _emit(tmp_path, [_row(T, "tumor"), _row(N, "normal")])
    assert lines[0].split(",") == HEADER
    rows = list(csv.DictReader(lines))
    tumour = next(r for r in rows if r["sample_id"] == T)
    assert tumour["filetype"] == "fastq"
    assert tumour["filepath"] == (META[T]["Link_PrimaryData"] + ";" + META[T]["Link_SecondaryData"])
    assert tumour["info"] == f"library_id:{T};lane:002"          # the L002 token of the file name
    assert tumour["subject_id"] == tumour["group_id"] == "PAT-260711ENG-9-PUB"
    normal = next(r for r in rows if r["sample_id"] == N)
    assert normal["info"] == f"library_id:{N};lane:001"          # no lane token: 001
    assert result.row_errors == [] and result.missing_input_files == []


def test_the_sheet_uses_only_columns_and_values_the_input_schema_allows(tmp_path):
    _, lines = _emit(tmp_path, [_row(T, "tumor"), _row(N, "normal", group="P2", )])
    for row in csv.DictReader(lines):
        assert set(row) <= set(INPUT["properties"])
        assert row["sample_type"] in INPUT["properties"]["sample_type"]["enum"]
        assert row["sequence_type"] in INPUT["properties"]["sequence_type"]["enum"]
        assert all(v and " " not in v for k, v in row.items()), row


def test_a_given_subject_id_is_kept(tmp_path):
    _, lines = _emit(tmp_path, [dict(_row(T, "tumor"), subject_id="SUBJ1")])
    assert next(csv.DictReader(lines))["subject_id"] == "SUBJ1"


def test_cohort_json_still_names_each_samples_uid_and_reads(tmp_path):
    _emit(tmp_path, [_row(T, "tumor"), _row(N, "normal")])
    cohort = json.loads((tmp_path / "cohort.json").read_text())
    assert [(c["d_seq_uid"], c["fastq_1"]) for c in cohort] == [
        (T, META[T]["Link_PrimaryData"]), (N, META[N]["Link_PrimaryData"])]


def test_a_single_end_sample_is_refused_not_written_half(tmp_path):
    result, _ = _emit(tmp_path, [_row(T, "tumor"), _row(R, "tumor", "rna")])
    assert len(result.row_errors) == 1 and R in result.row_errors[0]
    assert "paired-end" in result.row_errors[0]


def test_a_sample_with_no_local_fastq_is_a_missing_file(tmp_path):
    result, _ = _emit(tmp_path, [_row(T, "tumor"), _row("D.SEQ-260711ENG-7-PUB", "normal")])
    assert result.missing_input_files == ["D.SEQ-260711ENG-7-PUB"]


def test_write_samplesheet_returns_the_row_errors(tmp_path):
    state = {"resolved": {"uids": [T, R], "accessions": []}, "accession_file_paths": META}
    out = json.loads(tool_write_samplesheet(_Cfg(), state, {
        "pipeline_key": KEY, "cohorts": [{"label": "c", "rows": [_row(T, "tumor"),
                                                                 _row(R, "tumor", "rna")]}]},
        str(tmp_path)))
    assert out["ok"] is False and any("paired-end" in e for e in out["errors"])
    assert not (state.get("artifacts") or {}).get("samplesheet")


# --- the tumour/normal confirmation --------------------------------------------------------

def _sheet(*rows):
    return [dict(zip(HEADER, r)) for r in rows]


PAIR = _sheet(("P1", "P1", "T1", "tumor", "dna", "fastq", "x", "a;b"),
              ("P1", "P1", "N1", "normal", "dna", "fastq", "x", "c;d"))


def _ev(**verdicts):
    return {s: {"sample_type": {"verdict": v}} for s, v in verdicts.items()}


def test_a_pair_the_metadata_decided_goes_ahead():
    assert group_pairing_question(PAIR, _ev(T1="corroborated", N1="corroborated")) == (OK, "")


def test_an_undecided_sample_asks_for_the_table_and_a_confirmation_ends_it():
    kind, text = group_pairing_question(PAIR, _ev(T1="corroborated", N1="absent"))
    assert kind == TABLE and "| P1 | N1 | dna | normal (not from metadata) |" in text
    assert group_pairing_question(PAIR, _ev(T1="corroborated", N1="absent"),
                                  confirmed=True) == (OK, "")


def test_tumour_only_and_tumour_dna_with_rna_are_valid_groups():
    rows = _sheet(("P1", "P1", "T1", "tumor", "dna", "fastq", "x", "a;b"),
                  ("P1", "P1", "T1R", "tumor", "rna", "fastq", "x", "c;d"))
    assert group_pairing_question(rows, _ev(T1="corroborated", T1R="corroborated"))[0] == OK


@pytest.mark.parametrize("rows, words", [
    (_sheet(("P1", "P1", "N1", "normal", "dna", "fastq", "x", "a;b")), "no tumour"),
    (PAIR + _sheet(("P1", "P1", "T2", "tumor", "dna", "fastq", "x", "e;f")), "more than one"),
    (_sheet(("P1", "P1", "T1", "tumor", "dna", "fastq", "x", "a;b"),
            ("P1", "P1", "N1R", "normal", "rna", "fastq", "x", "c;d")), "normal RNA"),
    (_sheet(("P1", "P1", "T1", "tumour", "dna", "fastq", "x", "a;b")), "tumor or normal"),
    (_sheet(("P1", "P1", "T1", "tumor", "dna", "fastq", "x", "a;b"),
            ("P1", "P2", "N1", "normal", "dna", "fastq", "x", "c;d")), "subject_id"),
    (_sheet(("P1", "P1", "T1", "tumor", "rna", "fastq", "x", "a;b"),
            ("P1", "P1", "N1", "normal", "dna", "fastq", "x", "c;d")), "tumour DNA"),
])
def test_groups_oncoanalyser_would_abort_on_are_refused(rows, words):
    kind, text = group_pairing_question(rows, {})
    assert kind == REFUSAL and words in text, text


def _configure_state(tmp_path, evidence, bundle="GRCh38", n_msgs=1):
    sheet = tmp_path / "samplesheet.csv"
    with sheet.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
        w.writeheader()
        w.writerows(PAIR)
    return {"artifacts": {"samplesheet": str(sheet)}, "bundle_key": bundle,
            "messages": [{"role": "user", "content": f"m{i}"} for i in range(n_msgs)],
            "data_driven_evidence": evidence}


def _configure(state, tmp_path, params=None, **kw):
    return json.loads(tool_configure_run(_Cfg(), state, {"pipeline_key": KEY,
                                                         "params": params or {}, **kw},
                                         str(tmp_path)))


DECIDED = {"T1": {"sample_type": {"verdict": "corroborated", "value": "tumor"}},
           "N1": {"sample_type": {"verdict": "corroborated", "value": "normal"}}}


def test_configure_run_asks_before_an_undecided_pair_and_takes_a_reply(tmp_path):
    state = _configure_state(tmp_path, {**DECIDED, "N1": {"sample_type": {"verdict": "absent"}}})
    out = _configure(state, tmp_path)
    assert out["ok"] is False and out["needs_user_input"] == ["sample_type"]
    assert "| P1 | N1 | dna | normal (not from metadata) |" in out["ask_the_user"]
    assert _configure(state, tmp_path, pairing_confirmed=True)["needs_user_input"] == ["sample_type"]
    state["messages"].append({"role": "user", "content": "yes"})
    out = _configure(state, tmp_path, pairing_confirmed=True)
    assert out["ok"] is True, out


def test_configure_run_builds_a_wgts_run_on_the_hartwig_genome(tmp_path):
    state = _configure_state(tmp_path, DECIDED)
    out = _configure(state, tmp_path)
    assert out["ok"] is True, out
    assert out["schema_check"] == {"status": "ok"}
    params = out["resolved_params"]
    assert params["mode"] == "wgts"
    assert agent_tools.luria_run_genome(KEY, params["genome"]) == "GRCh38_hmf"


def test_configure_run_refuses_a_non_human_cohort(tmp_path):
    out = _configure(_configure_state(tmp_path, DECIDED, bundle="GRCm39"), tmp_path)
    assert out["ok"] is False and "human" in out["error"].lower()


def test_configure_run_refuses_targeted_mode(tmp_path):
    out = _configure(_configure_state(tmp_path, DECIDED), tmp_path, {"mode": "targeted"})
    assert out["ok"] is False and "panel" in out["error"]


def test_configure_runs_pairing_flag_names_oncoanalyser():
    desc = agent_tools._SCHEMA_BY_NAME["configure_run"]["input_schema"]["properties"][
        "pairing_confirmed"]["description"]
    assert "oncoanalyser" in desc


# --- launch on Luria -------------------------------------------------------------------------

def test_run_sh_passes_the_hartwig_genome_and_no_fasta():
    from chat_nextseek.luria.run_script import luria_run_genome, render_run_script
    assert luria_run_genome("nf-core/oncoanalyser", "GRCh38") == "GRCh38_hmf"
    sh = render_run_script(job_name="j", pipeline="nf-core/oncoanalyser", revision="3.0.0",
                           run_dir="/w/runs/r", work_dir="/w/work/r",
                           singularity_cache="/w/singularity_cache",
                           genome=luria_run_genome("nf-core/oncoanalyser", "GRCh38"),
                           resources=None, refs_root="/w/refs", reference_cli_flags=["genome"])
    assert "--genome GRCh38_hmf" in sh and "--fasta" not in sh and "--igenomes_ignore" not in sh
    assert "NXF_VER" not in sh


# --- template ------------------------------------------------------------------------------

def _schema(_p, _r):
    return {"nextflow_schema": SCHEMA}


def test_curated_defaults_pass_the_pinned_schema():
    merged, errors, _ = build_run_params(KEY, {}, "GRCh38")
    assert errors == []
    merged.pop("genome")                          # the launcher passes it, aliased
    errs, skipped = check_params(KEY, "3.0.0", merged, schema_getter=_schema)
    assert skipped is None and errs == []


def test_every_menu_param_is_one_the_pinned_schema_declares():
    errs, skipped = check_params(KEY, "3.0.0", {n: None for n in TEMPLATE["params"]},
                                 required=False, schema_getter=_schema)
    assert skipped is None and errs == []


def test_every_optional_param_says_what_calls_for_it():
    for name, spec in TEMPLATE["params"].items():
        help_text = spec.get("help", "")
        assert len(help_text) > 40, name
        assert "unless asked" not in help_text and "old behaviour" not in help_text, name


def test_template_rows_use_only_input_schema_columns():
    for row in TEMPLATE["samplesheet_template"]:
        assert set(row) <= set(INPUT["properties"]), set(row) - set(INPUT["properties"])


def test_the_template_says_what_is_not_staged_on_luria():
    blob = json.dumps(TEMPLATE).lower()
    assert "not staged" in blob and "download" in blob and TEMPLATE["runtime"]["setup_minutes"][1] > 0


# --- param atlas -------------------------------------------------------------------------------

def test_tumour_status_and_sequence_type_come_from_the_metadata():
    out = evaluate_leaf(KEY, {"Type": "Tumor tissue", "Name": "PT1 tumour",
                              "LibrarySource": "GENOMIC", "LibraryStrategy": "WGS",
                              "SequencingType": "WGS"})
    assert out["sample_type"] == {"value": "tumor", "verdict": "corroborated",
                                  "evidence": "Type=tumor; Name=tumor"}
    assert out["sequence_type"]["value"] == "dna" and out["sequence_type"]["verdict"] == "corroborated"
    assert out["mode"]["value"] == "wgts"
    rna = evaluate_leaf(KEY, {"Type": "adjacent normal", "LibrarySource": "TRANSCRIPTOMIC",
                              "LibraryStrategy": "RNA-Seq"})
    assert rna["sample_type"]["verdict"] == "conflict"
    assert rna["sequence_type"]["value"] == "rna" and rna["mode"]["value"] == "wgts"


def test_an_exome_library_has_no_mode_and_is_asked():
    out = evaluate_leaf(KEY, {"LibraryStrategy": "WXS", "SequencingType": "WES"})
    assert out["mode"]["verdict"] == "absent"


def test_the_sample_type_rule_is_confirmed_in_the_pairing_table():
    from chat_nextseek.seqera.param_atlas import data_driven_params
    spec = data_driven_params(KEY)["sample_type"]
    assert spec["target"] == "row_column" and spec["confirm_in"] == "pairing_table"


# --- atlas ---------------------------------------------------------------------------------

def test_the_dna_atlas_entry_is_human_only_and_points_at_sarek_both_ways():
    from chat_nextseek.seqera.nfcore_atlas import DNA_ATLAS_PATH, load_atlas
    dna = load_atlas(DNA_ATLAS_PATH)["pipelines"]
    entry = dna[KEY]
    assert entry["revision"] == "3.0.0" and "HUMAN ONLY" in entry["species_constraint"]
    assert entry["versus"]["sarek"]["ask_user"] and dna["sarek"]["versus"][KEY]["ask_user"]


# --- reingest map --------------------------------------------------------------------------

def test_the_map_covers_the_pin_and_takes_reads_or_alignments_as_parents():
    m = maps.load(KEY)
    assert m.covers_revision("3.0.0") and not m.covers_revision("2.3.0")
    assert m.accepts_parent_types == ["D.SEQ", "A.ALN"]
    assert m.samplesheet_sample_column == "sample_id"
    assert not m.qc_attributes                    # no MultiQC key has been seen in a run


@pytest.mark.parametrize("path, rule", [
    ("P1/alignments/T1/T1.redux.bam", "aln"),
    ("P1/alignments/T1R/T1R.md.bam", "aln"),
    ("P1/purple/T1.purple.somatic.vcf.gz", "vcf_somatic"),
    ("P1/purple/T1.purple.sv.vcf.gz", "vcf_sv"),
    ("P1/purple/T1.purple.cnv.somatic.tsv", "cnv"),
    ("P1/sigs/T1.sig.allocation.tsv", "signatures"),
    ("P1/isofox/T1R.isf.gene_data.tsv", "gex"),
])
def test_each_rule_matches_the_file_the_docs_name(path, rule):
    m = maps.load(KEY)
    winner = next(r.id for r in m.outputs
                  if any(fnmatch.fnmatch(path, p) for c in r.candidates
                         for p in maps.expand_braces(c)))
    assert winner == rule


def test_germline_calls_named_after_the_tumour_are_not_filed_under_it():
    m = maps.load(KEY)
    for path in ("P1/purple/T1.purple.germline.vcf.gz", "P1/purple/T1.purple.sv.germline.vcf.gz",
                 "P1/sage/germline/T1.sage.germline.vcf.gz"):
        assert not any(fnmatch.fnmatch(path, p) for r in m.outputs for c in r.candidates
                       for p in maps.expand_braces(c)), path

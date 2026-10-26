"""The contrasts CSV Nessie writes for rnasplice from the user's stated comparison.

The contract: the user says which group is the treatment and which the control,
Nessie writes the file and stages it with the run. The direction is never defaulted
(a swapped contrast flips the sign of every result), a group that is not a
samplesheet condition is refused before the run, and an absolute path is still the
user's own file and passes through untouched.
"""
import json

import yaml

import chat_nextseek.luria.submitter as sub
from chat_nextseek.pipeline import agent_tools as at
from chat_nextseek.seqera.contrasts import (
    condition_problems,
    is_user_path,
    parse_contrasts,
    samplesheet_conditions,
    write_contrasts_csv,
)


# --- parsing the user's answer ---------------------------------------------------

def test_treatment_vs_control_becomes_one_row():
    rows, errors = parse_contrasts("EC vs FB")
    assert errors == []
    assert rows == [{"contrast": "EC_vs_FB", "treatment": "EC", "control": "FB"}]


def test_several_comparisons_and_spellings():
    rows, errors = parse_contrasts("EC vs. FB; KO versus WT\nA VS B")
    assert errors == []
    assert [(r["treatment"], r["control"]) for r in rows] == [("EC", "FB"), ("KO", "WT"), ("A", "B")]


def test_a_list_of_comparisons():
    rows, errors = parse_contrasts(["EC vs FB", "FB vs EC"])
    assert errors == []
    assert [r["contrast"] for r in rows] == ["EC_vs_FB", "FB_vs_EC"]


def test_the_pasted_csv_keeps_its_own_contrast_ids():
    rows, errors = parse_contrasts("contrast,treatment,control\nHUVEC_FIB,EC,FB\n")
    assert errors == []
    assert rows == [{"contrast": "HUVEC_FIB", "treatment": "EC", "control": "FB"}]


def test_problems_return_no_rows():
    for answer in ("EC and FB", "EC vs EC", "", "EC vs FB\nEC vs FB", "contrast,treatment\nX,EC"):
        rows, errors = parse_contrasts(answer)
        assert rows == [] and errors, answer


def test_only_an_absolute_path_is_the_users_own_file():
    assert is_user_path("/net/bmc-lab/contrasts.csv")
    assert not is_user_path("EC vs FB")
    assert not is_user_path("contrasts.csv")


def test_groups_must_be_samplesheet_conditions(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,condition\nA,EC\nB,EC\nC,FB\n")
    assert samplesheet_conditions(sheet) == ["EC", "FB"]
    rows, _ = parse_contrasts("HUVEC vs FB")
    problems = condition_problems(rows, ["EC", "FB"])
    assert len(problems) == 1 and "HUVEC" in problems[0] and "EC, FB" in problems[0]


def test_written_file_has_the_rnasplice_header(tmp_path):
    rows, _ = parse_contrasts("EC vs FB")
    path = write_contrasts_csv(rows, tmp_path)
    assert path.name == "contrasts.csv"
    assert path.read_text() == "contrast,treatment,control\nEC_vs_FB,EC,FB\n"


# --- configure_run -----------------------------------------------------------------

class _Cfg:
    LOG_DIR = "/tmp"
    TOWER_ENV = {}


def _state(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1,fastq_2,strandedness,condition\n"
                     "S1,,,reverse,EC\nS2,,,reverse,EC\nS3,,,reverse,FB\nS4,,,reverse,FB\n")
    return {"artifacts": {"samplesheet": str(sheet), "base_dir": str(tmp_path)},
            "bundle_key": None, "data_driven_evidence": {}}


def _configure(tmp_path, state, params):
    return json.loads(at.tool_configure_run(
        _Cfg(), state, {"pipeline_key": "rnasplice", "params": params}, str(tmp_path)))


def test_the_question_asks_for_the_comparison_and_names_the_conditions(tmp_path):
    out = _configure(tmp_path, _state(tmp_path), {})
    assert out["ok"] is False and out["needs_user_input"] == ["contrasts"]
    assert "vs" in out["ask_the_user"] and "EC, FB" in out["ask_the_user"]


def test_a_stated_comparison_is_written_into_the_run(tmp_path):
    state = _state(tmp_path)
    out = _configure(tmp_path, state, {"contrasts": "EC vs FB"})
    assert out["ok"] is True, out
    assert out["resolved_params"]["contrasts"] == "contrasts.csv"
    assert out["contrasts_written"] == [{"contrast": "EC_vs_FB", "treatment": "EC", "control": "FB"}]
    assert (tmp_path / "contrasts.csv").read_text() == "contrast,treatment,control\nEC_vs_FB,EC,FB\n"
    assert state["artifacts"]["contrasts"] == str(tmp_path / "contrasts.csv")


def test_a_group_the_samplesheet_lacks_is_refused(tmp_path):
    state = _state(tmp_path)
    out = _configure(tmp_path, state, {"contrasts": "HUVEC vs FB"})
    assert out["ok"] is False and out["invalid_user_params"]
    assert not (tmp_path / "contrasts.csv").exists()
    assert "contrasts" not in state["artifacts"]


def test_a_users_own_path_passes_through(tmp_path):
    state = _state(tmp_path)
    out = _configure(tmp_path, state, {"contrasts": "/net/bmc-lab/mine.csv"})
    assert out["ok"] is True, out
    assert out["resolved_params"]["contrasts"] == "/net/bmc-lab/mine.csv"
    assert "contrasts_written" not in out
    assert "contrasts" not in state["artifacts"]


def test_switching_to_a_path_drops_the_written_file_from_the_run(tmp_path):
    state = _state(tmp_path)
    assert _configure(tmp_path, state, {"contrasts": "EC vs FB"})["ok"] is True
    assert _configure(tmp_path, state, {"contrasts": "/net/bmc-lab/mine.csv"})["ok"] is True
    assert at._staged_extra_files(state["artifacts"]) == {}


def test_rebuilding_the_samplesheet_drops_the_written_file(tmp_path):
    state = _state(tmp_path)
    assert _configure(tmp_path, state, {"contrasts": "EC vs FB"})["ok"] is True
    state["resolved"] = {"uids": ["S1"], "accessions": []}
    state["accession_file_paths"] = {}
    out = json.loads(at.tool_write_samplesheet(_Cfg(), state, {
        "pipeline_key": "rnasplice",
        "cohorts": [{"label": "c", "rows": [{"sample": "S1", "strandedness": "reverse",
                                              "condition": "EC"}]}]}, str(tmp_path)))
    assert out["ok"] is True, out
    assert "contrasts" not in state["artifacts"]


# --- submit stages it beside the samplesheet ------------------------------------------

def test_submit_stages_the_written_contrasts_file(tmp_path, monkeypatch):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1\nA,a.fq.gz\n")
    contrasts = tmp_path / "contrasts.csv"
    contrasts.write_text("contrast,treatment,control\nEC_vs_FB,EC,FB\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "nfcore_rnasplice", "pipeline": "nf-core/rnasplice", "revision": "1.0.4"}]}))
    staged = []
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path: "Submitted batch job 9\n")
    monkeypatch.setattr(sub, "scp_file",
                        lambda le, local, remote, *, key_path: staged.append((str(local), remote)))
    runs = sub.submit_luria(
        str(tmp_path / "launch.yml"),
        luria_env={"user": "u", "host": "luria.mit.edu", "key": "/k", "working_path": "/net/x"},
        samplesheet_local=str(sheet), genome="GRCh38",
        launch_params={"contrasts": "contrasts.csv"},
        extra_files={"contrasts.csv": str(contrasts)})
    assert runs and runs[0]["job_id"] == "9"
    run_dir = runs[0]["remote_dir"]
    assert (str(contrasts), f"{run_dir}/contrasts.csv") in staged


def test_a_pasted_csv_with_an_extra_column_does_not_crash():
    # csv.DictReader files the cells past the header under the key None, as a list (PR 135 SF-14).
    rows, errors = parse_contrasts("contrast,treatment,control\nEC_vs_FB,EC,FB\n,,,extra\nX_vs_Y,X,Y,more")
    assert errors == []
    assert [r["contrast"] for r in rows] == ["EC_vs_FB", "X_vs_Y"]

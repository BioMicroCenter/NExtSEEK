"""A data-driven param the metadata cannot settle is asked once and accepted after the user answers.

Bug guarded: check_run_params / check_row_column_params read only the cached per-leaf verdicts,
which resolve_samples computes once, so an absent or conflict verdict asked the user forever:
the agent relayed the question, the user answered ("Seq-Well" -> dropseq), the agent re-sent
the value, and the same ask came back every time. The gate stays fail-closed: the agent's
value is taken only for a param the tool asked about, after a user message that came later,
and only when it is one of the spec's allowed values.
"""
import json
from pathlib import Path

import chat_nextseek.pipeline.agent_tools as at
from chat_nextseek.seqera.param_atlas import (
    check_row_column_params,
    check_run_params,
    confirm_user_answer,
    data_driven_params,
    render_param_elicitation,
)


class _Cfg:
    LOG_DIR = "/tmp"
    TOWER_ENV = {}


def _absent(name="protocol"):
    return {name: {"value": None, "verdict": "absent", "evidence": "no signal"}}


def _scrnaseq_state(tmp_path, evidence):
    samplesheet = tmp_path / "samplesheet.csv"
    samplesheet.write_text("sample,fastq_1,fastq_2\n")
    return {"artifacts": {"samplesheet": str(samplesheet), "base_dir": str(tmp_path)},
            "bundle_key": None,
            "data_driven_evidence": evidence,
            "messages": [{"role": "user", "content": "run scrnaseq on D.SEQ-1 and D.SEQ-7"}]}


def _user_says(state, text):
    state["messages"].append({"role": "assistant", "content": "Which protocol?"})
    state["messages"].append({"role": "user", "content": text})


def _configure(state, tmp_path, params=None):
    tool_input = {"pipeline_key": "scrnaseq"}
    if params is not None:
        tool_input["params"] = params
    return json.loads(at.tool_configure_run(_Cfg(), state, tool_input, str(tmp_path)))


def _two_absent_leaves():
    return {"D.SEQ-1": _absent(), "D.SEQ-7": _absent()}


# --- configure_run (run_param: scrnaseq protocol) ----------------------------------


def test_absent_protocol_asks_and_records_the_ask(tmp_path):
    state = _scrnaseq_state(tmp_path, _two_absent_leaves())
    out = _configure(state, tmp_path)
    assert out["ok"] is False
    assert out["needs_user_input"] == ["protocol"]
    assert state["asked_params"]["protocol"] == 1


def test_the_users_answer_is_accepted_after_the_ask(tmp_path):
    state = _scrnaseq_state(tmp_path, _two_absent_leaves())
    _configure(state, tmp_path)
    _user_says(state, "Seq-Well")
    out = _configure(state, tmp_path, {"protocol": "dropseq"})
    assert out["ok"] is True, out
    assert out["resolved_params"]["protocol"] == "dropseq"
    assert out["user_confirmed"] == {"protocol": "dropseq"}
    for uid in ("D.SEQ-1", "D.SEQ-7"):
        verdict = state["data_driven_evidence"][uid]["protocol"]
        assert verdict["verdict"] == "user_confirmed"
        assert verdict["value"] == "dropseq"
        assert "absent" in verdict["evidence"]
    assert "protocol" not in state.get("asked_params", {})


def test_a_later_call_keeps_the_confirmed_value(tmp_path):
    state = _scrnaseq_state(tmp_path, _two_absent_leaves())
    _configure(state, tmp_path)
    _user_says(state, "Seq-Well")
    _configure(state, tmp_path, {"protocol": "dropseq"})
    _user_says(state, "use STAR instead")
    out = _configure(state, tmp_path, {"aligner": "star"})
    assert out["ok"] is True, out
    assert out["resolved_params"]["protocol"] == "dropseq"


def test_an_agent_value_with_no_ask_is_refused(tmp_path):
    state = _scrnaseq_state(tmp_path, _two_absent_leaves())
    out = _configure(state, tmp_path, {"protocol": "dropseq"})
    assert out["ok"] is False
    assert out["needs_user_input"] == ["protocol"]
    assert state["data_driven_evidence"]["D.SEQ-1"]["protocol"]["verdict"] == "absent"


def test_an_agent_value_with_no_new_user_message_is_refused(tmp_path):
    state = _scrnaseq_state(tmp_path, _two_absent_leaves())
    _configure(state, tmp_path)
    out = _configure(state, tmp_path, {"protocol": "dropseq"})
    assert out["ok"] is False
    assert out["needs_user_input"] == ["protocol"]
    assert state["data_driven_evidence"]["D.SEQ-1"]["protocol"]["verdict"] == "absent"


def test_a_tool_result_is_not_a_user_message(tmp_path):
    state = _scrnaseq_state(tmp_path, _two_absent_leaves())
    _configure(state, tmp_path)
    state["messages"].append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "{}"}]})
    out = _configure(state, tmp_path, {"protocol": "dropseq"})
    assert out["ok"] is False


def test_an_answer_outside_the_allowed_values_is_refused_and_says_why(tmp_path):
    state = _scrnaseq_state(tmp_path, _two_absent_leaves())
    _configure(state, tmp_path)
    _user_says(state, "Seq-Well")
    out = _configure(state, tmp_path, {"protocol": "Seq-Well"})
    assert out["ok"] is False
    assert out["needs_user_input"] == ["protocol"]
    assert "dropseq" in out["rejected_answers"]["protocol"]
    assert state["data_driven_evidence"]["D.SEQ-1"]["protocol"]["verdict"] == "absent"


def test_a_value_the_tool_filled_itself_is_not_an_answer(tmp_path):
    # One leaf decisive (dropseq), one absent: configure_run fills dropseq into the
    # params itself, then asks about the absent leaf. That self-filled value must not
    # count as the user's answer when the agent sends nothing.
    state = _scrnaseq_state(tmp_path, {
        "D.SEQ-1": {"protocol": {"value": "dropseq", "verdict": "derived_uncorroborated", "evidence": "e"}},
        "D.SEQ-7": _absent()})
    _configure(state, tmp_path)
    _user_says(state, "hmm, not sure")
    out = _configure(state, tmp_path, {})
    assert out["ok"] is False
    assert state["data_driven_evidence"]["D.SEQ-7"]["protocol"]["verdict"] == "absent"


def test_a_decisive_verdict_still_wins_over_a_disagreeing_value(tmp_path):
    state = _scrnaseq_state(tmp_path, {
        "D.SEQ-1": {"protocol": {"value": "10XV3", "verdict": "corroborated", "evidence": "e"}}})
    state["asked_params"] = {"protocol": 0}
    out = _configure(state, tmp_path, {"protocol": "dropseq"})
    assert out["ok"] is False
    assert any("10XV3" in e for e in out["errors"])
    assert state["data_driven_evidence"]["D.SEQ-1"]["protocol"]["verdict"] == "corroborated"


def test_changing_a_confirmed_value_asks_the_user_again(tmp_path):
    state = _scrnaseq_state(tmp_path, _two_absent_leaves())
    _configure(state, tmp_path)
    _user_says(state, "Seq-Well")
    _configure(state, tmp_path, {"protocol": "dropseq"})
    # The agent changes its mind on its own: not a correction loop, a fresh ask.
    out = _configure(state, tmp_path, {"protocol": "10XV3"})
    assert out["ok"] is False
    assert out["needs_user_input"] == ["protocol"]
    assert "dropseq" in out["ask_the_user"]
    _user_says(state, "sorry, it was 10x v3")
    out = _configure(state, tmp_path, {"protocol": "10XV3"})
    assert out["ok"] is True, out
    assert out["resolved_params"]["protocol"] == "10XV3"


def test_a_heterogeneous_cohort_is_settled_by_the_users_answer(tmp_path):
    state = _scrnaseq_state(tmp_path, {
        "D.SEQ-1": {"protocol": {"value": "10XV2", "verdict": "corroborated", "evidence": "e"}},
        "D.SEQ-7": {"protocol": {"value": "10XV3", "verdict": "corroborated", "evidence": "e"}}})
    out = _configure(state, tmp_path)
    assert out["needs_user_input"] == ["protocol"]
    _user_says(state, "they are all v3")
    out = _configure(state, tmp_path, {"protocol": "10XV3"})
    assert out["ok"] is True, out
    assert out["resolved_params"]["protocol"] == "10XV3"


# --- write_samplesheet (row_column: hlatyping seq_type) ----------------------------


def _hla_state():
    return {"resolved": {"uids": ["D.SEQ-1"], "accessions": []},
            "data_driven_evidence": {"D.SEQ-1": _absent("seq_type")},
            "accession_file_paths": {},
            "messages": [{"role": "user", "content": "type the HLA alleles of D.SEQ-1"}]}


def _write(state, tmp_path, seq_type):
    return json.loads(at.tool_write_samplesheet(_Cfg(), state,
        {"pipeline_key": "hlatyping",
         "cohorts": [{"label": "c", "rows": [{"sample": "D.SEQ-1", "seq_type": seq_type}]}]},
        log_dir=str(tmp_path)))


def test_samplesheet_asks_then_accepts_the_users_answer(tmp_path):
    state = _hla_state()
    out = _write(state, tmp_path, "rna")
    assert out["ok"] is False and out["needs_user_input"] == ["seq_type"]
    assert state["asked_params"]["seq_type"] == 1
    _user_says(state, "it is RNA-seq")
    out = _write(state, tmp_path, "rna")
    assert out["ok"] is True, out
    assert out["user_confirmed"] == {"seq_type": {"D.SEQ-1": "rna"}}
    verdict = state["data_driven_evidence"]["D.SEQ-1"]["seq_type"]
    assert verdict["verdict"] == "user_confirmed" and verdict["value"] == "rna"
    notes = Path(state["artifacts"]["base_dir"], "notes.md").read_text()
    assert "seq_type" in notes and "confirmed by the user" in notes


def test_samplesheet_refuses_a_row_value_with_no_new_user_message(tmp_path):
    state = _hla_state()
    _write(state, tmp_path, "rna")
    out = _write(state, tmp_path, "rna")
    assert out["ok"] is False and out["needs_user_input"] == ["seq_type"]
    assert state["data_driven_evidence"]["D.SEQ-1"]["seq_type"]["verdict"] == "absent"


def test_samplesheet_refuses_a_row_value_with_no_ask(tmp_path):
    state = _hla_state()
    _user_says(state, "go")
    out = _write(state, tmp_path, "rna")
    assert out["ok"] is False and out["needs_user_input"] == ["seq_type"]


def test_samplesheet_refuses_an_answer_outside_the_allowed_values(tmp_path):
    state = _hla_state()
    _write(state, tmp_path, "rna")
    _user_says(state, "RNA-seq")
    out = _write(state, tmp_path, "RNA-seq")
    assert out["ok"] is False
    assert "rna" in out["rejected_answers"]["seq_type"]


def test_configure_run_notes_name_the_confirmed_protocol(tmp_path):
    state = _scrnaseq_state(tmp_path, _two_absent_leaves())
    (tmp_path / "notes.md").write_text("# nf-core/scrnaseq run notes\n")
    _configure(state, tmp_path)
    _user_says(state, "Seq-Well")
    _configure(state, tmp_path, {"protocol": "dropseq"})
    _configure(state, tmp_path, {"protocol": "dropseq"})      # a re-run does not repeat the section
    notes = (tmp_path / "notes.md").read_text()
    assert notes.startswith("# nf-core/scrnaseq run notes")
    assert notes.count("## Values the user confirmed") == 1
    assert "protocol = 'dropseq'" in notes and "D.SEQ-1" in notes and "D.SEQ-7" in notes


# --- param_atlas helpers -----------------------------------------------------------


def test_check_run_params_reports_asks_per_param():
    evidence = {"D.SEQ-1": _absent(), "D.SEQ-7": {"protocol": {
        "value": "dropseq", "verdict": "derived_uncorroborated", "evidence": "e"}}}
    out = check_run_params("scrnaseq", {}, evidence)
    assert out["ask_by_param"] == {"protocol": ["D.SEQ-1"]}


def test_a_user_confirmed_verdict_is_decisive():
    evidence = {"D.SEQ-1": {"protocol": {"value": "dropseq", "verdict": "user_confirmed", "evidence": "e"}}}
    assert check_run_params("scrnaseq", {"protocol": "dropseq"}, evidence)["ask_uids"] == []
    rows = [{"sample": "D.SEQ-1", "seq_type": "rna"}]
    hla = {"D.SEQ-1": {"seq_type": {"value": "rna", "verdict": "user_confirmed", "evidence": "e"}}}
    out = check_row_column_params("hlatyping", rows, hla)
    assert out["ask_uids"] == [] and out["corrections"] == []


def test_a_row_disagreeing_with_a_user_confirmed_verdict_is_asked_not_corrected():
    rows = [{"sample": "D.SEQ-1", "seq_type": "dna"}]
    hla = {"D.SEQ-1": {"seq_type": {"value": "rna", "verdict": "user_confirmed", "evidence": "e"}}}
    out = check_row_column_params("hlatyping", rows, hla)
    assert out["ask_by_param"] == {"seq_type": ["D.SEQ-1"]} and out["corrections"] == []
    text = render_param_elicitation(out["ask_specs"], out["ask_uids"], hla)
    assert "confirmed" in text and "'rna'" in text


def test_confirm_user_answer_checks_the_allowed_values():
    spec = data_driven_params("scrnaseq")["protocol"]
    evidence = {"D.SEQ-1": _absent()}
    assert "dropseq" in confirm_user_answer(evidence, "protocol", ["D.SEQ-1"], "Seq-Well", spec)
    assert evidence["D.SEQ-1"]["protocol"]["verdict"] == "absent"
    assert confirm_user_answer(evidence, "protocol", ["D.SEQ-1"], "dropseq", spec) is None
    assert evidence["D.SEQ-1"]["protocol"] == {
        "value": "dropseq", "verdict": "user_confirmed",
        "evidence": "confirmed by the user (the metadata verdict was absent: no signal)"}

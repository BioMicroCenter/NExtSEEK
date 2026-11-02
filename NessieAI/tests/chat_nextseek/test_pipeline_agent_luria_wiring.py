import json

import pytest

import chat_nextseek.pipeline.agent as agent
from chat_nextseek.pipeline import agent_tools


def test_run_loop_uses_build_pipeline_tool_schemas(monkeypatch):
    captured = {}

    # Spy: record that the builder is called with the config, return a marker tool list.
    monkeypatch.setattr(agent, "build_pipeline_tool_schemas",
                        lambda config: (captured.setdefault("built_with", config), [{"name": "conclude"}])[1])
    monkeypatch.setattr(agent, "catalog_for_prompt", lambda: "CATALOG")
    monkeypatch.setattr(agent, "summarize_pinned_bundle", lambda session: "")

    class FakeClient:
        def chat_with_tools(self, *, messages, tools, system, model, **kwargs):
            captured["tools"] = tools
            # Return a 'conclude' tool_use so the loop terminates immediately.
            return {"content": [{"type": "tool_use", "name": "conclude", "id": "t1",
                                 "input": {"outcome": "answered", "message": "done"}}]}

    class FakeConfig:
        LURIA_ENV_COMPLETE = True
        TOWER_ENV_COMPLETE = False

        def get_agent_model(self, key):
            return FakeClient(), "fake-model", None

        def _load_prompt(self, name):
            return "SYS {catalog}"

    session = {}
    out = agent.start(session, FakeConfig(), user_query="run rnaseq on luria",
                      parser_plan=None, reporter_plan=None, log_dir="/tmp")
    assert out["action"] == "ask"  # conclude(outcome='answered') -> ask
    assert captured["built_with"].__class__.__name__ == "FakeConfig"
    assert captured["tools"] == [{"name": "conclude"}]


def test_run_loop_substitutes_launch_mode_into_prompt(monkeypatch):
    captured = {}
    monkeypatch.setattr(agent, "build_pipeline_tool_schemas", lambda config: [{"name": "conclude"}])
    monkeypatch.setattr(agent, "catalog_for_prompt", lambda: "CAT")
    monkeypatch.setattr(agent, "summarize_pinned_bundle", lambda session: "")

    class FakeClient:
        def chat_with_tools(self, *, messages, tools, system, model, **kwargs):
            captured["system"] = system
            return {"content": [{"type": "tool_use", "name": "conclude", "id": "t",
                                 "input": {"outcome": "answered", "message": "d"}}]}

    class FakeConfig:
        PIPELINE_LAUNCH_MODE = "luria"
        LURIA_ENV_COMPLETE = True
        TOWER_ENV_COMPLETE = False
        def get_agent_model(self, key):
            return FakeClient(), "m", None
        def _load_prompt(self, name):
            return "catalog={catalog} mode={launch_mode}"

    agent.start({}, FakeConfig(), user_query="q", parser_plan=None, reporter_plan=None, log_dir="/tmp")
    assert "mode=luria" in captured["system"]
    assert "{launch_mode}" not in captured["system"]


# --- submit follow-up message (job id + paths + monitoring commands) ---------

from chat_nextseek.pipeline.agent_tools import format_luria_followup

_RUN = {"job_id": "11240786", "run_name": "nfcore_all-samples",
        "remote_dir": "/net/bmc-pub10/data1/bmc/pipeline_cd/runs/nfcore_all-samples_260805_190557_0",
        "log": "/net/bmc-pub10/data1/bmc/pipeline_cd/runs/nfcore_all-samples_260805_190557_0/nfcore_all-samples.out"}


def test_followup_carries_exact_job_id_paths_and_commands():
    out = format_luria_followup([_RUN], "user@cluster.example.edu")
    assert "sacct -j 11240786" in out
    assert f'tail -f {_RUN["log"]}' in out
    assert f'{_RUN["remote_dir"]}/' in out
    assert "nfcore_all-samples.err" in out
    assert "ssh user@cluster.example.edu" in out


def test_followup_falls_back_to_squeue_when_job_id_missing():
    out = format_luria_followup([{**_RUN, "job_id": None}], "user@cluster.example.edu")
    assert "sacct" not in out
    assert "squeue -u user" in out


def test_followup_is_empty_without_runs():
    assert format_luria_followup([], "u@h") == ""
    assert format_luria_followup(None, None) == ""


def test_followup_labels_each_run_when_several():
    runs = [_RUN, {**_RUN, "job_id": "11240787", "run_name": "second-run"}]
    out = format_luria_followup(runs, "user@cluster.example.edu")
    assert "11240786" in out and "11240787" in out
    assert "*second-run*" in out


def test_conclude_submitted_appends_followup_to_model_message():
    session = {agent.PIPELINE_AGENT_KEY: {"active": True, "artifacts": {
        "luria_runs": [_RUN], "luria_ssh_target": "user@cluster.example.edu"}}}
    state = session[agent.PIPELINE_AGENT_KEY]
    out = agent._conclude(session, state, {"outcome": "submitted",
                                           "message": "Submitted nf-core/detaxizer to Luria — 2 samples."})
    assert out["action"] == "execute"
    assert out["reply"].startswith("Submitted nf-core/detaxizer to Luria — 2 samples.")
    assert "sacct -j 11240786" in out["reply"]
    assert state["active"] is False


def test_conclude_submitted_without_luria_runs_is_unchanged():
    session = {agent.PIPELINE_AGENT_KEY: {"active": True, "artifacts": {}}}
    state = session[agent.PIPELINE_AGENT_KEY]
    out = agent._conclude(session, state, {"outcome": "submitted", "message": "Submitted."})
    assert out["reply"] == "Submitted."


def test_followup_says_where_to_paste_and_what_each_command_checks():
    out = format_luria_followup([_RUN], "user@cluster.example.edu")
    # Where they go: a terminal, explicitly not this chat.
    assert "terminal" in out.lower()
    assert "not do anything typed into this chat" in out
    # What each one checks, in the user's words rather than the tool's.
    assert "Has it finished yet?" in out
    assert "What is it doing right now?" in out
    assert "Where are my results?" in out
    # And what the output will mean.
    assert "PENDING" in out and "COMPLETED" in out
    assert "Ctrl-C" in out


def test_followup_ends_with_the_run_done_handoff():
    # Nothing polls Luria, so the user closes the loop: the block's last line tells them
    # what to say, and that phrase is what the router sends to Container-CC's reingest.
    for runs in ([_RUN], [_RUN, {**_RUN, "job_id": "11240787", "run_name": "second-run"}]):
        last = format_luria_followup(runs, "user@cluster.example.edu").rstrip().splitlines()[-1]
        assert "my run is done" in last
        assert "register" in last and "outputs" in last


def test_followup_explains_the_squeue_fallback_too():
    out = format_luria_followup([{**_RUN, "job_id": None}], "user@cluster.example.edu")
    assert "Is it still running?" in out
    assert "still pending or running" in out


def test_start_records_the_session_owner_for_the_launch_record(monkeypatch):
    # The launch record names who launched the run, so "my run is done" can find it.
    # The Django session adapter exposes its owner; a plain dict session has none.
    monkeypatch.setattr(agent, "_run_loop", lambda session, config, **kw: {"action": "ask"})
    monkeypatch.setattr(agent, "summarize_pinned_bundle", lambda session: "")

    class OwnedSession(dict):
        owner_user_id = 7

    owned, plain = OwnedSession(), {}
    agent.start(owned, object(), user_query="run rnaseq")
    agent.start(plain, object(), user_query="run rnaseq")
    assert owned[agent.PIPELINE_AGENT_KEY]["launched_by_user_id"] == 7
    assert plain[agent.PIPELINE_AGENT_KEY]["launched_by_user_id"] is None


def test_the_prompt_hands_reingest_off_with_the_run_done_phrase():
    # Users read "I can't load results from here; ask the main NExtSEEK assistant" as a
    # dead end (seen live, rnavar). The agent must tell them the one thing to do: say
    # the run is done, and Nessie carries on.
    from pathlib import Path

    import chat_nextseek

    text = (Path(chat_nextseek.__file__).parent / "prompts" / "pipeline_agent.txt").read_text()
    assert '"my run is done"' in text
    assert "never say you cannot load" in text.lower()


# --- a submit that fails keeps the build, and says why in plain words (prod chat 2c55241a) ---

class _LuriaCfg:
    LURIA_ENV_COMPLETE = True
    def __init__(self, key):
        self.LURIA_ENV = {"user": "u", "key": str(key), "working_path": "/w", "host": "luria.example.edu"}


def _built(tmp_path):
    launch = tmp_path / "launch.yml"
    launch.write_text("launch:\n  - {name: r, pipeline: nf-core/rnaseq, revision: '3.18.0'}\n")
    return {"active": True, "launch_built_at_user_msgs": 1, "artifacts": {"launch": str(launch)},
            "messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "submit"}]}


def test_a_missing_key_is_a_plain_reason_with_no_path(tmp_path):
    state = _built(tmp_path)
    key = tmp_path / "secret-key-name"
    out = json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg(key), state))
    assert out["ok"] is False
    assert "Luria key" in out["message"] and "admin" in out["message"]
    assert "FileNotFoundError" not in out["message"] and "secret-key-name" not in json.dumps(out)
    assert "Luria key" in state["submit_failed"]


@pytest.mark.parametrize("reason, words", [
    ("entry 'r' failed: RuntimeError('ssh failed (255): user@luria: Permission denied (publickey).')", "refused"),
    ("entry 'r' failed: SshTimeout('ssh timed out after 60 s')", "try again"),
    ("entry 'r' failed: RuntimeError('ssh failed (255): Connection to luria closed by remote host.')", "try again"),
    ("entry 'r' failed: RuntimeError('scp failed: lost connection')", "try again"),
    ("entry 'r' failed: RuntimeError('scp: /net/x/run.sh: Permission denied')", "unexpected"),
    ("entry 'r' failed: RuntimeError('ssh failed (255): ssh: Could not resolve hostname luria')", "try again"),
    ("entry 'r' failed: RuntimeError('sbatch: error: QOSMaxSubmitJobPerUserLimit')", "scheduler"),
    ("entry 'r' failed: RuntimeError('scp: write: Disk quota exceeded')", "storage"),
    ("launch.yml not found: /app/outputs/x/launch.yml", "built again"),
    ("no launch entries in launch.yml", "built again"),
    ("entry 'r': local samplesheet not found", "built again"),
    # A file's name in a remote error does not mean the built run's files are gone.
    ("entry 'r' failed: RuntimeError('scp: /net/x/samplesheet.csv: Disk quota exceeded')", "storage"),
    ("entry 'r' failed: RuntimeError('scp: /net/x/samplesheet.csv: No space left on device')", "storage"),
    ("entry 'r' failed: RuntimeError('scp: /net/x/samplesheet.csv: Permission denied')", "unexpected"),
    ("entry 'r' failed: RuntimeError('ssh failed (1): bash: sbatch: command not found')", "scheduler"),
    ("something nobody has seen", "unexpected"),
])
def test_a_luria_failure_says_whose_it_is(monkeypatch, tmp_path, reason, words):
    def fake(*a, reasons, **k):
        reasons.append(reason)
        return []
    monkeypatch.setattr(agent_tools, "submit_luria", fake)
    state = _built(tmp_path)
    out = json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg("k"), state))
    assert out["ok"] is False and words in out["message"].lower()
    assert out["detail"] == reason
    assert "do not call conclude" in out["message"].lower()
    assert state["submit_failed"]


def test_a_raised_submit_is_a_plain_reason_too(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("cannot read the-key-path")
    monkeypatch.setattr(agent_tools, "submit_luria", boom)
    state = _built(tmp_path)
    out = json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg("the-key-path"), state))
    assert out["ok"] is False and "RuntimeError" not in out["message"]
    assert "the-key-path" not in json.dumps(out) and "<key>" in out["detail"]
    assert state["submit_failed"]


@pytest.mark.parametrize("outcome", ["rejected", "answered"])
def test_conclude_after_a_failed_submit_keeps_the_build_open(outcome):
    """In prod chat 2c55241a the model concluded "rejected", said "say submit to try again", and the build was closed."""
    state = {"active": True, "submit_failed": "No key.", "artifacts": {"launch": "/x"},
             "messages": [{"role": "assistant", "content": [{"type": "tool_use", "name": "conclude", "id": "t"}]}]}
    session = {agent.PIPELINE_AGENT_KEY: state}
    out = agent._conclude(session, state, {"outcome": outcome, "message": "It failed. Say submit to retry."})
    assert out == {"action": "ask", "reply": "It failed. Say submit to retry.", "params": None}
    assert state["active"] is True


def test_conclude_submitted_after_a_failed_submit_says_the_reason_not_submitted():
    """The model can send submit_to_luria and conclude in one response, writing "Submitted" before it sees the failure."""
    state = {"active": True, "submit_failed": "No key.", "artifacts": {"launch": "/x"},
             "messages": [{"role": "assistant", "content": [{"type": "tool_use", "name": "conclude", "id": "t"}]}]}
    out = agent._conclude({agent.PIPELINE_AGENT_KEY: state}, state, {"outcome": "submitted", "message": "Submitted."})
    assert out == {"action": "ask", "reply": "No key.", "params": None}


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_conclude_after_a_failed_submit_stores_the_reason_not_a_blank_block(blank):
    """llm_clients sends a stored text block to Converse as it is, and Converse refuses a blank one, so every later turn
    of the build would fail until "cancel"."""
    state = {"active": True, "submit_failed": "No key.", "artifacts": {"launch": "/x"},
             "messages": [{"role": "assistant", "content": [{"type": "tool_use", "name": "conclude", "id": "t"}]}]}
    out = agent._conclude({agent.PIPELINE_AGENT_KEY: state}, state, {"outcome": "rejected", "message": blank})
    assert out == {"action": "ask", "reply": "No key.", "params": None}
    assert state["messages"][-1]["content"] == [{"type": "text", "text": "No key."}]


class _PairingClient:
    """As Bedrock Converse: a tool_use with no tool_result in the next message is refused."""
    def __init__(self, scripted):
        self._scripted = list(scripted)

    def chat_with_tools(self, *, messages, tools, system, model, **kwargs):
        for i, m in enumerate(messages):
            uses = [b["id"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use"] \
                if isinstance(m.get("content"), list) else []
            if uses:
                next_c = messages[i + 1]["content"] if i + 1 < len(messages) else None
                answered = {b.get("tool_use_id") for b in next_c if isinstance(b, dict)} if isinstance(next_c, list) else set()
                assert set(uses) <= answered, f"message {i}: {uses} has no tool_result"
        return self._scripted.pop(0)


class _PairingCfg(_LuriaCfg):
    LOG_DIR = "/tmp"
    PIPELINE_LAUNCH_MODE = "luria"

    def __init__(self, client):
        super().__init__("k")
        self._client = client

    def get_agent_model(self, label):
        return self._client, "m", None

    def _load_prompt(self, name):
        return "SYSTEM"


def _use(name, **tool_input):
    return {"stop_reason": "tool_use",
            "content": [{"type": "tool_use", "name": name, "id": f"t-{name}", "input": tool_input}]}


def test_the_turn_after_a_conclude_on_a_failed_submit_is_not_refused_by_the_model(monkeypatch, tmp_path):
    """The stored history must stay legal, or every later turn of the chat fails."""
    monkeypatch.setattr(agent_tools, "submit_luria", lambda *a, **k: [_RUN])
    monkeypatch.setattr(agent, "catalog_for_prompt", lambda: "")
    client = _PairingClient([_use("conclude", outcome="rejected", message="It failed."), _use("submit_to_luria"),
                             _use("conclude", outcome="submitted", message="Submitted.")])
    state = {**_built(tmp_path), "submit_failed": "No key."}
    session = {agent.PIPELINE_AGENT_KEY: state}
    cfg = _PairingCfg(client)
    assert agent.handle_turn(session, cfg, "submit")["reply"] == "It failed."
    assert state["active"] is True
    out = agent.handle_turn(session, cfg, "submit")
    assert out["action"] == "execute" and state["active"] is False


def _handoff_turn(monkeypatch, state):
    monkeypatch.setattr(agent, "catalog_for_prompt", lambda: "")
    session = {agent.PIPELINE_AGENT_KEY: state}
    cfg = _PairingCfg(_PairingClient([_use("handoff", reason="a side question"), _use("submit_to_luria"),
                                      _use("conclude", outcome="submitted", message="Submitted.")]))
    return session, cfg


def test_a_side_question_keeps_a_built_run(monkeypatch, tmp_path):
    """Operator 2026-10-30. The handoff is answered in the history, so "submit" afterwards reaches the model."""
    monkeypatch.setattr(agent_tools, "submit_luria", lambda *a, **k: [_RUN])
    session, cfg = _handoff_turn(monkeypatch, _built(tmp_path))
    assert agent.handle_turn(session, cfg, "how many are female?")["action"] == "passthrough"
    state = session[agent.PIPELINE_AGENT_KEY]
    assert state["active"] is True and state["artifacts"]["launch"]
    assert agent.handle_turn(session, cfg, "submit")["action"] == "execute"


def test_a_side_question_closes_a_build_with_nothing_built(monkeypatch):
    session, cfg = _handoff_turn(monkeypatch, {"active": True, "artifacts": {}, "messages": []})
    assert agent.handle_turn(session, cfg, "how many are female?")["action"] == "passthrough"
    assert session[agent.PIPELINE_AGENT_KEY] == {}


def test_conclude_cancelled_after_a_failed_submit_still_cancels():
    state = {"active": True, "submit_failed": "No key."}
    out = agent._conclude({agent.PIPELINE_AGENT_KEY: state}, state, {"outcome": "cancelled", "message": "ok"})
    assert out["action"] == "cancel" and state["active"] is False


def test_a_retry_that_works_clears_the_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_tools, "submit_luria", lambda *a, **k: [_RUN])
    state = {**_built(tmp_path), "submit_failed": "No key."}
    out = json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg("k"), state))
    assert out["ok"] is True and "submit_failed" not in state
    done = agent._conclude({agent.PIPELINE_AGENT_KEY: state}, state, {"outcome": "submitted", "message": "Submitted."})
    assert done["action"] == "execute" and state["active"] is False


def test_an_unknown_sbatch_outcome_offers_no_retry(monkeypatch, tmp_path):
    def fake(*a, reasons, **k):
        reasons.append("entry 'r' failed: RuntimeError('sbatch outcome unknown: ssh failed (255): Connection closed')")
        return []
    monkeypatch.setattr(agent_tools, "submit_luria", fake)
    state = _built(tmp_path)
    out = json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg("k"), state))
    assert out["ok"] is False and "may have started" in state["submit_failed"]
    assert "may have started" in out["message"] and "check" in out["message"]
    assert "tries again" not in out["message"]
    assert "do not offer" in out["message"].lower()


def test_files_gone_offers_a_rebuild_not_a_retry(monkeypatch, tmp_path):
    def fake(*a, reasons, **k):
        reasons.append("launch.yml not found: /x")
        return []
    monkeypatch.setattr(agent_tools, "submit_luria", fake)
    out = json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg("k"), _built(tmp_path)))
    assert "built again" in out["message"] and "tries again" not in out["message"]


# --- one build, one job: a second submit is refused in code, not by instruction ---

_ALREADY_SENT = ('Not submitted again: this run was already sent to Luria. '
                 'Call conclude(outcome="submitted") so the user gets its job details.')


def _no_second_job(monkeypatch):
    monkeypatch.setattr(agent_tools, "submit_luria", lambda *a, **k: pytest.fail("a second job went to sbatch"))


def _submit_unknown(monkeypatch, state):
    def fake(*a, reasons, **k):
        reasons.append("entry 'r' failed: RuntimeError('sbatch outcome unknown: ssh failed (255): Connection closed')")
        return []
    monkeypatch.setattr(agent_tools, "submit_luria", fake)
    return json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg("k"), state))


def _rebuilt(state, tmp_path):
    """configure_run again, then the user's reply to the new build."""
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1,fastq_2,strandedness\n")
    state["artifacts"].update(samplesheet=str(sheet), base_dir=str(tmp_path))
    built = json.loads(agent_tools.tool_configure_run(_PairingCfg(None), state, {"pipeline_key": "rnaseq"}, str(tmp_path)))
    assert built["ok"] is True, built
    state["messages"].append({"role": "user", "content": "submit"})


def test_a_second_submit_of_a_submitted_run_is_refused(monkeypatch, tmp_path):
    """sbatch worked and the next model call failed: the build is still open, and "submit" must not queue a second job
    or lose the first job's record."""
    state = _built(tmp_path)
    monkeypatch.setattr(agent_tools, "submit_luria", lambda *a, **k: [_RUN])
    assert json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg("k"), state))["ok"] is True
    _no_second_job(monkeypatch)
    out = json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg("k"), state))
    assert out == {"ok": False, "message": _ALREADY_SENT}
    assert state["artifacts"]["luria_runs"] == [_RUN]


def test_the_refused_second_submit_ends_with_the_first_jobs_details(monkeypatch, tmp_path):
    _no_second_job(monkeypatch)
    monkeypatch.setattr(agent, "catalog_for_prompt", lambda: "")
    state = _built(tmp_path)
    state["artifacts"]["luria_runs"] = [_RUN]
    session = {agent.PIPELINE_AGENT_KEY: state}
    cfg = _PairingCfg(_PairingClient([_use("submit_to_luria"), _use("conclude", outcome="submitted", message="Submitted.")]))
    out = agent.handle_turn(session, cfg, "submit")
    assert out["action"] == "execute" and "sacct -j 11240786" in out["reply"] and state["active"] is False


def test_a_submit_after_an_unknown_outcome_is_refused(monkeypatch, tmp_path):
    """The job may be queued on Luria, so "submit" must not call sbatch again, whatever the model was told."""
    state = _built(tmp_path)
    _submit_unknown(monkeypatch, state)
    _no_second_job(monkeypatch)
    out = json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg("k"), state))
    assert out["ok"] is False and "may have started" in out["message"]
    assert "Do not offer another submit" in out["message"]
    assert "may have started" in state["submit_failed"]


def test_a_rebuilt_run_can_submit_after_a_submitted_one(monkeypatch, tmp_path):
    state = _built(tmp_path)
    state["artifacts"]["luria_runs"] = [_RUN]
    _rebuilt(state, tmp_path)
    assert "luria_runs" not in state["artifacts"]
    second = {**_RUN, "job_id": "11240787"}
    monkeypatch.setattr(agent_tools, "submit_luria", lambda *a, **k: [second])
    assert json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg("k"), state))["ok"] is True
    assert state["artifacts"]["luria_runs"] == [second]


def test_a_rebuilt_run_can_submit_after_an_unknown_outcome(monkeypatch, tmp_path):
    state = _built(tmp_path)
    _submit_unknown(monkeypatch, state)
    _rebuilt(state, tmp_path)
    assert "submit_failed" not in state
    monkeypatch.setattr(agent_tools, "submit_luria", lambda *a, **k: [_RUN])
    assert json.loads(agent_tools.tool_submit_to_luria(_LuriaCfg("k"), state))["ok"] is True


def test_a_side_question_closes_a_run_already_submitted(monkeypatch, tmp_path):
    """sbatch worked and conclude was never reached: a handoff must not keep that build open for good. The record stays,
    as after conclude, so the chat still knows it launched the run."""
    state = _built(tmp_path)
    state["artifacts"]["luria_runs"] = [_RUN]
    session, cfg = _handoff_turn(monkeypatch, state)
    assert agent.handle_turn(session, cfg, "how many are female?")["action"] == "passthrough"
    assert agent.is_active(session) is False
    assert session[agent.PIPELINE_AGENT_KEY]["artifacts"]["luria_runs"] == [_RUN]


def test_a_side_question_lets_these_mean_the_newest_search(monkeypatch, tmp_path):
    """The kept build was pinned to the search it started from. After a side search, "these" means the side search:
    the pin would offer a run on the old samples, and a "yes" would queue it."""
    state = _built(tmp_path)
    state["source_bundle_id"] = 7
    session, cfg = _handoff_turn(monkeypatch, state)
    agent.handle_turn(session, cfg, "find me the female ones")
    assert "source_bundle_id" not in session[agent.PIPELINE_AGENT_KEY]


@pytest.mark.parametrize("message, reply", [("Those samples have no reads.", "Those samples have no reads."),
                                            ("  ", "The run is still built.")])
def test_a_rejected_question_keeps_a_built_run(message, reply):
    """conclude(rejected) answers the question it was asked about. It must not take a built run with it, or a later
    "submit" finds nothing."""
    state = {"active": True, "artifacts": {"launch": "/x"},
             "messages": [{"role": "assistant", "content": [{"type": "tool_use", "name": "conclude", "id": "t"}]}]}
    session = {agent.PIPELINE_AGENT_KEY: state}
    out = agent._conclude(session, state, {"outcome": "rejected", "message": message})
    assert out == {"action": "ask", "reply": reply, "params": None}
    assert state["active"] is True
    assert state["messages"][-1]["content"] == [{"type": "text", "text": reply}]


def test_a_rejected_question_after_a_submit_closes_the_build():
    state = {"active": True, "artifacts": {"launch": "/x", "luria_runs": [_RUN]},
             "messages": [{"role": "assistant", "content": [{"type": "tool_use", "name": "conclude", "id": "t"}]}]}
    agent._conclude({agent.PIPELINE_AGENT_KEY: state}, state, {"outcome": "rejected", "message": "No."})
    assert state["active"] is False


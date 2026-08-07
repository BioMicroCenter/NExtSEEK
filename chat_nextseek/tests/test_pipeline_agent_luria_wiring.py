import chat_nextseek.pipeline.agent as agent


def test_run_loop_uses_build_pipeline_tool_schemas(monkeypatch):
    captured = {}

    # Spy: record that the builder is called with the config, return a marker tool list.
    monkeypatch.setattr(agent, "build_pipeline_tool_schemas",
                        lambda config: (captured.setdefault("built_with", config), [{"name": "conclude"}])[1])
    monkeypatch.setattr(agent, "catalog_for_prompt", lambda: "CATALOG")
    monkeypatch.setattr(agent, "summarize_pinned_bundle", lambda session: "")

    class FakeClient:
        def chat_with_tools(self, *, messages, tools, system, model):
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
        def chat_with_tools(self, *, messages, tools, system, model):
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
    out = format_luria_followup([_RUN], "juanita@luria.mit.edu")
    assert "sacct -j 11240786" in out
    assert f'tail -f {_RUN["log"]}' in out
    assert f'{_RUN["remote_dir"]}/' in out
    assert "nfcore_all-samples.err" in out
    assert "ssh juanita@luria.mit.edu" in out


def test_followup_falls_back_to_squeue_when_job_id_missing():
    out = format_luria_followup([{**_RUN, "job_id": None}], "juanita@luria.mit.edu")
    assert "sacct" not in out
    assert "squeue -u juanita" in out


def test_followup_is_empty_without_runs():
    assert format_luria_followup([], "u@h") == ""
    assert format_luria_followup(None, None) == ""


def test_followup_labels_each_run_when_several():
    runs = [_RUN, {**_RUN, "job_id": "11240787", "run_name": "second-run"}]
    out = format_luria_followup(runs, "juanita@luria.mit.edu")
    assert "11240786" in out and "11240787" in out
    assert "*second-run*" in out


def test_conclude_submitted_appends_followup_to_model_message():
    session = {agent.PIPELINE_AGENT_KEY: {"active": True, "artifacts": {
        "luria_runs": [_RUN], "luria_ssh_target": "juanita@luria.mit.edu"}}}
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
    out = format_luria_followup([_RUN], "juanita@luria.mit.edu")
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


def test_followup_explains_the_squeue_fallback_too():
    out = format_luria_followup([{**_RUN, "job_id": None}], "juanita@luria.mit.edu")
    assert "Is it still running?" in out
    assert "still pending or running" in out

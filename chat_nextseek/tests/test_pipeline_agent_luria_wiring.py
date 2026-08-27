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

from chat_nextseek.pipeline import agent as pa


class _StubClient:
    """Scripted chat_with_tools: yields queued responses in order."""
    def __init__(self, scripted):
        self._scripted = list(scripted)

    def chat_with_tools(self, *, messages, tools, system, model, max_tokens=None, temperature=0.0):
        return self._scripted.pop(0)


class _Cfg:
    LOG_DIR = "/tmp"
    def __init__(self, client):
        self._client = client
    def get_agent_model(self, label):
        return self._client, "us.anthropic.claude-opus-4-7", None
    def _load_prompt(self, name):
        return "SYSTEM PROMPT"


def test_end_turn_text_is_ask_and_stays_active():
    client = _StubClient([{"stop_reason": "end_turn", "content": [{"type": "text", "text": "Which pipeline?"}]}])
    session = {}
    result = pa.start(session, _Cfg(client), user_query="build something", parser_plan={}, reporter_plan={})
    assert result["action"] == "ask"
    assert result["reply"] == "Which pipeline?"
    assert pa.is_active(session) is True


def test_conclude_rejected_deactivates():
    client = _StubClient([{"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "t1", "name": "conclude",
         "input": {"outcome": "rejected", "message": "These are DNA samples; rnaseq needs RNA."}}]}])
    session = {}
    result = pa.start(session, _Cfg(client), user_query="run rnaseq on dna", parser_plan={}, reporter_plan={})
    assert result["action"] == "ask"
    assert "DNA" in result["reply"]
    assert pa.is_active(session) is False


def test_cancel_keyword_shortcuts():
    session = {"pipeline_agent": {"active": True, "messages": [], "resolved": {}, "artifacts": {}}}
    result = pa.handle_turn(session, _Cfg(_StubClient([])), "cancel")
    assert result["action"] == "cancel"
    assert pa.is_active(session) is False


def test_round_trip_tool_then_conclude_submitted(monkeypatch):
    monkeypatch.setattr("chat_nextseek.pipeline.agent.dispatch_pipeline_tool_call",
                        lambda **kw: '{"ok": true, "run_urls": ["https://tower/run/1"]}')
    client = _StubClient([
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "t1", "name": "submit_to_tower", "input": {}}]},
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "t2", "name": "conclude",
             "input": {"outcome": "submitted", "message": "Submitted: https://tower/run/1"}}]},
    ])
    session = {}
    result = pa.start(session, _Cfg(client), user_query="submit", parser_plan={}, reporter_plan={})
    assert result["action"] == "execute"
    assert "tower/run/1" in result["reply"]
    assert pa.is_active(session) is False
    # transcript is balanced: the t1 tool_use got a matching t1 tool_result before iter 2
    msgs = session["pipeline_agent"]["messages"]
    assert any(
        isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == "t1"
                for b in m["content"])
        for m in msgs
    )


def test_messages_persist_across_handle_turn():
    client = _StubClient([
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Which pipeline?"}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "OK, rnaseq."}]},
    ])
    session = {}
    cfg = _Cfg(client)
    pa.start(session, cfg, user_query="build", parser_plan={}, reporter_plan={})
    n_after_start = len(session["pipeline_agent"]["messages"])
    pa.handle_turn(session, cfg, "use rnaseq")
    assert len(session["pipeline_agent"]["messages"]) > n_after_start  # transcript accumulates


def test_guard_when_client_lacks_chat_with_tools():
    class _Dumb:
        pass

    class _CfgDumb:
        LOG_DIR = "/tmp"
        def get_agent_model(self, label):
            return _Dumb(), "model", None
        def _load_prompt(self, name):
            return "x"

    session = {}
    result = pa.start(session, _CfgDumb(), user_query="build", parser_plan={}, reporter_plan={})
    assert result["action"] == "ask"
    assert "Bedrock" in result["reply"]
    assert pa.is_active(session) is False


def test_resolve_write_configure_submit_happy_path(monkeypatch):
    # dispatch is stubbed to return canned tool results in call order
    results = iter([
        '{"ok": true, "leaf_count": 1, "leaves": [{"uid": "D.SEQ-1-PUB", "accessions": ["SRR1"]}], "detected_species": "Mus musculus", "bundle_key": "GRCm39", "param_menu": {"aligner": {}}, "reference_resources": ["fasta"]}',
        '{"ok": true, "samplesheet": "/tmp/s.csv", "total_rows": 1}',
        '{"ok": true, "resolved_params": {"aligner": "star_salmon", "genome": "GRCm39"}, "reference_status": "igenomes_fallback"}',
    ])
    monkeypatch.setattr("chat_nextseek.pipeline.agent.dispatch_pipeline_tool_call", lambda **kw: next(results))
    client = _StubClient([
        {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t1", "name": "resolve_samples", "input": {"kind": "explicit_uids", "uids": ["MUS-1-PUB"], "pipeline_key": "rnaseq"}}]},
        {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t2", "name": "write_samplesheet", "input": {"pipeline_key": "rnaseq", "cohorts": []}}]},
        {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t3", "name": "configure_run", "input": {"pipeline_key": "rnaseq", "params": {}}}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "rnaseq, genome GRCm39 (iGenomes fallback), aligner star_salmon. Submit?"}]},
    ])
    session = {}
    result = pa.start(session, _Cfg(client), user_query="run rnaseq on these mice", parser_plan={}, reporter_plan={})
    assert result["action"] == "ask"
    assert "GRCm39" in result["reply"]
    assert pa.is_active(session) is True   # paused for user confirmation, not concluded

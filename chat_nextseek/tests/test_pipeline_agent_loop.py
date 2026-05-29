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

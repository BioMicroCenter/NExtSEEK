"""G3: the system agent must receive the earlier turn of its own session."""
from types import SimpleNamespace
from unittest.mock import MagicMock
from chat_nextseek.agents import system as system_mod
from chat_nextseek.chat_memory import append_turn
from chat_nextseek.schemas import ParserPlan

PRIOR = "Zorp, Blip and Quux are the assays that link the two types. " + "pad " * 120 + "Tail Assay Mark."

def _config():
    c = MagicMock()
    c.FULL_SAMPLETYPES_MAP, c.FULL_ASSAYS_MAP = {}, {}
    c.MIN_SAMPLETYPES, c.MIN_ASSAYS, c.MIN_API_ENDPOINTS = [], [], []
    c.CAPABILITIES_DOC, c.NEO4J_SCHEMA, c.SYSTEM_AGENT_SYSTEM_PROMPT = "caps", {}, "sys"
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c

def _seen(monkeypatch, session):
    monkeypatch.setattr(system_mod, "live_catalog_context", lambda *a, **k: None)
    monkeypatch.setattr(system_mod.graph_catalog, "committed_schema", lambda config: {})
    sent = {}
    def fake(config, *, messages, system, **kw):
        sent["text"] = system + str(messages)
        return {"content": [{"type": "tool_use", "id": "1", "name": "answer",
                             "input": {"narrative": "ok", "mode": "get_capabilities"}}]}
    monkeypatch.setattr(system_mod, "call_tools", fake)
    system_mod.system_agent(_config(), "What are the three assays that you found?", {}, ParserPlan(mode="system_question"), session=session)
    return sent["text"]

def test_system_agent_sees_the_earlier_turn(monkeypatch):
    s = {}
    append_turn(s, user_query="assays from A to B", mode="system_question", assistant_reply=PRIOR)
    text = _seen(monkeypatch, s)
    assert "assays from A to B" in text and "Tail Assay Mark" in text

def test_fresh_session_adds_no_history(monkeypatch):
    text = _seen(monkeypatch, {})
    assert "CHAT_HISTORY" not in text


def test_a_number_restated_from_the_prior_reply_is_not_stripped(monkeypatch):
    s = {}
    append_turn(s, user_query="how many samples", mode="system_question", assistant_reply="We hold 3,558 samples.")
    monkeypatch.setattr(system_mod, "live_catalog_context", lambda *a, **k: None)
    monkeypatch.setattr(system_mod.graph_catalog, "committed_schema", lambda config: {})
    calls = []

    def fake(config, *, messages, system, **kw):
        calls.append(1)
        return {"content": [{"type": "tool_use", "id": "1", "name": "answer",
                             "input": {"narrative": "As I said, 3,558 samples.", "mode": "get_capabilities"}}]}
    monkeypatch.setattr(system_mod, "call_tools", fake)
    out = system_mod.system_agent(_config(), "say that again", {}, ParserPlan(mode="system_question"), session=s)
    assert len(calls) == 1 and "3,558" in out.narrative and "removed" not in out.narrative


def test_the_planner_path_forwards_the_session(monkeypatch):
    from chat_nextseek.agents.planner import tools
    seen = {}

    def fake(config, query, entity, plan, session=None):
        seen["session"] = session
        return SimpleNamespace(narrative="x")
    monkeypatch.setattr(tools, "system_agent", fake)
    step = SimpleNamespace(execution=SimpleNamespace(tool_query="q"))
    s = {"k": 1}
    tools._plan_tool_system_question(MagicMock(), s, step, "q", {}, None, {})
    assert seen["session"] is s

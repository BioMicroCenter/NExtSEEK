"""A follow-up whose models fail after it has already run something keeps what it found.

Since fix 5 the follow-up tool loop ends in ``LLMFatalError`` when its model and the
fallback both fail. Raised from a loop that had already run a query or a computation,
it killed the turn and dropped those results. The loop now hands back what it has, and
the turn answers from it through ``resolve_followup_outcome`` (the same partial reply a
loop that ran out of turns gives), with ``debug.followup`` written as usual. Only a loop
that ran nothing lets the fatal through, so the user is told the models were unavailable.
"""
from __future__ import annotations

import pytest

from chat_nextseek.agents.followup import FOLLOWUP_AGENT_KEY, resolve_followup_outcome, run_followup
from chat_nextseek.llm_clients import LLMFatalError, LLMServiceUnavailableError


class _Client:
    """Replays tool turns; an exception in the script is raised."""

    provider = "bedrock"

    def __init__(self, script):
        self.script = list(script)

    def chat_with_tools(self, *, messages, tools, system, model, **kwargs):
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def reset_connections(self):
        return True


class _Cfg:
    LOG_DIR = None
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None}
    AGENT_MODEL_CATALOG: dict = {}

    def __init__(self, client):
        self._client = client
        self.LLM_CLIENT = client
        self.LLM_MODEL = "m"
        self.LLM_CLIENTS = {"anth": client}

    def get_agent_model(self, label):
        assert label == FOLLOWUP_AGENT_KEY
        return self._client, "us.anthropic.claude-opus-4-7", None

    def _load_prompt(self, name):
        return "SYSTEM PROMPT"


def _tool_use(name, payload):
    return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t1", "name": name, "input": payload}]}


BUNDLE = {"id": 7, "user_query": "mouse samples", "mode": "graph_query",
          "graph_result": {"ok": True, "count": 3, "total": 3, "data": [{"uid": f"MUS-{i}"} for i in range(3)]}}
DOWN = LLMServiceUnavailableError("503 ServiceUnavailableException")


def test_a_fatal_after_a_query_returns_what_the_query_found():
    client = _Client([_tool_use("run_new_query", {"question": "which have RNA", "seed_uids": True}), DOWN])

    out = run_followup(_Cfg(client), user_text="which of those have RNA?", bundle=BUNDLE,
                       run_query=lambda **kw: {"ok": True, "count": 2, "examples": ["MUS-1", "MUS-2"]})

    assert out["reply"] is None
    assert [q["result"]["count"] for q in out["queries"]] == [2]
    assert out["tool_calls"] == ["run_new_query"]
    assert out["model_unavailable"] is True
    reply = resolve_followup_outcome(out)
    assert reply.startswith("2 records match") and "MUS-1" in reply


def test_a_fatal_after_a_computation_returns_what_it_computed():
    client = _Client([_tool_use("compute_over_rows", {"source": "stored", "code": "result = 2"}), DOWN])

    out = run_followup(_Cfg(client), user_text="how many of those are lung?", bundle=BUNDLE,
                       run_query=lambda **kw: {},
                       compute=lambda **kw: {"ok": True, "count": 2})

    assert [c["result"]["count"] for c in out["computes"]] == [2]
    assert resolve_followup_outcome(out).startswith("2 of the earlier result's rows match")


def test_a_fatal_before_anything_ran_still_ends_the_turn():
    client = _Client([_tool_use("read_stored_result", {}), DOWN])

    with pytest.raises(LLMFatalError) as excinfo:
        run_followup(_Cfg(client), user_text="q", bundle=BUNDLE, run_query=lambda **kw: {})
    assert excinfo.value.unavailable is True


def test_a_fatal_on_the_first_call_still_ends_the_turn():
    with pytest.raises(LLMFatalError):
        run_followup(_Cfg(_Client([DOWN])), user_text="q", bundle=BUNDLE, run_query=lambda **kw: {})


def test_the_orchestrator_writes_debug_followup_for_the_partial_answer(monkeypatch):
    """Through the orchestrator's seam: the outcome comes back (not None), so the turn's
    debug payload records the queries the loop ran."""
    from chat_nextseek import orchestrator as orch

    client = _Client([_tool_use("run_new_query", {"question": "which have RNA", "seed_uids": True}), DOWN])
    monkeypatch.setattr(orch, "run_followup", lambda config, **kw: run_followup(
        _Cfg(client), user_text=kw["user_text"], bundle=kw["bundle"],
        run_query=lambda **q: {"ok": True, "count": 2, "examples": ["MUS-1"]}))

    outcome = orch._run_followup_agent(_Cfg(client), session={}, user_text="which have RNA?", bundle=BUNDLE,
                                       log_dir=None, failure={})

    assert outcome is not None and outcome["queries"]
    assert resolve_followup_outcome(outcome).startswith("2 records match")

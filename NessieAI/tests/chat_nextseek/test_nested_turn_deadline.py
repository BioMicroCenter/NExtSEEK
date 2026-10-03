"""A nested NS turn opens its CallScope with the deadline it was given (plan 04, piece 4).

``deadline_epoch`` is Unix time (the Container-CC turn's deadline less the answer reserve, NessieAI/ns/op_limits);
the turn's model calls are cut to fit it and none starts past it (the ladder, test_call_scope_deadline.py). An NS turn
of its own opens with none. No model is called.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from chat_nextseek import call_scope
from chat_nextseek import orchestrator as orch
from chat_nextseek.llm_clients import LLMFatalError


@pytest.fixture
def turn(monkeypatch, tmp_path):
    seen: dict = {}
    events: list = []

    def first_step(*a, **k):
        scope = call_scope.current()
        seen["remaining"] = scope.remaining()
        if seen["remaining"] is not None and seen["remaining"] <= call_scope.DEADLINE_FLOOR_S:
            raise LLMFatalError("deadline: no time left for the entity call", agent="entity", reason="deadline")
        return {"reply": "ok"}

    monkeypatch.setattr(orch, "_identity_gate", lambda session, config, *a, **k: (config, None))
    monkeypatch.setattr(orch, "_ensure_query_log_dir", lambda session, config: str(tmp_path))
    monkeypatch.setattr(orch, "ArtifactStore", lambda log_dir: None)
    monkeypatch.setattr(orch, "_accepted_suggestion", lambda session, text: None)
    monkeypatch.setattr(orch, "append_turn", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_handle_pipeline_agent_turn", first_step)

    def run(**kw):
        orch.run_query({}, SimpleNamespace(), "q", lambda ev, data: events.append((ev, data)),
                       credentials={"api_user": "u", "api_pass": "p"}, **kw)
        return seen, events
    return run


def test_a_nested_turn_opens_its_scope_with_the_deadline(turn):
    seen, _ = turn(deadline_epoch=time.time() + 30)
    assert seen["remaining"] == pytest.approx(30, abs=2)


def test_a_turn_of_its_own_has_no_deadline(turn):
    seen, _ = turn()
    assert seen["remaining"] is None


def test_a_nested_turn_stops_at_the_deadline(turn):
    seen, events = turn(deadline_epoch=time.time() - 1)
    assert seen["remaining"] <= 0
    assert any(ev == "query_error" and data.get("fatal") for ev, data in events)

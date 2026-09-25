"""On the routed chat path, an NS turn that crashed ends on its real error, which carries its cost.

``cc-assistant/query/async/`` (the chat panel, the harness and the CI lane) runs an NS
turn inside ``NessieAI/cc/turn.py``'s ``_run``. When ``run_query`` meets an exception it
does not handle, it sends its own ``query_error`` (the real message, with the turn's
cost) and re-raises. ``_run``'s ``except Exception`` used to send a second one reading
"Internal pipeline error", and the task keeps the last, so the user lost the real error
(F13, operator ruling 2026-09-25). It now sends that only when no ``query_error`` went
out (``NessieAI.ns.turn._error_tracking_send_event``, the NS endpoints' guard). The
harness reads a turn's engine fields off the last ``query_error``
(``NessieAI/tests/nessie_tests/turn_cost.py``, ``read_turn``), which is now the real one.

This drives the real ``start_task`` and the real (collected) ``run_query``: only the
routing, the host seams and the step that crashes are stubbed. No model, no database.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from NessieAI.cc import turn as cc_turn
from NessieAI.router import router as cc_router
from NessieAI.tests.nessie_tests.turn_cost import read_turn
from chat_nextseek import model_prices, orchestrator, turn_spend
from chat_nextseek.llm_clients import LLMResponse

FLASH = "gemini-3.5-flash"
OPUS = "us.anthropic.claude-opus-4-7"
USAGE = {"prompt_tokens": 4000, "completion_tokens": 100, "thoughts_tokens": 300, "cached_tokens": 1000}


class _Thread:
    def __init__(self, target, daemon=None):
        self._target = target

    def start(self):
        self._target()


class _Adapter(dict):
    def reload(self):
        pass

    def save(self):
        pass


def _crash_after_a_call(*_a, **_k):
    turn_spend.record_call(
        {"agent": "graph_agent", "provider": "gcp", "model": FLASH, "attempt": 2, "outcome": "ok",
         "fallback_from": OPUS, "fallback_reason": "timeout"},
        resp=LLMResponse(content="x", raw=None, usage=dict(USAGE), model=FLASH, provider="gcp", metadata={}))
    raise RuntimeError("boom after a paid call")


def _start(monkeypatch, tmp_path, crash):
    """Run one routed NS turn whose first step is ``crash``; return the events it sent."""
    decision = cc_router.RouteDecision(
        route=cc_router.ROUTE_NS, model_class=None, model_id=None, reasoning="r", source="baml",
        router_model="gemini-3.1-pro-preview", router_cost_usd=0.004, router_usage={"calls": []},
        router_cost_partial=False)
    monkeypatch.setattr(cc_turn, "threading", SimpleNamespace(Thread=_Thread))
    monkeypatch.setattr(cc_turn, "_select_chat_config", lambda request, r: SimpleNamespace())
    monkeypatch.setattr(cc_turn, "_eval_config", lambda config, user, r: config)
    monkeypatch.setattr(cc_turn, "_decide_route", lambda *a, **k: decision)
    monkeypatch.setattr(cc_turn, "_record_ledger_row", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "_auto_title_if_unset", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "_emit_ns_run_root", lambda *a, **k: None)
    # The real, collected run_query, crashing in its first step.
    monkeypatch.setattr(orchestrator, "_identity_gate", lambda session, config, *a, **k: (config, None))
    monkeypatch.setattr(orchestrator, "_ensure_query_log_dir", lambda session, config: str(tmp_path))
    monkeypatch.setattr(orchestrator, "ArtifactStore", lambda log_dir: None)
    monkeypatch.setattr(orchestrator, "_accepted_suggestion", lambda session, text: None)
    monkeypatch.setattr(orchestrator, "_handle_pipeline_agent_turn", crash)

    progress: list[dict] = []
    cc_turn.start_task(
        SimpleNamespace(user=SimpleNamespace(username="u", is_superuser=False)),
        SimpleNamespace(query="how many mice", mode="standard", max_turn_length_s=None), force_cc=False,
        chat_session=SimpleNamespace(extra_state={}, session_id="s-1", results_history=[]),
        query_task=SimpleNamespace(task_id="t-1"),
        send_event=lambda ev, data: progress.append({"event": ev, "data": data}),
        adapter=_Adapter(), api_user="caller", api_pass="caller-pw",
        resolved_session_id="s-1",
    )
    return progress


def test_a_crashed_ns_turn_on_the_routed_path_ends_on_its_real_error_which_carries_its_cost(
        monkeypatch, tmp_path):
    progress = _start(monkeypatch, tmp_path, _crash_after_a_call)

    errors = [p["data"] for p in progress if p["event"] == "query_error"]
    assert len(errors) == 1, f"run_query's own error only, not a second generic one: {errors}"
    cost = model_prices.call_cost(FLASH, USAGE).cost_usd
    (error,) = errors
    assert error["error"] == "boom after a paid call"
    assert error["error"] != "Internal pipeline error"
    assert error["total_cost_usd"] == pytest.approx(cost, abs=1e-6)
    assert error["cost_partial"] is False and error["models_used"] == [FLASH]
    assert error["model_fallback"] == [{"agent": "graph_agent", "from": OPUS, "to": FLASH, "reason": "timeout"}]

    turn = read_turn({"progress": progress})
    assert turn["engine_cost"] == pytest.approx(cost, abs=1e-6), "the harness reads the engine cost off it"
    assert turn["router_cost"] == 0.004


def test_the_generic_error_still_ends_a_turn_that_crashed_before_any_error_went_out(monkeypatch, tmp_path):
    """A collected entry point that crashed without sending a query_error: the catch-all
    still ends the turn, with the cost the turn record took out on the exception
    (turn_spend.collects_turn)."""
    @turn_spend.collects_turn
    def _crash_without_reporting(*_a, **_k):
        turn_spend.record_call(
            {"agent": "graph_agent", "provider": "gcp", "model": FLASH, "attempt": 1, "outcome": "ok"},
            resp=LLMResponse(content="x", raw=None, usage=dict(USAGE), model=FLASH, provider="gcp", metadata={}))
        raise RuntimeError("died before reporting anything")

    monkeypatch.setattr(cc_turn, "run_query", _crash_without_reporting)
    progress = _start(monkeypatch, tmp_path, _crash_after_a_call)

    errors = [p["data"] for p in progress if p["event"] == "query_error"]
    assert len(errors) == 1
    (error,) = errors
    assert error["error"] == "Internal pipeline error" and error["agent"] == "unknown"
    assert error["session_id"] == "s-1"
    assert error["total_cost_usd"] == pytest.approx(model_prices.call_cost(FLASH, USAGE).cost_usd, abs=1e-6)
    assert error["cost_partial"] is False and error["models_used"] == [FLASH]

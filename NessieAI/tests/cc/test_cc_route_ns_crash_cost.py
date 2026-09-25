"""On the routed chat path, an NS turn that crashed still reports its cost in its last event.

``cc-assistant/query/async/`` (the chat panel, the harness and the CI lane) runs an NS
turn inside ``NessieAI/cc/turn.py``'s ``_run``. When ``run_query`` meets an exception it
does not handle, it sends its own ``query_error`` (with the turn's cost) and re-raises;
``_run``'s ``except Exception`` then sends a second, "Internal pipeline error". The
harness reads a turn's engine fields off the LAST ``query_error``
(``NessieAI/tests/nessie_tests/turn_cost.py``, ``read_turn``), so that second event must
carry the cost too, or the turn reads as unmeasured. The entry point's collector has
already closed by then; the record rides on the exception (``turn_spend.collects_turn``).

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


def test_a_crashed_ns_turn_on_the_routed_path_ends_on_an_event_that_carries_its_cost(monkeypatch, tmp_path):
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
    # The real, collected run_query, crashing in its first step after one paid call.
    monkeypatch.setattr(orchestrator, "_identity_gate", lambda session, config, *a, **k: (config, None))
    monkeypatch.setattr(orchestrator, "_ensure_query_log_dir", lambda session, config: str(tmp_path))
    monkeypatch.setattr(orchestrator, "ArtifactStore", lambda log_dir: None)
    monkeypatch.setattr(orchestrator, "_accepted_suggestion", lambda session, text: None)
    monkeypatch.setattr(orchestrator, "_handle_pipeline_agent_turn", _crash_after_a_call)

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

    errors = [p["data"] for p in progress if p["event"] == "query_error"]
    assert len(errors) == 2, "run_query's own error, then the CC turn's"
    cost = model_prices.call_cost(FLASH, USAGE).cost_usd
    last = errors[-1]
    assert last["error"] == "Internal pipeline error" and last["agent"] == "unknown"
    assert last["session_id"] == "s-1"
    assert last["total_cost_usd"] == pytest.approx(cost, abs=1e-6)
    assert last["cost_partial"] is False and last["models_used"] == [FLASH]
    assert last["model_fallback"] == [{"agent": "graph_agent", "from": OPUS, "to": FLASH, "reason": "timeout"}]

    turn = read_turn({"progress": progress})
    assert turn["engine_cost"] == pytest.approx(cost, abs=1e-6), "the harness reads the engine cost off it"
    assert turn["router_cost"] == 0.004

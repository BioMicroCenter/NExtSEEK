"""An open build takes a turn the router sent to CC (policy._decide_route). When the wizard says the turn is not about
the build, the router's CC call stands: turn.py runs the CC branch, with the wizard's cleared state saved first."""
from __future__ import annotations

from types import SimpleNamespace

from NessieAI.cc import turn as cc_turn
from NessieAI.router import router as cc_router


def _run(monkeypatch, run_query_result, attempted):
    events, saved = [], []

    class _Thread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class _Adapter(dict):
        def reload(self):
            pass

        def save(self):
            saved.append(dict(self))

    class _Chat(SimpleNamespace):
        def refresh_from_db(self, fields=None):
            pass

        def save(self, update_fields=None):
            pass

    seen = {}

    def fake_run_query(adapter, config, query, send_event, credentials=None, **kw):
        seen.update(kw)
        adapter["pipeline_agent"] = {}
        return run_query_result

    decision = cc_router.RouteDecision(route=cc_router.ROUTE_NS, model_class=None, model_id=None,
                                       reasoning="pipeline_active; router said cc (x)", source="pipeline",
                                       attempted_route=attempted, attempted_source="baml" if attempted else None)
    monkeypatch.setattr(cc_turn, "threading", SimpleNamespace(Thread=_Thread))
    monkeypatch.setattr(cc_turn, "_select_chat_config", lambda request, r: SimpleNamespace())
    monkeypatch.setattr(cc_turn, "_eval_config", lambda config, user, r: config)
    monkeypatch.setattr(cc_turn, "_decide_route", lambda *a, **k: decision)
    monkeypatch.setattr(cc_turn, "_record_ledger_row", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "_auto_title_if_unset", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "_emit_ns_run_root", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn.chat_search_index, "index_chat_quietly", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "run_query", fake_run_query)
    cc_turn.start_task(
        SimpleNamespace(user=SimpleNamespace(username="u", is_superuser=False)),
        SimpleNamespace(query="plot the species", mode="standard", max_turn_length_s=None), force_cc=False,
        chat_session=_Chat(extra_state={}, session_id="s-1", results_history=[]),
        query_task=SimpleNamespace(task_id="t-1"),
        send_event=lambda ev, data: events.append((ev, data)),
        adapter=_Adapter(), api_user="", api_pass="",
        resolved_session_id="s-1",
    )
    return events, saved, seen


def test_a_handoff_of_a_turn_the_router_sent_to_cc_runs_the_cc_branch(monkeypatch):
    events, saved, seen = _run(monkeypatch, {"handed_off": True}, cc_router.ROUTE_CC)
    assert seen["stop_on_handoff"] is True
    routes = [d for ev, d in events if ev == "route_decided"]
    assert [r["route"] for r in routes] == [cc_router.ROUTE_NS, cc_router.ROUTE_CC]
    assert routes[-1]["source"] == "pipeline_handoff"
    # This fake login stops the turn at the top of the CC branch, which is how the test knows it got there.
    assert any(ev == "query_error" and d["agent"] == "container_cc" for ev, d in events)
    assert saved and saved[0]["pipeline_agent"] == {}  # saved before CC starts, or the next turn finds the build open


def test_terminal_cost_counts_the_wizards_spend_on_a_handoff(monkeypatch):
    from NessieAI.ns import turn_memory
    monkeypatch.setattr(turn_memory, "_rows", lambda turn: SimpleNamespace(
        values=lambda *a: SimpleNamespace(first=lambda: {"ops_cost_usd": 0.1})))
    monkeypatch.setattr(turn_memory, "nested_turn_costs", lambda turn: (0.0, []))
    import nextseek_api.assistant.models_db as db
    monkeypatch.setattr(db.QueryTask.objects, "filter", lambda **k: SimpleNamespace(exists=lambda: False))
    out = turn_memory.terminal_cost(object(), {"cost_by_price_table_usd": 1.0}, router_fields={}, prerun_settled=None,
                                    wizard_fields={"total_cost_usd": 0.05, "cost_partial": True})
    assert out["ops_cost_usd"] == 0.15 and out["turn_cost_usd"] == 1.15
    assert out["cost_partial"] is True and "open build" in out["cost_partial_reason"]


def test_a_turn_the_router_sent_to_ns_is_not_offered_a_way_back(monkeypatch):
    events, _, seen = _run(monkeypatch, {"reply": "ok"}, None)
    assert "stop_on_handoff" not in seen
    assert [d["route"] for ev, d in events if ev == "route_decided"] == [cc_router.ROUTE_NS]
    assert not any(ev == "query_error" for ev, d in events)

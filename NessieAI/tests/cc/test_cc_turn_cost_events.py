"""A Container-CC turn's terminal event carries what the whole turn cost (plan 04, piece 3).

``ops_cost_usd``: the ops' and the vocabulary pre-run's spend (``CCTurn.ops_cost_usd``) plus the nested NS turns'
(``QueryTask.parent_cc_turn``). ``turn_cost_usd``: Claude Code's own ``total_cost_usd`` plus that plus the router's.
``cost_partial`` is false only when every part was counted: no op still running, every op priced, every nested turn
finished and priced, the pre-run settled, the router and Claude Code reported. Early-ending turns (timeout, budget,
no result frame, a crash) carry them too. No model, no container.
"""
from __future__ import annotations

import threading
import uuid

import pytest
from django.db.models import F

from NessieAI.cc import turn as cc_turn
from NessieAI.ns import turn_memory as tm
from NessieAI.router import router as cc_router
from NessieAI.tests.cc.turn_harness import cc_seams, decision, drive, rows
from nextseek_api.assistant.models_db import CCTurn, QueryTask

pytestmark = pytest.mark.django_db(transaction=True)
ROUTER = {"router_cost_usd": 0.004, "router_cost_partial": False, "router_usage": {"calls": []}}


def _row(query="q"):
    user, chat, task = rows(f"tc-{uuid.uuid4().hex[:8]}", query=query)
    import hashlib
    turn = CCTurn.objects.create(task=task, user=user, chat=chat,
                                 pass_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest())
    return user, chat, task, turn


def _child(turn, status, result):
    return QueryTask.objects.create(session=turn.chat, user=turn.user, query="nested", status=status,
                                    result=result, parent_cc_turn=turn)


def test_a_whole_turn_counts_claude_code_ops_nested_turns_and_the_router():
    _, _, _, turn = _row()
    tm.add_spend(turn, 0.05)
    _child(turn, "completed", {"total_cost_usd": 0.10, "cost_partial": False})

    out = tm.terminal_cost(turn, {"reply": "ok", "total_cost_usd": 0.30, "cost_partial": True,
                                  "cost_partial_reason": "ran nextseek-graph"},
                           router_fields=ROUTER, prerun_settled=None)

    assert out["ops_cost_usd"] == pytest.approx(0.15)
    assert out["turn_cost_usd"] == pytest.approx(0.454)
    assert out["cost_partial"] is False and "cost_partial_reason" not in out
    assert out["total_cost_usd"] == 0.30, "Claude Code's own number is kept"


def test_claude_codes_price_table_number_is_used_when_present():
    _, _, _, turn = _row()
    out = tm.terminal_cost(turn, {"reply": "ok", "total_cost_usd": 0.30, "cost_by_price_table_usd": 0.33},
                           router_fields={}, prerun_settled=None)
    assert out["turn_cost_usd"] == pytest.approx(0.33), "one price table for the whole turn"
    assert out["total_cost_usd"] == 0.30 and out["cost_by_price_table_usd"] == 0.33


@pytest.mark.parametrize("setup, reason", [
    ("in_flight", "still running"),
    ("partial_op", "not all priced or seen"),
    ("running_child", "nested NExtSEEK turn was still running"),
    ("unpriced_child", "nested NExtSEEK turn reported no cost"),
    ("prerun", "pre-run had not finished"),
    ("no_cc_cost", "Claude Code's own cost"),
    ("router_unknown", "router's cost"),
])
def test_any_part_not_counted_makes_the_turn_partial_and_says_which(setup, reason):
    _, _, _, turn = _row()
    data = {"reply": "ok", "total_cost_usd": 0.30}
    router, settled = dict(ROUTER), None
    if setup == "in_flight":
        CCTurn.objects.filter(pk=turn.pk).update(ops_in_flight=F("ops_in_flight") + 1)
    elif setup == "partial_op":
        tm.add_spend(turn, 0.01, partial=True)
    elif setup == "running_child":
        _child(turn, "running", None)
    elif setup == "unpriced_child":
        _child(turn, "completed", {"total_cost_usd": None})
    elif setup == "prerun":
        settled = threading.Event()
    elif setup == "no_cc_cost":
        data = {"error": "stopped"}
    else:
        router["router_cost_usd"] = None

    out = tm.terminal_cost(turn, data, router_fields=router, prerun_settled=settled)

    assert out["cost_partial"] is True
    assert reason in out["cost_partial_reason"]
    assert isinstance(out["ops_cost_usd"], float) and isinstance(out["turn_cost_usd"], float)


def test_a_forced_turn_has_no_router_part():
    _, _, _, turn = _row()
    out = tm.terminal_cost(turn, {"reply": "ok", "total_cost_usd": 0.2}, router_fields={}, prerun_settled=None)
    assert out["turn_cost_usd"] == pytest.approx(0.2) and out["cost_partial"] is False


def test_a_timed_out_turn_with_an_op_still_running_reports_its_ops_cost_as_partial(monkeypatch, tmp_path):
    """Review focus 2: the watchdog's query_error carries the ops' cost."""
    user, chat, task = rows(f"tc-{uuid.uuid4().hex[:8]}")

    def killed_mid_op(**kw):
        turn = CCTurn.objects.get(task=task)
        tm.add_spend(turn, 0.012)
        CCTurn.objects.filter(pk=turn.pk).update(ops_in_flight=F("ops_in_flight") + 1)
        kw["send_event"]("query_error", {"error": "This turn was stopped at its time limit.",
                                         "reason": "exec_timeout", "agent": "container_cc"})
    cc_seams(monkeypatch, tmp_path, killed_mid_op)

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_CC))

    (error,) = events.named("query_error")
    assert error["data"]["ops_cost_usd"] == pytest.approx(0.012)
    assert error["data"]["turn_cost_usd"] == pytest.approx(0.016)
    assert error["data"]["cost_partial"] is True
    assert "still running" in error["data"]["cost_partial_reason"]


def test_a_crashed_cc_turn_still_carries_its_ops_cost(monkeypatch, tmp_path):
    user, chat, task = rows(f"tc-{uuid.uuid4().hex[:8]}")

    def crashes(**kw):
        tm.add_spend(CCTurn.objects.get(task=task), 0.02)
        raise RuntimeError("container went away")
    cc_seams(monkeypatch, tmp_path, crashes)

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_CC))

    (error,) = events.named("query_error")
    assert error["data"]["error"] == "Internal pipeline error"
    assert error["data"]["ops_cost_usd"] == pytest.approx(0.02)


def test_an_ns_turns_events_are_left_as_they_were(monkeypatch):
    user, chat, task = rows(f"tc-{uuid.uuid4().hex[:8]}")
    monkeypatch.setattr(cc_turn, "run_query", lambda s, c, q, send, credentials=None, **kw: send(
        "query_complete", {"reply": "ok", "total_cost_usd": 0.01}))

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_NS))

    (done,) = events.named("query_complete")
    assert "ops_cost_usd" not in done["data"] and "turn_cost_usd" not in done["data"]

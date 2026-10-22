"""The ops of one turn, and the late aggregate parts, under real MySQL contention (plan 04 Task 9; lane M only).

No lock of the test's own: two ops start on the same stale row and write together, and two late parts finish at the
same moment on their own pool threads and connections. What keeps every strike, plan and cent, and gives the slot back
exactly once, is the row lock, F() and the conditional update. Anywhere else the ``mysql_lane`` marker skips these
(NessieAI/tests/ns/conftest.py). Run: lane_mysql NessieAI/tests/ns/test_turn_ops_mysql.py
"""
from __future__ import annotations

import json
import threading
from decimal import Decimal

import pytest
from django.db import connection

from chat_nextseek import call_scope
from NessieAI.ns import aggregate, granular
from NessieAI.ns import turn_memory as tm
from NessieAI.tests.ns.test_mysql_lane import race
from NessieAI.tests.ns.test_turn_ops import COST, Agents, _turn
from nextseek_api.assistant.models_db import CCTurn

pytestmark = [pytest.mark.mysql_lane, pytest.mark.django_db(transaction=True)]

ONE_OP = Decimal(str(round(COST, 6)))


def test_two_ops_at_once_share_one_vocabulary_and_lose_no_strike_plan_or_cost(monkeypatch):
    assert connection.vendor == "mysql"
    agents = Agents(monkeypatch)
    turn = _turn("How many mice?", vocabulary={})
    agents.on_graph = lambda query: call_scope.current().mark_failed(("gcp", f"model-{query}"), reason="timeout",
                                                                     agent="graph_agent")
    both = threading.Barrier(2, timeout=10)
    agents.on_parser = lambda text: both.wait()

    race(2, lambda i: agents.op("graph", f"question {i}", turn))

    assert sorted(s[1] for s in tm.load_strikes(turn)) == ["model-question 0", "model-question 1"]
    assert tm.get_plan(turn, "question 0") and tm.get_plan(turn, "question 1")
    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_cost_usd == 2 * ONE_OP and row.ops_cost_partial is False and row.ops_in_flight == 0


def test_two_late_parts_finishing_together_settle_once_each_and_release_the_slot_once(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn("How many mice?", vocabulary={"keywords": ["stored"]})
    release, running = threading.Event(), threading.Barrier(3, timeout=10)

    def on_graph(query):
        running.wait()
        assert release.wait(20)
        call_scope.current().mark_failed(("gcp", f"late-{query}"), reason="timeout", agent="graph_agent")
    agents.on_graph = on_graph
    monkeypatch.setattr(aggregate, "op_deadline_s", lambda limit_s: 1.0)
    monkeypatch.setattr(aggregate, "MIN_REMAINING_S", 0.0)
    settled = threading.Semaphore(0)  # one per late part, once its settlement and any slot release are done
    part_done = granular._LateSettlement.part_done

    def counted(self, part):
        try:
            part_done(self, part)
        finally:
            settled.release()
    monkeypatch.setattr(granular._LateSettlement, "part_done", counted)

    waiter = threading.Thread(target=running.wait)  # the test is the third arrival: both parts are running
    waiter.start()
    out = agents.op("aggregate", "How many mice?", turn, parts=json.dumps(["part a", "part b"]))
    waiter.join(10)
    assert {p["status"] for p in out["parts"]} == {"timed_out"}
    assert CCTurn.objects.get(pk=turn.pk).ops_in_flight == 1
    assert tm.take_op_slot(turn), "a second op of the turn: one extra release would free its slot"

    release.set()
    assert settled.acquire(timeout=15) and settled.acquire(timeout=15), "both late parts settled"

    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_in_flight == 1, "released once, by the last part: the second op's slot is still held"
    tm.release_op_slot(turn)
    assert row.ops_cost_usd == 2 * ONE_OP, "each late part's spend exactly once"
    assert sorted(s[1] for s in tm.load_strikes(turn)) == ["late-part a", "late-part b"]

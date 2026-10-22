"""An aggregate part still running when the op answers (plan 04, approved 2026-09-30).

The op answers at its deadline without waiting for the part. run_op keeps the turn's op slot while the part runs, so
the turn has one slot left (a second op may run, a third is BUSY) and the turn's cost reads partial ("an op was still
running"); when the part finishes, its own spend and failed models reach the turn exactly once and the slot is given
back. Deterministic: the slow part waits on an event the test sets only after the op has answered. Agents are faked;
no model is called. Every turn-memory call runs under one lock (SQLite shared cache; see test_turn_memory.py).
"""
from __future__ import annotations

import json
import threading
import time
from decimal import Decimal

import pytest

from chat_nextseek import call_scope, turn_spend
from NessieAI.ns import aggregate
from NessieAI.ns import turn_memory as tm
from NessieAI.ns.granular import OpBusyError
from NessieAI.tests.ns.test_turn_memory import serialize_turn_memory
from NessieAI.tests.ns.test_turn_ops import COST, Agents, _turn
from nextseek_api.assistant.models_db import CCTurn

pytestmark = pytest.mark.django_db(transaction=True)

ONE_OP = Decimal(str(round(COST, 6)))


def _row(lock, turn) -> CCTurn:
    with lock:
        return CCTurn.objects.get(pk=turn.pk)


def _wait_for(lock, turn, check, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if check(_row(lock, turn)):
            return True
        time.sleep(0.01)
    return False


def test_a_late_part_settles_once_after_the_answer_and_holds_the_slot_until_then(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn("How many mice?", vocabulary={"keywords": ["stored"]})
    lock = serialize_turn_memory(monkeypatch)
    spend_writes: list = []
    serialized_add = tm.add_spend

    def counted_add(t, usd, **kw):
        spend_writes.append(usd)
        return serialized_add(t, usd, **kw)
    monkeypatch.setattr(tm, "add_spend", counted_add)

    release, slow_running = threading.Event(), threading.Event()

    def on_graph(query):
        if query == "slow part":
            slow_running.set()
            assert release.wait(20)
            call_scope.current().mark_failed(("gcp", "late-model"), reason="timeout", agent="graph_agent")
    agents.on_graph = on_graph
    # The op answers 1 s after it starts, with whatever has finished; nothing is refused for lack of time.
    monkeypatch.setattr(aggregate, "op_deadline_s", lambda limit_s: 1.0)
    monkeypatch.setattr(aggregate, "MIN_REMAINING_S", 0.0)

    t0 = time.monotonic()
    out = agents.op("aggregate", "How many mice?", _row(lock, turn), parts=json.dumps(["fast part", "slow part"]))
    answered_in = time.monotonic() - t0

    assert answered_in < 5, "the answer did not wait for the slow part"
    assert slow_running.is_set()
    status = {part["question"]: part["status"] for part in out["parts"]}
    assert status["slow part"] == "timed_out" and status["fast part"] != "timed_out"
    row = _row(lock, turn)
    assert row.ops_in_flight == 1, "the slot is held while the part runs"
    assert row.ops_cost_usd == ONE_OP, "what finished is settled at the answer"
    assert spend_writes == [pytest.approx(COST, abs=1e-6)]
    assert tm.load_strikes(turn) == []

    # While it runs the turn has one slot left: a second op takes it, a third is BUSY.
    assert tm.take_op_slot(turn) is True
    with pytest.raises(OpBusyError):
        agents.op("graph", "mice by sex", _row(lock, turn))
    tm.release_op_slot(turn)

    release.set()
    assert _wait_for(lock, turn, lambda r: r.ops_in_flight == 0), "the last part gives the slot back"
    row = _row(lock, turn)
    assert row.ops_cost_usd == 2 * ONE_OP, "the late part's own spend reached the turn"
    assert len(spend_writes) == 2, "and exactly once"
    assert tm.load_strikes(turn) == [["gcp", "late-model", "timeout"]]
    assert row.ops_cost_partial is False


def test_an_op_with_nothing_late_gives_its_slot_back_at_once(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn("How many mice?", vocabulary={"keywords": ["stored"]})

    agents.op("aggregate", "How many mice?", turn, parts=json.dumps(["one part", "another part"]))

    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_in_flight == 0
    assert row.ops_cost_usd == Decimal(str(round(2 * COST, 6))), "both parts' spend, settled once with the op"


def test_recording_into_collects_inside_another_turns_collector():
    own = turn_spend.TurnSpend()
    with turn_spend.collecting() as outer:
        with turn_spend.recording_into(own):
            assert turn_spend.current() is own
        assert turn_spend.current() is outer
    assert turn_spend.current() is None

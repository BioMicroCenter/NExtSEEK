"""The turn's memory under real MySQL contention (plan 04, piece 3; spec rev 4; lane M only).

Each worker is a thread on its own connection (plan 03's ``race``), all released together, holding the same stale
in-memory row, with no lock of the test's own: what keeps every strike, plan and cent is MySQL's row lock
(select_for_update), F() and the conditional update. Anywhere else the ``mysql_lane`` marker skips these tests
(NessieAI/tests/ns/conftest.py). Run: lane_mysql NessieAI/tests/ns/test_turn_memory_mysql.py
"""
from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from NessieAI.ns import turn_memory as tm
from NessieAI.tests.ns.test_mysql_lane import race
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask

pytestmark = [pytest.mark.mysql_lane, pytest.mark.django_db(transaction=True)]

WORKERS = 8


def _turn() -> CCTurn:
    assert connection.vendor == "mysql", "lane_mysql only (PLAN-00)"
    user = get_user_model().objects.create_user(f"tmm-{uuid.uuid4().hex[:8]}", password="x")
    chat = ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=chat, user=user, query="How many mice?", status="running")
    return CCTurn.objects.create(task=task, user=user, chat=chat,
                                 pass_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest())


def test_concurrent_merge_strikes_lose_no_strike():
    turn = _turn()
    race(WORKERS, lambda i: tm.merge_strikes(turn, [["gcp", f"model-{i}", "timeout"]]))
    assert sorted(s[1] for s in tm.load_strikes(turn)) == sorted(f"model-{i}" for i in range(WORKERS))


def test_concurrent_store_plan_loses_no_plan():
    turn = _turn()
    race(WORKERS, lambda i: tm.store_plan(turn, f"question {i}", {"mode": "graph_query", "i": i}))
    for i in range(WORKERS):
        assert tm.get_plan(turn, f"question {i}") == {"mode": "graph_query", "i": i}
    assert len(CCTurn.objects.get(pk=turn.pk).plans) == WORKERS


def test_concurrent_store_plan_on_one_question_keeps_exactly_one():
    turn = _turn()
    race(WORKERS, lambda i: tm.store_plan(turn, "same question", {"mode": "graph_query", "i": i}))
    assert len(CCTurn.objects.get(pk=turn.pk).plans) == 1
    assert tm.get_plan(turn, "same question")["i"] in range(WORKERS)


def test_concurrent_add_spend_adds_to_the_exact_total():
    turn = _turn()

    def spend(i):
        for _ in range(25):
            tm.add_spend(turn, 0.001)
    race(WORKERS, spend)
    assert CCTurn.objects.get(pk=turn.pk).ops_cost_usd == Decimal("0.200000")


def test_concurrent_store_vocabulary_has_one_winner_and_it_is_what_is_stored():
    turn = _turn()
    won = race(WORKERS, lambda i: tm.store_vocabulary(turn, {"keywords": [f"worker-{i}"]}))
    assert won.count(True) == 1
    assert tm.get_vocabulary(turn) == {"keywords": [f"worker-{won.index(True)}"]}

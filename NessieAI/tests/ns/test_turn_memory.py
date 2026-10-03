"""The turn's shared memory on its CCTurn row (plan 04, piece 3): vocabulary, plans, failed models, op spend.

Every read goes to the database, never to the caller's in-memory row: ops of one turn run on different workers, each
holding its own stale copy. The vocabulary is stored once (a conditional update); plans and strikes are merged under
a row lock; spend is added with F(). The test database is SQLite in memory with a shared cache, where two connections
touching one table at the same moment fail at once instead of waiting, so the concurrency test runs each database call
under a lock and lets everything between the calls overlap: what it proves is that no write is computed from a stale
copy (MySQL's row lock is what serialises them on the boxes).
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from NessieAI.ns import turn_memory as tm
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask

pytestmark = pytest.mark.django_db(transaction=True)


def _turn(query: str = "How many mice?") -> CCTurn:
    user = get_user_model().objects.create_user(f"tm-{uuid.uuid4().hex[:8]}", password="x")
    chat = ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=chat, user=user, query=query, status="running")
    return CCTurn.objects.create(task=task, user=user, chat=chat,
                                 pass_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest())


def test_the_vocabulary_is_stored_once_and_read_back():
    turn = _turn()
    assert tm.get_vocabulary(turn) is None
    assert tm.store_vocabulary(turn, {"keywords": ["first"]}) is True
    assert tm.store_vocabulary(CCTurn.objects.get(pk=turn.pk), {"keywords": ["second"]}) is False
    assert tm.get_vocabulary(turn) == {"keywords": ["first"]}


def test_plans_are_keyed_by_the_exact_question_and_the_first_stays():
    turn = _turn()
    tm.store_plan(turn, "How many mice by sex?", {"mode": "graph_query", "n": 1})
    tm.store_plan(turn, "How many mice by sex?", {"mode": "graph_query", "n": 2})
    assert tm.get_plan(turn, "How many mice by sex?") == {"mode": "graph_query", "n": 1}
    assert tm.get_plan(turn, "How many  mice by sex?") is None, "whitespace is part of the key"
    assert tm.get_plan(turn, " How many mice by sex? ") is None
    assert tm.get_plan(turn, "how many mice by sex?") is None, "case is part of the key"
    assert tm.plan_key("a  b") == hashlib.sha256("a  b".encode("utf-8")).hexdigest()
    assert tm.plan_key("Ångström") == hashlib.sha256("Ångström".encode("utf-8")).hexdigest()


def test_whitespace_inside_a_quoted_literal_gives_a_different_plan_key():
    """A quoted value is matched as written ("wt  1" and "wt 1" are two sample titles), so its plan is its own."""
    two_spaces = 'Samples whose title is "wt  1"'
    one_space = 'Samples whose title is "wt 1"'
    assert tm.plan_key(two_spaces) != tm.plan_key(one_space)
    turn = _turn()
    tm.store_plan(turn, two_spaces, {"mode": "graph_query", "title": "wt  1"})
    assert tm.get_plan(turn, one_space) is None
    assert tm.get_plan(turn, two_spaces) == {"mode": "graph_query", "title": "wt  1"}


def test_the_entity_ops_question_match_still_collapses_whitespace():
    assert tm.normalize_question(" How many  mice?\n") == "How many mice?"


def test_a_failed_write_can_still_mark_the_cost_partial():
    turn = _turn()
    tm.mark_cost_partial(turn)
    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_cost_partial is True
    assert row.ops_cost_usd == Decimal("0")


def test_vocabulary_resolutions_are_counted():
    turn = _turn()
    assert tm.vocabulary_resolutions(turn) == 0
    tm.count_vocabulary_resolution(turn)
    tm.count_vocabulary_resolution(CCTurn.objects.get(pk=turn.pk))
    assert tm.vocabulary_resolutions(turn) == 2


def test_strikes_are_a_union_by_model_and_the_first_reason_stands():
    turn = _turn()
    tm.merge_strikes(turn, [["gcp", "gemini-3.5-flash", "timeout"], ["bad"]])
    tm.merge_strikes(turn, [["gcp", "gemini-3.5-flash", "unavailable"], ["bedrock", "sonnet", "unavailable"]])
    assert tm.load_strikes(turn) == [["gcp", "gemini-3.5-flash", "timeout"], ["bedrock", "sonnet", "unavailable"]]


def test_spend_is_added_and_a_partial_op_marks_the_turn():
    turn = _turn()
    tm.add_spend(turn, 0.012345678)
    tm.add_spend(turn, 0.0, partial=True)
    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_cost_usd == Decimal("0.012346")
    assert row.ops_cost_partial is True


def test_the_users_question_is_the_turns_own_task_query():
    assert tm.user_question(_turn("Find NHP samples")) == "Find NHP samples"


def serialize_turn_memory(monkeypatch) -> threading.Lock:
    """Run each turn_memory database call under one lock (SQLite shared cache; see the module docstring)."""
    lock = threading.Lock()
    for name in ("get_vocabulary", "store_vocabulary", "get_plan", "store_plan", "load_strikes", "merge_strikes",
                 "add_spend", "mark_cost_partial", "count_vocabulary_resolution", "vocabulary_resolutions",
                 "user_question", "take_op_slot", "release_op_slot"):
        real = getattr(tm, name)

        def serialized(*a, _real=real, **k):
            with lock:
                return _real(*a, **k)
        monkeypatch.setattr(tm, name, serialized)
    return lock


def test_two_workers_holding_stale_rows_lose_no_strike_plan_or_cost(monkeypatch):
    """Review focus 3 (memory half): two ops on two workers, each with its own copy loaded at op start."""
    turn = _turn()
    lock = serialize_turn_memory(monkeypatch)
    both_loaded = threading.Barrier(2, timeout=10)
    errors: list = []

    def worker(model: str, question: str, usd: float) -> None:
        try:
            with lock:
                stale = CCTurn.objects.get(pk=turn.pk)      # the worker's own copy, loaded at op start
            tm.load_strikes(stale)
            both_loaded.wait()
            tm.merge_strikes(stale, [["gcp", model, "timeout"]])
            tm.store_plan(stale, question, {"mode": "graph_query", "q": question})
            tm.add_spend(stale, usd)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=("model-a", "question a", 0.01)),
               threading.Thread(target=worker, args=("model-b", "question b", 0.02))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors, errors

    assert sorted(s[1] for s in tm.load_strikes(turn)) == ["model-a", "model-b"]
    assert tm.get_plan(turn, "question a") and tm.get_plan(turn, "question b")
    assert CCTurn.objects.get(pk=turn.pk).ops_cost_usd == Decimal("0.030000")

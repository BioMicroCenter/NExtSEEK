"""Which chat turn a reingest build or upload runs in, as the server sees it.

``nextseek_api.assistant.chat_turns`` reads the caller's own records: the QueryTask
that is still running and the TurnLedger row the CC turn wrote before its container
started. Nothing the agent sends is consulted, so it cannot claim a later turn.
"""
from __future__ import annotations

import datetime
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from nextseek_api.assistant import chat_turns
from nextseek_api.assistant.models_db import ChatSession, QueryTask, TurnLedger

pytestmark = pytest.mark.django_db


def _user():
    return get_user_model().objects.create_user(username=f"turns-{uuid.uuid4().hex[:8]}",
                                                password="x")


def _turn(user, *, session=None, number=1, route="container_cc", status="running"):
    session = session or ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=session, user=user, query="q", status=status)
    TurnLedger.objects.create(session=session, query_task=task, turn_number=number,
                              route=route, route_source="baml")
    return session, task


def test_the_one_running_cc_turn_is_named_by_chat_turn_number_and_task():
    user = _user()
    session, _ = _turn(user, number=1, status="completed")
    _, task = _turn(user, session=session, number=2)
    assert chat_turns.running_cc_turn(user) == {
        "session_id": str(session.session_id), "turn_number": 2, "task_id": str(task.task_id)}


def test_no_running_turn_is_none():
    user = _user()
    _turn(user, status="completed")
    assert chat_turns.running_cc_turn(user) is None


def test_a_running_nextseek_turn_is_not_a_cc_turn():
    user = _user()
    _turn(user, route="nextseek_query")
    assert chat_turns.running_cc_turn(user) is None


def test_two_running_cc_turns_are_ambiguous_and_refused():
    user = _user()
    _turn(user)
    _turn(user)
    assert chat_turns.running_cc_turn(user) is None


def test_another_users_running_turn_is_not_this_users():
    _turn(_user())
    assert chat_turns.running_cc_turn(_user()) is None


def test_a_turn_left_running_by_a_dead_worker_stops_counting():
    user = _user()
    _, stale = _turn(user)
    QueryTask.objects.filter(pk=stale.pk).update(
        created_at=timezone.now() - chat_turns.RUNNING_WINDOW - datetime.timedelta(minutes=1))
    session, task = _turn(user)
    assert chat_turns.running_cc_turn(user)["task_id"] == str(task.task_id)


def test_a_completed_turn_was_delivered():
    user = _user()
    _, task = _turn(user, status="completed")
    assert chat_turns.turn_delivered(user, str(task.task_id)) is True


@pytest.mark.parametrize("status", ["running", "error", "pending"])
def test_an_unfinished_or_failed_turn_was_not_delivered(status):
    user = _user()
    _, task = _turn(user, status=status)
    assert chat_turns.turn_delivered(user, str(task.task_id)) is False


def test_another_users_turn_and_a_bad_id_were_not_delivered():
    _, task = _turn(_user(), status="completed")
    user = _user()
    assert chat_turns.turn_delivered(user, str(task.task_id)) is False
    assert chat_turns.turn_delivered(user, "not-a-uuid") is False
    assert chat_turns.turn_delivered(user, None) is False

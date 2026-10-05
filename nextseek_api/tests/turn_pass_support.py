"""Shared set-up for the turn-pass tests: a user, their chat, a running task, an issued pass.

Not a test module (no ``test_`` prefix), so pytest does not collect it. Needs the database.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from django.contrib.auth import get_user_model

from nextseek_api.assistant import turn_pass
from nextseek_api.assistant.models_db import ChatSession, QueryTask

# A colon (splits a Basic pair), a quote and a backslash (escaped in JSON) and a non-ASCII letter (UTF-8 in the
# Basic header): the password has to come out of the held login exactly as it went in.
PASSWORD = 'pa:ss "w\\ord ü'


def make_user(username: str = "turn-user", *, password: str = PASSWORD, is_superuser: bool = False):
    return get_user_model().objects.create_user(
        username, password=password, is_superuser=is_superuser, is_staff=is_superuser,
    )


def make_turn(user=None, *, login=None, status: str = "running", deadline_in: float | None = 300.0, chat=None):
    """Issue a pass for a fresh running task; ``deadline_in`` seconds from now (None keeps the provisional expiry)."""
    user = user or make_user()
    chat = chat or ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=chat, user=user, query="q", status=status)
    turn, raw = turn_pass.issue_pass(
        task=task, chat=chat, user=user, login=login or (user.username, PASSWORD),
    )
    if deadline_in is not None:
        turn_pass.set_deadline(turn, time.time() + deadline_in)
    return turn, raw


def pass_header(raw: str) -> dict:
    return {"HTTP_AUTHORIZATION": f"NextseekTurn {raw}"}


def pass_request(turn, *, user=None) -> SimpleNamespace:
    """A request-shaped object authenticated by ``turn``'s pass, for calling helpers directly."""
    return SimpleNamespace(
        auth=turn, user=user or turn.user, META={}, COOKIES={}, session={}, method="GET",
    )

"""Which chat turn a reingest op call runs in, read from the server's own records.

A Container-CC op reaches Django with the user's own login and nothing else: the
agent names no chat and no turn, and nothing it could send is trusted for one. But
``NessieAI/cc/turn.py`` creates the turn's ``QueryTask`` as ``running`` and writes its
``TurnLedger`` row (chat, turn number, route) before the container starts, and the
task turns ``completed`` only when its reply has been delivered. So while an op call
is in flight, the caller's one running Container-CC turn is the turn it came from.

``build-upload-xlsx`` stamps each build with that turn, and ``upload-reingest``
uploads a build only from a later turn of the same chat whose build turn completed
(``NessieAI/ns/reingest/upload.py``). The engine never imports this module: the
REST layer hands both lookups in on the op session.
"""
from __future__ import annotations

import datetime
import uuid

from django.utils import timezone

from nextseek_api.assistant.models_db import QueryTask, TurnLedger

#: A task still "running" after this long was left behind by a dead worker, not a
#: live turn: a CC turn is capped at minutes (NEXTSEEK_CC_TIMEOUT_HARD_MAX). Ignoring
#: it keeps one crash from refusing every later upload as ambiguous.
RUNNING_WINDOW = datetime.timedelta(hours=1)


def running_cc_turn(user) -> dict | None:
    """The user's one Container-CC turn in flight, as {session_id, turn_number,
    task_id}, or None when there is none or more than one (which one called is then
    unknowable, so the caller refuses)."""
    rows = list(
        TurnLedger.objects.filter(
            route="container_cc", query_task__user=user, query_task__status="running",
            query_task__created_at__gte=timezone.now() - RUNNING_WINDOW,
        ).select_related("session", "query_task")[:2]
    )
    if len(rows) != 1:
        return None
    row = rows[0]
    return {"session_id": str(row.session.session_id), "turn_number": row.turn_number,
            "task_id": str(row.query_task.task_id)}


def turn_delivered(user, task_id) -> bool:
    """Did this user's turn ``task_id`` finish with its reply delivered?"""
    try:
        task_uuid = uuid.UUID(str(task_id))
    except (TypeError, ValueError):
        return False
    return QueryTask.objects.filter(task_id=task_uuid, user=user, status="completed").exists()

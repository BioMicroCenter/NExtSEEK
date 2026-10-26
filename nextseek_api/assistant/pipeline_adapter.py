from __future__ import annotations

import logging
from typing import Any, Callable

from django.utils import timezone

logger = logging.getLogger(__name__)

SendEvent = Callable[[str, dict[str, Any]], None]

# The statuses a terminal event may land on. query_error also lands on a completed row: a turn that answered and then
# could not save its session must read as failed, or the answer vanishes on reload (session c0062000, 2026-09-07).
# Neither lands on an error row, so a turn cut by a stopping container or reaped keeps the message it was given.
_LANDS_ON = {"query_complete": ("pending", "running"), "query_error": ("pending", "running", "completed")}

def make_db_event_callback(task_id: str, session_id: str) -> SendEvent:
    """Create a send_event callback that persists progress to QueryTask.

    Used by the async query endpoint (POST /assistant/query/async/).
    Each call appends to QueryTask.progress and updates status on
    terminal events (query_complete / query_error).
    """

    def send_event(event_type: str, data: dict[str, Any]) -> None:
        from nextseek_api.assistant.models_db import QueryTask

        if event_type in ("query_complete", "query_error"):
            data.setdefault("session_id", session_id)

        try:
            task = QueryTask.objects.get(task_id=task_id)
        except QueryTask.DoesNotExist:
            logger.error("QueryTask %s not found during event callback", task_id)
            return

        progress = task.progress or []
        entry = {"event": event_type, "data": data}
        progress.append(entry)
        task.progress = progress

        if event_type in _LANDS_ON:
            # One conditional UPDATE, race-safe unlike read-then-write.
            if QueryTask.objects.filter(pk=task.pk, status__in=_LANDS_ON[event_type]).update(
                status="completed" if event_type == "query_complete" else "error",
                result=data, progress=progress, updated_at=timezone.now(),
            ):
                return
            # The turn ended without it: status and result stay, and the event stays in progress for the trace under
            # a name no reader takes for the end of a turn.
            progress[-1] = {"event": "after_end", "data": entry}

        task.save(update_fields=["progress", "updated_at"])

    return send_event


def _emit_complete(
    send_event: SendEvent,
    reply: str,
    debug: dict[str, Any],
    bundle_id: int | None,
    *,
    artifacts: list[dict[str, Any]] | None = None,
) -> None:
    """Emit a ``query_complete`` event, optionally attaching artifacts."""
    data: dict[str, Any] = {"reply": reply, "debug": debug, "bundle_id": bundle_id}
    if artifacts:
        data["artifacts"] = artifacts
    send_event("query_complete", data)

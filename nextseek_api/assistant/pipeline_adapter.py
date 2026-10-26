from __future__ import annotations

import logging
from typing import Any, Callable

from django.utils import timezone

logger = logging.getLogger(__name__)

SendEvent = Callable[[str, dict[str, Any]], None]

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
        progress.append({"event": event_type, "data": data})
        task.progress = progress

        if event_type in ("query_complete", "query_error"):
            # One conditional UPDATE: a row already ended (a turn cut by a stopping container, or reaped) keeps its
            # status and result even when the turn's thread finishes late. Race-safe, unlike read-then-write.
            QueryTask.objects.filter(pk=task.pk, status__in=("pending", "running")).update(
                status="completed" if event_type == "query_complete" else "error",
                result=data, progress=progress, updated_at=timezone.now(),
            )
            return

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

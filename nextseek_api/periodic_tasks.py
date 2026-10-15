"""Housekeeping the Celery beat runs (and an operator can run by hand): end turns whose worker died, and clear
expired Django sessions."""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.core.management import call_command
from django.utils import timezone

logger = logging.getLogger(__name__)

STUCK_TURN_MESSAGE = "This answer stopped responding and was ended; please ask again."
_OPEN = ("pending", "running")


def reap_stale_query_tasks() -> int:
    """Mark pending/running QueryTasks not updated for ``STALE_TASK_SECONDS`` as errored; return how many.

    Every progress event saves the row (``make_db_event_callback``), so a live turn keeps ``updated_at`` fresh
    and only a turn whose worker died stops moving.
    """
    from nextseek_api.assistant import session_debug
    from nextseek_api.assistant.models_db import QueryTask
    from nextseek_api.assistant.pipeline_adapter import make_db_event_callback

    cutoff = timezone.now() - timedelta(seconds=session_debug.STALE_TASK_SECONDS)
    reaped = 0
    for task in QueryTask.objects.filter(status__in=_OPEN, updated_at__lt=cutoff).select_related("session"):
        # A turn can finish between the select and here: re-check so a result is never overwritten.
        if not QueryTask.objects.filter(pk=task.pk, status__in=_OPEN, updated_at__lt=cutoff).exists():
            continue
        make_db_event_callback(str(task.task_id), str(task.session.session_id))("query_error", {"error": STUCK_TURN_MESSAGE})
        reaped += 1
    if reaped:
        logger.warning("reaped %d stale query task(s)", reaped)
    return reaped


@shared_task(name="batch_upload.reap_stale_query_tasks")
def reap_stale_query_tasks_task():
    return reap_stale_query_tasks()


@shared_task(name="batch_upload.clear_expired_sessions")
def clear_expired_sessions():
    call_command("clearsessions")

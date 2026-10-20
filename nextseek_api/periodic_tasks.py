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


def _end_if_open(task, message: str, **still) -> bool:
    """Write ``message`` as the task's query_error unless it has finished (or, via ``still``, moved) meanwhile."""
    from nextseek_api.assistant.models_db import QueryTask
    from nextseek_api.assistant.pipeline_adapter import make_db_event_callback

    # A turn can finish between the select and here: re-check so a result is never overwritten.
    if not QueryTask.objects.filter(pk=task.pk, status__in=_OPEN, **still).exists():
        return False
    make_db_event_callback(str(task.task_id), str(task.session.session_id))("query_error", {"error": message})
    return True


def reap_stale_query_tasks() -> int:
    """Mark pending/running QueryTasks not updated for ``STALE_TASK_SECONDS`` as errored; return how many.

    Every progress event saves the row (``make_db_event_callback``), so a live turn keeps ``updated_at`` fresh
    and only a turn whose worker died stops moving.
    """
    from nextseek_api.assistant import session_debug
    from nextseek_api.assistant.models_db import QueryTask

    cutoff = timezone.now() - timedelta(seconds=session_debug.STALE_TASK_SECONDS)
    reaped = 0
    for task in QueryTask.objects.filter(status__in=_OPEN, updated_at__lt=cutoff).select_related("session"):
        if _end_if_open(task, STUCK_TURN_MESSAGE, updated_at__lt=cutoff):
            reaped += 1
    if reaped:
        logger.warning("reaped %d stale query task(s)", reaped)
    return reaped


def end_cut_turns(task_ids) -> int:
    """End the named turns a stopping web container could not finish; return how many were still open."""
    from nextseek_api.assistant.models_db import QueryTask
    from nextseek_api.assistant.turn_drain import CUT_MESSAGE

    cut = 0
    for task in QueryTask.objects.filter(task_id__in=list(task_ids), status__in=_OPEN).select_related("session"):
        if _end_if_open(task, CUT_MESSAGE):
            cut += 1
    return cut


@shared_task(name="batch_upload.reap_stale_query_tasks")
def reap_stale_query_tasks_task():
    return reap_stale_query_tasks()


@shared_task(name="batch_upload.clear_expired_sessions")
def clear_expired_sessions():
    call_command("clearsessions")


def purge_durable_state(now=None, backend=None, broker_url=None, uploads_dir=None, cancel_dir=None) -> dict:
    """Keep the durable upload-queue files from growing without bound (the beat runs this daily).

    Removes results older than the job TTL, consumed broker messages older than it, and saved sheets in uploads/
    older than it that no queued job still names. A sheet is never removed while a job could still use it: a job
    that started is over within the hard time limit, so only sheets older than the longer of the TTL and twice that
    limit are considered, and a sheet named in a still-queued message is kept.
    """
    import base64
    import json
    import os
    import time

    from sqlalchemy import create_engine, text

    from nextseek_api.batch_upload import celery_app as cm
    from nextseek_api.batch_upload.job_index import _get_ttl_seconds, state_dir

    now = now if now is not None else time.time()
    ttl = _get_ttl_seconds()
    out = {"results": 0, "messages": 0, "sheets": 0, "markers": 0}

    backend = backend if backend is not None else cm.app.backend
    from celery.backends.database.models import Task
    from datetime import datetime as _dt, timezone as _tz

    session = backend.ResultSession()
    try:
        out["results"] = session.query(Task).filter(
            Task.date_done < _dt.fromtimestamp(now - backend.expires.total_seconds(), _tz.utc).replace(tzinfo=None)
        ).count()
    finally:
        session.close()
    backend.cleanup()  # deletes results older than backend.expires (result_expires = the TTL)

    live_text = ""
    url = broker_url if broker_url is not None else cm.app.conf.broker_url
    if url.startswith("sqla+"):
        from datetime import datetime

        engine = create_engine(url[len("sqla+"):])
        try:
            with engine.begin() as conn:
                cutoff = datetime.fromtimestamp(now - ttl)
                out["messages"] = conn.execute(
                    text("DELETE FROM kombu_message WHERE visible = :no AND timestamp < :cutoff"),
                    {"no": False, "cutoff": cutoff},
                ).rowcount
                for (payload,) in conn.execute(text("SELECT payload FROM kombu_message WHERE visible = :yes"), {"yes": True}):
                    try:
                        live_text += base64.b64decode(json.loads(payload)["body"]).decode("utf-8", "replace")
                    except Exception:
                        live_text += str(payload)
        finally:
            engine.dispose()
    else:
        live_text = None  # a broker we cannot read: keep every sheet

    if live_text is not None:
        uploads = uploads_dir if uploads_dir is not None else os.path.join(state_dir(), "uploads")
        hard = int(cm.app.conf.task_time_limit or 7800)
        cutoff_s = now - max(ttl, 2 * hard)
        if os.path.isdir(uploads):
            for name in os.listdir(uploads):
                path = os.path.join(uploads, name)
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff_s and name not in live_text:
                    os.remove(path)
                    out["sheets"] += 1
    # Cancel markers older than the TTL: their job record is gone too.
    markers = cancel_dir if cancel_dir is not None else os.path.join(state_dir(), "cancel")
    if os.path.isdir(markers):
        for name in os.listdir(markers):
            path = os.path.join(markers, name)
            if os.path.isfile(path) and os.path.getmtime(path) < now - ttl:
                os.remove(path)
                out["markers"] += 1
    logger.info("purged durable queue state: %s", out)
    return out


@shared_task(name="batch_upload.purge_durable_state")
def purge_durable_state_task():
    purge_durable_state()

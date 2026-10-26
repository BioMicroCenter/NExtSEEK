"""Self-contained Celery app for batch upload.

Start the worker independently::

    celery -A nextseek_api.batch_upload.celery_app worker \
        --loglevel=info -Q batch_upload --concurrency=1
"""
from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")

import django  # noqa: E402

django.setup()

from celery import Celery  # noqa: E402

from nextseek_api.batch_upload.job_index import _get_ttl_seconds as _job_ttl_seconds  # noqa: E402
from nextseek_api.batch_upload.job_index import state_dir  # noqa: E402


def default_urls() -> tuple:
    """Broker and result-backend defaults: SQLite files on the durable state directory."""
    d = state_dir()
    return f"sqla+sqlite:///{d}/broker.sqlite", f"db+sqlite:///{d}/results.sqlite"


_DEFAULT_BROKER, _DEFAULT_BACKEND = default_urls()

app = Celery("batch_upload")

app.config_from_object(
    {
        "broker_url": os.environ.get("CELERY_BROKER_URL", _DEFAULT_BROKER),
        "result_backend": os.environ.get("CELERY_RESULT_BACKEND", _DEFAULT_BACKEND),
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        "task_routes": {
            "batch_upload.*": {"queue": "batch_upload"},
            # Celery's own housekeeping (celery.backend_cleanup) goes to a queue the one worker reads; on the
            # default "celery" queue nothing consumed it and results never expired.
            "celery.*": {"queue": "batch_upload"},
            # The paid summary sweep runs ~200 s on a backlog: its own queue and worker, so an upload never waits on it.
            "cc_assistant.sweep_cc_summaries": {"queue": "cc_summaries"},
            # Paper reingest stages run minutes against outside services: their own worker, so an upload never waits.
            "paper_reingest.*": {"queue": "paper_reingest"},
            "cc_assistant.*": {"queue": "batch_upload"},
            "attribute_mutations.*": {"queue": "attribute_mutations"},
        },
        # Results live as long as the job index does; the durable results file would otherwise only grow.
        "result_expires": _job_ttl_seconds(),
        # The periodic jobs return nothing anyone reads: do not write a result row every run.
        "task_annotations": {
            "cc_assistant.sweep_cc_summaries": {"ignore_result": True},
            "batch_upload.reap_stale_query_tasks": {"ignore_result": True},
            "batch_upload.clear_expired_sessions": {"ignore_result": True},
            "batch_upload.purge_durable_state": {"ignore_result": True},
            "batch_upload.sweep_chat_spill": {"ignore_result": True},
        },
        "beat_schedule": {
            # Each entry expires at its own interval, so runs missed while its worker is busy are dropped, not
            # piled up. The sweep has the cc_summaries worker; the other jobs share the batch_upload worker
            # (concurrency 1) with uploads on purpose, since the purge and reaper touch upload state.
            "cc-1c-summary-sweep": {
                "task": "cc_assistant.sweep_cc_summaries",
                "schedule": 300.0,
                "options": {"expires": 300.0},
            },
            "reap-stale-query-tasks": {
                "task": "batch_upload.reap_stale_query_tasks",
                "schedule": 300.0,
                "options": {"expires": 300.0},
            },
            "purge-durable-state": {
                "task": "batch_upload.purge_durable_state",
                "schedule": 86400.0,
                "options": {"expires": 86400.0},
            },
            "sweep-chat-spill": {
                "task": "batch_upload.sweep_chat_spill",
                "schedule": 86400.0,
                "options": {"expires": 86400.0},
            },
            "clear-expired-sessions": {
                "task": "batch_upload.clear_expired_sessions",
                "schedule": 86400.0,
                "options": {"expires": 86400.0},
            },
        },
        "task_soft_time_limit": int(os.environ.get("BATCH_TASK_SOFT_LIMIT", 7200)),
        "task_time_limit": int(os.environ.get("BATCH_TASK_HARD_LIMIT", 7800)),
    }
)

# Auto-discover tasks in this package
app.autodiscover_tasks(["nextseek_api.batch_upload", "nextseek_api.attributes"])

# Step 1c Celery sweep (registers cc_assistant.sweep_cc_summaries)
import nextseek_api.cc_assistant.cc_sweep  # noqa: F401, E402
import nextseek_api.cc_assistant.cc_upload_tasks  # noqa: F401, E402
import nextseek_api.paper_reingest.tasks  # noqa: F401, E402
import nextseek_api.periodic_tasks  # noqa: F401, E402

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

app = Celery("batch_upload")

app.config_from_object(
    {
        "broker_url": os.environ.get(
            "CELERY_BROKER_URL",
            "sqla+sqlite:////var/celery/broker.sqlite",
        ),
        "result_backend": os.environ.get(
            "CELERY_RESULT_BACKEND",
            "db+sqlite:////var/celery/results.sqlite",
        ),
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        "task_routes": {
            "batch_upload.*": {"queue": "batch_upload"},
            "cc_assistant.*": {"queue": "batch_upload"},
            "attribute_mutations.*": {"queue": "attribute_mutations"},
        },
        "beat_schedule": {
            # Each entry expires at its own interval, so runs missed while the one batch_upload worker is busy
            # are dropped, not piled up.
            # ponytail: these share the batch_upload worker (concurrency 1), so a long upload delays them; a
            # dedicated periodic queue and worker is the upgrade if that ever matters.
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
import nextseek_api.periodic_tasks  # noqa: F401, E402

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
        },
        "task_soft_time_limit": int(os.environ.get("BATCH_TASK_SOFT_LIMIT", 7200)),
        "task_time_limit": int(os.environ.get("BATCH_TASK_HARD_LIMIT", 7800)),
    }
)

# Auto-discover tasks in this package
app.autodiscover_tasks(["nextseek_api.batch_upload"])

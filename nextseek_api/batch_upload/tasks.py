"""Celery task wrapping the batch upload orchestrator."""
from __future__ import annotations

import logging
import os
import threading

from celery.exceptions import SoftTimeLimitExceeded

from .celery_app import app
from .config import BatchUploadConfig
from .orchestrator import run_batch_upload

log = logging.getLogger(__name__)


@app.task(bind=True, queue="batch_upload", name="batch_upload.run")
def run_batch_upload_task(
    self,
    xlsx_path: str,
    project_id: int,
    contributor_id: int,
    lababbv: str = "NA",
    config_overrides: dict = None,
):
    """Celery task entry point for batch upload.

    Receives xlsx_path, project_id, contributor_id from the DRF view.
    Updates Celery state with progress metadata.
    """
    config = BatchUploadConfig(**(config_overrides or {}))

    # Checkpoint directory under MEDIA_ROOT
    from django.conf import settings

    checkpoint_dir = os.path.join(
        getattr(settings, "MEDIA_ROOT", "/tmp"),
        "batch_upload_checkpoints",
        self.request.id or "unknown",
    )
    os.makedirs(checkpoint_dir, exist_ok=True)

    # Cancellation flag
    _stop_event = threading.Event()

    def should_stop() -> bool:
        return _stop_event.is_set()

    self.update_state(
        state="STARTED",
        meta={"stage": "INITIALIZING", "progress_pct": 0},
    )

    try:
        result = run_batch_upload(
            xlsx_path=xlsx_path,
            project_id=project_id,
            contributor_id=contributor_id,
            lababbv=lababbv,
            config=config,
            checkpoint_dir=checkpoint_dir,
            should_stop=should_stop,
        )

        self.update_state(
            state="SUCCESS",
            meta={
                "stage": "COMPLETE",
                "progress_pct": 100,
                "summary_path": result.get("summary_path"),
                "totals": result.get("totals"),
            },
        )

        return result

    except SoftTimeLimitExceeded:
        log.warning("Task %s hit soft time limit", self.request.id)
        _stop_event.set()
        return {
            "job_id": self.request.id,
            "summary_path": "",
            "totals": {"error": "Task timed out (soft limit)"},
            "errors": [],
        }
    except Exception as exc:
        log.exception("Task %s failed", self.request.id)
        self.update_state(
            state="FAILURE",
            meta={"stage": "ERROR", "error": str(exc)[:500]},
        )
        raise

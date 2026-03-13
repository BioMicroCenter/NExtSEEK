"""Celery task wrapping the batch upload orchestrator."""
from __future__ import annotations

import logging
import os
import threading

from celery.exceptions import SoftTimeLimitExceeded

from .celery_app import app
from .config import BatchUploadConfig
from .orchestrator import run_batch_upload_multi

log = logging.getLogger(__name__)


@app.task(bind=True, queue="batch_upload", name="batch_upload.run")
def run_batch_upload_task(
    self,
    xlsx_paths: list = None,
    xlsx_path: str = None,  # DEPRECATED: kept for in-flight message compat
    rows: list = None,
    project_id: int = None,
    contributor_id: int = None,
    lababbv: str = "NA",
    user_id: int = None,
    config_overrides: dict = None,
    neo4j_only: bool = False,
):
    """Celery task entry point for batch upload.

    Receives either rows (direct input) or xlsx_paths (file upload).
    xlsx_path (singular) is a deprecated alias for backwards compatibility.
    Updates Celery state with progress metadata.
    """
    if xlsx_path is not None and xlsx_paths is None:
        log.warning("xlsx_path (singular) is deprecated; use xlsx_paths or rows")
    if xlsx_paths is None:
        xlsx_paths = [xlsx_path] if xlsx_path else []
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
        meta={"stage": "INITIALIZING", "progress_pct": 0, "user_id": user_id},
    )

    try:
        result = run_batch_upload_multi(
            xlsx_paths=xlsx_paths,
            project_id=project_id,
            contributor_id=contributor_id,
            lababbv=lababbv,
            config=config,
            checkpoint_dir=checkpoint_dir,
            should_stop=should_stop,
            rows=rows,
            neo4j_only=neo4j_only,
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

        # Dispatch orphan resolution via apply_async (best-effort)
        identity_map = result.pop("_identity_map", {})
        parent_info_data = result.pop("_parent_info", {})
        if identity_map:
            try:
                resolve_orphans_task.apply_async(
                    kwargs={"identity_map": identity_map, "parent_info": parent_info_data},
                )
            except Exception as exc:
                log.warning("Failed to dispatch orphan resolution task: %s", exc)

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


@app.task(bind=True, queue="batch_upload", name="batch_upload.resolve_orphans")
def resolve_orphans_task(self, identity_map: dict, parent_info: dict):
    """Async task to resolve orphaned parent references after batch upload.

    Best-effort: failures are logged but do not affect the upload result.
    """
    if not identity_map:
        return {"resolved": 0}

    try:
        from neo4j import GraphDatabase

        from .config import Neo4jConfig
        from .db_engine import get_connection
        from .orphan_resolution import discover_orphans, resolve_orphans

        neo4j_config = Neo4jConfig.from_django_settings()
        if not neo4j_config.NEO4J_UPLOAD_ENABLED:
            log.info("Neo4j disabled, skipping orphan resolution")
            return {"resolved": 0}

        driver = GraphDatabase.driver(
            neo4j_config.URI,
            auth=(neo4j_config.NEO4J_USER, neo4j_config.PASSWORD),
        )
        try:
            orphans = discover_orphans(driver, neo4j_config.NEO4J_DB, identity_map)
            if not orphans:
                log.info("No orphans found")
                return {"resolved": 0}

            with get_connection() as conn:
                stats = resolve_orphans(
                    orphans=orphans,
                    parent_info=parent_info,
                    sql_conn=conn,
                    neo4j_driver=driver,
                    neo4j_database=neo4j_config.NEO4J_DB,
                )
            return stats
        finally:
            driver.close()

    except Exception as exc:
        log.warning("Orphan resolution failed (non-fatal): %s", exc, exc_info=True)
        return {"resolved": 0, "error": str(exc)}

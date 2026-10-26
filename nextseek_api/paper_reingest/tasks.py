"""Stage 1 as a Celery task on its own queue (paper_reingest, concurrency 1).

A source that is down pauses the run and retries after PAPER_REINGEST_PAUSE_S, up to
PAPER_REINGEST_PAUSE_RETRIES times; then the run fails at `acquire` (spec §10).
"""
from __future__ import annotations

import logging
import os

from NessieAI.ns.paper.acquire import acquire
from NessieAI.ns.paper.fetch import Fetcher, SourceUnavailable
from NessieAI.ns.paper.models import PaperArtifact
from NessieAI.ns.paper.store import load_artifact, save_artifact
from nextseek_api.paper_reingest.models_db import PaperReingestRun
from nextseek_api.paper_reingest.service import set_state

try:
    from nextseek_api.batch_upload.celery_app import app
except Exception:  # pragma: no cover
    from celery import shared_task as _shared

    def app_task(*a, **k):
        return _shared(*a, **k)
else:
    app_task = app.task

logger = logging.getLogger(__name__)



def _env_int(name: str, default: int) -> int:
    """A non-negative int from the environment; a bad value logs and falls back, since importing this
    module (celery_app does) must never fail on it."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        if value < 0:
            raise ValueError(raw)
    except ValueError:
        logger.warning("%s=%r is not a non-negative integer; using %d", name, raw, default)
        return default
    return value


PAUSE_COUNTDOWN_S = _env_int("PAPER_REINGEST_PAUSE_S", 600)
PAUSE_RETRIES = _env_int("PAPER_REINGEST_PAUSE_RETRIES", 12)


def run_acquire(run_id: str, *, retries_done: int, max_retries: int, fetcher_factory=Fetcher) -> str:
    run = PaperReingestRun.objects.get(run_id=run_id)
    if run.state not in ("queued", "paused"):
        return f"skipped:{run.state}"
    run.attempts += 1
    run.save(update_fields=["attempts", "updated_at"])
    set_state(run, "acquiring")
    try:
        paper = load_artifact(run_id, run.artifacts["paper"], PaperArtifact)
        evidence = acquire(paper, fetcher_factory())
        run.artifacts = {**run.artifacts, "evidence": save_artifact(run_id, "evidence", evidence)}
        run.save(update_fields=["artifacts", "updated_at"])
        set_state(run, "context")
    except SourceUnavailable as exc:
        if retries_done >= max_retries:
            set_state(run, "failed", failed_stage="acquire", reason=f"source unavailable: {exc}")
            return "failed"
        set_state(run, "paused", reason=f"waiting for {exc.host}: {exc.reason}")
        return "paused"
    except Exception as exc:
        logger.exception("paper reingest %s: acquire failed", run_id)
        set_state(run, "failed", failed_stage="acquire", reason=f"{type(exc).__name__}: {exc}")
        return "failed"
    return "context"


@app_task(bind=True, name="paper_reingest.acquire", max_retries=PAUSE_RETRIES)
def acquire_task(self, *, run_id: str):
    outcome = run_acquire(run_id, retries_done=self.request.retries, max_retries=self.max_retries)
    if outcome == "paused":
        raise self.retry(countdown=PAUSE_COUNTDOWN_S)
    return {"run_id": run_id, "outcome": outcome}

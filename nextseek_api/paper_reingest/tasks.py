"""Stage 1 as a Celery task on its own queue (paper_reingest, concurrency 1).

A source that is down pauses the run and retries after PAPER_REINGEST_PAUSE_S, up to
PAPER_REINGEST_PAUSE_RETRIES times; then the run fails at `acquire` (spec §10).
"""
from __future__ import annotations

import logging
import os

from django.db.models import F

from NessieAI.ns.paper.acquire import acquire
from NessieAI.ns.paper.context import build_context
from NessieAI.ns.paper.fetch import Fetcher, SourceUnavailable
from NessieAI.ns.paper.models import EvidenceArtifact, PaperArtifact
from NessieAI.ns.paper.store import load_artifact, save_artifact
from nextseek_api.paper_reingest import context_sources, service
from nextseek_api.paper_reingest.models_db import PaperReingestRun
from nextseek_api.paper_reingest.service import transition

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
    # Claim and finish with conditional UPDATEs: a second message for the run, a cancel or the boot sweep
    # that lands between them wins, and this call leaves the row as that writer left it.
    if not transition(run, ("queued", "paused"), "acquiring", attempts=F("attempts") + 1):
        return f"skipped:{run.state}"
    fields = {}
    try:
        paper = load_artifact(run_id, run.artifacts["paper"], PaperArtifact)
        evidence = acquire(paper, fetcher_factory())
        outcome = "context"
        fields["artifacts"] = {**run.artifacts, "evidence": save_artifact(run_id, "evidence", evidence)}
    except SourceUnavailable as exc:
        if retries_done >= max_retries:
            outcome = "failed"
            fields.update(failed_stage="acquire", reason=f"source unavailable: {exc}")
        else:
            outcome = "paused"
            fields["reason"] = f"waiting for {exc.host}: {exc.reason}"
    except Exception as exc:
        logger.exception("paper reingest %s: acquire failed", run_id)
        outcome = "failed"
        fields.update(failed_stage="acquire", reason=f"{type(exc).__name__}: {exc}")
    if not transition(run, ("acquiring",), outcome, **fields):
        logger.info("paper reingest %s: left %s, not %s (changed while stage 1 ran)", run_id, run.state, outcome)
        return f"skipped:{run.state}"
    return outcome


def after_acquire(run_id: str, outcome: str) -> None:
    """Stage 1 done: queue stage 2. The run row is committed (transition is one autocommit UPDATE)."""
    if outcome == "context":
        service.enqueue_context(run_id)


@app_task(bind=True, name="paper_reingest.acquire", max_retries=PAUSE_RETRIES)
def acquire_task(self, *, run_id: str):
    outcome = run_acquire(run_id, retries_done=self.request.retries, max_retries=self.max_retries)
    if outcome == "paused":
        raise self.retry(countdown=PAUSE_COUNTDOWN_S)
    after_acquire(run_id, outcome)
    return {"run_id": run_id, "outcome": outcome}


def run_context(run_id: str, *, retries_done: int, max_retries: int, seams_factory=None) -> str:
    """Stage 2 (spec §4): context.json. Claims `context`, leaves `curation_pending` for the web curation loop."""
    run = PaperReingestRun.objects.get(run_id=run_id)
    if not transition(run, ("context",), "contextualizing"):
        return f"skipped:{run.state}"
    fields = {}
    try:
        paper = load_artifact(run_id, run.artifacts["paper"], PaperArtifact)
        evidence = load_artifact(run_id, run.artifacts["evidence"], EvidenceArtifact)
        context = build_context(paper, evidence, (seams_factory or context_sources.context_seams)())
        outcome = "curation_pending"
        fields["artifacts"] = {**run.artifacts, "context": save_artifact(run_id, "context", context)}
    except context_sources.ContextSourceDown as exc:
        if retries_done >= max_retries:
            outcome = "failed"
            fields.update(failed_stage="context", reason=f"source unavailable: {exc}")
        else:
            outcome = "context"
            fields["reason"] = f"waiting for NExtSEEK's database: {exc}"
    except Exception as exc:
        logger.exception("paper reingest %s: context failed", run_id)
        outcome = "failed"
        fields.update(failed_stage="context", reason=f"{type(exc).__name__}: {exc}")
    if not transition(run, ("contextualizing",), outcome, **fields):
        logger.info("paper reingest %s: left %s, not %s (changed while stage 2 ran)", run_id, run.state, outcome)
        return f"skipped:{run.state}"
    return outcome


@app_task(bind=True, name="paper_reingest.context", max_retries=PAUSE_RETRIES)
def context_task(self, *, run_id: str):
    outcome = run_context(run_id, retries_done=self.request.retries, max_retries=self.max_retries)
    if outcome == "context":
        raise self.retry(countdown=PAUSE_COUNTDOWN_S)
    return {"run_id": run_id, "outcome": outcome}

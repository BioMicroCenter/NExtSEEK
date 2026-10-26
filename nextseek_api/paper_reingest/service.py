"""Paper reingest runs: create, find, move between states, enqueue stages."""
from __future__ import annotations

from NessieAI.ns.paper.models import PaperArtifact
from NessieAI.ns.paper.store import save_artifact
from nextseek_api.paper_reingest.models_db import PaperReingestRun

LIVE_STATES = ("queued", "acquiring", "context", "curating", "verifying", "building", "ready",
               "paused", "applying", "partially_applied")


def create_run(*, user, paper_input: str, paper: PaperArtifact, project_hint: int | None = None,
               chat_session_id: str = "") -> PaperReingestRun:
    ref = paper.ref
    run = PaperReingestRun(
        user=user, chat_session_id=chat_session_id, paper_input=paper_input[:1024],
        pmid=ref.pmid or "", pmcid=ref.pmcid or "", doi=ref.doi or "", title=(ref.title or "")[:1024],
        mode=paper.mode, project_hint=project_hint)
    # Write the artifact before the row exists: a failed write must not leave a live run with no paper.
    run.artifacts = {"paper": save_artifact(run.run_id, "paper", paper)}
    run.save()
    return run


def unfinished_run_for(user, doi: str | None, paper_input: str) -> PaperReingestRun | None:
    runs = PaperReingestRun.objects.filter(user=user, state__in=LIVE_STATES, superseded_by__isnull=True)
    return (runs.filter(doi=doi) if doi else runs.filter(paper_input=paper_input)).first()


def set_state(run: PaperReingestRun, state: str, *, failed_stage: str = "", reason: str = "") -> None:
    if state not in {s for s, _ in PaperReingestRun.STATES}:
        raise ValueError(f"unknown paper reingest state: {state!r}")
    run.state, run.failed_stage, run.reason = state, failed_stage, reason[:4000]
    run.save(update_fields=["state", "failed_stage", "reason", "updated_at"])


def enqueue_acquire(run: PaperReingestRun) -> None:
    raise NotImplementedError("wired in the next task")

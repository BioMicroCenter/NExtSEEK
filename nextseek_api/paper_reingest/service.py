"""Paper reingest runs: create, find, move between states, enqueue stages."""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Case, CharField, Value, When
from django.utils import timezone

from NessieAI.ns.paper import store
from NessieAI.ns.paper.models import PaperArtifact
from NessieAI.ns.paper.store import save_artifact
from nextseek_api.paper_reingest.models_db import LIVE_STATES, PaperReingestRun, paper_key


class RunAlreadyLive(Exception):
    """Another start of the same paper got its row in first; ``run`` is that live run."""

    def __init__(self, run: PaperReingestRun):
        super().__init__(f"a run for this paper is already in progress ({run.state}): {run.run_id}")
        self.run = run


def create_run(*, user, paper_input: str, paper: PaperArtifact, project_hint: int | None = None,
               chat_session_id: str = "") -> PaperReingestRun:
    ref = paper.ref
    run = PaperReingestRun(
        user=user, chat_session_id=chat_session_id, paper_input=paper_input[:1024],
        pmid=ref.pmid or "", pmcid=ref.pmcid or "", doi=ref.doi or "", title=(ref.title or "")[:1024],
        mode=paper.mode, project_hint=project_hint)
    # Write the artifact before the row exists: a failed write must not leave a live run with no paper.
    run.artifacts = {"paper": save_artifact(run.run_id, "paper", paper)}
    try:
        with transaction.atomic():  # a savepoint, so a refused row leaves the caller's transaction usable
            run.save()
    except IntegrityError:
        # The unique live_key: a start that read "no live run" a moment ago lost the race to another one.
        existing = unfinished_run_for(run.doi, run.paper_input)
        if existing is None:
            raise
        store.discard_run(run.run_id)
        raise RunAlreadyLive(existing) from None
    return run


def unfinished_run_for(doi: str | None, paper_input: str) -> PaperReingestRun | None:
    """The unfinished run of this paper, whoever started it (the DOI, else the input, as ``paper_key`` keys it)."""
    return PaperReingestRun.objects.filter(live_key=paper_key(doi, paper_input)).first()


def _check(state: str) -> None:
    if state not in {s for s, _ in PaperReingestRun.STATES}:
        raise ValueError(f"unknown paper reingest state: {state!r}")


def set_state(run: PaperReingestRun, state: str, *, failed_stage: str = "", reason: str = "") -> None:
    """Write ``state`` whatever the row holds now. A worker or the operator racing another writer uses
    ``transition`` instead."""
    _check(state)
    run.state, run.failed_stage, run.reason = state, failed_stage, reason[:4000]
    run.save(update_fields=["state", "failed_stage", "reason", "updated_at"])


def transition(run: PaperReingestRun, from_states, state: str, *, failed_stage: str = "", reason: str = "",
               **fields) -> bool:
    """Move the run to ``state`` only if the row is still in one of ``from_states``, in one filtered UPDATE, and
    say whether it moved. ``run`` is reloaded either way, so the caller sees what won. ``fields`` are written in
    the same UPDATE (``artifacts``, or ``attempts=F("attempts") + 1``)."""
    _check(state)
    # The key as models_db.PaperReingestRun.save would set it, decided by the row's own superseded_by.
    key = run.paper_key() if state in LIVE_STATES else None
    live_key = Case(When(superseded_by__isnull=True, then=Value(key)), default=Value(None), output_field=CharField())
    moved = PaperReingestRun.objects.filter(pk=run.pk, state__in=tuple(from_states)).update(
        state=state, failed_stage=failed_stage, reason=reason[:4000], updated_at=timezone.now(),
        live_key=live_key, **fields)
    run.refresh_from_db()
    return bool(moved)


def enqueue_acquire(run: PaperReingestRun) -> None:
    from nextseek_api.paper_reingest.tasks import acquire_task

    acquire_task.delay(run_id=run.run_id)


def enqueue_context(run_id: str) -> None:
    from nextseek_api.paper_reingest.tasks import context_task

    context_task.delay(run_id=run_id)

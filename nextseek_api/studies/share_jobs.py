"""Create, claim and advance share jobs (tool spec 16.4, 16.5; T38).

The discipline of ``nextseek_api/assay_registration/jobs.py`` (read its docstring): ``claim``, ``to_applying`` and
``to_queued`` are compare-and-set on ``state_version``; ``heartbeat``, ``finish_plan`` and ``finish_apply`` are scoped
to ``claim_owner`` and a state instead. Every writer that moves ``state_version`` moves it with
``models.F("state_version") + 1``, never a Python-side value.

States: ``planning`` (the worker plans it), ``planned``, ``plan_failed``, ``refused``; ``applying`` (the caller's apply
calls make its clones), ``queued`` (the worker runs its link unit), ``running``, ``applied``, ``apply_failed``,
``rolled_back`` (the studies tool's rollback undid its run: never applied again). A lease that expired makes a
``planning`` or ``running`` share claimable again: a crashed worker's share resumes, and the tool's journal makes its
unit idempotent. An ``apply_failed`` share whose error can never clear (``TERMINAL_ERRORS``) is not applied again
either: it needs a new share.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.db.models import Q
from django.utils import timezone

from nextseek_api.studies.models import ShareInput
from nextseek_api.studies.models_db import SampleShare

LEASE_SECONDS = 120
WORKER_ATTEMPTS = 3   # an error the worker did not expect, this many times on one share, ends it
PLAN_ENDS = frozenset({"planned", "plan_failed", "refused"})
APPLY_ENDS = frozenset({"applied", "apply_failed", "rolled_back"})
APPLICABLE = ("planned", "applying", "apply_failed")
TERMINAL_ERRORS = frozenset({"plan_stale", "destination_changed"})
_LEASED = ("planning", "running")


def create_share(inp: ShareInput, user) -> SampleShare:
    return SampleShare.objects.create(actor_django_user_id=user.id, actor_login=getattr(user, "username", ""),
                                      request=inp.model_dump(mode="json"))


def _claimable(now) -> Q:
    return (Q(state__in=SampleShare.CLAIMABLE, claim_owner__isnull=True)
            | Q(state__in=_LEASED, lease_expires_at__lt=now))


def next_claimable(*, planning_only: bool = False) -> SampleShare | None:
    found = SampleShare.objects.filter(_claimable(timezone.now()))
    if planning_only:
        found = found.filter(state="planning")
    return found.order_by("created_at", "id").first()


def claim(share: SampleShare, owner: str) -> bool:
    """Take a share to plan (``planning``) or to link (``queued`` becomes ``running``); an expired lease is claimable
    again. Compare-and-set on the ``state_version`` the caller read."""
    now = timezone.now()
    state = "planning" if share.state == "planning" else "running"
    updated = SampleShare.objects.filter(_claimable(now), pk=share.pk, state_version=share.state_version).update(
        claim_owner=owner, state=state, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
        last_heartbeat_at=now, updated_at=now, state_version=models.F("state_version") + 1)
    if updated == 1:
        share.refresh_from_db()
        return True
    return False


def heartbeat(share: SampleShare, owner: str) -> bool:
    now = timezone.now()
    return SampleShare.objects.filter(pk=share.pk, claim_owner=owner, state__in=_LEASED).update(
        last_heartbeat_at=now, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS), updated_at=now) == 1


def _release(**fields) -> dict:
    return {**fields, "claim_owner": None, "lease_expires_at": None, "last_heartbeat_at": None,
            "updated_at": timezone.now(), "state_version": models.F("state_version") + 1}


def finish_plan(share: SampleShare, owner: str, *, state: str, run_dir: str = "", plan_sha256: str = "",
                summary: dict | None = None, error: dict | None = None) -> bool:
    if state not in PLAN_ENDS:
        raise ValueError(f"unknown end of planning {state!r}")
    updated = SampleShare.objects.filter(pk=share.pk, claim_owner=owner, state="planning").update(
        **_release(state=state, run_dir=run_dir, plan_sha256=plan_sha256, summary=summary, error=error))
    return updated == 1


def applicable(share: SampleShare) -> bool:
    """The share may take an apply call: planned, applying, or apply_failed with an error that can clear."""
    if share.state not in APPLICABLE:
        return False
    return not (share.state == "apply_failed" and (share.error or {}).get("code") in TERMINAL_ERRORS)


def _move(share: SampleShare, *, to: str, **fields) -> bool:
    if not applicable(share):
        return False
    updated = SampleShare.objects.filter(pk=share.pk, state_version=share.state_version,
                                         state__in=APPLICABLE).update(
        state=to, updated_at=timezone.now(), state_version=models.F("state_version") + 1, **fields)
    if updated == 1:
        share.refresh_from_db()
        return True
    return False


def to_applying(share: SampleShare) -> bool:
    return _move(share, to="applying")


def to_queued(share: SampleShare) -> bool:
    return _move(share, to="queued")


def to_apply_failed(share: SampleShare, error: dict) -> bool:
    """An apply call's own refusal that ends the share (``destination_changed``): from an applicable state."""
    return _move(share, to="apply_failed", error=error)


def finish_apply(share: SampleShare, owner: str, *, state: str, receipt: dict | None = None,
                 error: dict | None = None) -> bool:
    if state not in APPLY_ENDS:
        raise ValueError(f"unknown end of applying {state!r}")
    updated = SampleShare.objects.filter(pk=share.pk, claim_owner=owner, state="running").update(
        **_release(state=state, receipt=receipt, error=error))
    return updated == 1


def worker_failed(share: SampleShare, owner: str, detail: str) -> str:
    """The worker raised on a share it holds: the error is counted on the row and the share keeps its lease, so another
    pass retries it once the lease runs out; at ``WORKER_ATTEMPTS`` the share ends ``plan_failed`` or
    ``apply_failed`` with the error. Returns the share's state after."""
    share.refresh_from_db()
    prior = share.error or {}
    attempts = (int(prior.get("attempts") or 0) if prior.get("code") == "worker_error" else 0) + 1
    error = {"code": "worker_error", "detail": detail[:500], "attempts": attempts}
    held = SampleShare.objects.filter(pk=share.pk, claim_owner=owner, state__in=_LEASED)
    if attempts < WORKER_ATTEMPTS:
        held.update(error=error, updated_at=timezone.now())
        return share.state
    end = "plan_failed" if share.state == "planning" else "apply_failed"
    return end if held.update(**_release(state=end, error=error)) == 1 else share.state


def back_to_queued(share: SampleShare, owner: str) -> bool:
    """The worker could not take the run lock: the share waits for the next pass, unchanged otherwise."""
    return SampleShare.objects.filter(pk=share.pk, claim_owner=owner, state="running").update(
        **_release(state="queued")) == 1


def end_rolled_back(run_dir: str) -> int:
    """The studies tool's rollback undid the share whose run directory is ``run_dir``: whatever its state, it ends
    ``rolled_back`` (its claim released), so neither an apply call nor the worker acts on it again."""
    if not run_dir:
        return 0
    return SampleShare.objects.filter(run_dir=run_dir).exclude(state="rolled_back").update(
        **_release(state="rolled_back"))

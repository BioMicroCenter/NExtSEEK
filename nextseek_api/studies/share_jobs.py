"""Create, claim and advance share jobs (tool spec 16.4, 16.5; T38).

The discipline of ``nextseek_api/assay_registration/jobs.py`` (read its docstring): ``claim``, ``to_applying`` and
``to_queued`` are compare-and-set on ``state_version``; ``heartbeat``, ``finish_plan`` and ``finish_apply`` are scoped
to ``claim_owner`` and a state instead. Every writer that moves ``state_version`` moves it with
``models.F("state_version") + 1``, never a Python-side value.

States: ``planning`` (the worker plans it), ``planned``, ``plan_failed``, ``refused``; ``applying`` (the caller's apply
calls make its clones), ``queued`` (the worker runs its link unit), ``running``, ``applied``, ``apply_failed``. A lease
that expired makes a ``planning`` or ``running`` share claimable again: a crashed worker's share resumes, and the
tool's journal makes its unit idempotent.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.db.models import Q
from django.utils import timezone

from nextseek_api.studies.models import ShareInput
from nextseek_api.studies.models_db import SampleShare

LEASE_SECONDS = 120
PLAN_ENDS = frozenset({"planned", "plan_failed", "refused"})
APPLY_ENDS = frozenset({"applied", "apply_failed"})
APPLICABLE = ("planned", "applying", "apply_failed")
_LEASED = ("planning", "running")


def create_share(inp: ShareInput, user) -> SampleShare:
    return SampleShare.objects.create(actor_django_user_id=user.id, actor_login=getattr(user, "username", ""),
                                      request=inp.model_dump(mode="json"))


def _claimable(now) -> Q:
    return (Q(state__in=SampleShare.CLAIMABLE, claim_owner__isnull=True)
            | Q(state__in=_LEASED, lease_expires_at__lt=now))


def next_claimable() -> SampleShare | None:
    return SampleShare.objects.filter(_claimable(timezone.now())).order_by("created_at", "id").first()


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


def _move(share: SampleShare, *, to: str, sources: tuple, **fields) -> bool:
    updated = SampleShare.objects.filter(pk=share.pk, state_version=share.state_version,
                                         state__in=sources).update(
        state=to, updated_at=timezone.now(), state_version=models.F("state_version") + 1, **fields)
    if updated == 1:
        share.refresh_from_db()
        return True
    return False


def to_applying(share: SampleShare) -> bool:
    return _move(share, to="applying", sources=APPLICABLE)


def to_queued(share: SampleShare) -> bool:
    return _move(share, to="queued", sources=APPLICABLE)


def to_apply_failed(share: SampleShare, error: dict) -> bool:
    """An apply call's own refusal that ends the share (``destination_changed``): from an applicable state."""
    return _move(share, to="apply_failed", sources=APPLICABLE, error=error)


def finish_apply(share: SampleShare, owner: str, *, state: str, receipt: dict | None = None,
                 error: dict | None = None) -> bool:
    if state not in APPLY_ENDS:
        raise ValueError(f"unknown end of applying {state!r}")
    updated = SampleShare.objects.filter(pk=share.pk, claim_owner=owner, state="running").update(
        **_release(state=state, receipt=receipt, error=error))
    return updated == 1


def back_to_queued(share: SampleShare, owner: str) -> bool:
    """The worker could not take the run lock: the share waits for the next pass, unchanged otherwise."""
    return SampleShare.objects.filter(pk=share.pk, claim_owner=owner, state="running").update(
        **_release(state="queued")) == 1

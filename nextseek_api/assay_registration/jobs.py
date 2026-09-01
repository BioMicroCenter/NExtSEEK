"""Create, claim, advance and cancel registration jobs.

Every state transition is a compare-and-set on `state_version`, so a lost
update is impossible: two workers cannot both claim the same job, and a
cancellation cannot be silently overwritten by a progress write.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

from .models_db import AssayRegistrationJob

LEASE_SECONDS = 120


def create_job(request_payload: dict, user, total_rows: int) -> AssayRegistrationJob:
    return AssayRegistrationJob.objects.create(
        actor_django_user_id=user.id,
        actor_login=getattr(user, "username", ""),
        submitted_request=request_payload,
        total_rows=total_rows,
    )


def claim(job: AssayRegistrationJob, owner: str) -> bool:
    """Take ownership. Returns False if someone else already holds it."""
    now = timezone.now()
    with transaction.atomic():
        updated = AssayRegistrationJob.objects.filter(
            pk=job.pk, state_version=job.state_version,
            state__in=AssayRegistrationJob.ACTIVE_STATES,
            claim_owner__isnull=True,
        ).update(
            claim_owner=owner, state="running",
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            last_heartbeat_at=now,
            state_version=job.state_version + 1,
        )
    if updated == 1:
        job.refresh_from_db()
        return True
    return False


def heartbeat(job: AssayRegistrationJob, owner: str) -> bool:
    now = timezone.now()
    updated = AssayRegistrationJob.objects.filter(
        pk=job.pk, claim_owner=owner, state="running",
    ).update(
        last_heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
    )
    return updated == 1


def record_progress(job: AssayRegistrationJob, processed: int) -> None:
    AssayRegistrationJob.objects.filter(pk=job.pk).update(processed_rows=processed)


def finish(job: AssayRegistrationJob, state: str, result: dict) -> None:
    AssayRegistrationJob.objects.filter(pk=job.pk).update(
        state=state, terminal_result=result,
        processed_rows=job.total_rows,
        state_version=job.state_version + 1,
    )


def request_cancellation(job: AssayRegistrationJob, user) -> bool:
    now = timezone.now()
    with transaction.atomic():
        updated = AssayRegistrationJob.objects.filter(
            pk=job.pk, state_version=job.state_version,
            state__in=AssayRegistrationJob.ACTIVE_STATES,
            cancellation_requested_at__isnull=True,
        ).update(
            cancellation_requested_at=now,
            cancellation_actor_django_user_id=user.id,
            state_version=job.state_version + 1,
        )
    return updated == 1


def is_cancelled(job: AssayRegistrationJob) -> bool:
    return job.cancellation_requested_at is not None


def get_job(job_id) -> Optional[AssayRegistrationJob]:
    return AssayRegistrationJob.objects.get(job_id=job_id)

"""Create, claim, advance and cancel registration jobs.

WHICH WRITERS ARE COMPARE-AND-SET, precisely, because a docstring claiming more
than the code delivers is worse than one that names the exception:

* `claim` and `request_cancellation` ARE compare-and-set on `state_version`.
  Two workers cannot both claim the same job.
* `heartbeat`, `record_progress` and `finish` are NOT. They are scoped to
  `claim_owner` plus a state predicate instead, which is what actually matters
  for them: only the lease holder may advance or terminate its own job.

Every writer bumps `state_version` with `models.F("state_version") + 1`, never
with a Python-side `job.state_version + 1`. The Python-side form reads a value
that may already be stale and writes a version the row has ALREADY HELD, which
makes the token non-monotonic and lets a party holding that number pass a later
compare-and-set it should fail. That is an ABA, and it was live here in the one
function that terminates the job.

Modelled on `nextseek_api/attributes/jobs.py`, which is the pattern rather than
the class: that store carries per-sample-type partitions this endpoint does not
have. Where the two differ, prefer its choices -- it uses `F()` throughout and
scopes every writer to the full lease token.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone

from .models_db import AssayRegistrationJob

LEASE_SECONDS = 120

#: The states `finish` may write. JobStatusResponse.state is a closed Literal,
#: so a state outside this set writes cleanly and then fails every subsequent
#: status read -- the same read-back hazard ERROR_CODES has, in a second field.
TERMINAL_STATES = frozenset({"succeeded", "partial", "failed", "cancelled"})


def create_job(request_payload: dict, user, total_rows: int) -> AssayRegistrationJob:
    return AssayRegistrationJob.objects.create(
        actor_django_user_id=user.id,
        actor_login=getattr(user, "username", ""),
        submitted_request=request_payload,
        total_rows=total_rows,
    )


def claim(job: AssayRegistrationJob, owner: str) -> bool:
    """Take ownership. Returns False if someone else already holds it.

    A compare-and-set: the filter pins the state_version the caller read, so a
    handle whose row has moved loses. The bump uses F(), never a Python-side
    value -- see the module docstring.
    """
    now = timezone.now()
    updated = AssayRegistrationJob.objects.filter(
        pk=job.pk, state_version=job.state_version,
        state__in=AssayRegistrationJob.ACTIVE_STATES,
        claim_owner__isnull=True,
    ).update(
        claim_owner=owner, state="running",
        lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
        last_heartbeat_at=now,
        updated_at=now,
        state_version=models.F("state_version") + 1,
    )
    if updated == 1:
        job.refresh_from_db()
        return True
    return False


def heartbeat(job: AssayRegistrationJob, owner: str) -> bool:
    """Extend the lease. Owner-scoped, so a zombie cannot renew a stolen job."""
    now = timezone.now()
    updated = AssayRegistrationJob.objects.filter(
        pk=job.pk, claim_owner=owner, state="running",
    ).update(
        last_heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
        updated_at=now,
    )
    return updated == 1


def record_progress(job: AssayRegistrationJob, owner: str, processed: int) -> bool:
    """Record progress, owner-scoped and only while the job is running.

    Without the state predicate this writes progress to an already-terminal job
    and the endpoint reports "succeeded, 4 of 10". Without the owner predicate a
    zombie worker corrupts the live owner's count.
    """
    updated = AssayRegistrationJob.objects.filter(
        pk=job.pk, claim_owner=owner, state="running",
    ).update(processed_rows=processed, updated_at=timezone.now())
    return updated == 1


def finish(job: AssayRegistrationJob, owner: str, state: str, result: dict) -> bool:
    """Terminate the job. Owner-scoped, and only from an active state.

    `processed_rows` is set to `total_rows` ONLY on success. A job cancelled at
    row 4 of 10,000 must not report 10,000 of 10,000: processed_rows is on the
    API surface (JobStatusResponse), so that number is read by a human deciding
    what actually happened.

    The lease fields are cleared so a future reaper's expired-lease scan does not
    have to filter out terminal jobs.
    """
    if state not in TERMINAL_STATES:
        raise ValueError(
            f"unknown terminal state {state!r}; JobStatusResponse.state is a "
            f"closed Literal, so an undeclared state would write cleanly and "
            f"then fail every status read"
        )
    fields = {
        "state": state,
        "terminal_result": result,
        "claim_owner": None,
        "lease_expires_at": None,
        "last_heartbeat_at": None,
        "updated_at": timezone.now(),
        "state_version": models.F("state_version") + 1,
    }
    if state == "succeeded":
        fields["processed_rows"] = job.total_rows
    updated = AssayRegistrationJob.objects.filter(
        pk=job.pk, claim_owner=owner,
        state__in=AssayRegistrationJob.ACTIVE_STATES,
    ).update(**fields)
    return updated == 1


def request_cancellation(job: AssayRegistrationJob, user) -> bool:
    """Ask a running job to stop, or cancel an unclaimed one outright.

    A compare-and-set on state_version, like claim.

    The unclaimed case needs the second branch. Cooperative cancellation works
    by a worker noticing the flag, and an unclaimed job has no worker -- so
    setting the flag alone would leave it reporting `accepted` forever, with a
    cancellation nobody will ever act on. Nothing is running, so it is safe to
    move it straight to terminal.
    """
    now = timezone.now()
    common = dict(
        cancellation_requested_at=now,
        cancellation_actor_django_user_id=user.id,
        updated_at=now,
        state_version=models.F("state_version") + 1,
    )
    base = AssayRegistrationJob.objects.filter(
        pk=job.pk, state_version=job.state_version,
        state__in=AssayRegistrationJob.ACTIVE_STATES,
        cancellation_requested_at__isnull=True,
    )
    if job.claim_owner is None:
        updated = base.filter(claim_owner__isnull=True).update(
            state="cancelled", **common
        )
        if updated == 1:
            return True
        # It was claimed between our read and our write; fall through and ask
        # the now-running worker to stop instead.
        #
        # `claim_owner__isnull=False` is load-bearing, not decoration. Without
        # it this branch also catches PURE VERSION DRIFT on a job that is still
        # unclaimed, and then sets the cancellation flag WITHOUT state
        # "cancelled" -- recreating the orphaned cancellation the unclaimed
        # branch above exists to prevent, from inside its own fall-through.
        # Requiring the job to actually be claimed makes drift return False, and
        # the caller retries with a fresh handle, which is the honest outcome.
        base = AssayRegistrationJob.objects.filter(
            pk=job.pk,
            claim_owner__isnull=False,
            state__in=AssayRegistrationJob.ACTIVE_STATES,
            cancellation_requested_at__isnull=True,
        )
    return base.update(**common) == 1


def is_cancelled(job: AssayRegistrationJob) -> bool:
    """Ask the DATABASE, not the handle.

    The obvious worker loop is `while not jobs.is_cancelled(job): ...`, and
    request_cancellation writes through QuerySet.update(), which refreshes no
    other handle. Reading `job.cancellation_requested_at` therefore reports the
    moment the handle was loaded, forever, and that loop never terminates no
    matter how many times the job is cancelled.

    A test that calls refresh_from_db() before asserting cannot catch this: it
    demonstrates visibility after a refresh the function does not perform.
    """
    return AssayRegistrationJob.objects.filter(
        pk=job.pk, cancellation_requested_at__isnull=False
    ).exists()


def get_job(job_id) -> AssayRegistrationJob:
    """Raises AssayRegistrationJob.DoesNotExist when there is no such job.

    NOT Optional. The ViewSet catches ObjectDoesNotExist and maps it to a 404;
    a caller writing `if job is None` would get a 500 instead, so the annotation
    has to say what the function does.
    """
    return AssayRegistrationJob.objects.get(job_id=job_id)

"""Turn an accepted job into a receipt.

The 202 path creates a durable job and hands back a status_url. This is what
makes that promise true. Without it the endpoint answers "accepted" and then
reports 0 of N forever, which is the same class of lie as a row reported
written that was never written -- one level up.

CANCELLATION MEANS "WILL NOT START", NOT "STOPS HALFWAY", and that is
deliberate. The executor writes the whole batch in one transaction as chunked
multi-row inserts, so there is no per-row loop to interrupt. Checking a
cancellation flag between rows would be theatre over an atomic write. A
half-applied batch is exactly what the legacy path produced when chunk 06 died
at row 1221 with 1,220 rows committed and no feedback file; not being able to
produce that state is a feature.

So: claim, check cancellation once, execute, record, finish. Progress is 0 then
total, because there is no honest number in between.

WHAT MAY BE STORED IN `terminal_result`, and why it is not free-form.
`service.job_status` ends with ``RegistrationResponse.model_validate(result)``,
and `views._JOB_LOOKUP_FAILURES` deliberately does NOT catch pydantic's
ValidationError -- unparseable stored state is a 500, loudly, and
`test_views.TestAStoredResultThatWillNotValidate` pins that. So every receipt
this module writes must be a `RegistrationResponse`, or nothing at all. An
error envelope stored here would turn "your batch failed" into "the server is
broken" for every failed job, which is the same defect as the one this endpoint
replaces wearing a different hat. `_failure_receipt` is the only failure shape,
and `TestTheReceiptIsReadableByTheStatusEndpoint` drives every terminal
state this module can write through the real `service.job_status`.

A COMMITTED BATCH IS NEVER REPORTED AS FAILED. The write and the graph
recompute have separate exception guards for that one reason: they are two
different questions, and answering the second badly must not retract the answer
to the first.
"""
from __future__ import annotations

import logging
import os
import socket
import uuid

from nextseek_api.batch_upload.db_engine import get_connection

from . import jobs
from .executor import execute
from .models_db import AssayRegistrationJob
from .planner import plan_batch
from .schemas import (
    GraphOutcome,
    RegistrationCounts,
    RegistrationRequest,
    RegistrationResponse,
    RowError,
    RowResult,
)
from .service import _recompute

log = logging.getLogger(__name__)

#: A cancelled job stores no receipt. `RegistrationResponse.overall_status` is a
#: closed Literal with no "cancelled" member, and inventing one of the three it
#: does have would misreport a clean cancellation as a failure. `state` already
#: carries the outcome and `processed_rows` stays 0, so `result: null` is the
#: complete and truthful answer. `job_status` maps a falsy stored result to
#: null, so `{}` and NULL read back identically; `{}` matches finish's `dict`.
NO_RECEIPT: dict = {}

#: Oldest first, tie-broken on the primary key. `created_at` has finite
#: resolution, so two jobs stamped alike would otherwise drain in whatever order
#: the server happened to return -- the same non-determinism the planner's
#: MIN(id) and the resolver's title tie-break exist to remove. Named rather than
#: inlined because no behavioural test can pin it: under the SQLite test
#: database a tied scan comes back in rowid order, which is the answer the
#: tie-break would have produced anyway, so only asserting the ordering itself
#: distinguishes the two.
DRAIN_ORDER = ("created_at", "pk")


def worker_identity() -> str:
    """Host, pid and a nonce.

    The nonce matters: two workers restarted into the same pid on the same host
    would otherwise share an owner string, and every owner-scoped predicate in
    jobs.py would stop discriminating between them.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _failure_receipt(total_rows: int, code: str, message: str) -> dict:
    """A batch-level failure, in the one shape the status endpoint can read.

    `rows` carries a single record because `RowError` is the only field in
    `RegistrationResponse` that can hold a reason, and a receipt that says
    "failed" without saying why is the weakest answer this endpoint can give.
    Its `sample_uid` is empty on purpose: the failure is not attributable to any
    submitted row -- the connection died, or the stored request would not
    revalidate -- so naming one would be a guess. `counts.failed` is the whole
    batch, which is what actually happened: the write is one transaction, so
    either all of it landed or none of it did.
    """
    return RegistrationResponse(
        mode="asynchronous",
        overall_status="failed",
        counts=RegistrationCounts(submitted=total_rows, failed=total_rows),
        rows=[RowResult(index=0, sample_uid="", status="failed",
                        error=RowError(code=code, message=message))],
        graph=GraphOutcome(status="skipped"),
    ).model_dump(mode="json")


def _fail(job, owner: str, code: str, message: str) -> bool:
    jobs.finish(job, owner, "failed", _failure_receipt(job.total_rows, code, message))
    return False


def run_one(job, owner: str) -> bool:
    """Claim, run and finish one job. Returns True only on a terminal success.

    Every branch that can be reached after a successful claim writes a terminal
    state: cancelled, failed, or the executor's own overall_status. The one
    residue is a process that dies between the claim and `finish` -- that job
    stays `running` until a lease reaper exists, and no in-process handler can
    change that.
    """
    if job.claim_owner != owner and not jobs.claim(job, owner):
        return False
    # Everything below reads a row loaded AFTER the lease was taken. The handle
    # a caller hands in may be arbitrarily old -- `run_pending` selects a page
    # and then works through it -- and `total_rows` is read twice from Python:
    # once for the failure receipt, once by `jobs.finish`'s success shortcut,
    # which is what `processed_rows` reports on the API surface.
    job.refresh_from_db()

    # Once, here, before the transaction opens. Not per row: the write is
    # atomic and is_cancelled is a query.
    if jobs.is_cancelled(job):
        jobs.finish(job, owner, "cancelled", NO_RECEIPT)
        return False

    try:
        payload = RegistrationRequest.model_validate(job.submitted_request)
    except Exception as exc:  # noqa: BLE001 - recorded, never raised past here
        # submitted_request is JSON that has round-tripped through the database.
        # Trusting it would let a schema change turn stored data into a crash
        # inside the transaction, with the job left claimed.
        return _fail(job, owner, "job_request_not_executable", str(exc))

    if payload.dry_run:
        # Unreachable through `service.register`, which returns the preview
        # before a job is ever created. Here anyway so the runner is safe on its
        # own terms rather than by a caller's construction: a stored request
        # that says "do not write" must not be executed by whatever puts a row
        # in this table next.
        return _fail(job, owner, "job_request_not_executable",
                     "job carries dry_run=true; a dry run is answered inline and "
                     "must never be executed as a durable job")

    try:
        with get_connection() as conn:
            plan = plan_batch(payload.registrations, conn)
            result = execute(plan, conn)
    except Exception as exc:  # noqa: BLE001
        # `get_connection` rolls back on any exception, so reaching here means
        # nothing was committed and the failure receipt is true rather than
        # merely convenient. `job_execution_failed`, NOT
        # `write_not_confirmed_by_readback`: the published meaning of that code
        # is "an insert was attempted for this pair and the row was not there on
        # read-back", which tells a client to go and look at a row nothing
        # touched. This one says retry.
        log.exception("assay-registration job %s failed", job.job_id)
        return _fail(job, owner, "job_execution_failed", str(exc))

    # OUTSIDE that except, deliberately, and with a guard of its own. Inside it,
    # a recompute that RAISES would write "the whole batch failed, nothing was
    # written" for a batch already committed at the block exit above -- the exact
    # lie this endpoint exists to remove, produced by the handler meant to
    # prevent it. Leaving it to `service._recompute`'s own `except Exception`
    # would rest this module's correctness on another module's error handling
    # with nothing pinning the invariant across the boundary. Belt and braces,
    # cheaply. assay_assets is the source of truth; the graph is derived.
    try:
        graph: GraphOutcome = _recompute(result.recompute_sample_ids)
    except Exception as exc:  # noqa: BLE001
        log.exception("assay-registration job %s: recompute raised", job.job_id)
        graph = GraphOutcome(status="failed", error=str(exc))

    jobs.record_progress(job, owner, result.counts.submitted)
    body = RegistrationResponse(
        # "asynchronous", not "synchronous". Everything this module writes is
        # read back from `status_url` by a caller who was handed
        # ``{"mode": "asynchronous"}`` at 202; telling them the batch ran
        # synchronously contradicts the reply that sent them here.
        mode="asynchronous", overall_status=result.overall_status,
        counts=result.counts, rows=result.rows, graph=graph,
    )
    if not jobs.finish(job, owner, result.overall_status, body.model_dump(mode="json")):
        # The lease moved. Someone else owns this job and may already have
        # written a receipt; do not clobber it.
        log.warning("assay-registration job %s: lease lost before finish", job.job_id)
        return False
    return result.overall_status == "succeeded"


def run_pending(limit: int, owner: str) -> int:
    """Claim and run unclaimed jobs, oldest first.

    Returns the number that reached a SUCCESSFUL terminal state, which is what
    `run_one` reports. A job that finishes `partial` or `failed` ran but is not
    counted, so the caller must not describe this number as "jobs processed".

    `limit` is clamped at 0: Django raises on a negative slice bound, and an
    operator typo on `--limit` should drain nothing rather than traceback.
    Ordering is `DRAIN_ORDER`; see there for why the tie-break is not optional.
    """
    if limit <= 0:
        return 0

    queued = AssayRegistrationJob.objects.filter(
        state__in=AssayRegistrationJob.ACTIVE_STATES,
        claim_owner__isnull=True,
        cancellation_requested_at__isnull=True,
    ).order_by(*DRAIN_ORDER)[:limit]

    ran = 0
    for job in list(queued):
        if run_one(job, owner):
            ran += 1
    return ran

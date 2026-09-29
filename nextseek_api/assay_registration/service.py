"""View-facing composition: plan, execute, recompute, and shape the response."""
from __future__ import annotations

import logging
from typing import Tuple

from django.conf import settings
from django.urls import reverse

from nextseek_api.batch_upload.db_engine import get_connection
from nextseek_api.graph_sync import hooks

from . import jobs
from .executor import execute, preview
from .planner import plan_batch
from .schemas import (
    ErrorResponse,
    GraphOutcome,
    JobStatusResponse,
    RegistrationCounts,
    RegistrationAcceptedResponse,
    RegistrationRequest,
    RegistrationResponse,
    RowError,
)

log = logging.getLogger(__name__)

_STATUS_FOR = {"succeeded": 200, "partial": 207, "failed": 409}


def _http_status(result) -> int:
    """Map an execution outcome to a status code, splitting `failed` in two.

    `overall_status == "failed"` means no row ended written or already_present,
    and there are two ways to arrive there that a status line must not conflate:

    * Every row was SKIPPED -- an unknown uid, an ambiguous assay title. That is
      the caller's data, the spec's "no executable rows at all", and 409 is
      right. The body carries every row's reason.
    * Rows were executable, were inserted, and were then absent on read-back
      (`write_not_confirmed_by_readback`). Nothing about the request was wrong.
      Answering 4xx attributes a server-side write failure to the caller and
      invites them to "fix" a request that was already correct. 500 says whose
      problem it is.

    A `partial` batch stays 207 even when some rows failed at read-back: rows
    DID write, and 207's meaning -- read the per-row report -- is unchanged.
    """
    if result.overall_status == "failed" and result.counts.failed:
        return 500
    return _STATUS_FOR[result.overall_status]


def _enqueue_graph_sync(changed_sample_ids) -> GraphOutcome:
    """Queue a graph sync for every sample whose assay links this batch changed.

    Registering a membership invalidates the assay labels on every DERIVED_FROM
    edge incident to that sample, in both directions. One rule owns those labels
    now (`nextseek_api/graph_sync/labels.py`), so this endpoint computes and
    writes none of them: it writes one `samples` outbox row per sample and
    returns, and the drain relabels the edges through `targeted.sync_samples`
    under the same rule every other writer gets. Nothing here waits on Neo4j,
    and that is why the outcome says `queued` rather than carrying a count:
    when this request answers, nothing has been recomputed yet.

    A failure here never invalidates the write. assay_assets is the source of
    truth and the labels are derived from it, so a row that could not be queued
    leaves a stale view, which is exactly the state the graph was in before the
    registration; rolling back a correct MySQL write to satisfy a derived store
    would be strictly worse. The loss is bounded rather than permanent: the
    nightly targeted sync compares each sample's source hash, which covers its
    assay links, against what its node was written from, and syncs what differs.

    The input is `ExecutionResult.recompute_sample_ids`, which is written UNION
    already_present, NOT the written-only set. Fed that, a re-POST of an
    identical batch would write nothing, hand this function an empty set and
    report `skipped` -- so the published recovery instruction, re-POST the
    batch, would repair nothing while reporting that there was nothing to
    repair. `skipped` means what it says: no row ended written or
    already_present, so no membership exists for a label to be derived from.
    """
    if not changed_sample_ids:
        return GraphOutcome(status="skipped")

    ids = sorted(int(sample_id) for sample_id in changed_sample_ids)
    # `hooks.enqueue` never raises: it logs a failure with its traceback,
    # counts it per kind, and reports False. Reporting `queued` over a row that
    # was never written would be the class of lie this endpoint exists to
    # remove, so what was lost is counted and named instead. `edges_recomputed`
    # stays 0: it is not a count of anything on either path.
    lost = [sample_id for sample_id in ids
            if not hooks.enqueue("samples", f"sample:{sample_id}")]
    if lost:
        return GraphOutcome(status="failed", error=(
            f"{len(lost)} of {len(ids)} samples could not be queued for a "
            "graph sync; the nightly targeted sync will find them"))
    return GraphOutcome(status="queued")


def register(payload: RegistrationRequest, request) -> Tuple[dict, int]:
    threshold = settings.ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD

    with get_connection() as conn:
        plan = plan_batch(payload.registrations, conn)

        if payload.dry_run:
            result = preview(plan)
            body = RegistrationResponse(
                mode="dry_run", overall_status=result.overall_status,
                counts=result.counts, rows=result.rows,
                graph=GraphOutcome(status="skipped"),
            )
            return body.model_dump(mode="json"), _http_status(result)

        if plan.execution_mode(threshold) == "asynchronous":
            job = jobs.create_job(payload.model_dump(mode="json"), request.user,
                                  plan.total_rows)
            body = RegistrationAcceptedResponse(
                mode="asynchronous", job_id=job.job_id,
                status_url=reverse("nextseek_api:assay-registrations-job",
                                   kwargs={"job_id": str(job.job_id)}),
                # `submitted` ONLY. Not preview(plan).counts, which labels every
                # row in to_write as "written" -- so a 25,765-row POST would
                # answer {"written": 25700} with zero rows written and, until a
                # worker claims the job, none ever written. That is precisely the
                # defect this endpoint replaces, reproduced on its own new path,
                # and worse than a stale number because nothing will ever make it
                # true. Every other bucket defaults to 0, which is the honest
                # value at 202. A caller who wants the projection asks for it by
                # name: that is what dry_run is for.
                counts=RegistrationCounts(submitted=plan.total_rows),
            )
            return body.model_dump(mode="json"), 202

        result = execute(plan, conn)

    # Outside the MySQL transaction, deliberately: a hook goes after the
    # writer's own commit, never inside it. See _enqueue_graph_sync.
    graph = _enqueue_graph_sync(result.recompute_sample_ids)

    body = RegistrationResponse(
        mode="synchronous", overall_status=result.overall_status,
        counts=result.counts, rows=result.rows, graph=graph,
    )
    return body.model_dump(mode="json"), _http_status(result)


def job_status(job_id) -> dict:
    job = jobs.get_job(job_id)
    result = job.terminal_result
    return JobStatusResponse(
        job_id=job.job_id, state=job.state,
        processed_rows=job.processed_rows, total_rows=job.total_rows,
        result=RegistrationResponse.model_validate(result) if result else None,
    ).model_dump(mode="json")


def cancel(job_id, user) -> Tuple[dict, int]:
    job = jobs.get_job(job_id)
    if not jobs.request_cancellation(job, user):
        body = ErrorResponse(errors=[RowError(
            code="not_cancellable",
            message="job is terminal or cancellation was already requested")])
        return body.model_dump(mode="json"), 409
    return job_status(job_id), 202

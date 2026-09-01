"""View-facing composition: plan, execute, recompute, and shape the response."""
from __future__ import annotations

import logging
from typing import Tuple

from django.conf import settings
from django.urls import reverse

from nextseek_api.batch_upload.db_engine import get_connection

from . import jobs
from .executor import execute, preview
from .graph import recompute_for_samples
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


def _neo4j():
    """Driver and database name, from settings. See graph.py trap 2.

    ``settings.NEO4J_DATABASE`` is a DICT here -- ``{"NAME", "URI", "AUTH"}`` --
    not a database name, and there are no NEO4J_URI / NEO4J_USER /
    NEO4J_PASSWORD settings at all. Every other Neo4j caller in this repo reads
    that one dict (services/sampletype_connections.py:193,
    batch_upload/scripts/backfill_shared_assays.py:165, which is the script
    graph.py was lifted from), so this does too. Inventing per-field names would
    have produced a driver pointed at bolt://neo4j:7687 with an empty password
    and a database named by a dict, and every symptom would have surfaced as a
    graph failure rather than as a configuration error.
    """
    from neo4j import GraphDatabase

    neo = settings.NEO4J_DATABASE
    return GraphDatabase.driver(neo["URI"], auth=neo["AUTH"]), neo["NAME"]


def _recompute(recompute_sample_ids) -> GraphOutcome:
    """Recompute derived labels. A failure here never invalidates the write.

    assay_assets is the source of truth and the edge labels are derived from
    it, so a failed recompute leaves a stale view, which is exactly the state
    the graph was in before the #118 backfill. Rolling back a correct MySQL
    write to satisfy a derived store would be strictly worse. Re-POSTing the
    identical batch repairs it: MySQL answers already_present for every row and
    the recompute runs again.

    That last sentence is TRUE ONLY BECAUSE the input is
    `ExecutionResult.recompute_sample_ids`, which is written UNION
    already_present. Fed the written-only set, a re-POST of an identical batch
    writes nothing, hands this function an empty set, and gets `skipped` -- so
    the published recovery instruction would repair nothing while reporting
    that there was nothing to repair. `skipped` now means what it says: no row
    ended written or already_present, so no membership exists for a label to be
    derived from.

    ``edges_recomputed`` counts RELATIONSHIPS, not edge pairs, and one pair can
    be carried by several DERIVED_FROM relationships (measured: 1,920 pairs,
    5,117 relationships, worst multiplicity 6). It is a report, not a
    reconciliation figure -- do not compare it against the number of rows
    written.
    """
    if not recompute_sample_ids:
        return GraphOutcome(status="skipped")
    try:
        driver, db_name = _neo4j()
        try:
            written = recompute_for_samples(recompute_sample_ids, driver, db_name)
        finally:
            driver.close()
        return GraphOutcome(status="succeeded", edges_recomputed=written)
    except Exception as exc:  # noqa: BLE001 - reported, never raised past here
        log.exception("assay-registration graph recompute failed")
        # `edges_recomputed` stays 0, and that is not a count of anything. The
        # read pass itself is what failed, so no honest figure exists; the error
        # string is the whole of what this outcome can say.
        return GraphOutcome(status="failed", error=str(exc))


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

    # Outside the MySQL transaction, deliberately. See _recompute.
    graph = _recompute(result.recompute_sample_ids)

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

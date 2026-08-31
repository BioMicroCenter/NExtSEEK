"""Write memberships, then report what the DATABASE holds, not what we asked for.

THE RECEIPT RULE. Every row's status is derived from a read-back inside the
transaction. `batch_insert_assay_assets` returns a count it computed from its
own pre-SELECT, and a count is not evidence that any particular row landed.
This is the exact shape of the defect being replaced: `DBtable.storeOneRecord`
(dmac/dbtable.py:109) sets ``status = 1`` and never updates it from the DB call
in either write branch, and `DBconnection.storeOneRecord` maps a failed insert
to ``primarykey = 0`` and returns normally, so a hard failure was reported as
success and the feedback workbook printed "successful:" for rows that never
wrote.

So: insert, then SELECT back the ids for exactly the pairs we intended. A pair
that is not there is `failed`, whatever the insert helper said. The caller
receives the primary key, so no second query is needed to reconcile.

DELETION IS UNREACHABLE FROM HERE. The only write call is
`batch_insert_assay_assets`, which contains no DELETE statement. A test asserts
that against both modules rather than trusting this comment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set

from nextseek_api.batch_upload.associations import batch_insert_assay_assets

from .planner import Plan, existing_membership_ids
from .schemas import RegistrationCounts, RowError, RowResult

#: Membership registration asserts belonging, not an input/output role.
#: The verified 25,765-row production write used 0 and ASSAY_ASSETS_DEFAULT
#: (seek/dbtable_assay_assets.py:26) uses 0. Note that
#: batch_upload/associations.py:126 defaults to 1 when passed None and
#: batch_upload/dag.py:167 computes 1/2 from lineage, so this must be explicit.
MEMBERSHIP_DIRECTION = 0


@dataclass
class ExecutionResult:
    rows: List[RowResult]
    counts: RegistrationCounts
    written_sample_ids: Set[int] = field(default_factory=set)
    overall_status: str = "succeeded"


def _tally(rows: List[RowResult], total: int) -> RegistrationCounts:
    counts = RegistrationCounts(submitted=total)
    for row in rows:
        setattr(counts, row.status, getattr(counts, row.status) + 1)
    return counts


def _overall(counts: RegistrationCounts) -> str:
    good = counts.written + counts.already_present
    bad = counts.skipped + counts.failed
    if good and bad:
        return "partial"
    if good:
        return "succeeded"
    return "failed"


def _finish(rows: List[RowResult], total: int, written_sample_ids: Set[int]) -> ExecutionResult:
    rows.sort(key=lambda r: r.index)
    counts = _tally(rows, total)
    return ExecutionResult(rows=rows, counts=counts,
                           written_sample_ids=written_sample_ids,
                           overall_status=_overall(counts))


def _base_row(resolved, status: str, **extra) -> RowResult:
    return RowResult(
        index=resolved.index, sample_uid=resolved.sample_uid, status=status,
        sample_id=resolved.sample_id, assay_id=resolved.assay_id,
        assay_title=resolved.assay_title, project_id=resolved.project_id,
        **extra,
    )


def preview(plan: Plan) -> ExecutionResult:
    """Report what a write WOULD do. Touches nothing."""
    rows: List[RowResult] = []
    write_indexes = {r.index for r in plan.to_write}
    for resolved in plan.resolved:
        if resolved.index in write_indexes:
            rows.append(_base_row(resolved, "written"))
        elif resolved.index in plan.already_present:
            rows.append(_base_row(resolved, "already_present",
                                  assay_assets_id=plan.already_present[resolved.index]))
        elif resolved.ok:
            # A duplicate of a pair already claimed earlier in the same request.
            rows.append(_base_row(resolved, "already_present"))
        else:
            rows.append(_base_row(resolved, "skipped", error=resolved.error))
    return _finish(rows, plan.total_rows, set())


def execute(plan: Plan, conn) -> ExecutionResult:
    """Insert, read back, and report from the read-back."""
    if plan.to_write:
        records = [
            (row.assay_id, row.sample_id, "Sample", MEMBERSHIP_DIRECTION, None, 1)
            for row in plan.to_write
        ]
        batch_insert_assay_assets(records, conn)

        pairs = sorted({(row.assay_id, row.sample_id) for row in plan.to_write})
        confirmed = existing_membership_ids(pairs, conn)
    else:
        confirmed = {}

    rows: List[RowResult] = []
    written_sample_ids: Set[int] = set()
    write_indexes = {r.index for r in plan.to_write}

    for resolved in plan.resolved:
        if resolved.index in plan.already_present:
            rows.append(_base_row(resolved, "already_present",
                                  assay_assets_id=plan.already_present[resolved.index]))
            continue
        if not resolved.ok:
            rows.append(_base_row(resolved, "skipped", error=resolved.error))
            continue

        key = (resolved.assay_id, resolved.sample_id)
        row_id = confirmed.get(key)
        if row_id is None:
            rows.append(_base_row(resolved, "failed", error=RowError(
                code="write_not_confirmed_by_readback",
                message="the insert reported no error but the row is not in "
                        "assay_assets; nothing was written for this pair",
                submitted_identifier=resolved.sample_uid,
            )))
            continue

        if resolved.index in write_indexes:
            rows.append(_base_row(resolved, "written", assay_assets_id=row_id))
            written_sample_ids.add(resolved.sample_id)
        else:
            # A duplicate pair inside the same request. It exists now because
            # its twin wrote it, so the honest status is already_present.
            rows.append(_base_row(resolved, "already_present", assay_assets_id=row_id))

    return _finish(rows, plan.total_rows, written_sample_ids)

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
    #: Sample ids whose derived graph labels this batch may have invalidated:
    #: every row that ended `written` OR `already_present`, not just the rows
    #: this request inserted. The name says what it holds, because the narrower
    #: written-only set silently disabled the documented repair path -- on a
    #: re-POST of an identical batch every pair is already_present, so nothing
    #: is written, so the recompute was skipped and an operator following the
    #: published recovery instruction was answered `graph: {"status":
    #: "skipped"}` and concluded the graph was fine.
    #:
    #: Widening costs nothing: RECOMPUTE_CYPHER is measured FLAT in batch size
    #: (0.40s for 3 edges, 0.50s for 20,000; see graph.py), and it is the same
    #: scope the caller named -- the rows they asked about.
    recompute_sample_ids: Set[int] = field(default_factory=set)
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


def _finish(rows: List[RowResult], total: int,
            recompute_sample_ids: Set[int]) -> ExecutionResult:
    rows.sort(key=lambda r: r.index)
    counts = _tally(rows, total)
    return ExecutionResult(rows=rows, counts=counts,
                           recompute_sample_ids=recompute_sample_ids,
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
    # Empty on purpose: a dry run writes nothing, so it invalidates no derived
    # label and has nothing to hand a recompute. `service.register` reports the
    # graph block as `skipped` on this path without consulting this field.
    return _finish(rows, plan.total_rows, set())


def execute(plan: Plan, conn) -> ExecutionResult:
    """Insert, read back, and report from the read-back.

    Requires an OPEN TRANSACTION, and enforces it rather than advertising it --
    the same move `ERROR_CODES` and `TERMINAL_STATES` already make. THE RECEIPT
    RULE above is a claim about the read-back seeing the insert atomically. On
    an autocommit connection the insert commits before the SELECT, and the two
    calls become separately visible to every other writer: the read-back stops
    being evidence about THIS transaction and degrades into "somebody has this
    pair", which is exactly the thing `DBtable.storeOneRecord` did wrong. Both
    current callers open a transaction, so this raises for nobody today; the
    point is that the property is preserved by construction instead of by
    caller convention.
    """
    if not conn.in_transaction():
        raise RuntimeError(
            "execute() requires an open transaction: the read-back that "
            "licenses every 'written' status is only evidence inside one. "
            "Call it under nextseek_api.batch_upload.db_engine.get_connection()."
        )

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
    recompute_sample_ids: Set[int] = set()
    write_indexes = {r.index for r in plan.to_write}

    for resolved in plan.resolved:
        if resolved.index in plan.already_present:
            rows.append(_base_row(resolved, "already_present",
                                  assay_assets_id=plan.already_present[resolved.index]))
            # Collected, NOT skipped. This is the whole re-POST repair path: on
            # an identical batch every pair lands here, `plan.to_write` is
            # empty, and a written-only set would leave the recompute with
            # nothing to do -- so the documented recovery would answer
            # `graph: {"status": "skipped"}` and repair nothing.
            recompute_sample_ids.add(resolved.sample_id)
            continue
        if not resolved.ok:
            rows.append(_base_row(resolved, "skipped", error=resolved.error))
            continue

        key = (resolved.assay_id, resolved.sample_id)
        row_id = confirmed.get(key)
        if row_id is None:
            # NOT collected. Nothing is in assay_assets for this pair, so there
            # is no membership for a recompute to derive a label from.
            rows.append(_base_row(resolved, "failed", error=RowError(
                code="write_not_confirmed_by_readback",
                message="the insert reported no error but the row is not in "
                        "assay_assets; nothing was written for this pair",
                submitted_identifier=resolved.sample_uid,
            )))
            continue

        recompute_sample_ids.add(resolved.sample_id)
        if resolved.index in write_indexes:
            rows.append(_base_row(resolved, "written", assay_assets_id=row_id))
        else:
            # A duplicate pair inside the same request. It exists now because
            # its twin wrote it, so the honest status is already_present.
            rows.append(_base_row(resolved, "already_present", assay_assets_id=row_id))

    return _finish(rows, plan.total_rows, recompute_sample_ids)

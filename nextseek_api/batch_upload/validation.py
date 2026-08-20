"""Sync validation flow: run the batch_upload pipeline through TRANSFORM only.

``run_validation_multi`` executes CONVERT -> (NAME_CHECK) -> UID_GEN ->
(DAG/LEVELS) -> PREFETCH -> TRANSFORM via the shared ``_run_pre_insert_stages``
and shapes the result into a :class:`ValidationResult`. No INSERT, no NEO4J,
no Celery, no summary CSV, and — via ``mutate_project_links=False`` — no
database side effects.

This module backs the ``POST /api/batch-upload/validate/`` endpoint.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional, Set

from .errors import ErrorCollector, ErrorType, Severity
from .models import (
    BatchUploadError,
    BatchUploadErrorGroup,
    BatchUploadTotals,
    ValidationResult,
)
from .orchestrator import _run_pre_insert_stages

log = logging.getLogger(__name__)

# The full set of validation stages a caller may request via ``checks``.
# ``structure`` (CONVERT + attribute-name check) always runs.
_ALL_CHECKS = ("structure", "name_check", "dag")

# Severities that do NOT make a sheet invalid. Everything else blocks —
# including any severity added to the enum later, so an unclassified entry
# fails closed rather than quietly passing validation. This mirrors
# ``classify_severity``, whose fallback for an unmapped ErrorType is ERROR.
#
# WARNING/INFO entries are still returned in ``errors[]``; they are advisory,
# not defects. The motivating case (issue #105) is a child row naming a parent
# that has not been uploaded yet: ``uid_gen`` deliberately preserves the token
# and ``orphan_resolution`` reconciles the edge on a later upload, so a forward
# reference is normal curation. Failing the sheet on it invites the curator to
# "fix" it by deleting the reference, destroying the lineage.
_NON_BLOCKING_SEVERITIES = frozenset({Severity.WARNING, Severity.INFO})


def _plural(n: int) -> str:
    """Return '' for a count of 1, 's' otherwise — for singular/plural phrasing."""
    return "" if n == 1 else "s"


def _project_errors(
    error_collector: ErrorCollector,
    generated_uids: Set[str],
) -> List[BatchUploadError]:
    """Project ErrorCollector entries to the public BatchUploadError shape.

    Carries ``row`` (0-based SAMPLES row index) and ``uid`` so the caller can
    locate the offending row. ``row_index`` of -1 (the pipeline's "no row"
    sentinel) maps to ``row=None``. ``uid`` is reported only when the row
    genuinely had one (update rows / pre-assigned UIDs): UIDs that UID_GEN
    auto-generated for new samples are throwaway in validate mode (lababbv is
    "NA", nothing is inserted), so they are suppressed — use ``row`` to locate
    new samples.

    Every collected error is returned — no cap. A validator that silently
    truncated its output would defeat its own purpose; callers that need a
    bounded view group via :func:`_group_errors`.
    """
    return [
        BatchUploadError(
            type=e.error_type.value,
            message=e.message,
            row=e.row_index if isinstance(e.row_index, int) and e.row_index >= 0 else None,
            uid=e.uid if (e.uid and e.uid not in generated_uids) else None,
        )
        for e in error_collector.all_errors()
    ]


def _has_blocking_error(error_collector: ErrorCollector) -> bool:
    """True if any collected entry carries a blocking severity.

    ``severity`` lives on the internal ``RowError`` and is dropped by
    :func:`_project_errors`, so this must read the collector, not the
    projection. Called before the projection precisely so the information is
    still available.
    """
    return any(
        e.severity not in _NON_BLOCKING_SEVERITIES
        for e in error_collector.all_errors()
    )


def _group_errors(errors: List[BatchUploadError]) -> List[BatchUploadErrorGroup]:
    """Collapse a flat error list into groups keyed by ``(type, message)``.

    A sheet where many rows fail the same check yields one flat error per row
    — visually identical entries differing only in ``row``. Grouping gives the
    UI one block per distinct failure. Groups are returned in first-seen order;
    each group's ``rows``/``uids`` are sorted and de-duplicated. This is purely
    a presentation aid — it does NOT drop any error (the flat ``errors`` list
    stays complete).
    """
    order: List[tuple] = []
    by_key: dict = {}
    for e in errors:
        key = (e.type, e.message)
        agg = by_key.get(key)
        if agg is None:
            agg = {"count": 0, "rows": set(), "uids": set()}
            by_key[key] = agg
            order.append(key)
        agg["count"] += 1
        if e.row is not None:
            agg["rows"].add(e.row)
        if e.uid:
            agg["uids"].add(e.uid)
    return [
        BatchUploadErrorGroup(
            type=key[0],
            message=key[1],
            count=by_key[key]["count"],
            rows=sorted(by_key[key]["rows"]),
            uids=sorted(by_key[key]["uids"]),
        )
        for key in order
    ]


def _finalize(
    totals: BatchUploadTotals,
    error_collector: ErrorCollector,
    warnings: dict,
    checks_run: List[str],
    checks_skipped: List[str],
    generated_uids: Set[str],
) -> ValidationResult:
    """Assemble a ValidationResult from pipeline output."""
    errors = _project_errors(error_collector, generated_uids)
    error_groups = _group_errors(errors)
    # `valid` is decided by severity, read off the collector before the
    # projection throws it away — NOT by ``errors`` being empty. ``errors``
    # stays complete and uncapped either way; a warning-only sheet is valid and
    # still reports every warning.
    #
    # ``totals.failed`` counts rows TRANSFORM could not build, and those block
    # regardless of severity: ``_classify_validation_error`` grades a couple of
    # genuine transform failures as WARNING/INFO (an unknown SampleType lands on
    # VALIDATION_SAMPLE_TYPE = WARNING, unparseable metadata on VALIDATION_JSON
    # = WARNING), and a sheet with rows that cannot be inserted is not valid.
    valid = (
        not _has_blocking_error(error_collector)
        and totals.error is None
        and totals.failed == 0
    )
    n_err, n_grp = len(errors), len(error_groups)
    if not errors:
        # Pipeline aborted before any row-level error was collected
        # (totals.error carries the reason — e.g. "No valid rows after CONVERT").
        summary = "No issues" if valid else "Validation could not complete"
    elif valid:
        # Non-blocking entries only. "No issues" would be a lie — the entries
        # are in ``errors[]`` and the UI renders them under an "Errors" heading
        # — but "N errors found" next to a PASSED verdict reads as a failure and
        # is what pushes a curator to delete a legitimate forward reference.
        # Name the count, and say plainly that none of it blocks.
        summary = (
            f"No blocking errors ({n_grp} distinct non-blocking "
            f"issue{_plural(n_grp)}, {n_err} total)"
            if n_grp < n_err
            else f"No blocking errors ({n_err} non-blocking issue{_plural(n_err)})"
        )
    elif n_grp < n_err:
        # Many errors collapse into fewer distinct (type, message) groups.
        summary = f"{n_grp} distinct error{_plural(n_grp)} ({n_err} total)"
    else:
        summary = f"{n_err} error{_plural(n_err)} found"
    return ValidationResult(
        job_id=None,
        summary_path=None,
        totals=totals,
        errors=errors,
        warnings=warnings or {},
        valid=valid,
        summary=summary,
        checks_run=checks_run,
        checks_skipped=checks_skipped,
        error_groups=error_groups,
    )


def run_validation_multi(
    xlsx_paths: List[str],
    project_id: int,
    contributor_id: int,
    lababbv: str = "NA",
    checks: Set[str] = frozenset({"structure"}),
    rows: Optional[List[dict]] = None,
) -> ValidationResult:
    """Validate a batch upload without inserting anything.

    Runs the pipeline through TRANSFORM and returns a :class:`ValidationResult`.
    The ``structure`` check (CONVERT + the attribute-name check in TRANSFORM)
    always runs; ``name_check`` and ``dag`` run only when present in ``checks``.

    Args:
        xlsx_paths: file path(s) to validate. Ignored when ``rows`` is given.
        project_id: SEEK project id (read-only here — no project mutation).
        contributor_id: authenticated user's person id.
        lababbv: lab abbreviation used for UID generation.
        checks: subset of {"structure", "name_check", "dag"}.
        rows: optional pre-parsed InputRowModel dicts (programmatic path),
            bypassing CONVERT — symmetric with ``run_batch_upload_multi``.
    """
    t0 = time.perf_counter()
    run_name_check = "name_check" in checks
    run_dag = "dag" in checks
    effective = {"structure"}
    if run_name_check:
        effective.add("name_check")
    if run_dag:
        effective.add("dag")
    checks_run = sorted(effective)
    checks_skipped = sorted(set(_ALL_CHECKS) - effective)

    log.info(
        "=== VALIDATION START === project_id=%d checks=%s", project_id, checks_run
    )

    pre = _run_pre_insert_stages(
        xlsx_paths=xlsx_paths,
        project_id=project_id,
        contributor_id=contributor_id,
        lababbv=lababbv,
        rows=rows,
        neo4j_only=False,
        should_stop=None,
        run_name_check=run_name_check,
        run_dag=run_dag,
        mutate_project_links=False,
    )
    elapsed = time.perf_counter() - t0
    error_collector = pre.error_collector

    if pre.aborted:
        totals = BatchUploadTotals(
            processed=0,
            success=0,
            skipped=0,
            failed=0,
            elapsed_s=elapsed,
            error=pre.abort_message or None,
            cancelled=(pre.abort_kind == "cancelled"),
        )
        return _finalize(
            totals, error_collector, {}, checks_run, checks_skipped,
            pre.generated_uids,
        )

    # Continue-state: the pipeline ran through TRANSFORM.
    insertable_count = len(pre.insertable_samples)
    skipped_count = len(pre.name_matched_outcomes)

    # ``dag`` check: surface dependency cycles as errors. The full pipeline only
    # emits CYCLE_UNRESOLVABLE during INSERT, which validation never reaches.
    if run_dag and pre.level_assignment and pre.level_assignment.cycle_uids:
        uid_to_row_idx = {
            r.UID: r.original_row_index
            for r in (pre.valid_rows or [])
            if r.UID is not None and r.original_row_index is not None
        }
        for uid in sorted(pre.level_assignment.cycle_uids):
            error_collector.add(
                row_index=uid_to_row_idx.get(uid, -1),
                uid=uid,
                error_type=ErrorType.CYCLE_UNRESOLVABLE,
                message="Sample is part of a dependency cycle",
            )

    totals = BatchUploadTotals(
        processed=insertable_count + skipped_count + pre.transform_errors,
        success=insertable_count,
        skipped=skipped_count,
        failed=pre.transform_errors,
        elapsed_s=elapsed,
        uids_generated=pre.uid_gen_report.get("uids_generated", 0),
    )
    return _finalize(
        totals, error_collector, pre.warnings, checks_run, checks_skipped,
        pre.generated_uids,
    )

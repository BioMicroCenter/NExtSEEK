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

from .errors import ErrorCollector, ErrorType
from .models import BatchUploadError, BatchUploadTotals, ValidationResult
from .orchestrator import _run_pre_insert_stages

log = logging.getLogger(__name__)

# The full set of validation stages a caller may request via ``checks``.
# ``structure`` (CONVERT + attribute-name check) always runs.
_ALL_CHECKS = ("structure", "name_check", "dag")


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
    valid = not errors and totals.error is None
    if valid:
        summary = "No issues"
    else:
        issue_count = len(errors) or (1 if totals.error else 0)
        summary = f"{issue_count} issue(s) found"
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

"""Main pipeline orchestrator: coordinates all 7 stages."""
from __future__ import annotations

import logging
import os
import time
import uuid as uuid_mod
from typing import Callable, Dict, List, Optional

from .config import BatchUploadConfig, Neo4jConfig
from .dag import build_relationships, compute_directions, detect_cycles
from .db_engine import get_connection
from .errors import ErrorCollector, ErrorType, _classify_validation_error
from .extract import stream_rows
from .insert import process_batches
from .models import (
    BatchResult,
    DirectionComputation,
    InputRowModel,
    InsertableSample,
    Metrics,
    RowOutcome,
)
from .neo4j_sync import upload_all
from .parallel import PARALLEL_THRESHOLD, process_batches_parallel
from .prefetch import prefetch_assay_ids, prefetch_project_sample_type_links, prefetch_sample_types
from .report import (
    ProgressReporter,
    build_row_summaries,
    write_summary_csv,
)
from .transform import build_insertable

log = logging.getLogger(__name__)


def run_batch_upload(
    xlsx_path: str,
    project_id: int,
    contributor_id: int,
    config: Optional[BatchUploadConfig] = None,
    checkpoint_dir: str = "",
    resume_uid: Optional[str] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    output_dir: Optional[str] = None,
) -> Dict:
    """Execute the full 7-stage batch upload pipeline.

    Stages: EXTRACT -> DAG -> PREFETCH -> TRANSFORM -> INSERT -> NEO4J -> REPORT

    Returns a result dict with job_id, summary_path, and totals.
    """
    if config is None:
        config = BatchUploadConfig()

    job_id = str(uuid_mod.uuid4())
    t0 = time.perf_counter()
    error_collector = ErrorCollector()
    neo4j_metrics: Optional[Metrics] = None

    # Determine output directory
    if not output_dir:
        from django.conf import settings
        output_dir = os.path.join(
            getattr(settings, "MEDIA_ROOT", "/tmp"),
            "batch_upload_reports",
        )
    os.makedirs(output_dir, exist_ok=True)
    summary_path = os.path.join(output_dir, f"summary_{job_id}.csv")

    log.info("=== BATCH UPLOAD START (job=%s) ===", job_id)
    log.info("Input: %s | project_id=%d | contributor_id=%d", xlsx_path, project_id, contributor_id)

    # ── Stage 1: EXTRACT ──────────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path)

    log.info("Stage 1/7: EXTRACT")
    valid_rows: List[InputRowModel] = []
    warnings: Dict[str, object] = {}
    try:
        unknown_columns, row_iter = stream_rows(xlsx_path, limit=config.limit)
        if unknown_columns:
            warnings["unknown_columns"] = unknown_columns
        for sr in row_iter:
            if sr.data is not None:
                valid_rows.append(sr.data)
            else:
                for err_msg in sr.errors:
                    error_collector.add(
                        row_index=sr.row_index,
                        uid=None,
                        error_type=_classify_validation_error(err_msg),
                        message=err_msg,
                    )
    except Exception as exc:
        log.exception("EXTRACT failed")
        error_collector.add(
            row_index=-1, uid=None, error_type=ErrorType.UNKNOWN,
            message=f"EXTRACT failed: {exc}",
        )
        return _error_result(job_id, summary_path, error_collector, str(exc))

    log.info("EXTRACT: %d valid rows, %d errors", len(valid_rows), len(error_collector.all_errors()))

    if not valid_rows:
        return _error_result(job_id, summary_path, error_collector, "No valid rows extracted")

    # ── Stage 2: DAG ──────────────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path)

    log.info("Stage 2/7: DAG")
    direction_computation = compute_directions(valid_rows)
    _parents_of, _children_of, edges = build_relationships(valid_rows)
    cycles = detect_cycles(edges)
    if cycles:
        log.warning("DAG: %d cycle(s) detected: %s", len(cycles), cycles[:5])

    # ── Stage 3: PREFETCH ─────────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path)

    log.info("Stage 3/7: PREFETCH")
    all_titles = list({r.SampleType for r in valid_rows})
    all_assay_ids = list({aid for r in valid_rows for aid in r.assay_ids})

    with get_connection() as conn:
        prefetch_sample_types(all_titles, conn)
        if all_assay_ids:
            prefetch_assay_ids(all_assay_ids, conn)
        # Gather unique sample type IDs for project linking
        st_map = prefetch_sample_types(all_titles, conn)
        st_ids = list(st_map.values())
        if project_id and st_ids:
            prefetch_project_sample_type_links(project_id, st_ids, conn)

    # ── Stage 4: TRANSFORM ────────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path)

    log.info("Stage 4/7: TRANSFORM")
    insertable_samples: List[InsertableSample] = []
    transform_errors = 0

    with get_connection() as conn:
        for row in valid_rows:
            try:
                sample, warnings = build_insertable(row, project_id, conn)
                insertable_samples.append(sample)
                for category, msgs in warnings.items():
                    for msg in msgs:
                        error_collector.add(
                            row_index=-1,
                            uid=row.UID,
                            error_type=ErrorType.VALIDATION_ASSAY,
                            message=msg,
                        )
            except Exception as exc:
                transform_errors += 1
                error_collector.add(
                    row_index=-1,
                    uid=row.UID,
                    error_type=_classify_validation_error(str(exc)),
                    message=f"TRANSFORM failed: {exc}",
                )

    log.info(
        "TRANSFORM: %d insertable, %d errors",
        len(insertable_samples),
        transform_errors,
    )

    if not insertable_samples:
        return _error_result(job_id, summary_path, error_collector, "No samples after transform")

    # ── Stage 5: INSERT ───────────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path)

    log.info("Stage 5/7: INSERT")
    reporter = ProgressReporter(total_rows=len(insertable_samples))

    use_parallel = (
        len(insertable_samples) >= PARALLEL_THRESHOLD
        and resume_uid is None
    )

    if use_parallel:
        log.info("INSERT: parallel mode (%d rows >= threshold %d)", len(insertable_samples), PARALLEL_THRESHOLD)
        batch_result = process_batches_parallel(
            rows=insertable_samples,
            project_id=project_id,
            contributor_id=contributor_id,
            config=config,
            direction_computation=direction_computation,
            error_collector=error_collector,
            reporter=reporter,
            should_stop=should_stop,
        )
    else:
        log.info("INSERT: sequential mode (%d rows)", len(insertable_samples))
        batch_result = process_batches(
            insertable_samples=insertable_samples,
            project_id=project_id,
            contributor_id=contributor_id,
            config=config,
            direction_computation=direction_computation,
            error_collector=error_collector,
            reporter=reporter,
            checkpoint_dir=checkpoint_dir,
            resume_uid=resume_uid,
            should_stop=should_stop,
        )

    log.info(
        "INSERT: inserted=%d, linked_project=%d, linked_assays=%d, permissions=%d",
        batch_result.inserted_count,
        batch_result.linked_project_count,
        batch_result.linked_assays_count,
        batch_result.permissions_inserted_count,
    )

    # ── Stage 6: NEO4J ────────────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path)

    log.info("Stage 6/7: NEO4J")
    neo4j_config = Neo4jConfig.from_django_settings()

    if neo4j_config.NEO4J_UPLOAD_ENABLED:
        try:
            with get_connection() as conn:
                neo4j_metrics = upload_all(
                    outcomes=batch_result.outcomes,
                    input_models=valid_rows,
                    direction_computation=direction_computation,
                    sql_conn=conn,
                    neo4j_config=neo4j_config,
                    insertable_samples=insertable_samples,
                )
            log.info("NEO4J: %s", neo4j_metrics)
        except Exception as exc:
            log.warning("NEO4J stage failed (non-fatal): %s", exc, exc_info=True)
    else:
        log.info("NEO4J: disabled (missing: %s)", neo4j_config.MISSING_KEYS)

    # ── Stage 7: REPORT ───────────────────────────────────────────────────
    log.info("Stage 7/7: REPORT")
    elapsed = time.perf_counter() - t0
    totals = {
        "processed": len(valid_rows),
        "success": sum(1 for o in batch_result.outcomes.values() if o.status == "success"),
        "skipped": sum(1 for o in batch_result.outcomes.values() if o.status == "skipped"),
        "failed": sum(1 for o in batch_result.outcomes.values() if o.status == "failed"),
        "elapsed_s": elapsed,
        "throughput_rps": batch_result.inserted_count / elapsed if elapsed > 0 else 0,
        "permissions_inserted": batch_result.permissions_inserted_count,
    }

    row_summaries = build_row_summaries(batch_result.outcomes, valid_rows, error_collector)
    write_summary_csv(summary_path, row_summaries, totals, neo4j_metrics, warnings=warnings or None)

    log.info(
        "=== BATCH UPLOAD COMPLETE (job=%s) === inserted=%d elapsed=%.1fs",
        job_id, batch_result.inserted_count, elapsed,
    )

    return {
        "job_id": job_id,
        "summary_path": summary_path,
        "totals": totals,
        "warnings": warnings,
        "errors": [
            {"type": e.error_type.value, "message": e.message}
            for e in error_collector.all_errors()[:50]
        ],
    }


def _cancelled_result(job_id: str, summary_path: str) -> Dict:
    return {
        "job_id": job_id,
        "summary_path": summary_path,
        "totals": {"processed": 0, "success": 0, "skipped": 0, "failed": 0, "cancelled": True},
        "errors": [],
    }


def _error_result(
    job_id: str, summary_path: str, error_collector: ErrorCollector, message: str
) -> Dict:
    return {
        "job_id": job_id,
        "summary_path": summary_path,
        "totals": {"processed": 0, "success": 0, "skipped": 0, "failed": 0, "error": message},
        "errors": [
            {"type": e.error_type.value, "message": e.message}
            for e in error_collector.all_errors()[:50]
        ],
    }

"""Main pipeline orchestrator: coordinates all 7 stages."""
from __future__ import annotations

import logging
import os
import time
import uuid as uuid_mod
from typing import Callable, Dict, List, Optional, Set

from .config import BatchUploadConfig, Neo4jConfig
from .dag import build_relationships, compute_directions, detect_cycles
from .db_engine import get_connection
from .errors import ErrorCollector, ErrorType, _classify_validation_error
from .extract import stream_rows
from .insert import process_batches
from .levels import LevelAssignment, cascade_failures, compute_levels
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
from .uid_gen import run_uid_gen

log = logging.getLogger(__name__)


def run_batch_upload(
    xlsx_path: str,
    project_id: int,
    contributor_id: int,
    lababbv: str = "NA",
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
    generated_uids: set = set()
    uid_gen_report: dict = {"uids_generated": 0}

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

    # ── Stage 1.25: NAME_CHECK ──────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path)

    log.info("Stage 1.25: NAME_CHECK")
    name_matched_outcomes: Dict[str, RowOutcome] = {}

    try:
        from .uid_gen import check_name_exists_in_db
        with get_connection() as conn:
            valid_rows, name_matches = check_name_exists_in_db(valid_rows, conn)

        if name_matches:
            if config.update_existing:
                # In upsert mode: re-add matched rows with existing UIDs for update processing
                log.info("NAME_CHECK: %d name matches, update_existing=True — will update", len(name_matches))
                # The matched rows were removed from valid_rows by check_name_exists_in_db
                # Store the matches — the INSERT stage will handle update via existing_samples
            else:
                # In default mode: skip matched rows
                log.info("NAME_CHECK: %d name matches, skipping as duplicates", len(name_matches))
                for identity, match_info in name_matches.items():
                    uid = match_info["uid"]
                    name_matched_outcomes[uid] = RowOutcome(
                        status="skipped",
                        reason="name_already_exists",
                        sample_id=match_info["sample_id"],
                    )
    except Exception as exc:
        log.exception("NAME_CHECK failed (non-fatal, continuing)")
        error_collector.add(
            row_index=-1, uid=None, error_type=ErrorType.UNKNOWN,
            message=f"NAME_CHECK failed: {exc}",
        )

    # ── Stage 1.5: UID_GEN ───────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path)

    log.info("Stage 1.5: UID_GEN")
    # Track which rows need UID generation so we can flag them in outcomes
    uids_before_gen = {r.UID for r in valid_rows if r.UID is not None}
    try:
        with get_connection() as conn:
            valid_rows, uid_gen_report = run_uid_gen(
                rows=valid_rows,
                lababbv=lababbv,
                conn=conn,
                error_collector=error_collector,
            )
        generated_uids = {r.UID for r in valid_rows if r.UID is not None} - uids_before_gen
        if uid_gen_report.get("uids_generated", 0) > 0:
            log.info("UID_GEN: generated %d UIDs", uid_gen_report["uids_generated"])
        if uid_gen_report.get("duplicates_removed", 0) > 0:
            log.info("UID_GEN: removed %d duplicates", uid_gen_report["duplicates_removed"])
    except Exception as exc:
        log.exception("UID_GEN failed")
        error_collector.add(
            row_index=-1, uid=None, error_type=ErrorType.UNKNOWN,
            message=f"UID_GEN failed: {exc}",
        )
        return _error_result(job_id, summary_path, error_collector, str(exc))

    if not valid_rows:
        return _error_result(job_id, summary_path, error_collector, "No valid rows after UID_GEN")

    # ── Stage 2: DAG ──────────────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path)

    log.info("Stage 2/7: DAG")
    direction_computation = compute_directions(valid_rows)
    parents_of, children_of, edges = build_relationships(valid_rows)
    cycles = detect_cycles(edges)
    if cycles:
        log.warning("DAG: %d cycle(s) detected: %s", len(cycles), cycles[:5])

    # ── Stage 2.5: LEVELS ────────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path)

    log.info("Stage 2.5: LEVELS")
    level_assignment = compute_levels(
        rows=valid_rows,
        parents_of=parents_of,
        children_of=children_of,
        cycles=cycles,
        conn_factory=get_connection,
    )
    log.info(
        "LEVELS: max_level=%d, orphans=%d, cycle_uids=%d, external_parents=%d",
        level_assignment.max_level,
        len(level_assignment.orphan_uids),
        len(level_assignment.cycle_uids),
        len(level_assignment.external_parents),
    )
    if level_assignment.orphan_uids:
        for uid in sorted(level_assignment.orphan_uids):
            error_collector.add(
                row_index=-1,
                uid=uid,
                error_type=ErrorType.VALIDATION_JSON,
                message="Parent UID(s) not found in batch or database; treating as root sample",
            )

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

    # ── Stage 5: INSERT (level-by-level) ────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path)

    log.info("Stage 5/7: INSERT")
    reporter = ProgressReporter(total_rows=len(insertable_samples))

    # Build uid -> InsertableSample lookup
    uid_to_insertable: Dict[str, InsertableSample] = {s.uuid: s for s in insertable_samples}

    # Seed cumulative_existing with external parents AND pre-existing spreadsheet UIDs
    cumulative_existing: Dict[str, int] = dict(level_assignment.external_parents)
    cumulative_existing.update(level_assignment.preexisting_uids)

    all_outcomes: Dict[str, RowOutcome] = {}
    failed_uids: Set[str] = set()
    total_inserted = 0
    total_project_links = 0
    total_assay_links = 0
    total_permissions = 0
    total_attempted: Set[str] = set()
    any_stopped_early = False

    for level_num in range(level_assignment.max_level + 1):
        if should_stop and should_stop():
            any_stopped_early = True
            break

        # Get UIDs at this level
        level_uids = [
            uid for uid, lvl in level_assignment.levels.items()
            if lvl == level_num
        ]
        level_uids_set = set(level_uids)

        # Cascade: fail any UIDs whose parent failed at a prior level
        cascaded = cascade_failures(failed_uids, children_of, level_uids_set)

        level_samples: List[InsertableSample] = []
        for uid in level_uids:
            if uid in cascaded:
                all_outcomes[uid] = RowOutcome(
                    status="failed",
                    reason=cascaded[uid],
                    topo_level=level_num,
                )
                failed_uids.add(uid)
                error_collector.add(
                    row_index=-1,
                    uid=uid,
                    error_type=ErrorType.PARENT_FAILED,
                    message=cascaded[uid],
                )
                continue
            sample = uid_to_insertable.get(uid)
            if sample:
                level_samples.append(sample)

        if not level_samples:
            log.info("INSERT level %d: 0 eligible samples", level_num)
            continue

        # Decide parallel vs sequential PER LEVEL
        use_parallel = (
            len(level_samples) >= PARALLEL_THRESHOLD
            and resume_uid is None
        )
        checkpoint_name_level = f"batch_checkpoint_L{level_num}.txt"

        if use_parallel:
            log.info(
                "INSERT level %d: parallel mode (%d rows)",
                level_num, len(level_samples),
            )
            level_result = process_batches_parallel(
                rows=level_samples,
                project_id=project_id,
                contributor_id=contributor_id,
                config=config,
                direction_computation=direction_computation,
                error_collector=error_collector,
                reporter=reporter,
                should_stop=should_stop,
                existing_samples=cumulative_existing,
                update_existing=config.update_existing,
            )
        else:
            log.info(
                "INSERT level %d: sequential mode (%d rows)",
                level_num, len(level_samples),
            )
            level_result = process_batches(
                insertable_samples=level_samples,
                project_id=project_id,
                contributor_id=contributor_id,
                config=config,
                direction_computation=direction_computation,
                error_collector=error_collector,
                reporter=reporter,
                checkpoint_dir=checkpoint_dir,
                checkpoint_name=checkpoint_name_level,
                resume_uid=resume_uid if level_num == 0 else None,
                should_stop=should_stop,
                existing_samples=cumulative_existing,
                update_existing=config.update_existing,
            )

        # Stamp topo_level on outcomes and track failures/successes
        for uid, outcome in level_result.outcomes.items():
            outcome.topo_level = level_num
            all_outcomes[uid] = outcome
            if outcome.status == "failed":
                failed_uids.add(uid)
            elif outcome.status in ("success", "skipped") and outcome.sample_id is not None:
                cumulative_existing[uid] = outcome.sample_id

        total_inserted += level_result.inserted_count
        total_project_links += level_result.linked_project_count
        total_assay_links += level_result.linked_assays_count
        total_permissions += level_result.permissions_inserted_count
        total_attempted.update(level_result.attempted_uids)
        if level_result.stopped_early:
            any_stopped_early = True

        log.info(
            "INSERT level %d: inserted=%d, failed=%d, cascaded=%d",
            level_num,
            level_result.inserted_count,
            sum(1 for o in level_result.outcomes.values() if o.status == "failed"),
            len(cascaded),
        )

    # Final pass: cycle UIDs (after all normal levels)
    if level_assignment.cycle_uids and not any_stopped_early:
        cycle_samples: List[InsertableSample] = []
        for uid in sorted(level_assignment.cycle_uids):
            if uid in failed_uids:
                continue
            # Check if all parents are now resolved
            uid_parents = parents_of.get(uid, set())
            all_resolved = all(
                p in cumulative_existing for p in uid_parents
            )
            if all_resolved and uid in uid_to_insertable:
                cycle_samples.append(uid_to_insertable[uid])
            else:
                all_outcomes[uid] = RowOutcome(
                    status="failed",
                    reason="cycle: parent(s) could not be resolved",
                    topo_level=-1,
                )
                failed_uids.add(uid)
                error_collector.add(
                    row_index=-1,
                    uid=uid,
                    error_type=ErrorType.CYCLE_UNRESOLVABLE,
                    message="cycle: parent(s) could not be resolved",
                )

        if cycle_samples:
            log.info("INSERT cycle pass: %d samples", len(cycle_samples))
            cycle_result = process_batches(
                insertable_samples=cycle_samples,
                project_id=project_id,
                contributor_id=contributor_id,
                config=config,
                direction_computation=direction_computation,
                error_collector=error_collector,
                reporter=reporter,
                checkpoint_dir=checkpoint_dir,
                checkpoint_name="batch_checkpoint_cycle.txt",
                should_stop=should_stop,
                existing_samples=cumulative_existing,
                update_existing=config.update_existing,
            )
            for uid, outcome in cycle_result.outcomes.items():
                outcome.topo_level = -1
                all_outcomes[uid] = outcome
                if outcome.status == "failed":
                    failed_uids.add(uid)
                elif outcome.status in ("success", "skipped") and outcome.sample_id is not None:
                    cumulative_existing[uid] = outcome.sample_id
            total_inserted += cycle_result.inserted_count
            total_project_links += cycle_result.linked_project_count
            total_assay_links += cycle_result.linked_assays_count
            total_permissions += cycle_result.permissions_inserted_count
            total_attempted.update(cycle_result.attempted_uids)

    # Merge name-matched outcomes (skipped duplicates from NAME_CHECK stage)
    all_outcomes.update(name_matched_outcomes)

    # Count updated samples from all levels
    total_updated = sum(
        1 for o in all_outcomes.values()
        if o.status == "success" and o.reason == "updated"
    )

    batch_result = BatchResult(
        inserted_count=total_inserted,
        linked_project_count=total_project_links,
        linked_assays_count=total_assay_links,
        outcomes=all_outcomes,
        attempted_uids=total_attempted,
        stopped_early=any_stopped_early,
        permissions_inserted_count=total_permissions,
        updated_count=total_updated,
    )

    log.info(
        "INSERT: inserted=%d, linked_project=%d, linked_assays=%d, permissions=%d",
        batch_result.inserted_count,
        batch_result.linked_project_count,
        batch_result.linked_assays_count,
        batch_result.permissions_inserted_count,
    )

    # Mark uid_generated flag on outcomes for rows that had UIDs auto-generated
    if generated_uids:
        for uid, outcome in batch_result.outcomes.items():
            if uid in generated_uids:
                outcome.uid_generated = True

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
        "uids_generated": uid_gen_report.get("uids_generated", 0),
        "updated": batch_result.updated_count,
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

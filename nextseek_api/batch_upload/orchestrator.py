"""Main pipeline orchestrator: coordinates all 7 stages."""
from __future__ import annotations

import logging
import os
import time
import uuid as uuid_mod
import json
from typing import Callable, Dict, List, Optional, Set, Tuple

from .config import BatchUploadConfig, Neo4jConfig
from .convert import merge_files
from .dag import build_relationships, compute_directions, detect_cycles
from .db_engine import get_connection
from .errors import ErrorCollector, ErrorType, _classify_validation_error
from .insert import load_existing_samples, process_batches
from .identity import extract_identity
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
from .prefetch import (
    prefetch_assay_ids,
    prefetch_project_sample_type_links,
    prefetch_sample_type_attributes,
    prefetch_sample_types,
)
from .report import (
    ProgressReporter,
    build_row_summaries,
    write_summary_csv,
)
from .transform import build_insertable
from .uid_gen import check_name_exists_in_db, run_uid_gen

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
    neo4j_only: bool = False,
) -> Dict:
    """Execute the batch upload pipeline for a single file (delegates to run_batch_upload_multi)."""
    return run_batch_upload_multi(
        xlsx_paths=[xlsx_path],
        project_id=project_id,
        contributor_id=contributor_id,
        lababbv=lababbv,
        config=config,
        checkpoint_dir=checkpoint_dir,
        resume_uid=resume_uid,
        should_stop=should_stop,
        output_dir=output_dir,
        neo4j_only=neo4j_only,
    )


def _build_neo4j_only_outcomes(
    insertable_samples: List[InsertableSample],
    error_collector: ErrorCollector,
) -> BatchResult:
    """Build synthetic outcomes for neo4j-only mode via batch DB lookup.

    Reuses load_existing_samples() — chunked WHERE IN, 1000/chunk.
    Outcomes built via dict comprehensions over set operations.
    """
    all_uids_list = [s.uuid.strip() for s in insertable_samples]

    with get_connection() as conn:
        uid_to_sample_id = load_existing_samples(all_uids_list, conn)

    # Normalize both sides: strip whitespace from DB-returned keys and input UIDs
    uid_to_sample_id = {uid.strip(): sid for uid, sid in uid_to_sample_id.items()}

    # Set operations — O(n), no per-row DB queries
    all_uids_set = set(all_uids_list)
    found_uids = set(uid_to_sample_id.keys())
    missing_uids = all_uids_set - found_uids

    # Batch-construct outcomes via dict comprehensions
    all_outcomes: Dict[str, RowOutcome] = {
        uid: RowOutcome(status="success", sample_id=uid_to_sample_id[uid])
        for uid in found_uids
    }
    all_outcomes.update({
        uid: RowOutcome(status="failed", reason="uid_not_found_in_db")
        for uid in missing_uids
    })

    if missing_uids:
        log.warning("INSERT (neo4j_only): %d UIDs not found in DB", len(missing_uids))
        for uid in missing_uids:
            error_collector.add(
                row_index=-1,
                uid=uid,
                error_type=ErrorType.VALIDATION_JSON,
                message="uid_not_found_in_db",
            )

    log.info(
        "INSERT (neo4j_only): resolved=%d, missing=%d",
        len(found_uids), len(missing_uids),
    )

    return BatchResult(
        inserted_count=0,
        linked_project_count=0,
        linked_assays_count=0,
        outcomes=all_outcomes,
        attempted_uids=all_uids_set,
        stopped_early=False,
        permissions_inserted_count=0,
        updated_count=0,
    )


def run_batch_upload_multi(
    xlsx_paths: List[str],
    project_id: int,
    contributor_id: int,
    lababbv: str = "NA",
    config: Optional[BatchUploadConfig] = None,
    checkpoint_dir: str = "",
    resume_uid: Optional[str] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    output_dir: Optional[str] = None,
    rows: Optional[List[dict]] = None,
    neo4j_only: bool = False,
) -> Dict:
    """Execute the full batch upload pipeline for one or more files.

    If ``rows`` is provided (list of InputRowModel dicts), Stage 0 (CONVERT) is
    skipped entirely.  Ontology validation only applies to file upload mode;
    in rows mode it is not performed (deferred).

    Stage 0 CONVERT (format detection + merge) -> NAME_CHECK -> UID_GEN -> DAG -> LEVELS
    -> PREFETCH -> TRANSFORM -> INSERT -> NEO4J -> REPORT.
    """
    if config is None:
        config = BatchUploadConfig()

    job_id = str(uuid_mod.uuid4())
    t0 = time.perf_counter()
    error_collector = ErrorCollector()
    neo4j_metrics: Optional[Metrics] = None
    generated_uids: set = set()
    uid_gen_report: dict = {"uids_generated": 0}
    uid_gen_failed_outcomes: Dict[str, RowOutcome] = {}

    if not output_dir:
        from django.conf import settings
        output_dir = os.path.join(
            getattr(settings, "MEDIA_ROOT", "/tmp"),
            "batch_upload_reports",
        )
    os.makedirs(output_dir, exist_ok=True)
    summary_path = os.path.join(output_dir, f"summary_{job_id}.csv")

    log.info("=== BATCH UPLOAD START (job=%s) ===", job_id)
    log.info("Input: %s | project_id=%d | contributor_id=%d", xlsx_paths, project_id, contributor_id)

    # ── Stage 0: CONVERT (format detection, multi-file merge, ontology) ───
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path, error_collector=error_collector)

    valid_rows: List[InputRowModel] = []
    warnings: Dict[str, object] = {}

    if rows is not None:
        log.info("Stage 0: CONVERT (skipped -- %d rows provided directly)", len(rows))
        from pydantic import TypeAdapter
        adapter = TypeAdapter(List[InputRowModel])
        valid_rows = adapter.validate_python(rows)
    else:
        log.info("Stage 0: CONVERT")
        try:
            converted = merge_files(xlsx_paths)
            if converted.ontology_result and not converted.ontology_result.is_valid:
                msg = (
                    f"Ontology validation failed: {len(converted.ontology_result.violations)} violation(s)."
                )
                for v in converted.ontology_result.violations[:10]:
                    error_collector.add(
                        row_index=v.row_index,
                        uid=None,
                        error_type=ErrorType.VALIDATION_JSON,
                        message=f"{v.attribute}={v.value!r} not in allowed terms for {v.vocab_name}",
                    )
                return _error_result(job_id, summary_path, error_collector, msg, valid_rows=valid_rows)
            valid_rows = converted.rows
            if converted.warnings:
                warnings["convert_warnings"] = converted.warnings
        except Exception as exc:
            log.exception("CONVERT failed")
            from pydantic import ValidationError as _PydanticValidationError
            if isinstance(exc, _PydanticValidationError):
                sub_errors = exc.errors()
                for sub in sub_errors:
                    loc = sub.get("loc", ())
                    row_idx = loc[0] if loc and isinstance(loc[0], int) else -1
                    field = ".".join(str(p) for p in loc[1:]) if len(loc) > 1 else ""
                    raw_msg = sub.get("msg", "")
                    cleaned = raw_msg.split(", ", 1)[1] if raw_msg.startswith("Value error, ") else raw_msg
                    message = (
                        f"INSTRUCTIONS row {row_idx}: {field}: {cleaned}"
                        if field
                        else f"INSTRUCTIONS row {row_idx}: {cleaned}"
                    )
                    error_collector.add(
                        row_index=row_idx if isinstance(row_idx, int) else -1,
                        uid=None,
                        error_type=ErrorType.VALIDATION_JSON,
                        message=message,
                    )
                summary = f"CONVERT failed: {len(sub_errors)} validation error(s) in INSTRUCTIONS sheet"
                return _error_result(job_id, summary_path, error_collector, summary, valid_rows=valid_rows)
            error_collector.add(
                row_index=-1, uid=None, error_type=ErrorType.UNKNOWN,
                message=f"CONVERT failed: {exc}",
            )
            return _error_result(job_id, summary_path, error_collector, str(exc), valid_rows=valid_rows)

    log.info("CONVERT: %d valid rows", len(valid_rows))

    if not valid_rows:
        return _error_result(job_id, summary_path, error_collector, "No valid rows after CONVERT", valid_rows=valid_rows)

    # ── Stage 1.25: NAME_CHECK ──────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path, valid_rows=valid_rows, error_collector=error_collector)

    name_matched_outcomes: Dict[str, RowOutcome] = {}

    if neo4j_only:
        log.info("Stage 1.25: NAME_CHECK (skipped -- neo4j_only mode)")
    else:
        log.info("Stage 1.25: NAME_CHECK")

        try:
            with get_connection() as conn:
                (
                    valid_rows,
                    name_matches,
                    name_matched_rows,
                    ambiguous_name_rows,
                ) = check_name_exists_in_db(valid_rows, conn)

            if name_matches:
                if config.update_existing:
                    # In upsert mode: re-inject matched rows with their existing UIDs
                    log.info("NAME_CHECK: %d name matches, update_existing=True — re-injecting with existing UIDs", len(name_matches))
                    identity_to_match = {
                        identity.lower(): match_info
                        for identity, match_info in name_matches.items()
                    }
                    for row, identity in name_matched_rows:
                        match_info = identity_to_match.get(identity.lower())
                        if match_info:
                            row.UID = match_info["uid"]
                            valid_rows.append(row)
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
            if ambiguous_name_rows:
                log.warning(
                    "NAME_CHECK: %d row(s) failed due to ambiguous name_identity matches",
                    len(ambiguous_name_rows),
                )
                for row, exc in ambiguous_name_rows:
                    error_collector.add(
                        row_index=getattr(row, "original_row_index", None) or exc.row_index,
                        uid=row.UID,
                        error_type=ErrorType.AMBIGUOUS_IDENTITY,
                        message=str(exc),
                    )
        except Exception as exc:
            log.exception("NAME_CHECK failed (non-fatal, continuing)")
            error_collector.add(
                row_index=-1, uid=None, error_type=ErrorType.UNKNOWN,
                message=f"NAME_CHECK failed: {exc}",
            )

    # ── Stage 1.5: UID_GEN ───────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path, valid_rows=valid_rows, error_collector=error_collector)

    if neo4j_only:
        log.info("Stage 1.5: UID_GEN (skipped -- neo4j_only mode)")
        # Batch-validate: all rows must have pre-assigned UIDs.
        # Boolean mask — single O(n) pass.
        all_uids = [r.UID for r in valid_rows]
        has_uid_mask = [uid is not None and uid.strip() != "" for uid in all_uids]
        if not all(has_uid_mask):
            # Partition in single O(n) pass via zip
            rows_with_uid = []
            for row, has in zip(valid_rows, has_uid_mask):
                if has:
                    rows_with_uid.append(row)
                else:
                    error_collector.add(
                        row_index=-1,
                        uid=None,
                        error_type=ErrorType.VALIDATION_JSON,
                        message="neo4j_only mode requires pre-assigned UID",
                    )
            log.warning(
                "UID_GEN (neo4j_only): %d rows lack UIDs, removed",
                len(valid_rows) - len(rows_with_uid),
            )
            valid_rows = rows_with_uid
    else:
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
            uid_gen_failed_outcomes = {
                failed["uid"]: RowOutcome(status="failed", reason=failed["reason"])
                for failed in uid_gen_report.get("failed_rows", [])
                if failed.get("uid")
            }
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
            return _error_result(job_id, summary_path, error_collector, str(exc), valid_rows=valid_rows)

    if not valid_rows:
        return _error_result(job_id, summary_path, error_collector, "No valid rows after UID_GEN", valid_rows=valid_rows)

    # ── Stage 2: DAG ──────────────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path, valid_rows=valid_rows, error_collector=error_collector)

    log.info("Stage 2/7: DAG")
    direction_computation = compute_directions(valid_rows)
    parents_of, children_of, edges = build_relationships(valid_rows)
    cycles = detect_cycles(edges)
    if cycles:
        log.warning("DAG: %d cycle(s) detected: %s", len(cycles), cycles[:5])

    # ── Stage 2.5: LEVELS ────────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path, valid_rows=valid_rows, error_collector=error_collector)

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
        return _cancelled_result(job_id, summary_path, valid_rows=valid_rows, error_collector=error_collector)

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
        if st_ids:
            prefetch_sample_type_attributes(st_ids, conn)
        if project_id and st_ids:
            prefetch_project_sample_type_links(project_id, st_ids, conn)

    # ── Stage 4: TRANSFORM ────────────────────────────────────────────────
    if should_stop and should_stop():
        return _cancelled_result(job_id, summary_path, valid_rows=valid_rows, error_collector=error_collector)

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
        return _error_result(job_id, summary_path, error_collector, "No samples after transform", valid_rows=valid_rows)

    # ── Stage 5: INSERT (level-by-level) ────────────────────────────────
    if neo4j_only:
        log.info("Stage 5/7: INSERT (skipped -- neo4j_only mode)")
        batch_result = _build_neo4j_only_outcomes(
            insertable_samples=insertable_samples,
            error_collector=error_collector,
        )
    else:
        if should_stop and should_stop():
            return _cancelled_result(job_id, summary_path, valid_rows=valid_rows, error_collector=error_collector)

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
        all_outcomes.update(uid_gen_failed_outcomes)

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
        return _cancelled_result(job_id, summary_path, valid_rows=valid_rows, error_collector=error_collector, outcomes=batch_result.outcomes)

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

    # ── Build identity map for orphan resolution ────────────────────────
    identity_map, parent_info = build_identity_map(valid_rows, batch_result.outcomes)

    # ── Stage 7: REPORT ───────────────────────────────────────────────────
    log.info("Stage 7/7: REPORT")
    elapsed = time.perf_counter() - t0
    success_count = sum(1 for o in batch_result.outcomes.values() if o.status == "success")
    skipped_count = sum(1 for o in batch_result.outcomes.values() if o.status == "skipped")
    failed_count = sum(1 for o in batch_result.outcomes.values() if o.status == "failed")
    totals = {
        "processed": success_count + skipped_count + failed_count,
        "success": success_count,
        "skipped": skipped_count,
        "failed": failed_count,
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
        "_identity_map": identity_map,
        "_parent_info": {uid: info for uid, info in parent_info.items()},
    }


def _ensure_summary_csv(
    summary_path: str,
    valid_rows: Optional[List[InputRowModel]] = None,
    outcomes: Optional[Dict[str, RowOutcome]] = None,
    error_collector: Optional[ErrorCollector] = None,
    extra_totals: Optional[dict] = None,
) -> None:
    """Write a fallback summary CSV if one has not been written yet."""
    if valid_rows is None:
        return
    if os.path.isfile(summary_path):
        return
    try:
        ec = error_collector or ErrorCollector()
        row_summaries = build_row_summaries(outcomes or {}, valid_rows, ec)
        totals: dict = {
            "processed": len(valid_rows),
            "success": 0, "skipped": 0, "failed": 0,
            "elapsed_s": 0.0, "throughput_rps": 0.0,
        }
        if extra_totals:
            totals.update(extra_totals)
        write_summary_csv(summary_path, row_summaries, totals)
    except Exception:
        log.warning("Failed to write fallback summary CSV", exc_info=True)


def _cancelled_result(
    job_id: str,
    summary_path: str,
    valid_rows: Optional[List[InputRowModel]] = None,
    error_collector: Optional[ErrorCollector] = None,
    outcomes: Optional[Dict[str, RowOutcome]] = None,
) -> Dict:
    _ensure_summary_csv(
        summary_path, valid_rows=valid_rows, outcomes=outcomes,
        error_collector=error_collector, extra_totals={"cancelled": True},
    )
    return {
        "job_id": job_id,
        "summary_path": summary_path,
        "totals": {"processed": 0, "success": 0, "skipped": 0, "failed": 0, "cancelled": True},
        "errors": [],
    }


def _error_result(
    job_id: str,
    summary_path: str,
    error_collector: ErrorCollector,
    message: str,
    valid_rows: Optional[List[InputRowModel]] = None,
    outcomes: Optional[Dict[str, RowOutcome]] = None,
) -> Dict:
    _ensure_summary_csv(
        summary_path, valid_rows=valid_rows, outcomes=outcomes,
        error_collector=error_collector, extra_totals={"error": message},
    )
    return {
        "job_id": job_id,
        "summary_path": summary_path,
        "totals": {"processed": 0, "success": 0, "skipped": 0, "failed": 0, "error": message},
        "errors": [
            {"type": e.error_type.value, "message": e.message}
            for e in error_collector.all_errors()[:50]
        ],
    }


# ── orphan resolution helpers ────────────────────────────────────────────

try:
    import orjson as _orjson

    def _json_loads_orch(s: str) -> dict:
        return _orjson.loads(s)
except ImportError:
    def _json_loads_orch(s: str) -> dict:  # type: ignore[misc]
        return json.loads(s)


def _parse_orphan_meta(model: InputRowModel) -> Optional[dict]:
    try:
        meta = _json_loads_orch(model.json_metadata) if model.json_metadata else {}
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    return meta


def build_identity_map(
    input_models: List[InputRowModel],
    outcomes: Dict[str, RowOutcome],
) -> Tuple[Dict[str, str], Dict[str, dict]]:
    """Build identity->UID map and parent_info from successful inserts.

    Returns:
        identity_map: {identity: UID} where identity is Name or File_PrimaryData
        parent_info: {UID: {"sample_id": int, "uuid": str}}
    """
    identity_map: Dict[str, str] = {}
    parent_info: Dict[str, dict] = {}

    for model in input_models:
        uid = model.UID
        if uid is None:
            continue
        outcome = outcomes.get(uid)
        if outcome is None or outcome.sample_id is None:
            continue

        meta = _parse_orphan_meta(model)
        identity = extract_identity(meta, uid=uid, sample_type=model.SampleType)
        if identity and identity not in identity_map:
            identity_map[identity] = uid

        parent_info[uid] = {"sample_id": outcome.sample_id, "uuid": uid}

    return identity_map, parent_info

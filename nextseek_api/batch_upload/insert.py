"""Stage 5: INSERT — Main batch processing loop (performance core)."""
from __future__ import annotations

import gc
import logging
import time
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Set, Tuple

import psutil
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .associations import batch_insert_assay_assets, batch_insert_projects_samples
from .checkpoint import determine_resume_uid, write_checkpoint
from .config import BatchUploadConfig
from .db_engine import get_connection
from .errors import ErrorCollector, ErrorType
from .insert_strategies import compute_first_letter, insert_samples
from .models import (
    BatchResult,
    DirectionComputation,
    InsertableSample,
    RowOutcome,
)
from .permissions import PermissionsInserter
from .policies import cleanup_unused_policies, insert_policies_for_uids
from .prefetch import clear_caches
from .report import ProgressReporter

log = logging.getLogger(__name__)


class AdaptiveBatchSizer:
    """Self-tuning batch sizer targeting a rows-per-second rate."""

    def __init__(
        self,
        target_rps: float = 100.0,
        min_size: int = 500,
        max_size: int = 5000,
        initial: int = 2000,
    ) -> None:
        self._target_rps = target_rps
        self._min = min_size
        self._max = max_size
        self._current = initial

    @property
    def current_size(self) -> int:
        return self._current

    def update(self, actual_rps: float, elapsed: float = 0.0) -> None:
        """Adjust batch size based on actual throughput."""
        if self._target_rps <= 0 or actual_rps <= 0:
            return
        ratio = actual_rps / self._target_rps
        if ratio > 1.1:
            self._current = int(self._current * 1.1)
        elif ratio < 0.9:
            self._current = int(self._current * 0.9)
        self._current = max(self._min, min(self._max, self._current))


def load_existing_samples(
    input_uuids: List[str], conn: Connection
) -> Dict[str, int]:
    """Load existing samples by UUID, returning uuid -> sample_id mapping.

    Uses chunked IN queries (1000 per chunk).
    """
    existing: Dict[str, int] = {}
    for chunk_start in range(0, len(input_uuids), 1000):
        chunk = input_uuids[chunk_start : chunk_start + 1000]
        params = {f"u_{i}": u for i, u in enumerate(chunk)}
        placeholders = ", ".join(f":u_{i}" for i in range(len(chunk)))
        sql = text(f"SELECT id, uuid FROM samples WHERE uuid IN ({placeholders})")
        rows = conn.execute(sql, params).fetchall()
        for sample_id, uuid in rows:
            existing[uuid] = sample_id
    return existing


def _plan_next_batch(
    rows: List[InsertableSample],
    start_idx: int,
    max_rows: int,
    max_payload_bytes: int,
) -> Tuple[List[InsertableSample], int, int]:
    """Plan the next batch respecting both row count and payload size.

    Returns (batch, next_idx, payload_estimate_bytes).
    Always includes at least 1 row.
    """
    batch = []
    payload_est = 0
    idx = start_idx

    while idx < len(rows) and len(batch) < max_rows:
        row = rows[idx]
        row_bytes = _estimate_row_payload_bytes(row.title, row.json_metadata)
        if batch and (payload_est + row_bytes) > max_payload_bytes:
            break
        batch.append(row)
        payload_est += row_bytes
        idx += 1

    return batch, idx, payload_est


def _estimate_row_payload_bytes(title: str, json_metadata: str) -> int:
    """Estimate the payload size of a single row in bytes."""
    return len(title.encode("utf-8")) + len(json_metadata.encode("utf-8")) + 128


def process_batches(
    insertable_samples: List[InsertableSample],
    project_id: int,
    contributor_id: int,
    config: BatchUploadConfig,
    direction_computation: DirectionComputation,
    conn_factory: Callable[[], Connection.__class__] = get_connection,
    error_collector: Optional[ErrorCollector] = None,
    reporter: Optional[ProgressReporter] = None,
    checkpoint_dir: str = "",
    checkpoint_name: str = "batch_checkpoint.txt",
    resume_uid: Optional[str] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    existing_samples: Optional[Dict[str, int]] = None,
    update_existing: bool = False,
) -> BatchResult:
    """Main batch processing loop.

    7-step transaction flow per batch:
    1. Insert policies
    2. Insert samples (RETURNING or fallback)
    3. Link projects to samples
    4. Link assays to samples
    5. Insert permissions (if enabled)
    6. Cleanup failed policies
    7. Write checkpoint
    """
    if error_collector is None:
        error_collector = ErrorCollector()
    if reporter is None:
        reporter = ProgressReporter(total_rows=len(insertable_samples))

    outcomes: Dict[str, RowOutcome] = {}
    attempted_uids: Set[str] = set()
    total_inserted = 0
    total_project_links = 0
    total_assay_links = 0
    total_permissions = 0
    stopped_early = False

    # Pre-flight: load existing samples
    all_uuids = [s.uuid for s in insertable_samples]
    if existing_samples is not None:
        all_uuids_set = set(all_uuids)
        existing = {uid: sid for uid, sid in existing_samples.items() if uid in all_uuids_set}
    else:
        with conn_factory() as conn:
            existing = load_existing_samples(all_uuids, conn)

    # Determine resume point
    effective_resume = determine_resume_uid(resume_uid, checkpoint_dir, checkpoint_name)

    # Filter and mark existing/skipped
    rows_to_process: List[InsertableSample] = []
    rows_to_update: List[InsertableSample] = []
    for sample in insertable_samples:
        if sample.uuid in existing:
            if update_existing:
                rows_to_update.append(sample)
            else:
                outcomes[sample.uuid] = RowOutcome(
                    status="skipped",
                    reason="duplicate",
                    sample_id=existing[sample.uuid],
                )
            continue
        if effective_resume and sample.uuid < effective_resume:
            outcomes[sample.uuid] = RowOutcome(
                status="skipped", reason="before checkpoint"
            )
            continue
        rows_to_process.append(sample)

    if not rows_to_process and not rows_to_update:
        log.info("INSERT: no rows to process or update")
        return BatchResult(
            inserted_count=0,
            linked_project_count=0,
            linked_assays_count=0,
            outcomes=outcomes,
            attempted_uids=attempted_uids,
            stopped_early=False,
            permissions_inserted_count=0,
        )

    # Setup
    sizer = AdaptiveBatchSizer(
        target_rps=config.target_rows_per_sec,
        min_size=config.min_batch_size,
        max_size=config.max_batch_size,
        initial=config.max_rows_per_batch,
    )
    permissions_inserter = PermissionsInserter(
        contributor_type=config.permissions_default_contributor_type,
        contributor_id=project_id,
        access_type=config.permissions_default_access_type,
        enabled=config.enable_auto_permissions,
    )
    direction_by_pair = direction_computation.direction_by_pair

    # Group by sample_type for query plan stability (match remote behavior)
    grouped_by_type: Dict[int, List[InsertableSample]] = defaultdict(list)
    for sample in rows_to_process:
        grouped_by_type[sample.sample_type_id].append(sample)

    reporter.begin_batch(len(rows_to_process))
    batch_idx = 0

    for _stype, type_rows in grouped_by_type.items():
        cursor = 0

        while cursor < len(type_rows):
            if should_stop and should_stop():
                stopped_early = True
                log.warning("INSERT: stopped early by cancellation")
                break

            batch, next_cursor, _payload = _plan_next_batch(
                type_rows, cursor, sizer.current_size, config.max_payload_bytes
            )
            cursor = next_cursor
            batch_uids = [s.uuid for s in batch]
            attempted_uids.update(batch_uids)
            batch_start = time.perf_counter()
            uuid_to_id: Dict[str, int] = {}

            try:
                with conn_factory() as conn:
                    # Step 1: Insert policies (stable-order dedup)
                    seen_uids: Set[str] = set()
                    deduped_uids: List[str] = []
                    for u in batch_uids:
                        if u not in seen_uids:
                            seen_uids.add(u)
                            deduped_uids.append(u)
                    uid_policy_pairs = insert_policies_for_uids(
                        deduped_uids, name="default policy", conn=conn
                    )
                    uid_to_policy = dict(uid_policy_pairs)

                    # Step 2: Insert samples
                    rows_payload = []
                    for sample in batch:
                        rows_payload.append({
                            "title": sample.title,
                            "sample_type_id": sample.sample_type_id,
                            "json_metadata": sample.json_metadata,
                            "uuid": sample.uuid,
                            "contributor_id": contributor_id,
                            "first_letter": compute_first_letter(sample.uuid),
                            "policy_id": uid_to_policy.get(sample.uuid, 0),
                        })
                    id_uuid_pairs = insert_samples(rows_payload, conn)
                    uuid_to_id = {uuid: sid for sid, uuid in id_uuid_pairs}

                    # Step 3: Link projects to samples
                    sample_ids = list(uuid_to_id.values())
                    project_link_count = batch_insert_projects_samples(
                        project_id, sample_ids, conn
                    )

                    # Step 4: Link assays to samples
                    assay_records = []
                    for sample in batch:
                        sid = uuid_to_id.get(sample.uuid)
                        if sid and sample.assay_ids:
                            for aid in sample.assay_ids:
                                direction = direction_by_pair.get((sample.uuid, aid), 1)
                                assay_records.append((aid, sid, "Sample", direction, None, None))
                    assay_link_count = batch_insert_assay_assets(
                        assay_records, conn
                    )

                    # Step 5: Insert permissions (if enabled)
                    policy_ids_for_perms = [
                        uid_to_policy[uid]
                        for uid in batch_uids
                        if uid in uuid_to_id and uid in uid_to_policy
                    ]
                    perm_count = permissions_inserter.insert_for_policy_ids(
                        policy_ids_for_perms, conn
                    )

                    # Step 6: Cleanup failed policies
                    failed_uids = [uid for uid in batch_uids if uid not in uuid_to_id]
                    failed_policy_ids = [uid_to_policy[uid] for uid in failed_uids if uid in uid_to_policy]
                    if failed_policy_ids:
                        cleanup_unused_policies(failed_policy_ids, conn)

                    # Step 7: Checkpoint
                    if batch_uids:
                        write_checkpoint(checkpoint_dir, checkpoint_name, batch_uids[-1])

                # Record outcomes
                for sample in batch:
                    sid = uuid_to_id.get(sample.uuid)
                    if sid:
                        outcomes[sample.uuid] = RowOutcome(
                            status="success",
                            sample_id=sid,
                            assays_linked_count=len(sample.assay_ids),
                        )
                    else:
                        outcomes[sample.uuid] = RowOutcome(
                            status="failed", reason="insert returned no id"
                        )
                        error_collector.add(
                            row_index=-1,
                            uid=sample.uuid,
                            error_type=ErrorType.DB_CONSTRAINT,
                            message="INSERT returned no id for this UUID",
                        )

                total_inserted += len(uuid_to_id)
                total_project_links += project_link_count
                total_assay_links += assay_link_count
                total_permissions += perm_count

            except Exception as exc:
                log.exception("Batch %d failed: %s", batch_idx, exc)
                for sample in batch:
                    outcomes[sample.uuid] = RowOutcome(
                        status="failed", reason=str(exc)[:200]
                    )
                    error_collector.add(
                        row_index=-1,
                        uid=sample.uuid,
                        error_type=ErrorType.DB_CONN,
                        message=f"Batch transaction failed: {exc}",
                    )

            # Post-batch maintenance
            batch_elapsed = time.perf_counter() - batch_start
            actual_rps = len(batch) / batch_elapsed if batch_elapsed > 0 else 0
            n_success = len(uuid_to_id)
            reporter.update_counts(
                success=n_success,
                skipped=0,
                failed=len(batch) - n_success,
            )

            if config.adaptive_batching:
                sizer.update(actual_rps, batch_elapsed)

            batch_idx += 1
            if batch_idx % config.gc_every_n_batches == 0:
                gc.collect()
            if batch_idx % config.clear_caches_every_n_batches == 0:
                clear_caches()

            # Memory check
            if config.mem_limit_mb:
                check_memory_limit(config.mem_limit_mb)

        # Break outer loop if stopped
        if stopped_early:
            break

    # ── Process updates for existing samples ──────────────────────────────
    total_updated = 0
    if rows_to_update:
        from .update import bulk_update_samples, load_existing_sample_details

        try:
            with conn_factory() as conn:
                update_uuids = [s.uuid for s in rows_to_update]
                details = load_existing_sample_details(update_uuids, conn)

                update_outcomes = bulk_update_samples(
                    rows=rows_to_update,
                    details=details,
                    project_id=project_id,
                    direction_by_pair=direction_computation.direction_by_pair,
                    conn=conn,
                    enable_auto_permissions=config.enable_auto_permissions,
                )

                outcomes.update(update_outcomes)
                total_updated = sum(
                    1 for o in update_outcomes.values() if o.status == "success"
                )
        except Exception as exc:
            log.exception("Bulk update failed: %s", exc)
            for sample in rows_to_update:
                if sample.uuid not in outcomes:
                    outcomes[sample.uuid] = RowOutcome(
                        status="failed", reason=f"update failed: {str(exc)[:200]}"
                    )
                    if error_collector:
                        error_collector.add(
                            row_index=-1,
                            uid=sample.uuid,
                            error_type=ErrorType.DB_CONSTRAINT,
                            message=f"Bulk update failed: {exc}",
                        )

    return BatchResult(
        inserted_count=total_inserted,
        linked_project_count=total_project_links,
        linked_assays_count=total_assay_links,
        outcomes=outcomes,
        attempted_uids=attempted_uids,
        stopped_early=stopped_early,
        permissions_inserted_count=total_permissions,
        updated_count=total_updated,
    )


def check_memory_limit(mem_limit_mb: int) -> None:
    """Log warning if RSS exceeds limit; trigger GC."""
    process = psutil.Process()
    rss_mb = process.memory_info().rss / (1024 * 1024)
    if rss_mb > mem_limit_mb:
        log.warning(
            "Memory limit exceeded: RSS=%.0f MB > limit=%d MB — triggering GC",
            rss_mb,
            mem_limit_mb,
        )
        gc.collect()

"""ThreadPoolExecutor parallelism for batch upload (>= 5000 rows)."""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from .config import BatchUploadConfig
from .db_engine import get_connection, get_engine
from .errors import ErrorCollector, _ThreadSafeErrorCollector
from .insert import process_batches
from .models import (
    BatchResult,
    DirectionComputation,
    InsertableSample,
    RowOutcome,
)
from .prefetch import prefetch_assay_ids, prefetch_sample_types
from .report import ProgressReporter, _ThreadSafeProgressReporter

log = logging.getLogger(__name__)

PARALLEL_THRESHOLD = 5000


@dataclass
class SharedState:
    """Thread-safe shared state for parallel workers."""

    outcomes: Dict[str, RowOutcome] = field(default_factory=dict)
    outcomes_lock: threading.Lock = field(default_factory=threading.Lock)
    reporter: ProgressReporter = field(default_factory=ProgressReporter)
    reporter_lock: threading.Lock = field(default_factory=threading.Lock)
    error_collector: ErrorCollector = field(default_factory=ErrorCollector)
    error_lock: threading.Lock = field(default_factory=threading.Lock)

    def update_outcomes(self, new_outcomes: Dict[str, RowOutcome]) -> None:
        with self.outcomes_lock:
            self.outcomes.update(new_outcomes)


def _split_equal(rows: List, n: int) -> List[List]:
    """Split rows into n approximately equal chunks."""
    if n <= 0:
        return [rows]
    chunk_size = max(1, len(rows) // n)
    chunks = []
    for i in range(0, len(rows), chunk_size):
        chunks.append(rows[i : i + chunk_size])
    # If we got more chunks than n, merge the last ones
    while len(chunks) > n:
        last = chunks.pop()
        chunks[-1].extend(last)
    return chunks


def _worker_process_chunk(
    worker_id: int,
    chunk: List[InsertableSample],
    shared_state: SharedState,
    project_id: int,
    contributor_id: int,
    config: BatchUploadConfig,
    direction_computation: DirectionComputation,
    should_stop: Optional[Callable[[], bool]] = None,
    existing_samples: Optional[Dict[str, int]] = None,
) -> BatchResult:
    """Process a chunk of rows in a worker thread."""
    log_prefix = f"[Worker-{worker_id}]"
    log.info("%s processing %d rows", log_prefix, len(chunk))

    safe_reporter = _ThreadSafeProgressReporter(shared_state.reporter, shared_state.reporter_lock)
    safe_errors = _ThreadSafeErrorCollector(shared_state.error_collector, shared_state.error_lock)

    result = process_batches(
        insertable_samples=chunk,
        project_id=project_id,
        contributor_id=contributor_id,
        config=config,
        direction_computation=direction_computation,
        conn_factory=get_connection,
        error_collector=safe_errors,
        reporter=safe_reporter,
        checkpoint_dir="",  # Per-worker checkpoints disabled
        checkpoint_name="",
        resume_uid=None,
        should_stop=should_stop,
        existing_samples=existing_samples,
    )

    # Merge outcomes
    shared_state.update_outcomes(result.outcomes)
    log.info(
        "%s completed: inserted=%d, failed=%d",
        log_prefix,
        result.inserted_count,
        sum(1 for o in result.outcomes.values() if o.status == "failed"),
    )
    return result


def process_batches_parallel(
    rows: List[InsertableSample],
    project_id: int,
    contributor_id: int,
    config: BatchUploadConfig,
    direction_computation: DirectionComputation,
    error_collector: Optional[ErrorCollector] = None,
    reporter: Optional[ProgressReporter] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    existing_samples: Optional[Dict[str, int]] = None,
) -> BatchResult:
    """Parallel batch processing for large datasets.

    Pre-warms caches in main thread, then splits work across workers.
    """
    if error_collector is None:
        error_collector = ErrorCollector()
    if reporter is None:
        reporter = ProgressReporter(total_rows=len(rows))

    # Compute worker count
    engine = get_engine()
    pool_size = engine.pool.size() if hasattr(engine.pool, "size") else 20
    max_overflow = 10
    num_workers = max(1, min(os.cpu_count() or 4, 8, (pool_size + max_overflow) // 2))
    log.info("Parallel mode: %d workers for %d rows", num_workers, len(rows))

    # Pre-warm caches in main thread
    log.info("Pre-warming caches...")
    all_sample_types = list({r.title for r in rows})  # InsertableSample doesn't have SampleType
    all_assay_ids = []
    for r in rows:
        all_assay_ids.extend(r.assay_ids)
    if all_assay_ids:
        with get_connection() as conn:
            prefetch_assay_ids(list(set(all_assay_ids)), conn)

    # Setup shared state
    shared_state = SharedState(
        reporter=reporter,
        error_collector=error_collector,
    )
    reporter.begin_batch(len(rows))

    # Split and dispatch
    chunks = _split_equal(rows, num_workers)

    futures = []
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        for i, chunk in enumerate(chunks):
            future = executor.submit(
                _worker_process_chunk,
                worker_id=i,
                chunk=chunk,
                shared_state=shared_state,
                project_id=project_id,
                contributor_id=contributor_id,
                config=config,
                direction_computation=direction_computation,
                should_stop=should_stop,
                existing_samples=existing_samples,
            )
            futures.append(future)

        # Wait for all workers
        results: List[BatchResult] = []
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                log.exception("Worker failed")

    # Aggregate results
    total_inserted = sum(r.inserted_count for r in results)
    total_project = sum(r.linked_project_count for r in results)
    total_assays = sum(r.linked_assays_count for r in results)
    total_perms = sum(r.permissions_inserted_count for r in results)
    all_attempted: Set[str] = set()
    for r in results:
        all_attempted.update(r.attempted_uids)

    return BatchResult(
        inserted_count=total_inserted,
        linked_project_count=total_project,
        linked_assays_count=total_assays,
        outcomes=shared_state.outcomes,
        attempted_uids=all_attempted,
        stopped_early=any(r.stopped_early for r in results),
        permissions_inserted_count=total_perms,
    )

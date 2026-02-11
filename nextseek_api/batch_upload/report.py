"""Stage 7: REPORT — Summary CSV and progress reporting."""
from __future__ import annotations

import csv
import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from .errors import ErrorCollector
from .models import InputRowModel, Metrics, RowOutcome

log = logging.getLogger(__name__)


@dataclass
class RowSummary:
    row_index: int
    uid: str
    status: str
    reason: str
    sample_type: str
    sample_id: Optional[int]
    assays_linked_count: Optional[int]
    access_type: Optional[int]


class ProgressReporter:
    """Reports progress with ETA calculation."""

    def __init__(self, total_rows: int = 0) -> None:
        self.total_rows = total_rows
        self._success = 0
        self._skipped = 0
        self._failed = 0
        self._start_time: Optional[float] = None

    def begin_batch(self, total: int) -> None:
        self.total_rows = total
        self._start_time = time.perf_counter()
        self._success = 0
        self._skipped = 0
        self._failed = 0

    def update_counts(self, success: int = 0, skipped: int = 0, failed: int = 0) -> None:
        self._success += success
        self._skipped += skipped
        self._failed += failed
        processed = self._success + self._skipped + self._failed

        if self._start_time and self.total_rows > 0:
            elapsed = time.perf_counter() - self._start_time
            rps = processed / elapsed if elapsed > 0 else 0
            remaining = self.total_rows - processed
            eta = remaining / rps if rps > 0 else 0
            log.info(
                "Progress: %d/%d (%.1f%%) | success=%d skipped=%d failed=%d | "
                "%.0f rows/s | ETA %.0fs",
                processed, self.total_rows,
                100 * processed / self.total_rows,
                self._success, self._skipped, self._failed,
                rps, eta,
            )

    @property
    def processed(self) -> int:
        return self._success + self._skipped + self._failed

    @property
    def elapsed(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.perf_counter() - self._start_time


class _ThreadSafeProgressReporter:
    """Thread-safe proxy wrapping a ProgressReporter."""

    def __init__(self, reporter: ProgressReporter, lock: Optional[threading.Lock] = None) -> None:
        self._reporter = reporter
        self._lock = lock or threading.Lock()

    def begin_batch(self, total: int) -> None:
        with self._lock:
            self._reporter.begin_batch(total)

    def update_counts(self, success: int = 0, skipped: int = 0, failed: int = 0) -> None:
        with self._lock:
            self._reporter.update_counts(success, skipped, failed)

    @property
    def processed(self) -> int:
        with self._lock:
            return self._reporter.processed

    @property
    def elapsed(self) -> float:
        with self._lock:
            return self._reporter.elapsed


def write_summary_csv(
    output_path: str,
    row_summaries: List[RowSummary],
    totals: dict,
    neo4j_metrics: Optional[Metrics] = None,
    warnings: Optional[dict] = None,
) -> None:
    """Write a summary CSV with per-row details and aggregate totals."""
    fieldnames = [
        "row_index", "uid", "status", "reason", "sample_type",
        "sample_id", "assays_linked_count", "access_type",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in row_summaries:
            writer.writerow({
                "row_index": row.row_index,
                "uid": row.uid,
                "status": row.status,
                "reason": row.reason or "",
                "sample_type": row.sample_type,
                "sample_id": row.sample_id or "",
                "assays_linked_count": row.assays_linked_count or 0,
                "access_type": row.access_type or "",
            })

        # Separator
        writer.writerow({k: "" for k in fieldnames})

        # Totals section
        totals_row = {k: "" for k in fieldnames}
        totals_row["row_index"] = "TOTALS"
        totals_row["uid"] = f"processed={totals.get('processed', 0)}"
        totals_row["status"] = f"success={totals.get('success', 0)}"
        totals_row["reason"] = f"skipped={totals.get('skipped', 0)}"
        totals_row["sample_type"] = f"failed={totals.get('failed', 0)}"
        totals_row["sample_id"] = f"elapsed={totals.get('elapsed_s', 0):.1f}s"
        totals_row["assays_linked_count"] = f"throughput={totals.get('throughput_rps', 0):.0f}rps"
        writer.writerow(totals_row)

        # Warnings section
        if warnings:
            warn_row = {k: "" for k in fieldnames}
            warn_row["row_index"] = "WARNINGS"
            unknown_cols = warnings.get("unknown_columns") if isinstance(warnings, dict) else None
            if unknown_cols:
                warn_row["uid"] = "unknown_columns=" + "; ".join(str(x) for x in unknown_cols)
            writer.writerow(warn_row)

        # Permissions totals
        if totals.get("permissions_inserted", 0) > 0:
            perm_row = {k: "" for k in fieldnames}
            perm_row["row_index"] = "PERMISSIONS"
            perm_row["uid"] = f"inserted={totals.get('permissions_inserted', 0)}"
            writer.writerow(perm_row)

        # Neo4j metrics
        if neo4j_metrics and neo4j_metrics.elapsed_ms_total > 0:
            neo4j_row = {k: "" for k in fieldnames}
            neo4j_row["row_index"] = "NEO4J"
            neo4j_row["uid"] = f"nodes_created={neo4j_metrics.nodes_created}"
            neo4j_row["status"] = f"nodes_matched={neo4j_metrics.nodes_matched}"
            neo4j_row["reason"] = f"derived_from={neo4j_metrics.derived_from_rels_created}"
            neo4j_row["sample_type"] = f"of_type={neo4j_metrics.of_type_rels_created}"
            neo4j_row["sample_id"] = f"st_nodes={neo4j_metrics.sample_type_nodes_created}"
            neo4j_row["assays_linked_count"] = f"elapsed={neo4j_metrics.elapsed_ms_total:.0f}ms"
            writer.writerow(neo4j_row)

    log.info("Summary CSV written to %s", output_path)


def build_row_summaries(
    outcomes: Dict[str, RowOutcome],
    input_models: List[InputRowModel],
    error_collector: ErrorCollector,
) -> List[RowSummary]:
    """Build RowSummary list from outcomes and input models."""
    uid_to_model = {m.UID: m for m in input_models}
    summaries: List[RowSummary] = []

    for idx, (uid, outcome) in enumerate(outcomes.items()):
        model = uid_to_model.get(uid)
        sample_type = model.SampleType if model else ""

        # Check for errors
        errors = error_collector.errors_for_uid(uid)
        reason = outcome.reason or ""
        if errors and not reason:
            reason = "; ".join(e.message for e in errors[:3])

        summaries.append(RowSummary(
            row_index=idx,
            uid=uid,
            status=outcome.status,
            reason=reason,
            sample_type=sample_type,
            sample_id=outcome.sample_id,
            assays_linked_count=outcome.assays_linked_count,
            access_type=None,
        ))

    return summaries

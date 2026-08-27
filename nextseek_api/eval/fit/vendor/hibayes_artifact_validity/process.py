"""Aggregator: list[ArtifactValidityRow] → per-task-family group totals."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import ArtifactValidityRow


@dataclass
class TaskFamilyArtifactAggregate:
    task_family: str
    n_total: int
    n_success: int


def aggregate_by_task_family(
    rows: list[ArtifactValidityRow],
) -> list[TaskFamilyArtifactAggregate]:
    """Filter NotExpected per DD-15/DD-37 and aggregate per task_family."""
    counters: dict[str, dict[str, int]] = defaultdict(lambda: {"n_total": 0, "n_success": 0})
    for r in rows:
        if not r.artifact_expected:
            continue  # DD-15/DD-37: NotExpected filtered out before modeling
        c = counters[r.task_family]
        c["n_total"] += 1
        if r.artifact_success:
            c["n_success"] += 1
    return [
        TaskFamilyArtifactAggregate(task_family=tf, n_total=v["n_total"], n_success=v["n_success"])
        for tf, v in counters.items()
    ]

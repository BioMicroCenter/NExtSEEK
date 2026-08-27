"""Aggregator: list[FunctionalUsefulnessRow] → per-task-family group totals."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import FunctionalUsefulnessRow


@dataclass
class TaskFamilyFunctionalAggregate:
    task_family: str
    n_total: int
    n_success: int


def aggregate_by_task_family(
    rows: list[FunctionalUsefulnessRow],
) -> list[TaskFamilyFunctionalAggregate]:
    """Aggregate per task_family.

    Unlike the artifact axis, there is no `NotExpected` filter — the functional
    axis applies to all queries (per Section 6 substitution rule).
    """
    counters: dict[str, dict[str, int]] = defaultdict(lambda: {"n_total": 0, "n_success": 0})
    for r in rows:
        c = counters[r.task_family]
        c["n_total"] += 1
        if r.functional_success:
            c["n_success"] += 1
    return [
        TaskFamilyFunctionalAggregate(
            task_family=tf, n_total=v["n_total"], n_success=v["n_success"]
        )
        for tf, v in counters.items()
    ]

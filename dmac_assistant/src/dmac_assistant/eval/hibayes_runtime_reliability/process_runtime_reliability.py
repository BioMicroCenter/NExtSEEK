"""Aggregator: list[RuntimeEvalRow] → list[TaskFamilyAggregate] + GlobalTotals.

Pure function module. No I/O. No globals. T05 will consume the output directly.
"""
from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tools.hibayes.exporter import FailureMode

from .models import RuntimeEvalRow, TaskFamilyAggregate


class GlobalTotals(BaseModel):
    """Run-wide rollup. Used by T06 for the report header and T07 for the CLI summary.

    Mirrors `TaskFamilyAggregate`'s cross-field validator so that an aggregator bug
    that miscounts `n_failure` is caught at model-construction time, not just by
    the canonical-fixture comparison test (review N-1).
    """
    model_config = ConfigDict(extra="forbid")

    n_total: int = Field(ge=0)
    n_success: int = Field(ge=0)
    n_failure: int = Field(ge=0)
    observed_success_rate: float = Field(ge=0.0, le=1.0)
    n_families: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_count_consistency(self) -> GlobalTotals:
        if self.n_total != self.n_success + self.n_failure:
            raise ValueError(
                f"GlobalTotals.n_total ({self.n_total}) != n_success+n_failure "
                f"({self.n_success}+{self.n_failure}={self.n_success + self.n_failure})"
            )
        if self.n_total > 0:
            expected_rate = self.n_success / self.n_total
            if abs(self.observed_success_rate - expected_rate) > 1e-6:
                raise ValueError(
                    f"GlobalTotals.observed_success_rate {self.observed_success_rate!r} "
                    f"!= n_success/n_total ({expected_rate!r})"
                )
        return self


# --- Pure helpers (REFACTOR step) ---------------------------------------------------


def _count_failure_modes(rows: list[RuntimeEvalRow]) -> tuple[int, int, int]:
    """Return (n_error, n_timeout, n_no_answer)."""
    n_error = sum(1 for r in rows if r.failure_mode is FailureMode.error)
    n_timeout = sum(1 for r in rows if r.failure_mode is FailureMode.timeout)
    n_no_answer = sum(1 for r in rows if r.failure_mode is FailureMode.no_answer)
    return n_error, n_timeout, n_no_answer


def _avg_cost_usd_none_aware(rows: list[RuntimeEvalRow]) -> float | None:
    """R-05: mean of non-None cost_usd; None if all rows have cost_usd=None."""
    costs = [r.cost_usd for r in rows if r.cost_usd is not None]
    if not costs:
        return None
    return sum(costs) / len(costs)


def _avg_field(rows: list[RuntimeEvalRow], attr: str) -> float:
    """Mean of a non-nullable numeric field on RuntimeEvalRow.

    Caller's responsibility to pass a non-nullable attr. Raises ZeroDivisionError on
    empty input — that signals an aggregator bug, not a runtime error.
    """
    return sum(getattr(r, attr) for r in rows) / len(rows)


def _count_artifact_rows(rows: list[RuntimeEvalRow]) -> int:
    return sum(1 for r in rows if r.artifact_count > 0)


# --- Aggregator ---------------------------------------------------------------------


def aggregate_by_task_family(
    rows: list[RuntimeEvalRow],
) -> tuple[list[TaskFamilyAggregate], GlobalTotals]:
    """Group rows by `task_family` (already normalized by the loader) and fold into aggregates.

    The aggregate ordering is stable: families appear in first-seen order so test
    JSON comparisons can rely on a deterministic sequence.
    """
    by_family: dict[str, list[RuntimeEvalRow]] = defaultdict(list)
    seen_order: list[str] = []
    for r in rows:
        if r.task_family not in by_family:
            seen_order.append(r.task_family)
        by_family[r.task_family].append(r)

    aggregates: list[TaskFamilyAggregate] = []
    for family in seen_order:
        group = by_family[family]
        n_total = len(group)
        n_success = sum(1 for r in group if r.runtime_success)
        n_failure = n_total - n_success
        observed_rate = n_success / n_total  # n_total > 0 always (group exists because rows did)
        n_error, n_timeout, n_no_answer = _count_failure_modes(group)

        aggregates.append(
            TaskFamilyAggregate(
                task_family=family,
                n_total=n_total,
                n_success=n_success,
                n_failure=n_failure,
                observed_success_rate=observed_rate,
                n_error=n_error,
                n_timeout=n_timeout,
                n_no_answer=n_no_answer,
                n_artifact_rows=_count_artifact_rows(group),
                avg_latency_seconds=_avg_field(group, "latency_seconds"),
                avg_cost_usd=_avg_cost_usd_none_aware(group),
                avg_tool_calls_total=_avg_field(group, "tool_calls_total"),
            )
        )

    total_n = sum(a.n_total for a in aggregates)
    total_success = sum(a.n_success for a in aggregates)
    totals = GlobalTotals(
        n_total=total_n,
        n_success=total_success,
        n_failure=total_n - total_success,
        observed_success_rate=(total_success / total_n) if total_n else 0.0,
        n_families=len(aggregates),
    )

    return aggregates, totals

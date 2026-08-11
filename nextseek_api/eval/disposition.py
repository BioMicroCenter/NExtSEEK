"""V8-D total disposition mapping for Plan 018 V4-3."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nextseek_api.eval.router_models_proposal import (
    ArtifactStatus,
    Disposition,
    ErrorClass,
    EvalRow,
    FailureMode,
)

__all__ = [
    "ArmBucket",
    "ExclusionReason",
    "OutcomeBucket",
    "classify_arm",
    "combined_success",
    "exclusion_census",
    "should_call_judge",
]


class OutcomeBucket(str, Enum):
    desired = "desired"
    not_desired = "not_desired"
    excluded = "excluded"
    pending = "pending"


class ExclusionReason(str, Enum):
    provider_outage = "provider_outage"
    zero_criteria = "zero_criteria"
    unevaluable = "unevaluable"
    unjudged = "unjudged"
    missing_arm = "missing_arm"
    infrastructure = "infrastructure"
    unknown = "unknown"


class ArmBucket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str
    route: str
    bucket: OutcomeBucket
    exclusion_reason: ExclusionReason | None = None
    scored_value: bool | None = None


def combined_success(
    *,
    runtime_success: bool,
    artifact_success: bool,
    functional_success: bool | None,
) -> bool | None:
    """success = runtime_success AND artifact_success AND functional_success."""
    if not runtime_success or not artifact_success:
        return False
    if functional_success is None:
        return None
    return functional_success


def should_call_judge(row: EvalRow, *, zero_criteria: bool = False, unevaluable: bool = False) -> bool:
    if zero_criteria or unevaluable:
        return False
    if row.disposition is Disposition.excluded:
        return False
    return row.runtime_success and row.artifact_success


def classify_arm(
    row: EvalRow | None,
    *,
    zero_criteria: bool = False,
    unevaluable: bool = False,
    missing: bool = False,
) -> ArmBucket:
    if missing or row is None:
        return ArmBucket(
            query_id="",
            route="",
            bucket=OutcomeBucket.pending,
            exclusion_reason=ExclusionReason.missing_arm,
        )
    if zero_criteria:
        return ArmBucket(
            query_id=row.query_id,
            route=row.route,
            bucket=OutcomeBucket.excluded,
            exclusion_reason=ExclusionReason.zero_criteria,
        )
    if unevaluable:
        return ArmBucket(
            query_id=row.query_id,
            route=row.route,
            bucket=OutcomeBucket.excluded,
            exclusion_reason=ExclusionReason.unevaluable,
        )
    if row.error_class is ErrorClass.provider_outage:
        return ArmBucket(
            query_id=row.query_id,
            route=row.route,
            bucket=OutcomeBucket.excluded,
            exclusion_reason=ExclusionReason.provider_outage,
        )
    outcome = row.outcome()
    if outcome is None:
        if row.functional_success is None and row.runtime_success and row.artifact_success:
            reason = ExclusionReason.unjudged
        else:
            reason = ExclusionReason.infrastructure
        return ArmBucket(
            query_id=row.query_id,
            route=row.route,
            bucket=OutcomeBucket.excluded,
            exclusion_reason=reason,
        )
    if outcome:
        return ArmBucket(
            query_id=row.query_id,
            route=row.route,
            bucket=OutcomeBucket.desired,
            scored_value=True,
        )
    return ArmBucket(
        query_id=row.query_id,
        route=row.route,
        bucket=OutcomeBucket.not_desired,
        scored_value=False,
    )


def classify_unknown_value(value: str) -> None:
    """Fail closed on unknown enum/status strings."""
    raise ValueError(f"unrecognised value fails closed: {value!r}")


def exclusion_census(buckets: list[ArmBucket]) -> dict[str, int]:
    counts: dict[str, int] = {
        "scored_desired": 0,
        "scored_not_desired": 0,
        "excluded_by_reason": 0,
        "pending": 0,
    }
    reason_counts: dict[str, int] = {}
    for b in buckets:
        if b.bucket is OutcomeBucket.desired:
            counts["scored_desired"] += 1
        elif b.bucket is OutcomeBucket.not_desired:
            counts["scored_not_desired"] += 1
        elif b.bucket is OutcomeBucket.pending:
            counts["pending"] += 1
        else:
            counts["excluded_by_reason"] += 1
            if b.exclusion_reason is not None:
                key = b.exclusion_reason.value
                reason_counts[key] = reason_counts.get(key, 0) + 1
    return {"totals": counts, "by_reason": reason_counts}

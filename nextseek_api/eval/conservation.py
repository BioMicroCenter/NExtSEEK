"""Conservation equation and fit-admission gate (Plan 018 V4-3)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nextseek_api.eval.disposition import ArmBucket, OutcomeBucket, exclusion_census

__all__ = [
    "ConservationReport",
    "DifferentialAttritionReport",
    "FitAdmission",
    "SupportGateConfig",
    "build_conservation_report",
    "build_differential_attrition_report",
    "build_fit_admission",
    "check_support_gate",
    "compute_sensitivity_bounds",
    "count_discordant_pairs",
]


class SupportGateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "support_gate/v1"
    min_retained_pairs: int = Field(default=5, ge=1)
    min_discordant_pairs: int = Field(default=2, ge=1)


@dataclass(frozen=True)
class ConservationReport:
    input_count: int
    scored_desired: int
    scored_not_desired: int
    excluded_by_reason: int
    pending: int
    by_reason: dict[str, int]

    @property
    def balanced(self) -> bool:
        return (
            self.input_count
            == self.scored_desired
            + self.scored_not_desired
            + self.excluded_by_reason
            + self.pending
        )


@dataclass(frozen=True)
class DifferentialAttritionReport:
    by_route: dict[str, dict[str, int]]
    exclusion_rate_delta: float
    route_imbalance: bool
    detail: str


class FitAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retained_pairs: list[tuple[str, str, str]]
    excluded_pair_ids: list[str] = Field(default_factory=list)
    pending_pair_ids: list[str] = Field(default_factory=list)


def build_conservation_report(buckets: list[ArmBucket]) -> ConservationReport:
    census = exclusion_census(buckets)
    totals = census["totals"]
    return ConservationReport(
        input_count=len(buckets),
        scored_desired=totals["scored_desired"],
        scored_not_desired=totals["scored_not_desired"],
        excluded_by_reason=totals["excluded_by_reason"],
        pending=totals["pending"],
        by_reason=census["by_reason"],
    )


def build_differential_attrition_report(buckets: list[ArmBucket]) -> DifferentialAttritionReport:
    """Per-route exclusion/pending rates; flag route-specific imbalance."""
    by_route: dict[str, dict[str, int]] = {}
    for bucket in buckets:
        route = bucket.route or "unknown"
        stats = by_route.setdefault(
            route,
            {"input": 0, "excluded": 0, "pending": 0, "scored": 0},
        )
        stats["input"] += 1
        if bucket.bucket is OutcomeBucket.excluded:
            stats["excluded"] += 1
        elif bucket.bucket is OutcomeBucket.pending:
            stats["pending"] += 1
        else:
            stats["scored"] += 1

    rates: dict[str, float] = {}
    for route, stats in by_route.items():
        if stats["input"]:
            rates[route] = (stats["excluded"] + stats["pending"]) / stats["input"]

    if len(rates) >= 2:
        values = list(rates.values())
        delta = max(values) - min(values)
        imbalance = delta > 0.25
        detail = f"exclusion+pending rate delta={delta:.3f} across routes {sorted(rates)}"
    elif rates:
        delta = 0.0
        imbalance = False
        detail = f"single route attrition rate={next(iter(rates.values())):.3f}"
    else:
        delta = 0.0
        imbalance = False
        detail = "no buckets"

    return DifferentialAttritionReport(
        by_route=by_route,
        exclusion_rate_delta=delta,
        route_imbalance=imbalance,
        detail=detail,
    )


def compute_sensitivity_bounds(
    admission: FitAdmission,
    pairs: list[dict[str, Any]],
    buckets_by_arm: dict[str, ArmBucket],
    *,
    config: SupportGateConfig | None = None,
) -> dict[str, Any]:
    """Bounds on retained/discordant pairs if pending arms resolve favorably or unfavorably."""
    cfg = config or SupportGateConfig()
    retained = len(admission.retained_pairs)
    discordant = count_discordant_pairs(pairs, buckets_by_arm)
    pending_pairs = len(admission.pending_pair_ids)
    excluded_pairs = len(admission.excluded_pair_ids)

    max_retained = retained + pending_pairs + excluded_pairs
    max_discordant = discordant + pending_pairs + excluded_pairs
    min_retained = retained
    min_discordant = discordant

    return {
        "retained_pairs": {
            "observed": retained,
            "min": min_retained,
            "max": max_retained,
            "min_passes_gate": min_retained >= cfg.min_retained_pairs,
            "max_passes_gate": max_retained >= cfg.min_retained_pairs,
        },
        "discordant_pairs": {
            "observed": discordant,
            "min": min_discordant,
            "max": max_discordant,
            "min_passes_gate": min_discordant >= cfg.min_discordant_pairs,
            "max_passes_gate": max_discordant >= cfg.min_discordant_pairs,
        },
        "pending_pairs": pending_pairs,
        "excluded_pairs": excluded_pairs,
    }


def build_fit_admission(
    pairs: list[dict[str, Any]],
    buckets_by_arm: dict[str, ArmBucket],
    *,
    paired_batch: "PairedExperimentalBatch | None" = None,
) -> FitAdmission:
    """Emit fit-admission with only scored retained pairs; excluded/pending never appear."""
    if paired_batch is not None:
        from nextseek_api.eval.fit.fit_boundary import (
            assert_paired_experimental_only,
            require_approved_paired_run,
        )

        assert_paired_experimental_only(paired_batch)
        require_approved_paired_run(paired_batch.paired_run_id)
    retained: list[tuple[str, str, str]] = []
    excluded: list[str] = []
    pending: list[str] = []
    for pair in pairs:
        pair_id = pair["pair_id"]
        ns = buckets_by_arm.get(pair.get("ns_arm_id", ""))
        cc = buckets_by_arm.get(pair.get("cc_arm_id", ""))
        if ns is None or cc is None or ns.bucket is OutcomeBucket.pending or cc.bucket is OutcomeBucket.pending:
            pending.append(pair_id)
            continue
        if ns.bucket is OutcomeBucket.excluded or cc.bucket is OutcomeBucket.excluded:
            excluded.append(pair_id)
            continue
        if ns.bucket not in (OutcomeBucket.desired, OutcomeBucket.not_desired):
            excluded.append(pair_id)
            continue
        if cc.bucket not in (OutcomeBucket.desired, OutcomeBucket.not_desired):
            excluded.append(pair_id)
            continue
        retained.append((pair_id, pair["query_id"], pair["family"]))
    return FitAdmission(
        retained_pairs=retained,
        excluded_pair_ids=excluded,
        pending_pair_ids=pending,
    )


def check_support_gate(
    admission: FitAdmission,
    config: SupportGateConfig | None = None,
    *,
    discordant_pairs: int | None = None,
    buckets: list[ArmBucket] | None = None,
    pairs: list[dict[str, Any]] | None = None,
    buckets_by_arm: dict[str, ArmBucket] | None = None,
) -> dict[str, Any]:
    cfg = config or SupportGateConfig()
    discordant = discordant_pairs if discordant_pairs is not None else 0
    retained = len(admission.retained_pairs)
    passes = retained >= cfg.min_retained_pairs and discordant >= cfg.min_discordant_pairs

    differential: dict[str, Any] | None = None
    if buckets is not None:
        report = build_differential_attrition_report(buckets)
        differential = {
            "by_route": report.by_route,
            "exclusion_rate_delta": report.exclusion_rate_delta,
            "route_imbalance": report.route_imbalance,
            "detail": report.detail,
        }

    sensitivity: dict[str, Any] | None = None
    if pairs is not None and buckets_by_arm is not None:
        sensitivity = compute_sensitivity_bounds(
            admission,
            pairs,
            buckets_by_arm,
            config=cfg,
        )

    return {
        "passes": passes,
        "retained_pairs": retained,
        "discordant_pairs": discordant,
        "min_retained_pairs": cfg.min_retained_pairs,
        "min_discordant_pairs": cfg.min_discordant_pairs,
        "attrition_report": {
            "excluded_pairs": len(admission.excluded_pair_ids),
            "pending_pairs": len(admission.pending_pair_ids),
        },
        "differential_attrition": differential,
        "sensitivity_bounds": sensitivity,
    }


def count_discordant_pairs(
    pairs: list[dict[str, Any]],
    buckets_by_arm: dict[str, ArmBucket],
) -> int:
    discordant = 0
    for pair in pairs:
        ns = buckets_by_arm.get(pair.get("ns_arm_id", ""))
        cc = buckets_by_arm.get(pair.get("cc_arm_id", ""))
        if ns is None or cc is None:
            continue
        if ns.bucket not in (OutcomeBucket.desired, OutcomeBucket.not_desired):
            continue
        if cc.bucket not in (OutcomeBucket.desired, OutcomeBucket.not_desired):
            continue
        ns_success = ns.bucket is OutcomeBucket.desired
        cc_success = cc.bucket is OutcomeBucket.desired
        if ns_success != cc_success:
            discordant += 1
    return discordant

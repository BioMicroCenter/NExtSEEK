"""Conservation equation and fit-admission gate (Plan 018 V4-3)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nextseek_api.eval.disposition import ArmBucket, OutcomeBucket, exclusion_census

__all__ = [
    "ConservationReport",
    "FitAdmission",
    "SupportGateConfig",
    "build_conservation_report",
    "build_fit_admission",
    "check_support_gate",
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


def build_fit_admission(
    pairs: list[dict[str, Any]],
    buckets_by_arm: dict[str, ArmBucket],
) -> FitAdmission:
    """Emit fit-admission with only scored retained pairs; excluded/pending never appear."""
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
) -> dict[str, Any]:
    cfg = config or SupportGateConfig()
    discordant = discordant_pairs if discordant_pairs is not None else 0
    retained = len(admission.retained_pairs)
    passes = retained >= cfg.min_retained_pairs and discordant >= cfg.min_discordant_pairs
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

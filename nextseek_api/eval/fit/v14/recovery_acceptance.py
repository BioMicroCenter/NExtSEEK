"""V4-4 recovery acceptance predicates (living-plan DONE oracle)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from nextseek_api.eval.fit.v14.decision import DecisionStatus, GenerationDecision
from nextseek_api.eval.fit.v14.recovery_matrix import RecoveryScenario, ground_truth

__all__ = [
    "FEASIBILITY_SLOT_INDICES",
    "RecoveryAcceptanceReport",
    "evaluate_recovery_results",
    "slot_winner",
    "winner_matches_gt",
]


# Representative first-five frozen slots (one seed each from five scenarios).
FEASIBILITY_SLOT_INDICES: tuple[int, ...] = (0, 5, 10, 15, 30)


@dataclass(frozen=True)
class RecoveryAcceptanceReport:
    gate: str
    strong_effect: dict[str, dict[str, int]]
    indecisive: dict[str, dict[str, int]]
    wrong_direction: int
    diagnostics_failures: int
    wall_s: float
    serial_s: float
    reasons: tuple[str, ...]


def slot_winner(decision: GenerationDecision) -> str:
    for c in decision.candidates:
        if not c.activated:
            continue
        if c.status in {DecisionStatus.quality_ns, DecisionStatus.latency_ns}:
            return "ns"
        if c.status in {DecisionStatus.quality_cc, DecisionStatus.latency_cc}:
            return "cc"
    return "none"


def winner_matches_gt(scenario: str, gt: str, winner: str) -> bool:
    if gt == "strong_ns":
        return winner == "ns"
    if gt == "strong_cc":
        return winner == "cc"
    return winner == "none"


def evaluate_recovery_results(
    results: Sequence[dict[str, Any]],
    *,
    use_mcmc: bool,
    wall_s: float,
    serial_s: float,
    wall_limit_s: float = 3600.0,
) -> RecoveryAcceptanceReport:
    if not use_mcmc:
        return RecoveryAcceptanceReport(
            gate="PROFILE_ONLY",
            strong_effect={},
            indecisive={},
            wrong_direction=0,
            diagnostics_failures=0,
            wall_s=wall_s,
            serial_s=serial_s,
            reasons=("use_mcmc=False is profiling-only, not DONE evidence",),
        )

    strong: dict[str, dict[str, int]] = {}
    indec: dict[str, dict[str, int]] = {}
    wrong = 0
    diag_fail = 0
    reasons: list[str] = []

    for r in results:
        scenario = r["scenario"]
        gt = r["ground_truth"]
        winner = r["winner"]
        if not r.get("diagnostics_ok", False):
            diag_fail += 1

        if gt.startswith("strong_"):
            bucket = strong.setdefault(scenario, {"correct": 0, "total": 0})
            bucket["total"] += 1
            if winner_matches_gt(scenario, gt, winner):
                bucket["correct"] += 1
            else:
                wrong += 1
                reasons.append(f"wrong-direction {scenario} seed={r.get('seed')} gt={gt} winner={winner}")
        else:
            bucket = indec.setdefault(scenario, {"correct": 0, "total": 0})
            bucket["total"] += 1
            if winner == "none":
                bucket["correct"] += 1
            else:
                wrong += 1
                reasons.append(f"indecisive-violation {scenario} seed={r.get('seed')} winner={winner}")

    if wall_s > wall_limit_s or serial_s > wall_limit_s:
        return RecoveryAcceptanceReport(
            gate="INCONCLUSIVE",
            strong_effect=strong,
            indecisive=indec,
            wrong_direction=wrong,
            diagnostics_failures=diag_fail,
            wall_s=wall_s,
            serial_s=serial_s,
            reasons=tuple(reasons + [f"wall/serial exceeded {wall_limit_s}s"]),
        )

    for scenario, counts in strong.items():
        if counts["total"] >= 5 and counts["correct"] < 4:
            reasons.append(f"strong-effect {scenario}: {counts['correct']}/{counts['total']} < 4/5")

    for scenario, counts in indec.items():
        if counts["total"] >= 5 and counts["correct"] < 5:
            reasons.append(f"indecisive {scenario}: {counts['correct']}/{counts['total']} < 5/5")

    if diag_fail:
        reasons.append(f"diagnostics_ok false on {diag_fail}/{len(results)} slots")

    gate = "PASS" if not reasons else "FAIL"
    return RecoveryAcceptanceReport(
        gate=gate,
        strong_effect=strong,
        indecisive=indec,
        wrong_direction=wrong,
        diagnostics_failures=diag_fail,
        wall_s=wall_s,
        serial_s=serial_s,
        reasons=tuple(reasons),
    )


def scenario_from_value(value: str) -> RecoveryScenario:
    return RecoveryScenario(value)

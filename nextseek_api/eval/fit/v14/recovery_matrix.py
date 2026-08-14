"""Frozen 8×5 recovery matrix for V4-4 validation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from nextseek_api.eval.fit.v14.pair_rows import JointQualityState, LatencyObservationKind, PairFitRow

__all__ = [
    "RECOVERY_SCENARIOS",
    "RecoveryScenario",
    "RecoverySlot",
    "build_scenario_rows",
    "matrix_fingerprint",
    "recovery_seeds",
]


class RecoveryScenario(str, Enum):
    null_equal = "null_equal"
    ns_strong_quality = "ns_strong_quality"
    cc_strong_quality = "cc_strong_quality"
    quality_eq_ns_faster = "quality_eq_ns_faster"
    quality_eq_cc_faster = "quality_eq_cc_faster"
    below_min_support = "below_min_support"
    right_censor_30pct = "right_censor_30pct"
    adversarial_outliers = "adversarial_outliers"


RECOVERY_SCENARIOS: tuple[RecoveryScenario, ...] = tuple(RecoveryScenario)


def recovery_seeds() -> tuple[int, ...]:
    return (11, 22, 33, 44, 55)


@dataclass(frozen=True)
class RecoverySlot:
    scenario: RecoveryScenario
    seed: int
    slot_index: int


def matrix_fingerprint() -> str:
    payload = {
        "contract": "v14b-ruling-b",
        "scenarios": [s.value for s in RECOVERY_SCENARIOS],
        "seeds": list(recovery_seeds()),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _mk_row(i: int, family: str, state: JointQualityState, lat_kind: LatencyObservationKind, **kw: Any) -> PairFitRow:
    return PairFitRow(
        pair_id=f"p{i}",
        query_id=f"q{i}",
        family=family,
        joint_state=state,
        latency_kind=lat_kind,
        log_latency_ns=kw.get("log_latency_ns"),
        log_latency_cc=kw.get("log_latency_cc"),
        log_d_lower=kw.get("log_d_lower"),
        log_d_upper=kw.get("log_d_upper"),
        cost_usd=kw.get("cost_usd"),
    )


def build_scenario_rows(scenario: RecoveryScenario) -> list[PairFitRow]:
    import math

    rows: list[PairFitRow] = []
    if scenario == RecoveryScenario.below_min_support:
        for i in range(4):
            base = 2.0 + i * 0.08
            rows.append(
                _mk_row(
                    i,
                    "small_fam",
                    JointQualityState.both_succeed,
                    LatencyObservationKind.observed,
                    log_latency_ns=math.log(base),
                    log_latency_cc=math.log(base * 1.04),
                )
            )
        return rows

    n = 8
    if scenario in {
        RecoveryScenario.quality_eq_ns_faster,
        RecoveryScenario.quality_eq_cc_faster,
        RecoveryScenario.null_equal,
        RecoveryScenario.adversarial_outliers,
        RecoveryScenario.right_censor_30pct,
    }:
        n = 12
    for i in range(n):
        if scenario == RecoveryScenario.null_equal:
            if i % 3 == 0:
                rows.append(_mk_row(i, "fam_a", JointQualityState.both_fail, LatencyObservationKind.latency_uninformative))
                continue
            base = 2.0 + i * 0.05
            if i % 2 == 0:
                log_ns, log_cc = math.log(base), math.log(base * 1.05)
            else:
                log_ns, log_cc = math.log(base * 1.05), math.log(base)
            rows.append(
                _mk_row(
                    i,
                    "fam_a",
                    JointQualityState.both_succeed,
                    LatencyObservationKind.observed,
                    log_latency_ns=log_ns,
                    log_latency_cc=log_cc,
                )
            )
            continue
        if scenario == RecoveryScenario.ns_strong_quality:
            st = JointQualityState.nextseek_only_succeeds
        elif scenario == RecoveryScenario.cc_strong_quality:
            st = JointQualityState.container_cc_only_succeeds
        elif scenario in {RecoveryScenario.quality_eq_ns_faster, RecoveryScenario.quality_eq_cc_faster, RecoveryScenario.right_censor_30pct}:
            st = JointQualityState.both_succeed
        elif scenario == RecoveryScenario.adversarial_outliers:
            st = JointQualityState.both_fail if i % 4 == 0 else JointQualityState.both_succeed
        else:
            st = JointQualityState.nextseek_only_succeeds if i % 5 else JointQualityState.container_cc_only_succeeds

        if scenario == RecoveryScenario.right_censor_30pct and i % 3 == 0:
            rows.append(_mk_row(i, "fam_a", st, LatencyObservationKind.both_censored))
            continue
        if scenario == RecoveryScenario.right_censor_30pct:
            base = 2.0 + i * 0.05
            if i % 2 == 0:
                log_ns, log_cc = math.log(base), math.log(base * 1.04)
            else:
                log_ns, log_cc = math.log(base * 1.04), math.log(base)
            rows.append(
                _mk_row(
                    i,
                    "fam_a",
                    st,
                    LatencyObservationKind.observed,
                    log_latency_ns=log_ns,
                    log_latency_cc=log_cc,
                )
            )
            continue
        if scenario == RecoveryScenario.quality_eq_ns_faster:
            rows.append(
                _mk_row(
                    i,
                    "fam_a",
                    st,
                    LatencyObservationKind.observed,
                    log_latency_ns=math.log(1.0 + i * 0.02),
                    log_latency_cc=math.log(2.0 + i * 0.02),
                )
            )
            continue
        if scenario == RecoveryScenario.quality_eq_cc_faster:
            rows.append(
                _mk_row(
                    i,
                    "fam_a",
                    st,
                    LatencyObservationKind.observed,
                    log_latency_ns=math.log(2.0 + i * 0.02),
                    log_latency_cc=math.log(1.0 + i * 0.02),
                )
            )
            continue
        if scenario == RecoveryScenario.adversarial_outliers:
            if i % 4 == 0:
                rows.append(_mk_row(i, "fam_a", JointQualityState.both_fail, LatencyObservationKind.latency_uninformative))
                continue
            base = 2.0 + i * 0.04
            if i % 2 == 0:
                log_ns, log_cc = math.log(base), math.log(base * 1.04)
            else:
                log_ns, log_cc = math.log(base * 1.04), math.log(base)
            rows.append(
                _mk_row(
                    i,
                    "fam_a",
                    JointQualityState.both_succeed,
                    LatencyObservationKind.observed,
                    log_latency_ns=log_ns,
                    log_latency_cc=log_cc,
                )
            )
            continue
        if scenario == RecoveryScenario.ns_strong_quality:
            rows.append(
                _mk_row(
                    i,
                    "fam_a",
                    st,
                    LatencyObservationKind.observed,
                    log_latency_ns=math.log(1.2 + i * 0.04),
                    log_latency_cc=math.log(2.4 + i * 0.04),
                )
            )
            continue
        if scenario == RecoveryScenario.cc_strong_quality:
            rows.append(
                _mk_row(
                    i,
                    "fam_a",
                    st,
                    LatencyObservationKind.observed,
                    log_latency_ns=math.log(2.4 + i * 0.04),
                    log_latency_cc=math.log(1.2 + i * 0.04),
                )
            )
            continue
        rows.append(_mk_row(i, "fam_a", st, LatencyObservationKind.observed, log_latency_ns=math.log(2.0 + i * 0.01), log_latency_cc=math.log(2.1)))
    return rows


def ground_truth(scenario: RecoveryScenario) -> str:
    if scenario in {RecoveryScenario.ns_strong_quality, RecoveryScenario.quality_eq_ns_faster}:
        return "strong_ns"
    if scenario in {RecoveryScenario.cc_strong_quality, RecoveryScenario.quality_eq_cc_faster}:
        return "strong_cc"
    return "indecisive"

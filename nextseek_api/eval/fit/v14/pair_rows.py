"""Pair-preserving fit input rows (V10-E: no RouteFamilyAggregate)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nextseek_api.eval.conservation import FitAdmission
from nextseek_api.eval.router_models_proposal import RouteFamilyAggregate

__all__ = [
    "JointQualityState",
    "LatencyObservationKind",
    "PairFitRow",
    "RouteFamilyAggregateRejected",
    "build_pair_rows",
    "joint_state_from_success",
]


class RouteFamilyAggregateRejected(TypeError):
    """RouteFamilyAggregate discards pair identity and cannot be fit input."""


class JointQualityState(str, Enum):
    both_succeed = "both_succeed"
    nextseek_only_succeeds = "nextseek_only_succeeds"
    container_cc_only_succeeds = "container_cc_only_succeeds"
    both_fail = "both_fail"


class LatencyObservationKind(str, Enum):
    observed = "observed"
    ns_right_censored = "ns_right_censored"
    cc_right_censored = "cc_right_censored"
    both_censored = "both_censored"
    latency_uninformative = "latency_uninformative"


class PairFitRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pair_id: str
    query_id: str
    family: str
    joint_state: JointQualityState
    latency_kind: LatencyObservationKind
    log_latency_ns: float | None = None
    log_latency_cc: float | None = None
    log_d_lower: float | None = None
    log_d_upper: float | None = None
    cost_usd: float | None = None


def joint_state_from_success(ns_success: bool, cc_success: bool) -> JointQualityState:
    if ns_success and cc_success:
        return JointQualityState.both_succeed
    if ns_success:
        return JointQualityState.nextseek_only_succeeds
    if cc_success:
        return JointQualityState.container_cc_only_succeeds
    return JointQualityState.both_fail


def _latency_fields(
    ns_success: bool,
    cc_success: bool,
    ns_latency: float | None,
    cc_latency: float | None,
    ns_censored: bool,
    cc_censored: bool,
) -> tuple[LatencyObservationKind, float | None, float | None, float | None, float | None]:
    if ns_censored and cc_censored:
        return LatencyObservationKind.both_censored, None, None, None, None
    if ns_censored and cc_latency is not None and cc_latency > 0:
        log_cc = float(np_log(cc_latency))
        return LatencyObservationKind.ns_right_censored, None, log_cc, log_cc, None
    if cc_censored and ns_latency is not None and ns_latency > 0:
        log_ns = float(np_log(ns_latency))
        return LatencyObservationKind.cc_right_censored, log_ns, None, None, log_ns
    if ns_latency is not None and cc_latency is not None and ns_latency > 0 and cc_latency > 0:
        return (
            LatencyObservationKind.observed,
            float(np_log(ns_latency)),
            float(np_log(cc_latency)),
            None,
            None,
        )
    return LatencyObservationKind.latency_uninformative, None, None, None, None


def np_log(x: float) -> float:
    import math

    return math.log(x)


def build_pair_rows(
    admission: FitAdmission,
    arm_records: dict[str, dict[str, Any]],
    *,
    paired_batch: "PairedExperimentalBatch | None" = None,
) -> list[PairFitRow]:
    if paired_batch is not None:
        from nextseek_api.eval.fit.fit_boundary import (
            assert_paired_experimental_only,
            require_approved_paired_run,
        )

        assert_paired_experimental_only(paired_batch)
        require_approved_paired_run(paired_batch.paired_run_id)
    rows: list[PairFitRow] = []
    for pair_id, query_id, family in admission.retained_pairs:
        ns_id = _arm_for_pair(arm_records, pair_id, "nextseek")
        cc_id = _arm_for_pair(arm_records, pair_id, "container_cc")
        ns = arm_records[ns_id]
        cc = arm_records[cc_id]
        ns_success = bool(ns.get("combined_success", ns.get("success", False)))
        cc_success = bool(cc.get("combined_success", cc.get("success", False)))
        kind, log_ns, log_cc, lo, hi = _latency_fields(
            ns_success,
            cc_success,
            ns.get("latency_seconds"),
            cc.get("latency_seconds"),
            bool(ns.get("latency_censored", False)),
            bool(cc.get("latency_censored", False)),
        )
        rows.append(
            PairFitRow(
                pair_id=pair_id,
                query_id=query_id,
                family=family,
                joint_state=joint_state_from_success(ns_success, cc_success),
                latency_kind=kind,
                log_latency_ns=log_ns,
                log_latency_cc=log_cc,
                log_d_lower=lo,
                log_d_upper=hi,
                cost_usd=ns.get("cost_usd") or cc.get("cost_usd"),
            )
        )
    return rows


def _arm_for_pair(arm_records: dict[str, dict[str, Any]], pair_id: str, route: str) -> str:
    for arm_id, rec in arm_records.items():
        if rec.get("pair_id") == pair_id and rec.get("route") == route:
            return arm_id
    raise KeyError(f"missing {route} arm for pair {pair_id}")


def reject_aggregate_input(aggregate: RouteFamilyAggregate) -> None:
    raise RouteFamilyAggregateRejected(
        "RouteFamilyAggregate discards query_id/pair identity and cannot be V4-4 fit input"
    )

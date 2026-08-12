"""Route-conditional observational monitoring (V4-7). Telemetry only — no comparative posterior updates."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from nextseek_api.eval.online_observation import DEFAULT_SELECTION_CAVEAT, OnlineObservationalRow

__all__ = [
    "AlertKind",
    "RouteMonitoringAlert",
    "RouteMonitoringSnapshot",
    "MONITORING_DISCLAIMER",
    "build_monitoring_snapshot",
    "build_route_monitoring_summary",
    "detect_monitoring_alerts",
    "format_monitoring_alerts",
]


MONITORING_DISCLAIMER = DEFAULT_SELECTION_CAVEAT

_DEFAULT_POLICY = "unknown"
_DEFAULT_FAMILY = "(missing)"
_DEFAULT_OUTCOME = "unknown"


class AlertKind(str, Enum):
    policy_drift = "policy_drift"
    family_mix_shift = "family_mix_shift"
    missingness_spike = "missingness_spike"
    route_outcome_change = "route_outcome_change"


@dataclass(frozen=True)
class RouteMonitoringAlert:
    kind: AlertKind
    message: str


@dataclass(frozen=True)
class RouteMonitoringSnapshot:
    policy_counts: dict[str, int]
    family_counts: dict[str, int]
    missing_family_count: int
    total: int
    route_outcome_counts: dict[str, dict[str, int]]


def _distribution_drift(
    baseline_counts: dict[str, int],
    current_counts: dict[str, int],
) -> float:
    """Total variation distance between normalized count distributions."""
    keys = set(baseline_counts) | set(current_counts)
    if not keys:
        return 0.0
    baseline_total = sum(baseline_counts.values()) or 1
    current_total = sum(current_counts.values()) or 1
    return max(
        abs(baseline_counts.get(k, 0) / baseline_total - current_counts.get(k, 0) / current_total)
        for k in keys
    )


def build_monitoring_snapshot(
    rows: Iterable[OnlineObservationalRow],
    *,
    outcome_by_observation_id: Mapping[str, str] | None = None,
) -> RouteMonitoringSnapshot:
    policy_counts: dict[str, int] = defaultdict(int)
    family_counts: dict[str, int] = defaultdict(int)
    route_outcome_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    missing_family_count = 0
    total = 0
    outcomes = outcome_by_observation_id or {}

    for row in rows:
        total += 1
        policy = row.assignment_policy or _DEFAULT_POLICY
        policy_counts[policy] += 1
        if row.task_family:
            family_counts[row.task_family] += 1
        else:
            missing_family_count += 1
            family_counts[_DEFAULT_FAMILY] += 1
        outcome = outcomes.get(row.observation_id, _DEFAULT_OUTCOME)
        route_outcome_counts[row.route][outcome] += 1

    return RouteMonitoringSnapshot(
        policy_counts=dict(policy_counts),
        family_counts=dict(family_counts),
        missing_family_count=missing_family_count,
        total=total,
        route_outcome_counts={
            route: dict(counts) for route, counts in route_outcome_counts.items()
        },
    )


def detect_monitoring_alerts(
    baseline: RouteMonitoringSnapshot,
    current: RouteMonitoringSnapshot,
    *,
    drift_threshold: float = 0.15,
    missingness_spike_threshold: float = 0.10,
    outcome_shift_threshold: float = 0.20,
) -> list[RouteMonitoringAlert]:
    """Compare current observational traffic to baseline; emit operational alerts."""
    alerts: list[RouteMonitoringAlert] = []
    if baseline.total == 0 or current.total == 0:
        return alerts

    policy_drift = _distribution_drift(baseline.policy_counts, current.policy_counts)
    if policy_drift >= drift_threshold:
        alerts.append(
            RouteMonitoringAlert(
                kind=AlertKind.policy_drift,
                message=(
                    f"assignment_policy distribution shifted (TV distance {policy_drift:.2f} "
                    f">= {drift_threshold:.2f})"
                ),
            )
        )

    family_drift = _distribution_drift(baseline.family_counts, current.family_counts)
    if family_drift >= drift_threshold:
        alerts.append(
            RouteMonitoringAlert(
                kind=AlertKind.family_mix_shift,
                message=(
                    f"task_family mix shifted (TV distance {family_drift:.2f} "
                    f">= {drift_threshold:.2f})"
                ),
            )
        )

    baseline_missing_rate = baseline.missing_family_count / baseline.total
    current_missing_rate = current.missing_family_count / current.total
    if current_missing_rate - baseline_missing_rate >= missingness_spike_threshold:
        alerts.append(
            RouteMonitoringAlert(
                kind=AlertKind.missingness_spike,
                message=(
                    f"task_family missingness rose from {baseline_missing_rate:.2f} to "
                    f"{current_missing_rate:.2f} (delta >= {missingness_spike_threshold:.2f})"
                ),
            )
        )

    for route in sorted(set(baseline.route_outcome_counts) & set(current.route_outcome_counts)):
        baseline_counts = baseline.route_outcome_counts[route]
        current_counts = current.route_outcome_counts[route]
        baseline_total = sum(baseline_counts.values()) or 1
        current_total = sum(current_counts.values()) or 1
        outcomes = set(baseline_counts) | set(current_counts)
        max_shift = max(
            abs(baseline_counts.get(o, 0) / baseline_total - current_counts.get(o, 0) / current_total)
            for o in outcomes
        )
        if max_shift >= outcome_shift_threshold:
            alerts.append(
                RouteMonitoringAlert(
                    kind=AlertKind.route_outcome_change,
                    message=(
                        f"route {route!r} outcome mix shifted (max outcome delta {max_shift:.2f} "
                        f">= {outcome_shift_threshold:.2f})"
                    ),
                )
            )

    return alerts


def format_monitoring_alerts(alerts: Iterable[RouteMonitoringAlert]) -> str:
    lines = ["Monitoring alerts:"]
    for alert in alerts:
        lines.append(f"  [{alert.kind.value}] {alert.message}")
    return "\n".join(lines)


def build_route_monitoring_summary(
    rows: Iterable[OnlineObservationalRow],
    *,
    baseline: RouteMonitoringSnapshot | None = None,
    outcome_by_observation_id: Mapping[str, str] | None = None,
) -> str:
    """Aggregate observational rows into route-conditional monitoring text."""
    row_list = list(rows)
    by_route: dict[str, list[OnlineObservationalRow]] = defaultdict(list)
    for row in row_list:
        by_route[row.route].append(row)
    if not by_route:
        return MONITORING_DISCLAIMER
    lines = [MONITORING_DISCLAIMER, ""]
    for route in sorted(by_route):
        group = by_route[route]
        lines.append(f"Route {route}: {len(group)} observational turn(s)")
        policy = next((r.assignment_policy for r in group if r.assignment_policy), None)
        if policy:
            lines.append(f"  assignment_policy={policy}")
        gen = next((r.generation_hash for r in group if r.generation_hash), None)
        if gen:
            lines.append(f"  active_generation_hash={gen}")
        unavailable = sum(1 for r in group if r.propensity_unavailable)
        if unavailable:
            lines.append(f"  propensity_unavailable={unavailable}/{len(group)} turn(s)")
    if baseline is not None and row_list:
        current = build_monitoring_snapshot(row_list, outcome_by_observation_id=outcome_by_observation_id)
        alerts = detect_monitoring_alerts(baseline, current)
        if alerts:
            lines.append("")
            lines.append(format_monitoring_alerts(alerts))
    return "\n".join(lines)

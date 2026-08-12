"""Route-conditional observational monitoring (V4-7). Telemetry only — no comparative posterior updates."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from nextseek_api.eval.online_observation import DEFAULT_SELECTION_CAVEAT, OnlineObservationalRow

__all__ = ["build_route_monitoring_summary", "MONITORING_DISCLAIMER"]


MONITORING_DISCLAIMER = DEFAULT_SELECTION_CAVEAT


def build_route_monitoring_summary(rows: Iterable[OnlineObservationalRow]) -> str:
    """Aggregate observational rows into route-conditional monitoring text."""
    by_route: dict[str, list[OnlineObservationalRow]] = defaultdict(list)
    for row in rows:
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
    return "\n".join(lines)

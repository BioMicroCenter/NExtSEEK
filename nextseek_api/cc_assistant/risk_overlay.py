"""Telemetry-only routing risk overlay (V4-5 / Task 12)."""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from nextseek_api.assistant.models_db import FamilyPosterior

__all__ = ["RiskVerdict", "assess"]


@dataclass(frozen=True)
class RiskVerdict:
    level: str
    reason: str
    may_reroute: bool = False


def _overlay_enabled() -> bool:
    return bool(getattr(settings, "NEXTSEEK_RISK_OVERLAY_ENABLED", False))


def _posterior_for(route: str, task_family: str) -> FamilyPosterior | None:
    from nextseek_api.eval.generation_store import get_active_snapshot

    snapshot = get_active_snapshot()
    if snapshot is None:
        return None
    for row in snapshot.posteriors:
        if row.task_family == task_family and row.route == route:
            return row
    return None


def assess(route: str, task_family: str) -> RiskVerdict:
    if not _overlay_enabled():
        return RiskVerdict(level="disabled", reason="overlay disabled", may_reroute=False)

    row = _posterior_for(route, task_family)
    if row is None:
        return RiskVerdict(level="unknown", reason="no posterior for route/family", may_reroute=False)

    if row.band == "TooUncertain":
        return RiskVerdict(level="unknown", reason="band too uncertain", may_reroute=False)

    if row.band == "Brittle":
        return RiskVerdict(level="high", reason="brittle band", may_reroute=False)

    if row.band == "Reliable":
        return RiskVerdict(level="low", reason="reliable band", may_reroute=False)

    return RiskVerdict(level="medium", reason=f"band {row.band}", may_reroute=False)

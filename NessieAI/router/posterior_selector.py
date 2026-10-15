"""Comparative posterior route selector (V4-6 / Task 12).

HiBayes is imported lazily, inside ``get_active_snapshot``, so importing the
router does not load ``NessieAI.hibayes`` (whose generation store needs the
ORM at module scope). ``GenerationSnapshot`` is an annotation only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings

from NessieAI.router.family_labels import corpus_snapshot

if TYPE_CHECKING:
    from NessieAI.hibayes.generation_store import GenerationSnapshot

__all__ = ["SelectorResult", "posterior_routing_enabled", "select_route"]

ROUTE_NS = "nextseek_query"
ROUTE_CC = "container_cc"

_DECISIVE_BANDS = frozenset({"Reliable", "Watch"})
_FALLBACK_STATUSES = frozenset(
    {
        "legacy_fallback",
        "indecisive",
        "multiplicity_indecisive",
        "too_uncertain",
        "empty_candidate_set",
    }
)


@dataclass(frozen=True)
class SelectorResult:
    route: str
    generation_id: int
    generation_hash: str
    decision_status: str
    reasoning: str


def posterior_routing_enabled() -> bool:
    return bool(getattr(settings, "NEXTSEEK_POSTERIOR_ROUTING_ENABLED", False))


def get_active_snapshot() -> GenerationSnapshot | None:
    """The active HiBayes generation, from the generation store (lazy import)."""
    from NessieAI.hibayes.generation_store import get_active_snapshot as _active_snapshot

    return _active_snapshot()


def snapshot_is_current(snap: GenerationSnapshot) -> bool:
    """True when the snapshot is content-valid and matches the live taxonomy and corpus."""
    try:
        current = corpus_snapshot()
    except Exception:
        return False
    return (
        snap.content_valid
        and snap.taxonomy_version == current.taxonomy_version
        and snap.corpus_hash == current.corpus_sha256
    )


def select_route(task_family: str, *, snapshot: GenerationSnapshot | None = None) -> SelectorResult | None:
    """Return a decisive posterior route for task_family or None to fall back."""
    if not task_family or task_family == "unrelated":
        return None

    try:
        snap = snapshot if snapshot is not None else get_active_snapshot()
    except Exception:  # DB/store failures must never block an assistant turn.
        return None
    if snap is None:
        return None

    if not snapshot_is_current(snap):
        return None

    if snap.decision_status in _FALLBACK_STATUSES:
        return None

    rows = [p for p in snap.posteriors if p.task_family == task_family]
    if not rows:
        return None

    eligible = [p for p in rows if p.band in _DECISIVE_BANDS]
    if not eligible:
        return None

    best = max(eligible, key=lambda p: p.posterior_mean)
    runner_up = sorted(eligible, key=lambda p: p.posterior_mean, reverse=True)
    if len(runner_up) > 1 and abs(runner_up[0].posterior_mean - runner_up[1].posterior_mean) < 0.05:
        return None

    route = best.route
    if route not in {ROUTE_NS, ROUTE_CC}:
        return None

    return SelectorResult(
        route=route,
        generation_id=snap.generation_id,
        generation_hash=snap.generation_hash,
        decision_status=snap.decision_status,
        reasoning=f"posterior favours {route} for {task_family} (mean={best.posterior_mean:.3f})",
    )

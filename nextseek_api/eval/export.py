"""Ledger -> versioned online observational rows (V4-7)."""
from __future__ import annotations

from nextseek_api.assistant.models_db import TurnLedger
from nextseek_api.eval.evidence_kinds import EvidenceKind, ONLINE_OBSERVATION_SCHEMA_VERSION
from nextseek_api.eval.online_observation import (
    DEFAULT_SELECTION_CAVEAT,
    PROPENSITY_UNAVAILABLE_REASON,
    OnlineObservationalRow,
)
from nextseek_api.eval.router_models_proposal import RouteSource

EVAL_ROW_SCHEMA_VERSION = 3

__all__ = [
    "EVAL_ROW_SCHEMA_VERSION",
    "EvalRow",
    "export_rows",
    "export_observational_rows",
    "ledger_row_to_observational",
]


class EvalRow:
    """Legacy thin export row — superseded by OnlineObservationalRow for monitoring."""

    def __init__(
        self,
        session_id: str,
        turn_number: int,
        route: str,
        route_source: str,
        task_family: str | None,
        family_source: str | None,
        created_at: object,
        schema_version: int = EVAL_ROW_SCHEMA_VERSION,
    ):
        self.session_id = session_id
        self.turn_number = turn_number
        self.route = route
        self.route_source = route_source
        self.task_family = task_family
        self.family_source = family_source
        self.created_at = created_at
        self.schema_version = schema_version


def ledger_row_to_observational(row: TurnLedger) -> OnlineObservationalRow:
    try:
        route_source = RouteSource(row.route_source)
    except ValueError as exc:
        raise ValueError(f"unknown route_source {row.route_source!r}") from exc
    if route_source is RouteSource.forced:
        raise ValueError("forced ledger rows are paired experimental, not observational export")
    observation_id = f"{row.session_id}:{row.turn_number}"
    ledger_propensity = getattr(row, "assignment_propensity", None)
    if ledger_propensity is not None:
        return OnlineObservationalRow(
            schema_version=ONLINE_OBSERVATION_SCHEMA_VERSION,
            evidence_kind=EvidenceKind.online_observational,
            observation_id=observation_id,
            session_id=str(row.session_id),
            turn_number=row.turn_number,
            route=row.route,
            route_source=route_source,
            task_family=row.task_family,
            assignment_propensity=float(ledger_propensity),
            propensity_unavailable=False,
            propensity_unavailable_reason=None,
            assignment_policy=row.route_source,
            generation_id=row.pinned_generation_id,
            generation_hash=row.pinned_generation_hash or None,
            selection_caveat=DEFAULT_SELECTION_CAVEAT,
        )
    return OnlineObservationalRow(
        schema_version=ONLINE_OBSERVATION_SCHEMA_VERSION,
        evidence_kind=EvidenceKind.online_observational,
        observation_id=observation_id,
        session_id=str(row.session_id),
        turn_number=row.turn_number,
        route=row.route,
        route_source=route_source,
        task_family=row.task_family,
        assignment_propensity=None,
        propensity_unavailable=True,
        propensity_unavailable_reason=PROPENSITY_UNAVAILABLE_REASON,
        assignment_policy=row.route_source,
        generation_id=row.pinned_generation_id,
        generation_hash=row.pinned_generation_hash or None,
        selection_caveat=DEFAULT_SELECTION_CAVEAT,
    )


def export_observational_rows(since=None) -> list[OnlineObservationalRow]:
    qs = TurnLedger.objects.all().order_by("created_at")
    if since is not None:
        qs = qs.filter(created_at__gt=since)
    rows: list[OnlineObservationalRow] = []
    for ledger in qs:
        if ledger.route_source == RouteSource.forced.value:
            continue
        rows.append(ledger_row_to_observational(ledger))
    return rows


def export_rows(since=None):
    """Legacy export — returns thin EvalRow dataclass-like objects."""
    return [
        EvalRow(
            session_id=str(r.session_id),
            turn_number=r.turn_number,
            route=r.route,
            route_source=r.route_source,
            task_family=r.task_family,
            family_source=r.family_source,
            created_at=r.created_at,
        )
        for r in TurnLedger.objects.all().order_by("created_at")
        if since is None or r.created_at > since
    ]

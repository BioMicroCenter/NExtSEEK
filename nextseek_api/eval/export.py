"""Ledger -> versioned evaluation rows."""
from dataclasses import dataclass

from nextseek_api.assistant.models_db import TurnLedger

EVAL_ROW_SCHEMA_VERSION = 3

__all__ = ["EVAL_ROW_SCHEMA_VERSION", "EvalRow", "export_rows"]


@dataclass(frozen=True)
class EvalRow:
    session_id: str
    turn_number: int
    route: str
    route_source: str
    task_family: str | None
    family_source: str | None
    created_at: object
    schema_version: int = EVAL_ROW_SCHEMA_VERSION


def export_rows(since=None):
    qs = TurnLedger.objects.all().order_by("created_at")
    if since is not None:
        qs = qs.filter(created_at__gt=since)
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
        for r in qs
    ]

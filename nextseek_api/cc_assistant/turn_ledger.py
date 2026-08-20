"""Single write path for the per-turn ledger."""
from django.db import IntegrityError, transaction

from nextseek_api.assistant.models_db import TurnLedger

__all__ = ["LedgerCollision", "record_turn"]


class LedgerCollision(RuntimeError):
    """Two turns claimed the same (session, turn_number)."""


def record_turn(
    session_id,
    turn_number,
    route,
    route_source,
    task_family,
    family_source,
    *,
    pinned_generation_id=None,
    pinned_generation_hash="",
    attempted_route=None,
    attempted_source=None,
):
    try:
        with transaction.atomic():
            return TurnLedger.objects.create(
                session_id=session_id,
                turn_number=turn_number,
                route=route,
                route_source=route_source,
                task_family=task_family,
                family_source=family_source,
                pinned_generation_id=pinned_generation_id,
                pinned_generation_hash=pinned_generation_hash or "",
                attempted_route=attempted_route,
                attempted_source=attempted_source,
            )
    except IntegrityError as exc:
        raise LedgerCollision(
            f"turn {turn_number} already recorded for session {session_id}"
        ) from exc

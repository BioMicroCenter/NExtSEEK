"""Single write path for the per-turn ledger.

``record_turn`` writes one row under an explicit number and raises ``LedgerCollision`` when
the session already has it. ``record_next_turn`` is what a live turn uses: it allocates the
session's next free number, never lower than the caller's floor, and moves on to the number
after when a concurrent turn of the same session commits it first. So a turn that died
before reaching the chat log, a chat past the chat-log cap, or two turns of one chat in
flight at once each still leave their own row.
"""
from django.db import IntegrityError, transaction
from django.db.models import Max

from nextseek_api.assistant.models_db import TurnLedger

__all__ = ["LedgerCollision", "next_turn_number", "record_next_turn", "record_turn"]

# How many numbers record_next_turn tries before giving up. Each retry means another turn of
# the SAME chat committed in between, so a handful covers any real concurrency.
ALLOCATION_ATTEMPTS = 8


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
    query_task=None,
    pinned_generation_id=None,
    pinned_generation_hash="",
    attempted_route=None,
    attempted_source=None,
):
    try:
        with transaction.atomic():
            return TurnLedger.objects.create(
                session_id=session_id,
                query_task=query_task,
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


def next_turn_number(session_id, floor=1) -> int:
    """The session's next free turn number: one past its highest row, and never below ``floor``."""
    top = TurnLedger.objects.filter(session_id=session_id).aggregate(top=Max("turn_number"))["top"]
    return max(int(floor), (top or 0) + 1)


def record_next_turn(session_id, floor, route, route_source, task_family, family_source, **fields):
    """Write this turn's row under the session's next free number; return the row.

    ``floor`` is the number the caller would have used on its own (the live turn passes
    ``len(chat_log) + 1``), so numbers stay aligned with the chat log wherever the ledger is
    not ahead of it. ``fields`` are ``record_turn``'s keyword arguments. Raises
    ``LedgerCollision`` only if every one of ``ALLOCATION_ATTEMPTS`` numbers was taken first.
    """
    collision = None
    for _ in range(ALLOCATION_ATTEMPTS):
        number = next_turn_number(session_id, floor)
        try:
            return record_turn(
                session_id, number, route, route_source, task_family, family_source, **fields
            )
        except LedgerCollision as exc:
            collision = exc
    raise collision

"""Pure, Django-light helpers shared by migration 0009.

Renormalizes ChatSession.extra_state["chat_log"] so every entry carries a
sequential int turn_id (1..N in order), moving any str (UUID) turn_id — written
by the CC path as ``str(run_id)`` — into a distinct ``cc_run_id`` field on that
entry. This heals the poison-pill bug where a str turn_id crashed the NS read
site (``str + int`` TypeError).

Kept as a separate module (mirrors ``_cc_transcript_heal``) so the pure transform
is unit-testable without invoking the migration machinery. DATA-ONLY: no DDL —
``cc_run_id`` is just another key inside the ``extra_state`` JSONField.
"""
from __future__ import annotations

from typing import Any


def renumber_chat_log(entries: Any) -> list:
    """Return a NEW chat_log list with dict entries renumbered 1..N in order.

    - Each dict entry gets a sequential int ``turn_id`` (1-based, in list order).
    - If an entry's original ``turn_id`` is a str, it is preserved as
      ``cc_run_id`` (unless ``cc_run_id`` is already present — never clobbered).
    - Non-dict entries (never observed in the live data) are passed through
      untouched and do NOT consume a turn number.
    - Idempotent: re-running over already-normalized entries reproduces them.
    """
    if not isinstance(entries, list):
        return entries
    out: list = []
    n = 0
    for entry in entries:
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        n += 1
        new_entry = dict(entry)
        old_tid = new_entry.get("turn_id")
        if isinstance(old_tid, str) and "cc_run_id" not in new_entry:
            new_entry["cc_run_id"] = old_tid
        new_entry["turn_id"] = n
        out.append(new_entry)
    return out


def normalize_extra_state(extra_state: Any) -> Any:
    """Return extra_state with its chat_log renumbered, or the input unchanged.

    Tolerant of every shape the live column exhibits: None, dict without a
    chat_log, chat_log that is JSON null or a non-list. Only a NEW dict is
    returned when the chat_log is a list; the caller may compare identity/value
    to decide whether a DB write is needed.
    """
    if not isinstance(extra_state, dict):
        return extra_state
    log = extra_state.get("chat_log")
    if not isinstance(log, list):
        return extra_state
    new_es = dict(extra_state)
    new_es["chat_log"] = renumber_chat_log(log)
    return new_es


def forwards_apply(ChatSession) -> None:
    """RunPython forward: normalize every session's chat_log in place.

    Defensive + idempotent: skips rows whose normalized value is unchanged so
    ``updated_at`` is not churned and untouched sessions are never rewritten.
    Uses ``update_fields=["extra_state"]`` so the auto_now ``updated_at`` is left
    alone for the rows we DO write.
    """
    for session in ChatSession.objects.all().iterator():
        before = session.extra_state
        after = normalize_extra_state(before)
        if after is before or after == before:
            continue
        session.extra_state = after
        session.save(update_fields=["extra_state"])

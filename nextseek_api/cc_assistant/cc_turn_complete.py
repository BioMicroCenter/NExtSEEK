"""Neutral, Django-free helpers for CC turn completion persistence (Task 11/11a)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# First-party in-tree package; importable here because the running app already
# hard-depends on chat_nextseek (no circular import: chat_memory pulls in only
# stdlib, never nextseek_api). The chat_log ENTRY turn_id is derived through the
# SAME helper the NS writer uses, so both paths share one derivation + schema.
from chat_nextseek.chat_memory import next_turn_id


@dataclass
class TurnCompletePayload:
    chat_session: Any  # ChatSession at Django use sites; unused by pure helpers
    user_query: str
    assistant_reply: str
    ts: str
    artifacts: list[dict] | None
    cc_traces: list[dict]
    turn_id: str  # the Celery run UUID (str(run_id)) — load-bearing for the
    # transcript (CCSessionTranscript.turn_id) and artifact keys; NOT the
    # chat_log entry's turn_id (that is a sequential int, see below).
    cc_session_id: str | None
    raw_jsonl: bytes


def serialize_cc_chat_log_entry(payload: TurnCompletePayload, *, turn_id: int) -> dict:
    """Serialize a CC turn into a chat_log entry.

    ``turn_id`` is the entry's *sequential int* id (shared schema); the run UUID
    (``payload.turn_id``) is preserved on the entry as ``cc_run_id`` so the
    transcript/artifact linkage is still recoverable from the chat_log.
    """
    return {
        "user_query": payload.user_query,
        "assistant_reply": payload.assistant_reply,
        "mode": "cc",
        "ts": payload.ts,
        "artifacts": payload.artifacts,
        "cc_traces": payload.cc_traces,
        "turn_id": turn_id,
        "cc_run_id": payload.turn_id,
        "router_choice": "container_cc",
        "status": "completed",
    }


def serialize_non_answer_entry(*, user_query: str, router_choice: str | None,
                               status: str, error: str | None, ts: str,
                               turn_id: int) -> dict:
    """A chat_log entry for a turn that produced no real assistant reply."""
    entry: dict = {
        "turn_id": turn_id,
        "ts": ts,
        "mode": "cc" if router_choice == "container_cc" else (router_choice or "unknown"),
        "user_query": user_query,
        "router_choice": router_choice,
        "status": status,
    }
    if error:
        entry["error"] = error
    return entry


def apply_non_answer_to_extra_state(extra_state: dict | None, *, user_query: str,
                                    router_choice: str | None, status: str,
                                    error: str | None, ts: str,
                                    cap: int = 50) -> dict:
    """Pure RMW: NEW extra_state with the non-answer turn appended to chat_log."""
    es = dict(extra_state or {})
    existing_log = list(es.get("chat_log") or [])
    entry = serialize_non_answer_entry(
        user_query=user_query, router_choice=router_choice, status=status,
        error=error, ts=ts, turn_id=next_turn_id(existing_log))
    es["chat_log"] = append_capped(existing_log, entry, cap=cap)
    return es


def new_terminal_tracker() -> dict:
    return {"error": None, "completed": False}


def wrap_send_event(cb, tracker):
    """Wrap send_event to record terminal state; FIRST query_error wins (AR-16)."""
    def _wrapped(event_type, data):
        if event_type == "query_error" and tracker["error"] is None:
            tracker["error"] = str((data or {}).get("error") or "") or "unknown error"
        elif event_type == "query_complete":
            tracker["completed"] = True
        cb(event_type, data)
    return _wrapped


def should_append_non_answer(tracker: dict, *, unrelated: bool) -> bool:
    return bool(tracker["error"]) and not tracker["completed"] and not unrelated


def append_capped(chat_log: list, entry: dict, *, cap: int = 50) -> list:
    """Append ``entry`` to ``chat_log`` and keep only the **newest** ``cap`` turns
    (FIFO eviction of the oldest). Returns a NEW list (does not mutate the input).

    Pure + Django-free so it is hermetically unit-tested (the live gate never
    reaches the 50-turn boundary). Newest-kept is load-bearing: a ``chat_log[:cap]``
    mutation (keep oldest) must FAIL `test_append_capped_keeps_newest_in_order`."""
    out = list(chat_log)
    out.append(entry)
    if len(out) > cap:
        out = out[-cap:]
    return out


def apply_turn_to_extra_state(extra_state: dict | None, payload: TurnCompletePayload,
                              *, cap: int = 50) -> dict:
    """Pure RMW transform: return a NEW extra_state dict with the turn appended to
    BOTH stores in one shot — ``chat_log`` (reload source of truth) AND the locked
    SPEC-3 **E5/§6.5** ``cc_traces`` mirror. Django-free so the E5 mirror is
    hermetically guarded: removing the ``es["cc_traces"]`` append must FAIL
    ``test_apply_turn_writes_chat_log_and_cc_traces_mirror``. The mirror is FIFO-capped
    (same ``cap``) to stay small per §6.5 (loaded on every session read)."""
    es = dict(extra_state or {})
    existing_log = list(es.get("chat_log") or [])
    entry = serialize_cc_chat_log_entry(payload, turn_id=next_turn_id(existing_log))
    es["chat_log"] = append_capped(existing_log, entry, cap=cap)
    cc_traces = list(es.get("cc_traces") or [])
    for tr in payload.cc_traces:
        cc_traces = append_capped(cc_traces, tr, cap=cap)
    es["cc_traces"] = cc_traces
    return es

"""Neutral, Django-free helpers for CC turn completion persistence (Task 11/11a)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TurnCompletePayload:
    chat_session: Any  # ChatSession at Django use sites; unused by pure helpers
    user_query: str
    assistant_reply: str
    ts: str
    artifacts: list[dict] | None
    cc_traces: list[dict]
    turn_id: str
    cc_session_id: str | None
    raw_jsonl: bytes


def serialize_cc_chat_log_entry(payload: TurnCompletePayload) -> dict:
    return {
        "user_query": payload.user_query,
        "assistant_reply": payload.assistant_reply,
        "mode": "cc",
        "ts": payload.ts,
        "artifacts": payload.artifacts,
        "cc_traces": payload.cc_traces,
        "turn_id": payload.turn_id,
    }


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
    es["chat_log"] = append_capped(
        list(es.get("chat_log") or []), serialize_cc_chat_log_entry(payload), cap=cap)
    cc_traces = list(es.get("cc_traces") or [])
    for tr in payload.cc_traces:
        cc_traces = append_capped(cc_traces, tr, cap=cap)
    es["cc_traces"] = cc_traces
    return es

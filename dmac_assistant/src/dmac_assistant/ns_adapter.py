"""NS event to WebSocket frame adapter.

This module is intentionally pure: the WebSocket dispatch loop owns per-turn
state such as ``event_index`` and whether a terminal event has already been
emitted.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_TOOL_USE_EVENTS: frozenset[str] = frozenset(
    {"agent_started", "agent_complete", "search_started", "search_complete"}
)
_RUNNER_INTERNAL_EVENTS: frozenset[str] = frozenset(
    {"ns_runner_error", "ns_runner_error_type"}
)
_DETAIL_ALLOW_LIST: frozenset[str] = frozenset(
    {"error", "partial", "failure", "debug_error", "debug_fatal_error", "unknown"}
)
_STATUS_FAILURE_VALUES: frozenset[str] = frozenset({"error", "partial", "failure"})

KNOWN_QUERY_ERROR_AGENTS: frozenset[str] = frozenset(
    {
        "catalog",
        "entity",
        "parser",
        "api",
        "search",
        "graph",
        "reporter",
        "report_writer",
        "chatter",
        "memory",
        "system",
        "planner",
        "context_engineer",
        "evaluator",
    }
)


def ns_event_to_frames(
    event: dict[str, Any],
    session_id: str,
    event_index: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    """Translate one chat_nextseek JSONL event into WebSocket frames.

    Returns ``(frames, is_terminal)``. Terminal events are ``query_complete`` and
    ``query_error``; the caller is responsible for first-terminal-wins behavior.
    """
    name = event.get("event")
    if not isinstance(name, str):
        log.debug("ns_adapter: dropping event without string event field")
        return [], False

    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    if name in _TOOL_USE_EVENTS:
        return _tool_use_frames(name, payload, event_index), False
    if name == "query_complete":
        return _query_complete_frames(payload, session_id), True
    if name == "query_error":
        return _query_error_frames(payload, session_id), True
    if name in _RUNNER_INTERNAL_EVENTS:
        log.debug("ns_adapter: runner-internal event %r dropped", name)
        return [], False

    log.debug("ns_adapter: unknown event %r dropped", name)
    return [], False


def _tool_use_frames(
    name: str,
    payload: dict[str, Any],
    event_index: int,
) -> list[dict[str, Any]]:
    return [
        {
            "type": "tool_use",
            "tool": f"ns:{name}",
            "input": payload,
            "id": f"ns-evt-{event_index}",
        }
    ]


def _present_and_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if value is False:
        return False
    if isinstance(value, str):
        return bool(value)
    try:
        return bool(value)
    except Exception:  # pragma: no cover - bool() is defensive for arbitrary JSON
        return True


def _detect_query_complete_failure(payload: dict[str, Any]) -> str | None:
    status = payload.get("status")
    if isinstance(status, str) and status in _STATUS_FAILURE_VALUES:
        return status

    error_type = payload.get("error_type")
    if _present_and_non_empty(error_type):
        return "unknown"

    top_error = payload.get("error")
    if _present_and_non_empty(top_error):
        return "unknown"

    debug = payload.get("debug")
    if isinstance(debug, dict):
        debug_error = debug.get("error")
        if _present_and_non_empty(debug_error):
            return "debug_error"

        debug_fatal = debug.get("fatal_error")
        if _present_and_non_empty(debug_fatal):
            return "debug_fatal_error"

    return None


def _query_complete_frames(
    payload: dict[str, Any],
    session_id: str,
) -> list[dict[str, Any]]:
    detail = _detect_query_complete_failure(payload)
    if detail is not None:
        if detail not in _DETAIL_ALLOW_LIST:
            detail = "unknown"
        return [
            {
                "type": "error",
                "reason": "ns_query_complete_with_error",
                "detail": detail,
            },
            {"type": "session_ended", "session_id": session_id},
        ]

    reply = payload.get("reply", "")
    if not isinstance(reply, str):
        reply = ""
    return [
        {"type": "assistant_message", "content": reply},
        {"type": "session_ended", "session_id": session_id},
    ]


def _query_error_frames(
    payload: dict[str, Any],
    session_id: str,
) -> list[dict[str, Any]]:
    agent = payload.get("agent")
    if not isinstance(agent, str) or agent not in KNOWN_QUERY_ERROR_AGENTS:
        agent = "unknown"
    return [
        {"type": "error", "reason": "ns_query_error", "detail": agent},
        {"type": "session_ended", "session_id": session_id},
    ]

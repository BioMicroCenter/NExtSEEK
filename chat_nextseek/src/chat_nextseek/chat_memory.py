"""Rolling per-session chat memory.

A compact, token-cheap turn log kept in the existing session_state store. Every
successful `run_query` / `run_query_plan` appends one entry; agents that need
conversational context (parser, multi_parser, chatter, wizard) read the tail.

Each turn is a *summary* — not the full bundle — small enough that 5 turns fit
in a few hundred tokens. `bundle_id` cross-references `results_history` for any
agent that needs to drill into full payloads (memory_coder).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

    from .session import SessionState


CHAT_LOG_KEY = "chat_log"
MAX_TURNS = 50
DEFAULT_TAIL = 5
REPLY_PREVIEW_CHARS = 280
MAX_UID_EXAMPLES = 5

_TAG_PATTERN = re.compile(r"<[^>]+>")

# Shared chat_log entry contract. BOTH writers — this module's `append_turn`
# (NS path) and nextseek_api's `serialize_cc_chat_log_entry` (CC path) — emit
# entries that satisfy these base fields. Anything else (key_entities,
# result_summary on NS; artifacts, cc_traces, cc_run_id on CC) is writer-specific.
CHAT_LOG_REQUIRED_FIELDS: dict[str, type] = {
    "turn_id": int,
    "ts": str,
    "mode": str,
    "user_query": str,
}
CHAT_LOG_OPTIONAL_REPLY_FIELD = "assistant_reply"


class ChatLogEntryError(ValueError):
    """Raised when a chat_log entry violates the shared base schema."""


def next_turn_id(log: Any) -> int:
    """Next sequential turn id: (max over int-coercible turn_ids) + 1, else 1.

    Type-tolerant by construction — a str (UUID) turn_id from a legacy CC entry
    is skipped rather than added, so this never raises the `str + int` TypeError
    the old `log[-1]["turn_id"] + 1` derivation did. Uses *max*, not the tail,
    so it stays correct after FIFO eviction (where the largest id may not be
    last). `bool` is excluded even though it subclasses `int`.
    """
    if not isinstance(log, list):
        return 1
    max_id = 0
    for entry in log:
        if not isinstance(entry, dict):
            continue
        tid = entry.get("turn_id")
        if isinstance(tid, bool):
            continue
        if isinstance(tid, int):
            max_id = max(max_id, tid)
        elif isinstance(tid, str) and tid.strip().lstrip("-").isdigit():
            max_id = max(max_id, int(tid))
    return max_id + 1


def validate_chat_log_entry(entry: Any) -> None:
    """Assert `entry` satisfies the shared base schema; raise ChatLogEntryError.

    Test-time invariant guard: both writers' outputs are pinned against this so a
    future shape divergence (e.g. a str turn_id leaking back in) fails loudly
    instead of silently poisoning the read site.
    """
    if not isinstance(entry, dict):
        raise ChatLogEntryError(
            f"chat_log entry must be a dict, got {type(entry).__name__}")
    for field, typ in CHAT_LOG_REQUIRED_FIELDS.items():
        if field not in entry:
            raise ChatLogEntryError(
                f"chat_log entry missing required field {field!r}")
        val = entry[field]
        if typ is int and isinstance(val, bool):
            raise ChatLogEntryError(
                f"chat_log entry field {field!r} must be int, got bool")
        if not isinstance(val, typ):
            raise ChatLogEntryError(
                f"chat_log entry field {field!r} must be {typ.__name__}, "
                f"got {type(val).__name__}")
    reply = entry.get(CHAT_LOG_OPTIONAL_REPLY_FIELD)
    if reply is not None:
        if not isinstance(reply, str):
            raise ChatLogEntryError(
                f"chat_log entry field 'assistant_reply' must be str or None, "
                f"got {type(reply).__name__}")
        if reply == "":
            raise ChatLogEntryError(
                "chat_log entry field 'assistant_reply' must be non-empty when present "
                "(use None/absent for non-answer turns)")


def _strip_html(value: Any) -> str:
    """Strip HTML tags (NExtSEEK API wraps UIDs in <a href=...> links)."""
    if value is None:
        return ""
    return _TAG_PATTERN.sub("", str(value)).strip()


def _truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _shrink_filters(filters: Any) -> dict[str, Any]:
    """Drop empty/None filter keys; cap list-valued ones at 5 entries."""
    if not isinstance(filters, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in filters.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list) and len(value) > 5:
            out[key] = value[:5] + [f"…+{len(value) - 5} more"]
        else:
            out[key] = value
    return out


def _extract_first_uids(result_payload: Any, limit: int = MAX_UID_EXAMPLES) -> list[str]:
    """Best-effort grab of leading UIDs from a heterogeneous result shape."""
    if not isinstance(result_payload, dict):
        return []
    rows: list[Any] = []
    data = result_payload.get("data")
    if isinstance(data, dict):
        for key in ("rows", "nodes", "data"):
            val = data.get(key)
            if isinstance(val, list):
                rows = val
                break
    elif isinstance(data, list):
        rows = data

    uids: list[str] = []
    for row in rows[:limit]:
        if isinstance(row, dict):
            uid = row.get("uid") or row.get("UID") or row.get("uuid") or row.get("sample_uid")
            cleaned = _strip_html(uid)
            if cleaned:
                uids.append(cleaned)
        elif isinstance(row, str):
            cleaned = _strip_html(row)
            if cleaned:
                uids.append(cleaned)
    return uids


def _extract_count(result_payload: Any) -> int | None:
    if not isinstance(result_payload, dict):
        return None
    data = result_payload.get("data")
    if isinstance(data, dict):
        for key in ("total", "total_samples", "total_nodes", "count"):
            val = data.get(key)
            if isinstance(val, int):
                return val
        for key in ("rows", "nodes", "data"):
            val = data.get(key)
            if isinstance(val, list):
                return len(val)
    elif isinstance(data, list):
        return len(data)
    if isinstance(result_payload.get("count"), int):
        return result_payload["count"]
    return None


def _key_entities(entity_result: Any) -> dict[str, list[str]]:
    if hasattr(entity_result, "model_dump"):
        entity_result = entity_result.model_dump()
    if not isinstance(entity_result, dict):
        return {"sampletypes": [], "assays": [], "projects": [], "uids": []}
    return {
        "sampletypes": [s.get("code") for s in (entity_result.get("sampletypes") or []) if isinstance(s, dict) and s.get("code")],
        "assays": [a.get("code") for a in (entity_result.get("assays") or []) if isinstance(a, dict) and a.get("code")],
        "projects": list(entity_result.get("projects") or []),
        "uids": list(entity_result.get("uids") or []),
    }


def append_turn(
    session: "SessionState | SessionStateProxy",
    *,
    user_query: str,
    mode: str | None,
    intent_summary: str | None = None,
    entity_result: Any = None,
    tool_summary: dict[str, Any] | None = None,
    result_payload: Any = None,
    assistant_reply: str | None = None,
    bundle_id: int | None = None,
    wizard_state: dict[str, Any] | None = None,
    router_choice: str | None = "nextseek_query",
    status: str = "completed",
    error: str | None = None,
) -> None:
    """Append a compact turn summary. Capped FIFO at MAX_TURNS."""
    log = session.get(CHAT_LOG_KEY) or []
    if not isinstance(log, list):
        log = []

    turn = {
        "turn_id": next_turn_id(log),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_query": _truncate(user_query, 500),
        "intent_summary": _truncate(intent_summary, 240),
        "mode": mode or "",
        "key_entities": _key_entities(entity_result),
        "tool_summary": _shrink_tool_summary(tool_summary),
        "result_summary": {
            "count": _extract_count(result_payload),
            "first_uids": _extract_first_uids(result_payload),
        },
        "assistant_reply": _strip_debug_block(assistant_reply or ""),
        "assistant_reply_preview": _truncate(_strip_debug_block(assistant_reply or ""), REPLY_PREVIEW_CHARS),
        "bundle_id": bundle_id,
        "router_choice": router_choice,
        "status": status,
    }
    if error:
        turn["error"] = error
    if wizard_state:
        turn["wizard_state"] = wizard_state

    log.append(turn)
    if len(log) > MAX_TURNS:
        log = log[-MAX_TURNS:]
    session[CHAT_LOG_KEY] = log


def _shrink_tool_summary(tool_summary: dict[str, Any] | None) -> dict[str, Any]:
    """Trim a tool_summary dict so it doesn't bleed tokens (e.g., full requestBody)."""
    if not isinstance(tool_summary, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in tool_summary.items():
        if value in (None, "", [], {}):
            continue
        if key == "filters":
            out[key] = _shrink_filters(value)
            continue
        if isinstance(value, dict):
            # Cap dict values to top-level keys + small payloads
            if len(json.dumps(value, default=str)) > 400:
                out[key] = {"keys": list(value.keys())[:8]}
            else:
                out[key] = value
        elif isinstance(value, list) and len(value) > 5:
            out[key] = value[:5] + [f"…+{len(value) - 5} more"]
        else:
            out[key] = value
    return out


def _strip_debug_block(reply: str) -> str:
    """The chatter appends a ```json debug block; drop it for the preview."""
    if not reply:
        return ""
    idx = reply.find("**Debug info**")
    if idx > 0:
        reply = reply[:idx]
    return reply.strip()


def _is_answered(entry: Any) -> bool:
    """True when the entry carries a real assistant reply (non-empty str)."""
    return isinstance(entry, dict) and bool(entry.get(CHAT_LOG_OPTIONAL_REPLY_FIELD))


def recent_turns(
    session: "SessionState | SessionStateProxy | None",
    n: int = DEFAULT_TAIL,
) -> list[dict[str, Any]]:
    """Return the most recent up-to-N answered turns. Older first; empty list if no log."""
    if session is None:
        return []
    log = session.get(CHAT_LOG_KEY) or []
    if not isinstance(log, list):
        return []
    if n <= 0:
        return []
    answered = [e for e in log if _is_answered(e)]
    return answered[-n:]


def format_for_prompt(turns: list[dict[str, Any]]) -> str:
    """Render a compact, LLM-friendly chat history block. Empty string if no turns."""
    if not turns:
        return ""
    lines = ["CHAT_HISTORY (most recent last; refer back to these for pronouns/follow-ups):"]
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        header = f"- turn {turn.get('turn_id', '?')} ({turn.get('mode') or 'unknown'})"
        if turn.get("wizard_state"):
            ws = turn["wizard_state"]
            step = ws.get("step")
            if step is not None:
                header += f" [wizard step={step}]"
        lines.append(header)
        if turn.get("user_query"):
            lines.append(f"  user: {turn['user_query']}")
        if turn.get("intent_summary"):
            lines.append(f"  intent: {turn['intent_summary']}")
        entities = turn.get("key_entities") or {}
        ent_parts = []
        for key in ("sampletypes", "assays", "projects", "uids"):
            val = entities.get(key)
            if val:
                ent_parts.append(f"{key}={val}")
        if ent_parts:
            lines.append("  entities: " + ", ".join(ent_parts))
        ts = turn.get("tool_summary") or {}
        if ts:
            lines.append(f"  tool: {json.dumps(ts, default=str, separators=(',', ':'))}")
        rs = turn.get("result_summary") or {}
        if rs.get("count") is not None or rs.get("first_uids"):
            count_str = rs.get("count")
            uids_str = ", ".join(rs.get("first_uids") or []) or "—"
            lines.append(f"  result: count={count_str} uids=[{uids_str}]")
        if turn.get("assistant_reply_preview"):
            lines.append(f"  assistant: {turn['assistant_reply_preview']}")
    return "\n".join(lines)


def history_block(
    session: "SessionState | SessionStateProxy | None",
    n: int = DEFAULT_TAIL,
) -> str:
    """Convenience: fetch tail-N and format in one call. Empty if nothing logged."""
    return format_for_prompt(recent_turns(session, n=n))


def build_tool_summary_for_mode(
    mode: str,
    *,
    api_plan: dict | None = None,
    parser_plan: dict | None = None,
    graph_plan: dict | None = None,
    reporter_plan: dict | None = None,
    target_endpoint: str | None = None,
) -> dict[str, Any]:
    """Project per-mode execution details down to a tiny dict suitable for the log."""
    if mode in {"new_search", "refine_last_search"}:
        endpoint = (api_plan or {}).get("endpoint") or target_endpoint
        filters = (parser_plan or {}).get("filters") if parser_plan else None
        return {"endpoint": endpoint, "filters": filters}
    if mode == "graph_query":
        return {
            "cypher_preview": _truncate((graph_plan or {}).get("cypher"), 160),
            "explanation": _truncate((graph_plan or {}).get("explanation"), 160),
        }
    if mode == "reporter":
        return {
            "reporter_mode": (reporter_plan or {}).get("reporter_mode"),
            "summary_mode": (reporter_plan or {}).get("summary_mode"),
            "report_type": (reporter_plan or {}).get("report_type"),
            "project": (reporter_plan or {}).get("project"),
        }
    if mode == "ask_about_last_results":
        return {"target_bundle": (parser_plan or {}).get("target_result_id")}
    if mode in {"system_question", "unsupported"}:
        return {}
    return {}


def clear_log(session: "SessionState | SessionStateProxy") -> None:
    """Drop the chat log for this session (used by tests / explicit user reset)."""
    session[CHAT_LOG_KEY] = []


# Common English stopwords + NExtSEEK chrome — strip from keyword overlap scoring.
_RECALL_STOPWORDS = frozenset({
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or", "but", "is", "are",
    "was", "were", "be", "been", "being", "did", "do", "does", "done", "have", "has", "had",
    "will", "would", "could", "should", "may", "might", "can", "shall",
    "this", "that", "these", "those", "it", "its", "as",
    "from", "with", "by", "into", "out", "up", "down", "over", "under",
    "find", "show", "tell", "list", "give", "get", "what", "which", "who", "whom", "whose",
    "how", "many", "much", "where", "when", "why",
    "first", "second", "third", "very", "originally", "earliest", "earlier", "back",
    "search", "results", "result", "session", "above", "below", "before", "after",
    "samples", "sample", "data", "datasets", "dataset", "those", "them", "they",
    "i", "we", "you", "me", "my", "our", "your",
    "did", "going", "way",
})

_RECALL_WORD_PATTERN = re.compile(r"\b[\w.-]{2,}\b")


def _significant_words(text: str) -> set[str]:
    """Lowercase content words from `text` minus stopwords; preserves dots/dashes
    (so 'D.SEQ', 'NDMA', 'MUS-200901ENG-23-PUB' survive intact)."""
    if not text:
        return set()
    return {
        w.lower()
        for w in _RECALL_WORD_PATTERN.findall(text)
        if w.lower() not in _RECALL_STOPWORDS
    }


def resolve_bundle_for_recall(
    session: "SessionState | SessionStateProxy | None",
    user_text: str,
    *,
    fallback_to_latest: bool = True,
) -> dict | None:
    """Pick the bundle a user's memory question is most likely about.

    Scores every bundle in `results_history` by significant-word overlap between
    the current `user_text` and the bundle's stored `user_query`. Used by the
    orchestrator's memory branch when the parser sets `target_result_id=null`
    (which happens when the parser identifies the relevant bundle is outside
    the recent_results_summary window).

    Returns the highest-scoring bundle, or the most recent bundle as a fallback
    when no overlap is found. Returns None only when results_history is empty.
    """
    if session is None:
        return None
    history = session.get("results_history") or []
    if not history:
        return None
    keywords = _significant_words(user_text)
    if not keywords:
        return history[-1] if fallback_to_latest else None
    best_score = 0
    best_bundle: dict | None = None
    for bundle in history:
        bundle_query = bundle.get("user_query") or ""
        bundle_words = _significant_words(bundle_query)
        score = len(keywords & bundle_words)
        # Tiebreak by recency: same score = pick the more recent bundle.
        if score > best_score:
            best_score = score
            best_bundle = bundle
    if best_bundle is None and fallback_to_latest:
        return history[-1]
    return best_bundle

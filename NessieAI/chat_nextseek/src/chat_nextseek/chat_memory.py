"""Rolling per-session chat memory.

A compact, token-cheap turn log kept in the existing session_state store. Every
successful `run_query` / `run_query_plan` appends one entry; agents that need
conversational context (parser, multi_parser, chatter, wizard) read the tail.

Each turn is a *summary* — not the full bundle — small enough that
`MEMORY_WINDOW` turns fit in a few thousand tokens. `bundle_id` cross-references `results_history` for any
agent that needs to drill into full payloads (memory_coder).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any, TYPE_CHECKING

from .artifacts import load_api_result_full, load_memory_payload

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

    from .session import SessionState


CHAT_LOG_KEY = "chat_log"
MAX_TURNS = 50
#: How far back the parser looks in both of its memories of the conversation: the
#: answered turns of this log (`history_block`) and the result bundles of the
#: recent-results summary (`build_recent_results_summary`). They used to be 5 turns
#: against 8 bundles, so a bundle the summary listed could come from a turn the log no
#: longer showed. 8 because the bundle window was widened from 3 to 8 to cure a recall
#: cliff in long sessions; the chatter reads the same default.
MEMORY_WINDOW = 8
DEFAULT_TAIL = MEMORY_WINDOW
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


def select_refine_bundle(
    history: Any,
    target_result_id: Any,
) -> tuple[dict | None, dict[str, Any]]:
    """The stored result a refine modifies, and a record of how it was chosen.

    The parser names the result it means in ``target_result_id``, on a refine as on a
    memory question: the recent-results summary lists every bundle's id for exactly
    that purpose. The refine branch used to read ``results_history[-1]`` whatever the
    parser said, so "rerun the first search but only females" refined the newest
    result. Now the named bundle wins; with no name the newest is still the one a
    refine means ("same thing but with X").

    An id that is not in the session falls back to the newest rather than failing a
    turn that worked before, and the record says so (``chosen_by="named_missing"``).
    No keyword guess: "now only the lung ones" modifies the newest result, whatever
    an older query happened to mention.
    """
    requested = target_result_id
    bundles = [b for b in history if isinstance(b, dict)] if isinstance(history, list) else []
    if not bundles:
        return None, {"bundle_id": None, "requested": requested, "chosen_by": "none"}
    if requested is not None and not isinstance(requested, bool):
        named = named_bundle(bundles, requested)
        if named is not None:
            return named, {"bundle_id": named.get("id"), "requested": requested, "chosen_by": "named"}
        chosen_by = "named_missing"
        print(f"[DEBUG][REFINE] parser named bundle id={requested!r}, which this session does not hold; "
              "refining the newest result instead")
    else:
        chosen_by = "newest"
    newest = bundles[-1]
    return newest, {"bundle_id": newest.get("id"), "requested": requested, "chosen_by": chosen_by}


# --- Reading a stored result bundle: the one reader every agent uses (moved from agents/followup.py) ---

def _rows_in(data: Any) -> list | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("rows", "samples", "results", "nodes"):
            if isinstance(data.get(key), list):
                return data[key]
        groups = _grouped_records(data)
        if groups is not None:  # a retrieve body: the samples, not the sample-type groups
            return _flatten_groups(groups)
        if isinstance(data.get("data"), list):  # a plain list under data.data (people, SEEK passthrough)
            return data["data"]
    return None


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _payload_extent(data: Any) -> tuple[int | None, bool]:
    """``(total, a further page follows)`` as a stored result body states them.

    ``{"rows", "total"}`` is a search, and a plan-mode step's copy of one; ``truncated`` true
    says it is cut short (a plan filter over a capped step, whose total is not known). A DRF
    page is ``{count, next, previous, results}``: ``count`` is its total, and a ``next`` that
    is not null means a page follows, with or without a count.
    """
    if not isinstance(data, Mapping):
        return None, False
    total = data["total"] if _is_count(data.get("total")) else None
    if total is None and _is_count(data.get("total_samples")):  # a retrieve body
        total = data["total_samples"]
    page = isinstance(data.get("results"), list)
    if total is None and page and _is_count(data.get("count")):
        total = data["count"]
    return total, (page and data.get("next") is not None) or data.get("truncated") is True


def bundle_rows_and_extent(bundle: dict) -> tuple[list, int | None, bool, bool]:
    """``(rows, total stored beside them, a further page follows or they were cut short, from a plan step)``.

    A plan bundle's rows are its final step's (``_step_rows``), read first: its graph result is
    its first graph step's, and its memory payload the builder's pick, and either can be an
    earlier set than the one the plan ended with.
    A graph result's rows come with its own total, which ``describe_stored_result`` reads.
    Any other result's rows and total come from memory_payload, then the full API result,
    the first total found winning: a plan-mode filter step keeps its own rows and total in
    memory_payload, and the larger total of the search it filtered, in the full API result,
    is not theirs. A memory payload that was just read back from the full API result is not
    loaded a second time.
    """
    plan_rows, plan_total, plan_capped = _step_rows(bundle)
    if plan_rows is not None:
        return plan_rows, plan_total, plan_capped, True

    graph_result = bundle.get("graph_result") or {}
    if isinstance(graph_result.get("data"), list):
        return graph_result["data"], None, False, False

    def sources():
        yield load_memory_payload(bundle)
        kept = bundle.get("memory_payload")
        if not (isinstance(kept, dict) and kept and "data" not in kept):
            yield load_api_result_full(bundle)

    rows: list | None = None
    total: int | None = None
    more = False
    for candidate in sources():
        if not isinstance(candidate, dict):
            continue
        data = candidate.get("data")
        if rows is None:
            rows = _rows_in(data)
        stated, follows = _payload_extent(data)
        if total is None:
            total = stated
        more = more or follows
        if rows is not None and total is not None:
            break
    return rows or [], total, more, False


def _step_rows(bundle: dict) -> tuple[list | None, int | None, bool]:
    """``(rows, total, capped)`` of the step that produced a plan bundle's final result: the LAST successful step
    in ``step_results`` whose output holds rows, so "those" after a search and a filter is the filtered set. Its
    total and cap are ``plan_step_extent``'s. ``(None, None, False)`` when no step holds rows.
    """
    last = _final_step_key(bundle)
    if last is None:
        return None, None, False
    step_results = bundle["step_results"]
    total, capped = plan_step_extent(step_results, last, plan_steps=_plan_steps(bundle))
    return _step_rows_of(step_results[last]), total, capped


def _final_step_key(bundle: dict) -> Any:
    """The ``step_results`` key of the LAST successful plan step whose output holds rows, or None."""
    step_results = bundle.get("step_results")
    if not isinstance(step_results, Mapping):
        return None
    last = None
    for key, result in step_results.items():
        if _step_rows_of(result) is not None:
            last = key
    return last


def _step_rows_of(result: Any) -> list | None:
    """A successful plan step's rows, or None."""
    if not isinstance(result, Mapping) or not result.get("ok"):
        return None
    output = result.get("output")
    return output["data"] if isinstance(output, Mapping) and isinstance(output.get("data"), list) else None


def _plan_steps(bundle: dict) -> list:
    """The plan's steps as the bundle keeps them (``plan``, the planner output), or []."""
    plan = bundle.get("plan")
    steps = plan.get("steps") if isinstance(plan, Mapping) else None
    return list(steps) if isinstance(steps, list) else []


def _field(step: Any, name: str) -> Any:
    return step.get(name) if isinstance(step, Mapping) else getattr(step, name, None)


def _find_step(step_results: Mapping, key: Any) -> tuple[Any, Any]:
    """``(key as stored, result)`` for ``key``, matched as text: a session round trip makes an int key a string."""
    for stored, result in step_results.items():
        if str(stored) == str(key):
            return stored, result
    return None, None


def _step_inputs(step_results: Mapping, key: Any, plan_steps: list) -> list:
    """The earlier steps a plan step's rows derive from: a filter's ``source_step_id``; for the intersection, the
    steps the plan marks ``intersect``, or every other step when the plan's steps are not known; and for any step,
    the ``depends_on`` and ``input_mapping`` sources the plan records for it (a search seeded with an earlier step's
    UIDs)."""
    result = step_results.get(key)
    output = result.get("output") if isinstance(result, Mapping) else None
    output = output if isinstance(output, Mapping) else {}
    inputs: list = []
    if result.get("tool") == "coding_filter" or "source_step_id" in output:
        if output.get("source_step_id") is not None:
            inputs.append(output["source_step_id"])
    if result.get("tool") == "intersection" or str(key) == "intersection":
        marked = [_field(s, "step_id") for s in plan_steps if _field(s, "combine_mode") == "intersect"]
        inputs.extend(marked if plan_steps else [k for k in step_results if str(k) != str(key)])
    step = next((s for s in plan_steps if str(_field(s, "step_id")) == str(key)), None)
    if step is not None:
        if _field(step, "depends_on") is not None:
            inputs.append(_field(step, "depends_on"))
        mapping = _field(step, "input_mapping")
        for ref in (mapping.values() if isinstance(mapping, Mapping) else ()):
            if _field(ref, "from_step") is not None:
                inputs.append(_field(ref, "from_step"))
    return [i for i in inputs if str(i) != str(key)]


def _own_extent(output: Mapping) -> tuple[int | None, bool]:
    """``(total, capped)`` as one step's output states them. A graph step's ``count`` is only the number of rows
    returned, so its ``total`` is its own ``total`` (``orchestrator._plan_graph_result``), and a step that hit its
    LIMIT is ``truncated``. A step without a ``total`` (a search, a filter) has its ``count`` as its total unless it
    was cut short. Capped means truncated, or a total larger than the rows it holds."""
    rows = output.get("data") if isinstance(output.get("data"), list) else []
    truncated = bool(output.get("truncated"))
    total = output["total"] if _is_count(output.get("total")) else None
    if total is None and not truncated and _is_count(output.get("count")):
        total = output["count"]
    return total, truncated or (total is not None and total > len(rows))


def plan_step_extent(step_results: Mapping, key: Any, *, plan_steps: list | None = None) -> tuple[int | None, bool]:
    """``(total, capped)`` of plan step ``key``'s rows.

    A step is capped by its own output (``_own_extent``), or by any earlier successful capped step it derives from
    (``_step_inputs``, followed through every step in between): a filter over 1,000 of 36,622 rows holds a filtered
    part of a part, however whole its own count looks. An inherited cap reads as capped with an unknown total.
    """
    plan_steps = list(plan_steps or [])
    stored, result = _find_step(step_results, key)
    output = result.get("output") if isinstance(result, Mapping) else None
    if not isinstance(output, Mapping):
        return None, False
    total, capped = _own_extent(output)
    if capped:
        return total, True
    seen = {str(stored)}
    pending = list(_step_inputs(step_results, stored, plan_steps))
    while pending:
        source, source_result = _find_step(step_results, pending.pop())
        if source is None or str(source) in seen:
            continue
        seen.add(str(source))
        if _step_rows_of(source_result) is None:
            continue
        if _own_extent(source_result["output"])[1]:
            return None, True
        pending.extend(_step_inputs(step_results, source, plan_steps))
    return total, False


_UID_KEY = re.compile(r"(?i)(?:.*[._])?u?uid")


def row_uid(row: Any) -> str:
    """The sample UID one row names, '' when none. The one rule: a UID column, else a column the query named
    ``x_uid`` or ``s.uuid``, else a whole node the row holds (its ``uuid``). Never ``id`` (the SEEK id) or
    ``name`` (a breakdown's label), and no scan of values."""
    if not isinstance(row, dict):
        return ""
    for key in ("uid", "UID", "uuid", "UUID", "sample_uid"):
        if row.get(key):
            return _strip_html(row[key])
    for key, value in row.items():
        if isinstance(value, str) and _UID_KEY.fullmatch(str(key)) and value.strip():
            return _strip_html(value)
    for value in row.values():
        if isinstance(value, dict) and value.get("uuid"):
            return _strip_html(value["uuid"])
    return ""


def uids_from_rows(rows: list) -> list[str]:
    return [uid for uid in map(row_uid, rows) if uid]


def bundle_rows(bundle: dict) -> list:
    """Rows from wherever this bundle put them, without loading a payload twice."""
    return bundle_rows_and_extent(bundle)[0]


def bundle_uids(bundle: dict) -> list[str]:
    return uids_from_rows(bundle_rows(bundle))


def named_bundle(history: Any, target_result_id: Any) -> dict | None:
    """The stored result the parser's ``target_result_id`` names, or None (no id, a bool, or not in the session)."""
    if target_result_id is None or isinstance(target_result_id, bool) or not isinstance(history, list):
        return None
    return next((b for b in reversed(history) if isinstance(b, dict) and b.get("id") == target_result_id), None)


#: `POST samples/retrieve/` (and its alias `admin/samples/retrieve/`) answers `{total_samples,
#: total_sample_types, total_children, failed_uids, data: [{sample_type, n_samples, samples: [...]}],
#: lineage_complete}` (`SampleRetrieveResponse`, `nextseek_api/models.py`). `data["data"]` is a list,
#: so every row-counting and row-previewing helper below "recognised" it and then
#: counted and previewed the sample TYPES rather than the samples.
_GROUP_LABEL_KEY = "sample_type"
_GROUP_RECORDS_KEY = "samples"


def _grouped_records(data: object) -> list[dict] | None:
    """The groups of a grouped-by-sample-type body, or None for any other shape.

    Deliberately exact rather than structural: it matches only a list whose every
    member carries both a ``sample_type`` label and a ``samples`` list. A SEEK
    JSON:API passthrough also puts a list at ``data["data"]`` (its members carry
    ``id``/``type``/``attributes``), and that one must keep its existing handling.
    """
    if not isinstance(data, dict):
        return None
    groups = data.get("data")
    if not isinstance(groups, list) or not groups:
        return None
    for group in groups:
        if not isinstance(group, dict):
            return None
        if _GROUP_LABEL_KEY not in group or not isinstance(group.get(_GROUP_RECORDS_KEY), list):
            return None
    return groups


def _flatten_groups(groups: list[dict]) -> list[dict]:
    """Records from every group, round-robin, each tagged with its group label.

    Round-robin, not group order, so a preview of a mixed lineage shows every type
    present. B7 is the reply that reported 1,904 lineage records and showed none of
    the PAT samples the user asked about; a preview drawn from the first group only
    would reproduce that.
    """
    tagged = [
        [
            {_GROUP_LABEL_KEY: group.get(_GROUP_LABEL_KEY), **record}
            for record in group.get(_GROUP_RECORDS_KEY) or []
            if isinstance(record, dict)
        ]
        for group in groups
    ]
    out: list[dict] = []
    for index in range(max((len(rows) for rows in tagged), default=0)):
        for rows in tagged:
            if index < len(rows):
                out.append(rows[index])
    return out

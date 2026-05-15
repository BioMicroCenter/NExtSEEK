"""One-shot directive-driven nf-core pipeline agent.

Replaces the interactive nfcore_wizard. A user message is parsed into a
{pipeline, samples, group_by} triple, resolved against the catalog +
lineage, sanity-checked against the metadata bundle, then built into a
samplesheet via the existing reporter→report_writer→emitter chain. The
user validates, optionally edits, then submits to Tower.

Public surface:
- `is_active(session)`
- `start(session, config, *, user_query, parser_plan, reporter_plan, log_dir=None)`
- `handle_turn(session, config, user_text, *, log_dir=None)`
- `clear(session)`
- `snapshot_for_chat_log(session)`
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

    from .config import ChatConfig
    from .session import SessionState

# Expose the LLM helper at module level so tests can patch it via
# "chat_nextseek.pipeline_agent._pipeline_directive_parse".
from .agents import _pipeline_directive_parse  # noqa: E402


PIPELINE_AGENT_KEY = "pipeline_agent"


# ─────────────────────────────────────────────────────────────────────────────
# Phase enum (kept as string literals to survive JSON round-trips on the
# Django side without a dependency on Python enums)
# ─────────────────────────────────────────────────────────────────────────────

PHASE_DIRECTIVE_PARSE = "directive_parse"
PHASE_AWAITING_GROUPBY = "awaiting_groupby_clarification"
PHASE_AWAITING_SANITY = "awaiting_sanity_clarification"
PHASE_AWAITING_PIPELINE_SWITCH = "awaiting_pipeline_switch"
PHASE_AWAITING_VALIDATION = "awaiting_validation"
PHASE_SUBMITTING = "submitting"
PHASE_DONE = "done"

ACTIVE_PHASES = frozenset({
    PHASE_AWAITING_GROUPBY,
    PHASE_AWAITING_SANITY,
    PHASE_AWAITING_PIPELINE_SWITCH,
    PHASE_AWAITING_VALIDATION,
})


CANCEL_TOKENS = {"cancel", "/cancel", "abort", "never mind", "drop", "drop it"}
SUBMIT_TOKENS = {"submit", "send", "send it", "go", "launch", "submit it", "send to tower"}


# ─────────────────────────────────────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────────────────────────────────────


def _state(session: "SessionState | SessionStateProxy") -> dict[str, Any]:
    raw = session.get(PIPELINE_AGENT_KEY)
    return raw if isinstance(raw, dict) else {}


def _save_state(session: "SessionState | SessionStateProxy", state: dict[str, Any]) -> None:
    session[PIPELINE_AGENT_KEY] = state


def is_active(session: "SessionState | SessionStateProxy") -> bool:
    state = _state(session)
    if not state:
        return False
    return bool(state.get("active")) and state.get("phase") in ACTIVE_PHASES


def clear(session: "SessionState | SessionStateProxy") -> None:
    session[PIPELINE_AGENT_KEY] = {}


def snapshot_for_chat_log(session: "SessionState | SessionStateProxy") -> dict[str, Any]:
    state = _state(session)
    if not state:
        return {}
    directive = state.get("directive") or {}
    resolution = state.get("resolution") or {}
    return {
        "phase": state.get("phase"),
        "pipeline": directive.get("pipeline_key"),
        "group_by_phrase": directive.get("group_by_phrase"),
        "source_uid_count": len(resolution.get("source_uids") or []),
        "leaf_count": len(resolution.get("leaves_filtered") or []),
        "samplesheet_row_count": len(state.get("samplesheet_rows") or []),
    }


def _wants_cancel(text: str) -> bool:
    norm = (text or "").strip().lower()
    return norm in CANCEL_TOKENS or norm.startswith("/cancel")


def _wants_submit(text: str) -> bool:
    norm = (text or "").strip().lower()
    return norm in SUBMIT_TOKENS


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points (implementations land in later tasks)
# ─────────────────────────────────────────────────────────────────────────────


def start(
    session: "SessionState | SessionStateProxy",
    config: "ChatConfig",
    *,
    user_query: str,
    parser_plan: Any,
    reporter_plan: Any,
    log_dir: str | None = None,
) -> dict[str, Any]:
    """Entry point invoked by the orchestrator on a fresh NFCORE intent.

    Parses the user message into a DirectiveParseOutput, stores state, and
    routes to the appropriate sub-flow (build/question/reject).
    """
    pinned_summary = _summarize_pinned_bundle(session)
    parsed = _pipeline_directive_parse(
        config=config,
        user_query=user_query,
        pinned_bundle_summary=pinned_summary,
    )

    state = {
        "active": True,
        "phase": PHASE_DIRECTIVE_PARSE,
        "directive": parsed.model_dump(),
        "original_query": user_query,
    }
    _save_state(session, state)

    if parsed.sub_mode == "reject":
        clear(session)
        reason = parsed.rejection_reason or "Not a samplesheet directive."
        reply = (
            f"{reason} I only run when you ask to build a pipeline samplesheet "
            "(e.g. 'run rnaseq on these mice'). Ask the regular assistant for other questions."
        )
        return {"action": "cancel", "reply": reply, "params": None}

    if parsed.sub_mode == "question":
        return _run_question_flow(session, config, parsed, log_dir=log_dir)

    return _run_build_flow(session, config, parsed, log_dir=log_dir)


def handle_turn(
    session: "SessionState | SessionStateProxy",
    config: "ChatConfig",
    user_text: str,
    *,
    log_dir: str | None = None,
) -> dict[str, Any]:
    """Advance the pipeline_agent one user turn."""
    state = _state(session)
    if not state.get("active"):
        return {"action": "passthrough", "reply": "", "params": None}

    if _wants_cancel(user_text):
        clear(session)
        return {
            "action": "cancel",
            "reply": "Cancelled. Ask me a fresh question whenever you're ready.",
            "params": None,
        }

    phase = state.get("phase")

    if phase == PHASE_AWAITING_VALIDATION:
        if _wants_submit(user_text):
            return _handle_submit(session, config, log_dir=log_dir)
        return _handle_edit(session, config, user_text, log_dir=log_dir)

    if phase == PHASE_AWAITING_GROUPBY:
        return _handle_groupby_clarification(session, config, user_text, log_dir=log_dir)

    if phase == PHASE_AWAITING_PIPELINE_SWITCH:
        return _handle_pipeline_switch(session, config, user_text, log_dir=log_dir)

    if phase == PHASE_AWAITING_SANITY:
        return _handle_sanity_clarification(session, config, user_text, log_dir=log_dir)

    clear(session)
    return {"action": "passthrough", "reply": "", "params": None}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _summarize_pinned_bundle(session: "SessionState | SessionStateProxy") -> str:
    """One-line summary of the user's most recent results bundle, for the
    directive-parse LLM's pinned context. Empty string when none."""
    history = session.get("results_history") or []
    if not isinstance(history, list) or not history:
        return ""
    last = history[-1]
    if not isinstance(last, dict):
        return ""
    user_q = last.get("user_query") or ""
    rows = ((last.get("api_result_full") or {}).get("data") or {}).get("rows") or []
    return f"last search: query={user_q!r}, ~{len(rows) if isinstance(rows, list) else 0} rows"


def _run_build_flow(session, config, parsed, *, log_dir):
    """Implemented across Tasks 6 (resolve) → 7 (sanity) → 8 (groupby) → 9 (build).
    Stubbed for Task 5 — returns a placeholder reply."""
    return {
        "action": "ask",
        "reply": "(build flow lands in Tasks 6–9; directive parsed successfully)",
        "params": None,
    }


def _run_question_flow(session, config, parsed, *, log_dir):
    """Implemented in Task 12."""
    clear(session)
    return {
        "action": "passthrough",
        "reply": "(question sub-mode lands in Task 12)",
        "params": None,
    }


def _handle_submit(session, config, *, log_dir):
    raise NotImplementedError("_handle_submit lands in Task 11")


def _handle_edit(session, config, user_text, *, log_dir):
    raise NotImplementedError("_handle_edit lands in Task 10")


def _handle_groupby_clarification(session, config, user_text, *, log_dir):
    raise NotImplementedError("_handle_groupby_clarification lands in Task 8")


def _handle_pipeline_switch(session, config, user_text, *, log_dir):
    raise NotImplementedError("_handle_pipeline_switch lands in Task 7")


def _handle_sanity_clarification(session, config, user_text, *, log_dir):
    raise NotImplementedError("_handle_sanity_clarification lands in Task 7")

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

    Stubbed in Task 4; implemented across Tasks 5–9.
    """
    raise NotImplementedError("pipeline_agent.start lands in Task 5")


def handle_turn(
    session: "SessionState | SessionStateProxy",
    config: "ChatConfig",
    user_text: str,
    *,
    log_dir: str | None = None,
) -> dict[str, Any]:
    """Advance the pipeline_agent one user turn.

    Stubbed in Task 4; implemented across Tasks 5–12.
    """
    raise NotImplementedError("pipeline_agent.handle_turn lands in Tasks 5–12")

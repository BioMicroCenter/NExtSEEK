"""builder_tools.py — Tool wrappers for the nf-core wizard builder step.

Exposes BUILDER_TOOL_SCHEMAS (Anthropic native tool dicts) and
dispatch_tool_call(), which the tool-use loop in agents.py calls for
every tool_use content block returned by the builder LLM.

Import layout
--------------
- memory_agent_answer is imported at module level (no circular risk because
  agents.py does not import from builder_tools at module load time; Task 6
  will add a lazy import there).
- _plan_tool_* helpers are imported lazily inside the wrapper functions to
  keep the dependency clear and avoid any future circular-import issues.
- run_query is imported lazily inside _tool_passthrough_to_parser to prevent
  the orchestrator → builder_tools → orchestrator cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .agents import memory_agent_answer

if TYPE_CHECKING:
    from .config import ChatConfig
    from .session import SessionState


# ---------------------------------------------------------------------------
# Schema definitions — descriptions copied verbatim from the plan spec
# ---------------------------------------------------------------------------

BUILDER_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "memory_query",
        "description": (
            "Answer a question about the user's currently-pinned sample set "
            "(typically the last search). Use this for 'show me the UIDs', "
            "'how many are tumor', 'which lab', 'what fields exist', etc. "
            "Does not modify selection state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Natural-language question about the pinned bundle.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "refine_last_search",
        "description": (
            "Apply API filters to narrow the currently-selected UIDs. Use this "
            "to handle 'only tumor', 'drop published', 'restrict to BTC'. "
            "Returns the filtered UIDs; the wizard prompt is responsible for "
            "asking the user to confirm before committing via finalize_turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "API filter dict (sampletype_code, assay_codes, "
                        "keywords, projects, etc.) to apply to the current "
                        "selection."
                    ),
                },
            },
            "required": ["filters"],
        },
    },
    {
        "name": "advanced_search",
        "description": (
            "Run a fresh NExtSEEK search. Returns UIDs that match. Use this "
            "for 'also add the FLY samples' or 'find me the IMPACT controls'. "
            "The wizard prompt must ask the user whether to REPLACE the current "
            "selection or UNION new UIDs into it before committing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-form NExtSEEK search query.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "passthrough_to_parser",
        "description": (
            "Forward an off-topic question (e.g. 'what's NDMA?', 'what "
            "investigations exist?') to the normal parser pipeline and return "
            "its reply. Use ONLY when the question is unrelated to samplesheet "
            "construction. Does not modify selection state. The wizard prompt "
            "must re-orient the user back to the builder after presenting the "
            "passthrough answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_text": {
                    "type": "string",
                    "description": "The user's off-topic question, verbatim.",
                },
            },
            "required": ["user_text"],
        },
    },
    {
        "name": "finalize_turn",
        "description": (
            "End this builder turn. MUST be called as the LAST tool of every "
            "turn. action='stay' continues the conversation; action='advance' "
            "transitions to the confirm step; action='cancel' clears the "
            "wizard. selection_updates is a partial dict merged into the "
            "current selection (uids, cohort_criteria, enrichment_fields)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["stay", "advance", "cancel"],
                },
                "selection_updates": {
                    "type": "object",
                    "description": "Partial dict merged into selection.",
                },
                "reply": {
                    "type": "string",
                    "description": "User-facing assistant message for this turn.",
                },
            },
            "required": ["action", "reply"],
        },
    },
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_pinned_bundle(session: "SessionState") -> dict | None:
    """Return the bundle dict the current selection is pinned to, or None.

    Lookup order:
    1. Match by bundle_id in session["nfcore_wizard"]["pinned_context"]["bundle_id"].
    2. If bundle_id is None but results_history is non-empty, return the last entry.
    3. Return None if results_history is empty or missing.
    """
    state = session.get("nfcore_wizard") or {}
    pinned = state.get("pinned_context") or {}
    bundle_id = pinned.get("bundle_id")
    history = session.get("results_history") or []
    if not isinstance(history, list):
        return None
    if bundle_id is None:
        return history[-1] if history else None
    for bundle in history:
        if isinstance(bundle, dict) and bundle.get("bundle_id") == bundle_id:
            return bundle
    return None


def _uids_from_plan_tool_result(result: dict) -> tuple[list[str], int]:
    """Extract (uids, count) from a planner-tool result dict.

    Falls back to the tool's reported `output.count` when data extraction
    yields nothing — this preserves the underlying tool's count signal in
    partial-failure cases (e.g. API returned a count but the row payload
    was empty).
    """
    if not isinstance(result, dict):
        return [], 0
    output = result.get("output") if isinstance(result.get("output"), dict) else {}
    rows = output.get("data") or []
    uids: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                uid = row.get("uid") or row.get("UID") or row.get("id")
                if uid:
                    uids.append(str(uid))
    reported_count = output.get("count")
    if uids:
        count = len(uids)
    elif isinstance(reported_count, int):
        count = reported_count
    else:
        count = 0
    return uids, count


def _make_minimal_plan_step(tool: str, filters: dict, query: str = "", step_id: int = 1) -> Any:
    """Build a PlanStep with only the fields required by _plan_tool_new_search / _plan_tool_refine_last_search."""
    from .schemas.planner import PlanStep, StepExecutionPayload

    execution = StepExecutionPayload(
        filters=filters,
        tool_query=query,
    )
    return PlanStep(
        step_id=step_id,
        tool=tool,  # type: ignore[arg-type]
        context_prompt=query,
        execution=execution,
    )


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _tool_memory_query(config: "ChatConfig", session: "SessionState", tool_input: dict) -> str:
    """Answer a question about the pinned bundle via memory_agent_answer."""
    bundle = _resolve_pinned_bundle(session)
    if bundle is None:
        return (
            "No pinned result bundle found. Ask the user to run a search or paste UIDs first "
            "so there is a bundle to query."
        )
    question = tool_input.get("question") or ""
    return memory_agent_answer(config=config, user_query=question, result_bundle=bundle)


def _tool_refine_last_search(config: "ChatConfig", session: "SessionState", tool_input: dict) -> dict:
    """Apply filter dict to the last search and return {uids, count}.

    Approach: build a minimal PlanStep with tool="refine_last_search" so that
    _plan_tool_refine_last_search can read session["results_history"] to inherit
    the previous search context, then overlay the provided filters.
    """
    from .agents import _plan_tool_refine_last_search

    filters = tool_input.get("filters") or {}
    try:
        step = _make_minimal_plan_step(
            tool="refine_last_search",
            filters=filters,
            query="",
        )
        result = _plan_tool_refine_last_search(
            config=config,
            session=session,
            step=step,
            query="",
            entity_result={},
            log_dir=None,
            enriched_context={},
        )
        uids, count = _uids_from_plan_tool_result(result)
        return {"uids": uids, "count": count}
    except Exception as exc:
        print(f"[DEBUG][BUILDER_TOOLS][refine_last_search] error: {exc!r}")
        return {"uids": [], "count": 0, "error": str(exc)}


def _tool_advanced_search(config: "ChatConfig", session: "SessionState", tool_input: dict) -> dict:
    """Run a fresh NExtSEEK search and return {uids, count}.

    Approach: build a minimal PlanStep with tool="new_search" and delegate to
    _plan_tool_new_search, which handles entity resolution internally via the
    existing synthetic_plan / api_agent_build_request path.
    """
    from .agents import _plan_tool_new_search

    query = tool_input.get("query") or ""
    try:
        step = _make_minimal_plan_step(
            tool="new_search",
            filters={},
            query=query,
        )
        result = _plan_tool_new_search(
            config=config,
            session=session,
            step=step,
            query=query,
            entity_result={},
            log_dir=None,
            enriched_context={},
        )
        uids, count = _uids_from_plan_tool_result(result)
        return {"uids": uids, "count": count}
    except Exception as exc:
        print(f"[DEBUG][BUILDER_TOOLS][advanced_search] error: {exc!r}")
        return {"uids": [], "count": 0, "error": str(exc)}


def _tool_passthrough_to_parser(config: "ChatConfig", session: "SessionState", tool_input: dict) -> str:
    """Forward an off-topic question to the normal run_query pipeline.

    Temporarily clears session["nfcore_wizard"] to prevent the wizard wedge
    from intercepting the call and recursing, then restores it in a finally block.
    """
    # Lazy import to prevent orchestrator → builder_tools → orchestrator cycle.
    from .orchestrator import run_query

    user_text = tool_input.get("user_text") or ""
    saved_wizard = session.get("nfcore_wizard")
    # Temporarily deactivate wizard so run_query takes the normal parser path.
    session["nfcore_wizard"] = {}
    try:
        result = run_query(session=session, config=config, user_text=user_text)
    except Exception as exc:
        print(f"[DEBUG][BUILDER_TOOLS][passthrough_to_parser] error: {exc!r}")
        return f"Couldn't answer that off-topic question: {exc}"
    finally:
        if saved_wizard is not None:
            session["nfcore_wizard"] = saved_wizard
        elif "nfcore_wizard" in session:
            # We set it to {}; remove if it wasn't present before.
            del session["nfcore_wizard"]
    if isinstance(result, dict):
        return result.get("reply", "")
    return str(result)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def dispatch_tool_call(
    *,
    config: "ChatConfig",
    session: "SessionState",
    tool_name: str,
    tool_input: dict,
) -> Any:
    """Run one builder tool call and return its result.

    ``finalize_turn`` is a control-flow signal: the loop driver in agents.py
    intercepts it before it reaches this dispatcher. If it somehow arrives here
    (e.g. in tests), returning None is the defined contract.

    Raises ValueError for unknown tool names.
    """
    if tool_name == "finalize_turn":
        return None
    if tool_name == "memory_query":
        return _tool_memory_query(config, session, tool_input)
    if tool_name == "refine_last_search":
        return _tool_refine_last_search(config, session, tool_input)
    if tool_name == "advanced_search":
        return _tool_advanced_search(config, session, tool_input)
    if tool_name == "passthrough_to_parser":
        return _tool_passthrough_to_parser(config, session, tool_input)
    raise ValueError(f"Unknown wizard builder tool: {tool_name!r}")

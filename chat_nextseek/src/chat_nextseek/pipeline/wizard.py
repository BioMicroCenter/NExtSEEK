"""nfcore wizard — legacy interactive flow superseded by pipeline.agent.

DEPRECATED: This module is the predecessor of pipeline.agent. Scheduled for
removal once pipeline.agent covers all the slot-collection and builder flows
that nfcore_wizard currently handles. New code should use pipeline.agent.

Tracked by tests/test_nfcore_wizard_*.py — when those tests are migrated to
exercise pipeline.agent instead, this file can be deleted.

----

Two-step state machine: builder → confirm. The builder step is a single
free-form slot-fill chat dispatched through `_wizard_agent_builder` in
`agents.py`, which runs an Anthropic-style function-calling loop with
tools for memory queries, search refinement, fresh searches, parser
passthrough, and turn finalization. The four slots (pipeline, uids,
cohort_criteria, enrichment_fields) can be filled in any order — the
LLM prompts toward whichever slots are still missing.

Cancel detection (the literal tokens `cancel` / `/cancel` / `abort` /
`exit wizard`) is a deterministic short-circuit that bypasses the LLM
entirely.

Public surface:
- `is_active(session)`            — True iff a wizard is mid-flight
- `start(session, config, ...)`   — begin a new wizard; returns the intro reply
- `handle_turn(session, config, user_text)` — advance with the next user message
- `clear(session)`                — drop wizard state
- `snapshot_for_chat_log(session)` — compact state for chat_log turn metadata
- `format_available_pipelines(config)` — dynamic intro list

`handle_turn` / `start` return a WizardResult dict:
    {"action": "ask"|"execute"|"cancel"|"passthrough", "reply": str,
     "params": dict | None}
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

    from ..config import ChatConfig
    from ..session import SessionState


WIZARD_KEY = "nfcore_wizard"

# Note: STEP_PIPELINE was retired when the wizard moved to a slot-fill model;
# the constant is left for back-compat with any persisted session state that
# stored step="pipeline" — `is_active()` treats it as an old-schema sentinel.
STEP_PIPELINE = "pipeline"
STEP_BUILDER = "builder"
STEP_CONFIRM = "confirm"
STEP_DONE = "done"

STEPS_ORDERED = [STEP_BUILDER, STEP_CONFIRM]

CANCEL_TOKENS = {"cancel", "/cancel", "abort", "exit wizard", "back to chat"}
MAX_OFF_TOPIC_STAYS = 2  # consecutive stays where user msg doesn't fit step → auto-cancel

_UID_TAG_PATTERN = re.compile(r"<[^>]+>")


def _clean_uid(value: Any) -> str:
    """Strip HTML tags + whitespace from a raw UID value (the API often wraps
    UIDs in `<a href=...>UID</a>` for the legacy web UI)."""
    if value is None:
        return ""
    return _UID_TAG_PATTERN.sub("", str(value)).strip()


def _state(session: "SessionState | SessionStateProxy") -> dict[str, Any]:
    raw = session.get(WIZARD_KEY)
    return raw if isinstance(raw, dict) else {}


def _save_state(session: "SessionState | SessionStateProxy", state: dict[str, Any]) -> None:
    session[WIZARD_KEY] = state


def is_active(session: "SessionState | SessionStateProxy") -> bool:
    state = _state(session)
    if not state:
        return False
    # Old-schema guard: pre-builder-mode state had top-level uids/cohort_criteria/enrichment_fields.
    # Clear it and treat as inactive so the user can re-enter the wizard fresh.
    if "uids" in state or "cohort_criteria" in state or "enrichment_fields" in state:
        session[WIZARD_KEY] = {}
        return False
    # Old-schema guard 2: pre-slot-fill state had a separate pipeline step. Sessions
    # parked on step="pipeline" can't progress under the new state machine, so reset.
    if state.get("step") == STEP_PIPELINE:
        session[WIZARD_KEY] = {}
        return False
    return bool(state.get("active")) and state.get("step") not in (None, STEP_DONE)


def clear(session: "SessionState | SessionStateProxy") -> None:
    session[WIZARD_KEY] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline catalog rendering (dynamic from reports/nfcore/*.json)
# ─────────────────────────────────────────────────────────────────────────────


def _pipeline_catalog_for_prompt(config: "ChatConfig") -> list[dict[str, Any]]:
    """List of {key, display_name, description, required_columns} for every
    pipeline template in `reports/nfcore/`. Used by both the user-facing
    intro list and the wizard_agent's STEP_CONTEXT."""
    try:
        from ..seqera.catalog import NFCORE_PIPELINE_CATALOG
    except Exception:
        NFCORE_PIPELINE_CATALOG = {}

    out: list[dict[str, Any]] = []
    base_dir = Path(config.BASE_DIR) / "reports" / "nfcore"
    if not base_dir.is_dir():
        return out
    for path in sorted(base_dir.glob("*.json")):
        key = path.stem.lower()
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            out.append({"key": key, "display_name": f"nf-core/{key}", "description": "(template unreadable)"})
            continue
        pipeline_block = doc.get("pipeline") or {}
        catalog_entry = NFCORE_PIPELINE_CATALOG.get(key, {})
        out.append({
            "key": key,
            "display_name": pipeline_block.get("name") or f"nf-core/{key}",
            "description": (
                catalog_entry.get("description")
                or (doc.get("description") or "").split(". ")[0].strip()
                or ""
            ),
            "required_columns": pipeline_block.get("required_columns")
                or catalog_entry.get("required_columns")
                or [],
        })
    return out


def format_available_pipelines(config: "ChatConfig") -> str:
    """Render a markdown bullet list of pipelines."""
    entries = _pipeline_catalog_for_prompt(config)
    if not entries:
        return "_(No pipeline templates available in `reports/nfcore/`.)_"
    lines = []
    for entry in entries:
        if entry["description"]:
            lines.append(f"- `{entry['key']}` — **{entry['display_name']}**: {entry['description']}")
        else:
            lines.append(f"- `{entry['key']}` — **{entry['display_name']}**")
    return "\n".join(lines)


def _known_pipeline_keys(config: "ChatConfig") -> list[str]:
    return [e["key"] for e in _pipeline_catalog_for_prompt(config)]


# ─────────────────────────────────────────────────────────────────────────────
# Cancel detection (deterministic short-circuit)
# ─────────────────────────────────────────────────────────────────────────────


def _wants_cancel(text: str) -> bool:
    norm = (text or "").strip().lower()
    return norm in CANCEL_TOKENS or norm.startswith("/cancel")


# ─────────────────────────────────────────────────────────────────────────────
# Step-shape heuristics: does the user's message look like an answer to THIS step?
# Used by the stay-counter auto-cancel so the wizard isn't a roach motel.
# ─────────────────────────────────────────────────────────────────────────────

# Verbs that strongly signal "user wants the chatbot to do a NEW task" — not an
# answer about the current wizard step. Cheap heuristic; pairs with the LLM
# prompt's cancel-on-out-of-scope rule.
_OFF_TOPIC_TRIGGER_PHRASES = (
    "find me", "find all", "show me", "list ", "tell me about", "how many",
    "what investigations", "what samples", "what assays", "what studies",
    "going way back", "earlier you", "previously you", "originally",
    "search for", "retrieve", "give me a list",
)


def _is_off_topic_for_step(step: str, user_text: str) -> bool:
    """Cheap heuristic: True iff the user message looks like a fresh task / past-search
    query rather than an answer or clarifying question about the current step.

    Conservative — false positives just keep the user in the wizard one extra turn.
    False negatives (missing a clear off-topic) defer to the LLM's cancel-on-cancel rule.
    """
    norm = (user_text or "").strip().lower()
    if not norm:
        return False
    return any(phrase in norm for phrase in _OFF_TOPIC_TRIGGER_PHRASES)


# ─────────────────────────────────────────────────────────────────────────────
# Step-specific context builders
# ─────────────────────────────────────────────────────────────────────────────


def _last_search_context(session: "SessionState | SessionStateProxy") -> dict[str, Any]:
    """Pull the most recent search/graph bundle's count + UID preview.

    Also reports whether we walked past zero-result bundles to find this one,
    so the wizard can transparently tell the user which bundle was used when
    the *most recent* search returned no rows.
    """
    history = session.get("results_history") or []
    skipped: list[dict[str, Any]] = []  # bundles with zero UIDs we walked past
    for bundle in reversed(history):
        if not isinstance(bundle, dict):
            continue
        api_full = bundle.get("api_result_full") or {}
        data = api_full.get("data") if isinstance(api_full, dict) else None
        rows: list[Any] = []
        if isinstance(data, dict):
            for key in ("rows", "nodes", "data"):
                val = data.get(key)
                if isinstance(val, list):
                    rows = val
                    break
        elif isinstance(data, list):
            rows = data
        if not rows:
            graph_result = bundle.get("graph_result") or {}
            if isinstance(graph_result, dict):
                graph_data = graph_result.get("data") or []
                if isinstance(graph_data, list):
                    rows = graph_data
        if not rows:
            # Walked past a zero-result bundle — record for transparency.
            skipped.append({
                "bundle_id": bundle.get("id"),
                "user_query": bundle.get("user_query"),
                "mode": bundle.get("mode"),
            })
            continue
        uids: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                cleaned = _clean_uid(
                    row.get("uid") or row.get("UID") or row.get("uuid") or row.get("sample_uid")
                )
                if cleaned:
                    uids.append(cleaned)
            elif isinstance(row, str):
                cleaned = _clean_uid(row)
                if cleaned:
                    uids.append(cleaned)
        if uids:
            return {
                "bundle_id": bundle.get("id"),
                "user_query": bundle.get("user_query"),
                "mode": bundle.get("mode"),
                "uid_count": len(uids),
                "uid_preview": uids[:10],
                "skipped_bundles": skipped,  # bundles walked past en route to this one
                "_all_uids": uids,  # private — used by handle_turn when LLM says use_last_search
            }
    return {}


def _build_metadata_field_summary(
    config: "ChatConfig",
    uids: list[str],
    *,
    sample_limit: int = 15,
) -> dict[str, Any]:
    """Fetch metadata for up to N UIDs and produce a compact field summary
    for the wizard_agent to reason about cohorts / enrichment.

    Returns a dict with `fetched_for`, `by_sample_type` (each entry has
    `fields: {name: {examples, n_populated}}`), and `lineage_edges`. Returns
    a minimal dict on failure — the LLM is told to handle that gracefully.
    """
    if not uids:
        return {"fetched_for": [], "by_sample_type": {}, "lineage_edges": []}

    from ..helpers import (
        annotate_metadata_with_sampletypes,
        build_metadata_summary,
        fetch_reporter_metadata,
        filter_summary_to_sequencing_lineage,
    )

    sample_uids = list(uids[:sample_limit])
    try:
        raw = fetch_reporter_metadata(config, sample_uids)
        if not raw.get("ok"):
            return {
                "fetched_for": sample_uids,
                "error": raw.get("error") or "Metadata fetch returned not-ok",
                "by_sample_type": {},
                "lineage_edges": [],
            }
        annotated = annotate_metadata_with_sampletypes(config, raw)
        summary = build_metadata_summary({"__sample__": annotated})
        # Trim to lineage-relevant types (D.SEQ + ancestors) — same trimming
        # the report writer uses.
        try:
            summary = filter_summary_to_sequencing_lineage(summary)
        except Exception:
            pass

        # Strip internal _uid_index + drop set-typed _seen markers
        cleaned_by_st: dict[str, dict] = {}
        for st, block in (summary.get("by_sample_type") or {}).items():
            fields_out: dict[str, dict] = {}
            for fname, fdata in (block.get("fields") or {}).items():
                if not isinstance(fdata, dict):
                    continue
                fields_out[fname] = {
                    "n_populated": fdata.get("n_populated"),
                    "examples": fdata.get("examples") or [],
                }
            cleaned_by_st[st] = {
                "n_samples": block.get("n_samples"),
                "fields": fields_out,
            }
        return {
            "fetched_for": sample_uids,
            "by_sample_type": cleaned_by_st,
            "lineage_edges": summary.get("lineage_edges") or [],
            "note": (
                f"Metadata sampled from {len(sample_uids)} of {len(uids)} UIDs "
                "to keep latency low. Field values shown are a representative slice."
            ),
        }
    except Exception as e:
        print(f"[DEBUG][WIZARD] _build_metadata_field_summary failed: {e!r}")
        return {
            "fetched_for": sample_uids,
            "error": repr(e),
            "by_sample_type": {},
            "lineage_edges": [],
        }


def _check_sequencing_presence(
    config: "ChatConfig",
    uids: list[str],
    *,
    sample_limit: int = 15,
) -> dict[str, Any]:
    """Sample up to N of the wizard's selected UIDs and report whether any
    of them have sequencing samples (D.SEQ or equivalent) in their lineage.

    Returns: ``{has_sequencing: bool, sampled: int, error: str | None}``.

    Fail-open on infrastructure errors: if metadata fetch fails (network,
    auth, API), returns ``has_sequencing=True`` so a flaky backend doesn't
    block the user. The guardrail only blocks when we successfully fetched
    and confirmed the lineage contains no sequencing types.
    """
    if not uids:
        return {"has_sequencing": False, "sampled": 0, "error": None}
    summary = _build_metadata_field_summary(config, uids, sample_limit=sample_limit)
    if summary.get("error"):
        return {"has_sequencing": True, "sampled": 0, "error": summary["error"]}
    from ..helpers import _is_sequencing_type

    by_st = summary.get("by_sample_type") or {}
    has_seq = any(_is_sequencing_type(st) for st in by_st.keys())
    return {
        "has_sequencing": has_seq,
        "sampled": len(summary.get("fetched_for") or []),
        "error": None,
    }


def _no_sequencing_reply(total_uids: int, sampled: int) -> str:
    return (
        f"Hold on — I checked the lineage of {sampled} of your {total_uids} "
        f"selected UIDs and none of them have sequencing samples (D.SEQ or "
        f"equivalent) attached. nf-core pipelines need raw reads to run, so "
        f"a samplesheet built from this set wouldn't yield any FASTQs.\n\n"
        f"You can refine the selection to samples with a sequencing assay, "
        f"or run a fresh search like \"find RNA-seq samples in <project>\". "
        f"What would you like to do?"
    )


def _missing_slots(state: dict[str, Any]) -> list[str]:
    """List the *required* slot keys that aren't yet filled. Only `pipeline`
    and `uids` are required; `cohort_criteria` and `enrichment_fields` are
    optional in the build."""
    selection = state.get("selection") or {}
    missing: list[str] = []
    if not state.get("pipeline"):
        missing.append("pipeline")
    if not (selection.get("uids") or []):
        missing.append("uids")
    return missing


def _step_context(
    config: "ChatConfig",
    session: "SessionState | SessionStateProxy",
    state: dict[str, Any],
    step: str,
) -> dict[str, Any]:
    """Assemble whatever context the wizard_agent needs for THIS step."""
    if step == STEP_CONFIRM:
        return {
            "step_goal": "User reviews summary; respond yes (advance) / no (cancel) / restart.",
            "summary": _build_confirm_summary(state),
        }

    return {"step_goal": "(unknown step)"}


def _build_confirm_summary(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "pipeline": state.get("pipeline"),
        "uid_count": len(state.get("uids") or []),
        "uid_preview": (state.get("uids") or [])[:6],
        "cohort_criteria": state.get("cohort_criteria") or [],
        "cohort_count": len(state.get("cohort_criteria") or []) or 1,
        "enrichment_fields": state.get("enrichment_fields") or [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step transition + metadata prefetch
# ─────────────────────────────────────────────────────────────────────────────


def _advance_step(state: dict[str, Any]) -> str | None:
    """Return the next step in the ordered list, or None if we're past the end."""
    current = state.get("step")
    try:
        idx = STEPS_ORDERED.index(current)
    except ValueError:
        return None
    if idx + 1 >= len(STEPS_ORDERED):
        return None
    return STEPS_ORDERED[idx + 1]


def _enter_step(
    config: "ChatConfig",
    state: dict[str, Any],
    new_step: str,
) -> None:
    """Set step, run any on-entry side effects."""
    state["step"] = new_step


# ─────────────────────────────────────────────────────────────────────────────
# Public entry: start
# ─────────────────────────────────────────────────────────────────────────────


def start(
    session: "SessionState | SessionStateProxy",
    config: "ChatConfig",
    *,
    user_query: str,
    parser_plan: Any,
    reporter_plan: Any,
) -> dict[str, Any]:
    """Initialize a new wizard flow. Returns a WizardResult dict for the
    orchestrator (action=ask, with the intro message)."""
    valid_keys = _known_pipeline_keys(config)

    plan_dump = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else (parser_plan or {})
    rep_dump = reporter_plan.model_dump() if hasattr(reporter_plan, "model_dump") else (reporter_plan or {})

    report_type = (rep_dump.get("report_type") or plan_dump.get("report_type") or "").upper()
    seeded_pipeline: str | None = None
    if report_type.startswith("NFCORE_"):
        candidate = report_type[len("NFCORE_"):].lower()
        if candidate in valid_keys:
            seeded_pipeline = candidate

    seeded_uids: list[str] = list(rep_dump.get("uids") or [])
    if not seeded_uids:
        filters = plan_dump.get("filters") or {}
        if isinstance(filters, dict):
            seeded_uids = list(filters.get("uids") or [])
    seeded_uids = [_clean_uid(u) for u in seeded_uids if u]

    state: dict[str, Any] = {
        "active": True,
        "step": STEP_BUILDER,
        "original_query": user_query,
        "pipeline": seeded_pipeline,
        "selection": {
            "uids": list(seeded_uids) if seeded_uids else [],
            "cohort_criteria": [],
            "enrichment_fields": [],
        },
        "pinned_context": None,
    }
    _save_state(session, state)

    return {"action": "ask", "reply": _slot_checklist_intro(config, state), "params": None}


def _slot_checklist_intro(config: "ChatConfig", state: dict[str, Any]) -> str:
    """Render the wizard's opening reply: a checklist of the four slots with
    current state and an open-ended "where would you like to start?" prompt.
    User can fill the slots in any order."""
    selection = state.get("selection") or {}
    pipeline = state.get("pipeline")
    uids = selection.get("uids") or []
    cohorts = selection.get("cohort_criteria") or []
    enrichment = selection.get("enrichment_fields") or []

    def _slot_line(label: str, status: str, *, optional: bool = False) -> str:
        suffix = " *(optional)*" if optional else ""
        return f"- **{label}**{suffix} — {status}"

    pipeline_status = f"`{pipeline}`" if pipeline else "*not set*"
    uids_status = f"{len(uids)} UIDs queued" if uids else "*not set*"
    cohorts_status = (
        f"{len(cohorts)} criterion(s) defined" if cohorts else "*not set* (single-cohort default)"
    )
    enrichment_status = (
        f"{len(enrichment)} field(s) — {', '.join(enrichment)}" if enrichment else "*not set*"
    )

    lines = [
        "I'll help you build an nf-core / Seqera samplesheet. Here's what we need to fill out — "
        "you can tackle them in any order:",
        "",
        _slot_line("Pipeline", pipeline_status),
        _slot_line("Samples", uids_status),
        _slot_line("Cohort criteria", cohorts_status, optional=True),
        _slot_line("Enrichment fields", enrichment_status, optional=True),
        "",
        "Pipelines available right now (from `reports/nfcore/`):",
        format_available_pipelines(config),
        "",
        "**Where would you like to start?** You can also ask anything along the way — "
        "what samples are pinned, what fields exist, how a pipeline differs from another, etc.",
        "",
        "_Type `cancel` at any time to drop out._",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Builder step dispatcher
# ─────────────────────────────────────────────────────────────────────────────


_WIZARD_HISTORY_MAX_TURNS = 10


def _wizard_builder_history_messages(
    session: "SessionState | SessionStateProxy | dict",
) -> list[dict]:
    """Build alternating user/assistant messages from the session's chat_log
    for the wizard builder agent to consume as prior turns.

    Filters to ``mode == "nfcore_wizard"`` turns with non-empty user_query AND
    a non-empty assistant reply. Returns the last ``_WIZARD_HISTORY_MAX_TURNS``
    matching turns expanded into alternating-role messages, oldest first.

    Uses the FULL ``assistant_reply`` (not the 280-char ``assistant_reply_preview``)
    because the builder's working memory needs to see exactly what UIDs / values
    it told the user in past turns — the preview truncates mid-list and causes
    the LLM to re-derive (and often drift from) prior commitments.
    """
    if session is None:
        return []
    log = session.get("chat_log") if hasattr(session, "get") else None
    if not isinstance(log, list):
        return []
    matched: list[dict] = []
    for turn in log:
        if not isinstance(turn, dict):
            continue
        if turn.get("mode") != "nfcore_wizard":
            continue
        user_q = turn.get("user_query") or ""
        reply = turn.get("assistant_reply") or turn.get("assistant_reply_preview") or ""
        if not user_q or not reply:
            continue
        matched.append(turn)
    tail = matched[-_WIZARD_HISTORY_MAX_TURNS:]
    messages: list[dict] = []
    for turn in tail:
        reply_text = turn.get("assistant_reply") or turn.get("assistant_reply_preview") or ""
        messages.append({"role": "user", "content": turn["user_query"]})
        messages.append({"role": "assistant", "content": reply_text})
    return messages


def _wizard_agent_builder(config, session, user_text, **kwargs):
    """Module-level shim so tests can patch 'chat_nextseek.nfcore_wizard._wizard_agent_builder'.

    Delegates to agents._wizard_agent_builder; imported lazily to avoid circular imports.
    """
    from ..agents import _wizard_agent_builder as _impl
    return _impl(config=config, session=session, user_text=user_text, **kwargs)


def _handle_builder_step(
    session: "SessionState | SessionStateProxy",
    config: "ChatConfig",
    user_text: str,
    log_dir: str | None = None,
) -> dict[str, Any]:
    """Dispatch one builder-step turn through the tool-augmented wizard_agent.

    Merges `selection_updates` into session['nfcore_wizard']['selection'],
    transitions to confirm on action='advance', clears state on action='cancel'.
    """
    from ..agents import WizardToolLoopError

    try:
        history = _wizard_builder_history_messages(session)
        output = _wizard_agent_builder(
            config=config, session=session, user_text=user_text,
            history_messages=history,
        )
    except WizardToolLoopError:
        return {
            "action": "ask",
            "reply": (
                "I got stuck thinking about that — could you rephrase or be "
                "more specific? Your current samplesheet selection is still intact."
            ),
            "params": None,
        }

    state = _state(session)
    selection = dict(state.get("selection") or {
        "uids": [], "cohort_criteria": [], "enrichment_fields": [],
    })
    valid_pipeline_keys = _known_pipeline_keys(config)
    invalid_pipeline_attempt: str | None = None
    for key, value in (output.selection_updates or {}).items():
        if key in ("uids", "cohort_criteria", "enrichment_fields"):
            selection[key] = value
        elif key == "pipeline":
            # Pipeline lives at top-level state, not inside selection; validate
            # against the catalog before committing so a typo can't propagate.
            candidate = (value or "").strip().lower() if isinstance(value, str) else ""
            if candidate and candidate in valid_pipeline_keys:
                state["pipeline"] = candidate
            elif candidate:
                invalid_pipeline_attempt = candidate
    state["selection"] = selection

    if invalid_pipeline_attempt:
        _save_state(session, state)
        return {
            "action": "ask",
            "reply": (
                f"`{invalid_pipeline_attempt}` isn't one of the available pipelines. "
                f"Pick from: {', '.join(valid_pipeline_keys)}."
            ),
            "params": None,
        }

    if output.action == "advance":
        missing = _missing_slots(state)
        if missing:
            _save_state(session, state)
            return {
                "action": "ask",
                "reply": _missing_slots_reply(missing, state, config),
                "params": None,
            }
        selection_uids = list(selection.get("uids") or [])
        seq_check = _check_sequencing_presence(config, selection_uids)
        if selection_uids and not seq_check["has_sequencing"]:
            _save_state(session, state)
            return {
                "action": "ask",
                "reply": _no_sequencing_reply(len(selection_uids), seq_check["sampled"]),
                "params": None,
            }
        state["step"] = STEP_CONFIRM
        _save_state(session, state)
        return {"action": "ask", "reply": output.reply, "params": None}

    if output.action == "cancel":
        clear(session)
        return {"action": "cancel", "reply": output.reply, "params": None}

    _save_state(session, state)
    return {"action": "ask", "reply": output.reply, "params": None}


def _missing_slots_reply(missing: list[str], state: dict[str, Any], config: "ChatConfig") -> str:
    """Compose a reply when the LLM tried to advance with required slots unfilled."""
    parts: list[str] = ["Almost — still need:"]
    for slot in missing:
        if slot == "pipeline":
            parts.append(
                f"- a **pipeline** (one of: {', '.join(_known_pipeline_keys(config)) or '(catalog empty)'})"
            )
        elif slot == "uids":
            parts.append("- a **sample set** (paste UIDs, ask me to pull your last search, or run a fresh search)")
    parts.append("")
    parts.append("Once those are in we can review and build.")
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry: handle_turn (LLM-dispatched)
# ─────────────────────────────────────────────────────────────────────────────


def handle_turn(
    session: "SessionState | SessionStateProxy",
    config: "ChatConfig",
    user_text: str,
    *,
    log_dir: str | None = None,
) -> dict[str, Any]:
    """Advance the wizard one user turn. Each step dispatches through
    `wizard_agent` (LLM) so the user can ask, explore, and rabbit-hole
    inside any step before committing."""
    state = _state(session)
    if not state.get("active"):
        return {"action": "passthrough", "reply": "", "params": None}

    # Deterministic cancel short-circuit (skip the LLM round-trip).
    if _wants_cancel(user_text):
        clear(session)
        return {
            "action": "cancel",
            "reply": "Cancelled the nf-core setup. Ask me a fresh question whenever you're ready.",
            "params": None,
        }

    step = state.get("step") or STEP_BUILDER
    if step == STEP_DONE:
        clear(session)
        return {"action": "passthrough", "reply": "", "params": None}

    # Builder step has its own tool-augmented dispatcher — short-circuit here.
    if step == STEP_BUILDER:
        return _handle_builder_step(session, config, user_text, log_dir=log_dir)

    # Local imports to avoid orchestrator-time cycle.
    from ..agents import wizard_agent
    from ..chat_memory import history_block

    step_ctx = _step_context(config, session, state, step)
    chat_history = history_block(session, n=5)

    out = wizard_agent(
        config,
        step=step,
        user_text=user_text,
        wizard_state={k: v for k, v in state.items() if k != "metadata_summary"},
        step_context=step_ctx,
        chat_history=chat_history,
        original_query=state.get("original_query") or "",
        log_dir=log_dir,
    )

    action = out.action
    extracted = out.extracted or {}
    reply = (out.reply or "").strip() or "_(no reply)_"

    # Persist the agent's decision at session-level so tests / debugging code
    # can introspect "what action did the wizard just take" — survives clear()
    # on cancel/execute so the cancel-turn itself can be asserted on.
    session["nfcore_wizard_last_action"] = action
    session["nfcore_wizard_last_extracted_keys"] = sorted(extracted.keys())

    if action == "cancel":
        clear(session)
        return {"action": "cancel", "reply": reply, "params": None}

    if action == "restart":
        # Reset all answers to the new-schema shape (matching what start() produces);
        # writing old top-level keys would trigger is_active()'s old-schema guard.
        state["pipeline"] = None
        state["selection"] = {"uids": [], "cohort_criteria": [], "enrichment_fields": []}
        state["pinned_context"] = None
        state["step"] = STEP_BUILDER
        _save_state(session, state)
        # Re-emit the slot-checklist intro so the user sees the fresh canvas.
        return {"action": "ask", "reply": _slot_checklist_intro(config, state), "params": None}

    if action == "stay":
        # Track consecutive off-topic stays. If the user's message has no recognizable
        # pipeline/sample/cohort intent for this step AND we've stayed for too many
        # turns in a row, auto-cancel so the user isn't trapped.
        if _is_off_topic_for_step(step, user_text):
            count = int(state.get("_off_topic_stay_count", 0)) + 1
            state["_off_topic_stay_count"] = count
            print(f"[DEBUG][WIZARD] off-topic stay count={count} on step={step}")
            if count >= MAX_OFF_TOPIC_STAYS:
                clear(session)
                return {
                    "action": "cancel",
                    "reply": (
                        f"{reply}\n\n"
                        "_(Cancelling the nf-core setup so I can answer your question. "
                        "Re-trigger with 'build me an nf-core samplesheet' when you're ready.)_"
                    ),
                    "params": None,
                }
        else:
            state["_off_topic_stay_count"] = 0
        _save_state(session, state)
        return {"action": "ask", "reply": reply, "params": None}

    if action == "advance":
        state["_off_topic_stay_count"] = 0
        # Apply the step's extraction, then move to the next step.
        ok, error = _apply_extraction(config, session, state, step, extracted)
        if not ok:
            # Defensive: tell the user we couldn't parse, stay on the step.
            return {
                "action": "ask",
                "reply": f"{reply}\n\n_(Internal note — couldn't apply the captured answer: {error}. Please rephrase.)_",
                "params": None,
            }

        next_step = _advance_step(state)
        if next_step is None:
            # We were at the last step (CONFIRM) and the user confirmed: execute.
            if step == STEP_CONFIRM and bool(extracted.get("confirmed")):
                params = build_execution_params(state)
                state["step"] = STEP_DONE
                state["active"] = False
                _save_state(session, state)
                return {"action": "execute", "reply": reply, "params": params}
            # Shouldn't happen, but be safe:
            clear(session)
            return {"action": "ask", "reply": reply, "params": None}

        _enter_step(config, state, next_step)
        _save_state(session, state)
        return {"action": "ask", "reply": reply, "params": None}

    # Unknown action — fall back to stay.
    _save_state(session, state)
    return {"action": "ask", "reply": reply, "params": None}


# ─────────────────────────────────────────────────────────────────────────────
# Per-step extraction logic
# ─────────────────────────────────────────────────────────────────────────────


def _apply_extraction(
    config: "ChatConfig",
    session: "SessionState | SessionStateProxy",
    state: dict[str, Any],
    step: str,
    extracted: dict[str, Any],
) -> tuple[bool, str | None]:
    """Apply the LLM's extracted answer to wizard state for `step`. Returns
    (ok, error) — if ok=False, the caller stays on the step and re-asks."""
    try:
        if step == STEP_CONFIRM:
            if not bool(extracted.get("confirmed")):
                return False, "confirm step requires confirmed=true on advance"
            return True, None

        return False, f"unknown step {step}"
    except Exception as e:
        return False, repr(e)


# ─────────────────────────────────────────────────────────────────────────────
# Execution params (handed to generate_report_outputs)
# ─────────────────────────────────────────────────────────────────────────────


def build_execution_params(state: dict[str, Any]) -> dict[str, Any]:
    """Turn wizard state into a kwarg dict ready for
    `generate_report_outputs(..., pre_supplied_cohorts=...)`."""
    pipeline = state.get("pipeline") or "rnaseq"
    report_type = f"NFCORE_{pipeline.upper()}"
    selection = state.get("selection") or {}
    uids: list[str] = list(selection.get("uids") or [])
    cohort_criteria: list[dict[str, str]] = selection.get("cohort_criteria") or []
    enrichment_fields: list[str] = selection.get("enrichment_fields") or []

    if not cohort_criteria:
        cohorts = [{
            "label": pipeline,
            "pipeline": pipeline,
            "rationale": "User selected via wizard; single cohort.",
            "enrichment_metadata_fields": enrichment_fields,
            "cohort_criterion": {},
            "expected_sample_count": len(uids),
        }]
    else:
        cohorts = []
        for idx, criterion in enumerate(cohort_criteria, start=1):
            label_parts = [f"{k}-{v}" for k, v in criterion.items()]
            label = (
                f"{pipeline}-{'-'.join(label_parts)}"
                .lower()
                .replace(" ", "-")
                .replace("/", "-")
                .replace("=", "-")
            )
            cohorts.append({
                "label": label or f"{pipeline}-cohort-{idx}",
                "pipeline": pipeline,
                "rationale": f"User-specified cohort criterion {criterion} via wizard.",
                "enrichment_metadata_fields": enrichment_fields,
                "cohort_criterion": criterion,
                "expected_sample_count": 0,
            })

    return {
        "uids": uids,
        "report_type": report_type,
        "pre_supplied_cohorts": cohorts,
        "selector_rationale": "Pipeline + cohorts chosen interactively via the nf-core wizard.",
    }


def snapshot_for_chat_log(session: "SessionState | SessionStateProxy") -> dict[str, Any]:
    """Compact wizard-state snapshot for chat_log turn metadata."""
    state = _state(session)
    if not state:
        return {}
    selection = state.get("selection") or {}
    return {
        "active": bool(state.get("active")),
        "step": state.get("step"),
        "pipeline": state.get("pipeline"),
        "uid_count": len(selection.get("uids") or []),
        "cohort_count": len(selection.get("cohort_criteria") or []) or 1,
        "enrichment_count": len(selection.get("enrichment_fields") or []),
    }

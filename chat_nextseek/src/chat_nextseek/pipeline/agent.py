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

import re as _re

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

    from ..config import ChatConfig
    from ..session import SessionState

# Expose LLM helpers at module level so tests can patch them via
# "chat_nextseek.pipeline_agent._pipeline_directive_parse" etc.
from ..agents import (  # noqa: E402
    _pipeline_edit_step,
    _pipeline_question_step,
    report_writer_agent,
)
from .steps.directive import _pipeline_directive_parse  # noqa: E402
from .steps.sanity import _pipeline_sanity_check  # noqa: E402
from .steps.groupby import _pipeline_groupby_resolution  # noqa: E402
from ..helpers import (
    annotate_metadata_with_sampletypes,
    enumerate_lineage_leaves,
    fetch_reporter_metadata,
    generate_report_outputs,
)
from ..seqera.catalog import NFCORE_PIPELINE_CATALOG
from ..seqera.submitter import submit_launch


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


def _uids_from_last_search(session: "SessionState | SessionStateProxy") -> list[str]:
    history = session.get("results_history") or []
    if not isinstance(history, list):
        return []
    for bundle in reversed(history):
        if not isinstance(bundle, dict):
            continue
        api_full = bundle.get("api_result_full") or {}
        data = api_full.get("data") if isinstance(api_full, dict) else None
        rows = []
        if isinstance(data, dict):
            for key in ("rows", "nodes", "data"):
                val = data.get(key)
                if isinstance(val, list):
                    rows = val
                    break
        elif isinstance(data, list):
            rows = data
        out: list[str] = []
        for row in rows or []:
            if isinstance(row, dict):
                uid = row.get("uid") or row.get("UID") or row.get("uuid") or row.get("sample_uid")
                if uid:
                    out.append(_re.sub(r"<[^>]+>", "", str(uid)).strip())
            elif isinstance(row, str):
                out.append(_re.sub(r"<[^>]+>", "", row).strip())
        if out:
            return out
    return []


def _resolve_samples(
    config: "ChatConfig",
    session: "SessionState | SessionStateProxy",
    samples_ref: Any,
    pipeline_key: str,
) -> dict[str, Any]:
    """Run the catalog-driven sample resolver.

    Returns a dict with:
      - source_uids: list[str]
      - leaves_all: list[dict] (every leaf walked, before catalog assay filter)
      - leaves_filtered: list[dict] (leaves that match accepted_assay_patterns)
      - dropped_by_type_mismatch: list[dict] — RESERVED, always []. Type filtering
        happens inside enumerate_lineage_leaves before we see the data, so this
        bucket can't be populated here. Reserved for a future split if type
        filtering is moved out of the lineage helper.
      - dropped_by_assay_mismatch: list[dict] (leaves whose assay didn't match)
      - source_uids_with_no_leaves: list[str]
      - accessions: list[str] (only set for kind="accessions")
      - metadata_bundle: the annotated bundle, for downstream consumers
      - error: str (only set when resolution fails up-front)
    """
    catalog = NFCORE_PIPELINE_CATALOG.get(pipeline_key, {})
    accepted_types = catalog.get("accepted_leaf_sample_types") or []
    accepted_patterns = [
        _re.compile(p, _re.IGNORECASE) for p in (catalog.get("accepted_assay_patterns") or [])
    ]
    input_kind = catalog.get("samplesheet_input_kind", "fastq")

    if samples_ref.kind == "accessions":
        if input_kind != "accession":
            return {"error": f"Accession inputs only work with fetchngs (got {pipeline_key})."}
        return {
            "source_uids": [],
            "accessions": list(samples_ref.accessions),
            "leaves_all": [],
            "leaves_filtered": [],
            "dropped_by_type_mismatch": [],
            "dropped_by_assay_mismatch": [],
            "source_uids_with_no_leaves": [],
            "metadata_bundle": {},
        }

    if samples_ref.kind == "last_search":
        source_uids = _uids_from_last_search(session)
        if not source_uids:
            return {"error": "No pinned search to use. Run a search first or paste UIDs."}
    elif samples_ref.kind == "explicit_uids":
        source_uids = list(samples_ref.uids)
        if not source_uids:
            return {"error": "Explicit UIDs requested but none provided."}
    elif samples_ref.kind == "named_cohort":
        return {"error": "Named cohorts aren't supported yet — paste UIDs or use last search."}
    else:
        return {"error": f"Unknown samples_ref.kind={samples_ref.kind!r}"}

    raw = fetch_reporter_metadata(config, source_uids)
    if not raw.get("ok"):
        return {"error": f"Metadata fetch failed: {raw.get('error') or 'unknown error'}"}

    annotated = annotate_metadata_with_sampletypes(config, raw)
    leaves_all = enumerate_lineage_leaves(annotated, accepted_types=accepted_types)

    leaves_filtered: list[dict] = []
    dropped_by_assay: list[dict] = []
    if accepted_patterns:
        for leaf in leaves_all:
            assay = leaf.get("assay") or ""
            if assay and any(pat.search(assay) for pat in accepted_patterns):
                leaves_filtered.append(leaf)
            else:
                dropped_by_assay.append(leaf)
    else:
        # No patterns means "accept everything that matched type" (e.g. methylseq edge cases)
        leaves_filtered = list(leaves_all)

    leaves_seen_sources = {leaf["source_uid"] for leaf in leaves_filtered}
    orphans = [uid for uid in source_uids if uid not in leaves_seen_sources]

    return {
        "source_uids": source_uids,
        "accessions": [],
        "leaves_all": leaves_all,
        "leaves_filtered": leaves_filtered,
        "dropped_by_type_mismatch": [],
        "dropped_by_assay_mismatch": dropped_by_assay,
        "source_uids_with_no_leaves": orphans,
        "metadata_bundle": annotated,
    }


def _run_build_flow(session, config, parsed, *, log_dir):
    """Full build-flow: resolve → sanity → groupby/build (Tasks 6–9).

    Task 6 added resolution; Task 7 adds the sanity LLM step.
    _run_groupby_or_build is a stub that lands in Tasks 8–9.
    """
    from ..schemas.pipeline import SamplesRef

    state = _state(session)
    state["phase"] = PHASE_DIRECTIVE_PARSE

    # 1. Resolve samples
    samples_ref = SamplesRef(
        **(parsed.samples_ref.model_dump() if parsed.samples_ref else {"kind": "last_search"})
    )
    resolution = _resolve_samples(config, session, samples_ref, parsed.pipeline_key)
    if "error" in resolution:
        clear(session)
        return {"action": "cancel", "reply": resolution["error"], "params": None}
    state["resolution"] = {k: v for k, v in resolution.items() if k != "metadata_bundle"}
    state["metadata_bundle"] = resolution.get("metadata_bundle") or {}

    # 2. Sanity check (skip for accession-based inputs — fetchngs has no lineage)
    catalog_entry = NFCORE_PIPELINE_CATALOG.get(parsed.pipeline_key, {})
    if catalog_entry.get("samplesheet_input_kind") == "accession":
        state["sanity"] = {
            "verdict": "proceed",
            "leaves_to_use": resolution.get("accessions") or [],
        }
        _save_state(session, state)
        return _run_groupby_or_build(session, config, log_dir=log_dir)

    sanity = _pipeline_sanity_check(
        config=config,
        pipeline_key=parsed.pipeline_key,
        resolution=resolution,
    )
    state["sanity"] = sanity.model_dump()
    _save_state(session, state)

    if sanity.verdict == "mismatch":
        alt = sanity.suggested_alternative_pipeline
        if alt and alt in NFCORE_PIPELINE_CATALOG:
            state["phase"] = PHASE_AWAITING_PIPELINE_SWITCH
            _save_state(session, state)
            return {
                "action": "ask",
                "reply": (
                    f"I can't run {parsed.pipeline_key} on this set — {sanity.confidence_note}. "
                    f"Did you mean nf-core/{alt}? Reply 'yes {alt}' to switch, "
                    f"or 'cancel'."
                ),
                "params": None,
            }
        # No actionable alternative — surface the mismatch and clear.
        clear(session)
        return {
            "action": "cancel",
            "reply": (
                f"I can't run {parsed.pipeline_key} on this set — {sanity.confidence_note}. "
                f"Try a different pipeline or different samples."
            ),
            "params": None,
        }

    if sanity.verdict == "ambiguous":
        state["phase"] = PHASE_AWAITING_SANITY
        _save_state(session, state)
        return {
            "action": "ask",
            "reply": sanity.clarifying_question or "Need clarification on pipeline match.",
            "params": None,
        }

    return _run_groupby_or_build(session, config, log_dir=log_dir)


def _run_groupby_or_build(session, config, *, log_dir):
    """Run the group-by resolution loop (if needed) then advance to build.

    If directive.group_by_phrase is null/empty, skip group-by and call
    _run_build_step directly (single-cohort build).

    If _pipeline_groupby_resolution returns requires_clarification=True,
    pause the agent in PHASE_AWAITING_GROUPBY and return the clarifying
    question to the user.

    Otherwise, store the GroupByResolution in state and call _run_build_step.
    """
    state = _state(session)
    directive = state.get("directive") or {}
    group_by_phrase = directive.get("group_by_phrase") or ""
    pipeline_key = directive.get("pipeline_key") or ""

    if not group_by_phrase:
        # Single-cohort build — no group-by resolution needed.
        return _run_build_step(session, config, log_dir=log_dir)

    # Build the metadata summary for the group-by tools.
    bundle = state.get("metadata_bundle") or {}
    try:
        from ..helpers import build_metadata_summary, filter_summary_to_sequencing_lineage
        summary = build_metadata_summary({"__sample__": bundle})
        summary = filter_summary_to_sequencing_lineage(summary)
    except Exception:
        summary = {"by_sample_type": {}}

    user_hint = state.get("groupby_user_hint") or ""

    resolution = _pipeline_groupby_resolution(
        config=config,
        pipeline_key=pipeline_key,
        group_by_phrase=group_by_phrase,
        metadata_summary=summary,
        user_hint=user_hint,
    )

    if resolution.requires_clarification:
        # Pause — ask the user to disambiguate.
        state["phase"] = PHASE_AWAITING_GROUPBY
        state["candidates_pending_user_pick"] = [
            c.model_dump() for c in (resolution.candidates or [])
        ]
        _save_state(session, state)
        question = resolution.clarifying_question or "Which field did you mean?"
        return {"action": "ask", "reply": question, "params": None}

    # Commit the resolution and proceed to build.
    state["groupby"] = resolution.model_dump()
    state.pop("groupby_user_hint", None)
    state.pop("candidates_pending_user_pick", None)
    _save_state(session, state)
    return _run_build_step(session, config, log_dir=log_dir)


def _run_question_flow(session, config, parsed, *, log_dir):
    """Question sub-mode: answer a pipeline-domain Q&A in one LLM call and clear state."""
    pinned_summary = _summarize_pinned_bundle(session)
    state = _state(session)
    answer = _pipeline_question_step(
        config=config,
        user_query=state.get("original_query", ""),
        pinned_bundle_summary=pinned_summary,
    )
    clear(session)
    return {"action": "passthrough", "reply": answer, "params": None}


def _handle_submit(session, config, *, log_dir):
    state = _state(session)
    artifacts = state.get("build_artifacts") or {}
    launch_yml = artifacts.get("launch_yml")

    if not launch_yml:
        return {
            "action": "ask",
            "reply": "No launch artifact to submit. Try rebuilding first, or 'cancel'.",
            "params": None,
        }

    tower_env = getattr(config, "tower_env", {}) or {}
    if not tower_env.get("access_token") or not tower_env.get("workspace"):
        return {
            "action": "ask",
            "reply": (
                f"Tower env not configured. Samplesheet is at {artifacts.get('samplesheet', '(unknown)')}. "
                "Submit manually via `seqerakit launch.yml` or configure TOWER_* env vars and retry."
            ),
            "params": None,
        }

    state["phase"] = PHASE_SUBMITTING
    _save_state(session, state)

    try:
        run_urls = submit_launch(launch_yml, tower_env=tower_env)
    except Exception as e:
        print(f"[DEBUG][PIPELINE_AGENT][submit] error: {e!r}")
        state["phase"] = PHASE_AWAITING_VALIDATION
        _save_state(session, state)
        return {
            "action": "ask",
            "reply": f"Submit failed: {e}. Try again or 'cancel'.",
            "params": None,
        }

    if not run_urls:
        state["phase"] = PHASE_AWAITING_VALIDATION
        _save_state(session, state)
        return {
            "action": "ask",
            "reply": "Tower didn't return any run URLs. Check seqera logs and retry.",
            "params": None,
        }

    clear(session)
    urls_block = "\n".join(f"- {url}" for url in run_urls)
    return {
        "action": "execute",
        "reply": f"Submitted to Tower:\n{urls_block}",
        "params": {"tower_run_urls": run_urls},
    }


def _read_samplesheet_rows(samplesheet_path: str | None) -> list[dict]:
    """Parse the emitted samplesheet.csv into a list of row dicts so the edit
    step can mutate it. Returns [] if the file is missing or empty."""
    if not samplesheet_path:
        return []
    from pathlib import Path
    import csv as _csv
    path = Path(samplesheet_path)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            return list(_csv.DictReader(f))
    except Exception as exc:
        print(f"[DEBUG][PIPELINE_BUILD] failed to read samplesheet {path}: {exc!r}")
        return []


def _re_emit_samplesheet(session, config, rows, *, log_dir):
    """Re-emit samplesheet artifacts with new rows, reusing the existing run dir.

    Reads pipeline_key + cohorts from session state; writes the new
    samplesheet.csv without re-running the reporter or report_writer.
    Falls back to a full rebuild if the artifact paths are missing.
    """
    from pathlib import Path
    import csv as _csv

    state = _state(session)
    artifacts = state.get("build_artifacts") or {}
    samplesheet_path = artifacts.get("samplesheet")
    if not samplesheet_path:
        return _run_build_step(session, config, log_dir=log_dir)

    out_path = Path(samplesheet_path)
    if not rows:
        out_path.write_text("", encoding="utf-8")
    else:
        fieldnames = list(rows[0].keys())
        with out_path.open("w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow(r)

    return artifacts


def _handle_edit(session, config, user_text, *, log_dir):
    state = _state(session)
    directive = state.get("directive") or {}
    rows = state.get("samplesheet_rows") or []

    edit = _pipeline_edit_step(
        config=config,
        pipeline_key=directive.get("pipeline_key", "rnaseq"),
        current_rows=rows,
        user_text=user_text,
    )

    if edit.action == "apply":
        state["samplesheet_rows"] = list(edit.rows)
        _save_state(session, state)
        _re_emit_samplesheet(session, config, edit.rows, log_dir=log_dir)
        return {
            "action": "ask",
            "reply": (
                f"Applied edit — {len(edit.rows)} row(s) remain. "
                "Reply 'submit' to send to Tower, 'cancel' to drop, or describe more edits."
            ),
            "params": None,
        }

    if edit.action == "ask":
        return {
            "action": "ask",
            "reply": edit.ask_reply or "Could you be more specific?",
            "params": None,
        }

    # reject
    return {
        "action": "ask",
        "reply": (
            f"Can't apply that edit: {edit.reject_reason or 'unknown reason'}. "
            "Try a different edit, 'submit', or 'cancel'."
        ),
        "params": None,
    }


def _handle_groupby_clarification(session, config, user_text, *, log_dir):
    """Handler invoked when phase=awaiting_groupby_clarification.

    Stores the user's response as a hint, resets phase to directive_parse,
    and re-runs the group-by resolution loop with the hint attached.
    """
    state = _state(session)
    state["groupby_user_hint"] = user_text
    state["phase"] = PHASE_DIRECTIVE_PARSE
    _save_state(session, state)
    return _run_groupby_or_build(session, config, log_dir=log_dir)


def _run_build_step(session, config, *, log_dir):
    """Build step: constructs cohorts from GroupByResolution and delegates to
    generate_report_outputs, then transitions to awaiting_validation."""
    from ..schemas import ParserFilters, ParserPlan
    from ..schemas.chat import ReporterPlan

    state = _state(session)
    directive = state.get("directive") or {}
    sanity = state.get("sanity") or {}
    groupby = state.get("groupby")  # may be None

    pipeline_key = directive.get("pipeline_key") or "rnaseq"
    report_type = f"NFCORE_{pipeline_key.upper()}"
    leaves = list(sanity.get("leaves_to_use") or [])

    cohorts: list[dict] = []
    if groupby:
        field = (groupby or {}).get("field") or {}
        field_name = field.get("field_name", "")
        for val in (groupby or {}).get("distinct_values") or []:
            label = f"{pipeline_key}-{field_name}-{val}".lower().replace(" ", "-")
            cohorts.append({
                "label": label,
                "pipeline": pipeline_key,
                "rationale": f"Cohort split on {field_name}={val} via pipeline_agent.",
                "enrichment_metadata_fields": [],
                "cohort_criterion": {field_name: val},
                "expected_sample_count": 0,
            })
    if not cohorts:
        cohorts = [{
            "label": pipeline_key,
            "pipeline": pipeline_key,
            "rationale": "Single-cohort build via pipeline_agent.",
            "enrichment_metadata_fields": [],
            "cohort_criterion": {},
            "expected_sample_count": len(leaves),
        }]

    original_query = state.get("original_query") or f"Build nf-core/{pipeline_key} samplesheet."
    selector_rationale = "Pipeline_agent directive-driven build."

    synthetic_parser_plan = ParserPlan(
        mode="reporter",
        report_mode="report_generation",
        report_type=report_type,
        intent_summary=f"Pipeline-agent nf-core export ({report_type}).",
        filters=ParserFilters(uids=leaves),
    )
    synthetic_reporter_plan = ReporterPlan(
        reporter_mode="report_generation",
        report_type=report_type,
        uids=leaves,
        reporter_context={"per_sample_reports": False},
        notes=selector_rationale,
    )

    reporter_result, report_writer_output, saved_files, reply_body = generate_report_outputs(
        config=config,
        user_query=original_query,
        parser_plan=synthetic_parser_plan,
        reporter_plan=synthetic_reporter_plan,
        uids=leaves,
        pre_supplied_cohorts=cohorts,
        report_writer_fn=report_writer_agent,
        log_dir=log_dir,
        per_sample_reports=False,
    )

    state["build_artifacts"] = saved_files or {}
    state["samplesheet_rows"] = _read_samplesheet_rows((saved_files or {}).get("samplesheet"))
    state["phase"] = PHASE_AWAITING_VALIDATION
    _save_state(session, state)

    dropped_summary = sanity.get("dropped_leaves_summary") or ""
    reply_parts = [
        f"Built nf-core/{pipeline_key} samplesheet for {len(leaves)} samples in {len(cohorts)} cohort(s).",
    ]
    if dropped_summary:
        reply_parts.append(dropped_summary)
    reply_parts.append(reply_body or "")
    reply_parts.append(
        "\nReply 'submit' to send to Tower, 'cancel' to drop, or describe edits "
        "(e.g. 'drop SAMPLE-3, change strandedness on SAMPLE-7 to reverse')."
    )

    return {
        "action": "ask",
        "reply": "\n\n".join(p for p in reply_parts if p),
        "params": None,
    }


def _handle_pipeline_switch(session, config, user_text, *, log_dir):
    """Handler invoked when phase=awaiting_pipeline_switch."""
    state = _state(session)
    sanity = state.get("sanity") or {}
    alt = sanity.get("suggested_alternative_pipeline")
    text = (user_text or "").strip().lower()

    if (text == "yes" or text.startswith("yes ")) and alt and alt in NFCORE_PIPELINE_CATALOG:
        from ..schemas.pipeline import DirectiveParseOutput
        directive = state.get("directive") or {}
        directive["pipeline_key"] = alt
        state["directive"] = directive
        _save_state(session, state)
        return _run_build_flow(session, config,
                               DirectiveParseOutput(**directive),
                               log_dir=log_dir)

    clear(session)
    return {"action": "cancel", "reply": "OK, cancelled.", "params": None}


def _handle_sanity_clarification(session, config, user_text, *, log_dir):
    """Handler invoked when phase=awaiting_sanity_clarification."""
    state = _state(session)
    if "cancel" in (user_text or "").lower():
        clear(session)
        return {"action": "cancel", "reply": "Cancelled.", "params": None}
    state["sanity"] = {**(state.get("sanity") or {}), "verdict": "proceed"}
    _save_state(session, state)
    return _run_groupby_or_build(session, config, log_dir=log_dir)

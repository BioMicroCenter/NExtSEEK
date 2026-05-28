from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from .artifacts import ArtifactStore, build_metadata_bundle, build_saved_report_file_manifest
from .chat_memory import append_turn, build_tool_summary_for_mode, resolve_bundle_for_recall
from .pipeline import wizard as nfcore_wizard
from .pipeline import agent as pipeline_agent
from .agents import (
    chatter_agent_answer,
    chatter_agent_plan,
    entity_agent,
    graph_agent,
    memory_agent_answer,
    multi_parser_agent,
    parser_agent,
    plan_evaluator_agent,
    planner_agent,
    report_writer_agent,
    reporter_agent,
    system_agent,
    _execute_single_plan_step,
    _materialize_intersection_result,
    _step_signature,
)
from .config import ChatConfig
from .llm_clients import LLMFatalError
from .helpers import (
    _extract_required_paths,
    _retry_advanced_search_if_empty,
    fix_sample_endpoint,
    generate_report_outputs,
    log_api_call,
    run_reporter_summary,
    shortlist_catalog,
    slim_api_result_for_llm,
    tool_nextseek_api_request,
    tool_neo4j_query,
)
from .schemas import APIRequestPlan, EntityAgentOutput, ParserPlan, PlannerOutput, ReportWriterOutput
from .session import SessionState
from .tee import Tee

SendEvent = Callable[[str, dict[str, Any]], None]


def _emit_query_complete(
    send_event: SendEvent | None,
    reply: str,
    debug: dict[str, Any],
    bundle_id: int | None,
    *,
    artifacts: list[dict[str, Any]] | None = None,
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the final query payload and emit a `query_complete` event when requested."""
    payload: dict[str, Any] = {
        "reply": reply,
        "debug": debug,
        "bundle_id": bundle_id,
    }
    if artifacts:
        payload["artifacts"] = artifacts
    if files:
        payload["files"] = files
    if send_event:
        send_event("query_complete", payload)
    return payload


def _sanitize_output_component(value: str | None, default: str = "unknown") -> str:
    """Return a filesystem-safe path component for per-run output directories."""
    text = (value or "").strip()
    if not text:
        return default
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return sanitized or default


def _ensure_query_log_dir(session: SessionState | SessionStateProxy, config: ChatConfig) -> str:
    """
    Ensure the session has a per-run output directory.
    Layout:
      <OUTPUTS_DIR>/<YYMMDD_HHMMSS>_<API_USER>/files
    """
    existing = session.get("log_dir")
    if existing:
        return str(existing)

    ts = datetime.now().strftime("%y%m%d_%H%M%S")
    api_user = _sanitize_output_component(getattr(config, "API_USER", None))
    run_root = Path(config.OUTPUTS_DIR) / f"{ts}_{api_user}"
    files_dir = run_root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    session["run_root_dir"] = str(run_root)
    session["log_dir"] = str(files_dir)
    session["console_log_path"] = str(run_root / "console.txt")
    session["chat_log_path"] = str(run_root / "chat.txt")
    session["api_log_path"] = str(run_root / "api_requests.json")
    session["prompts_log_path"] = str(run_root / "prompts.json")

    sys.stdout = Tee(sys.stdout, session["console_log_path"])
    sys.stderr = Tee(sys.stderr, session["console_log_path"])

    if not session.get("config_snapshot_logged"):
        try:
            config_snapshot = config.get_config_snapshot()
            print("[CONFIG] Snapshot:\n" + json.dumps(config_snapshot, indent=2))
        except Exception as e:
            print("[CONFIG] Failed to log config snapshot:", repr(e))
        session["config_snapshot_logged"] = True

    return str(files_dir)


def _write_graph_debug(log_dir: str, ts: str, payload: dict) -> str | None:
    """Write the graph agent debug payload to a timestamped JSON file in log_dir."""
    try:
        store = ArtifactStore(log_dir)
        entry = store.write_json(
            key=f"graph_debug_{ts}",
            label="Graph query debug JSON",
            filename=f"graph_debug_{ts}.json",
            payload=payload,
            kind="graph",
        )
        if entry:
            print(f"[GRAPH TEST] Debug written to {entry['path']}")
            return entry["path"]
        return None
    except Exception as e:
        print(f"[GRAPH TEST] Failed to write debug file: {e!r}")
        return None


def _build_graph_refine_context(last_bundle: dict) -> str:
    """Prior graph-query context for a refine, mirroring the REST refine block
    in api_agent_build_request (prior user query + prior plan)."""
    graph_plan = last_bundle.get("graph_plan") or {}
    prior_cypher = graph_plan.get("cypher") or ""
    prior_query = last_bundle.get("user_query") or ""
    return (
        "Previous graph query context (you are refining it):\n"
        f"Prior user query: {prior_query or '[none]'}\n"
        f"Prior Cypher:\n{prior_cypher or '[none]'}"
    )


def _execute_graph_turn(
    *,
    config: ChatConfig,
    session,
    user_text: str,
    entity_result,
    plan,
    log_dir,
    artifact_store,
    send_event,
    debug_payload: dict,
    t_total_start: float,
    refine_context: str | None = None,
):
    send_event("agent_started", {"agent": "graph", "mode": "graph_query"})
    _t0 = time.perf_counter()
    print("\n[GRAPH] Running graph agent...")
    graph_plan = graph_agent(config, user_text, entity_result, plan, refine_context=refine_context)
    print(f"[DEBUG][GRAPH] Explanation: {graph_plan.explanation}")
    print(f"[DEBUG][GRAPH] Cypher:\n{graph_plan.cypher}")

    if not graph_plan.cypher:
        reply = f"Graph agent could not generate a query.\n\nReason: {graph_plan.explanation}"
        session["last_debug"] = debug_payload
        send_event("agent_complete", {"agent": "graph", "summary": None})
        print(f"[TIMING][GRAPH] {time.perf_counter() - _t0:.2f}s")
        print(f"[TIMING][TOTAL] {time.perf_counter() - t_total_start:.2f}s")
        return _emit_query_complete(send_event, reply, debug_payload, None)

    send_event(
        "agent_complete",
        {"agent": "graph", "summary": {"cypher": graph_plan.cypher, "explanation": graph_plan.explanation}},
    )

    send_event("search_started", {"source": "neo4j", "cypher": graph_plan.cypher})
    graph_result = tool_neo4j_query(config, graph_plan.cypher, graph_plan.parameters)
    if not graph_result.get("ok"):
        neo4j_error = graph_result.get("error", "Unknown error")
        print(f"[GRAPH] Cypher failed, retrying: {neo4j_error}")
        retry_ctx = (
            f"Your previous Cypher query failed with this error:\n{neo4j_error}\n\n"
            "Revisit the schema carefully - check property types, relationship directions, "
            "and graph_topology - then generate a corrected query."
        )
        graph_plan_retry = graph_agent(
            config, user_text, entity_result, plan,
            retry_context=retry_ctx, refine_context=refine_context,
        )
        if graph_plan_retry.cypher:
            graph_plan = graph_plan_retry
            graph_result = tool_neo4j_query(config, graph_plan.cypher, graph_plan.parameters)
    send_event(
        "search_complete",
        {"source": "neo4j", "ok": graph_result.get("ok"), "count": graph_result.get("count")},
    )

    history = session.get("results_history", [])
    bundle_id = len(history) + 1
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    graph_debug_path = _write_graph_debug(
        log_dir, ts,
        {
            "timestamp": ts,
            "user_query": user_text,
            "model": config.MODEL_MODE,
            "entity_output": entity_result.model_dump(),
            "parser_output": plan.model_dump(),
            "graph_output": graph_plan.model_dump(),
            "neo4j_output": {
                "ok": graph_result.get("ok"),
                "count": graph_result.get("count"),
                "error": graph_result.get("error"),
                "counters": graph_result.get("counters"),
                "data_preview": (graph_result.get("data") or [])[:20],
            },
        },
    )
    print(f"[TIMING][GRAPH] {time.perf_counter() - _t0:.2f}s")

    result_files: list[dict[str, Any]] = []
    entry = artifact_store.register_path(
        key="graph_debug", label="Graph query debug JSON", path=graph_debug_path,
        kind="graph", bundle_id=bundle_id,
    )
    if entry:
        result_files.append(entry)
    bundle = build_metadata_bundle(
        bundle_id=bundle_id, mode="graph_query", user_query=user_text,
        parser_plan=plan.model_dump(), graph_plan=graph_plan.model_dump(),
        graph_result=graph_result, terminal_reply=None,
        search_context={"endpoint": "neo4j"}, files=result_files,
        paths={"graph_debug_path": graph_debug_path},
    )
    history.append(bundle)
    session["results_history"] = history

    debug_payload["graph_plan"] = graph_plan.model_dump()
    debug_payload["graph_result"] = {k: v for k, v in graph_result.items() if k != "data"}

    send_event("agent_started", {"agent": "chatter", "mode": "graph_query"})
    _t1 = time.perf_counter()
    reply = chatter_agent_answer(
        config, user_text, entity_result.model_dump(), plan.model_dump(),
        graph_plan=graph_plan.model_dump(), graph_result=graph_result,
        log_dir=log_dir, session=session,
    )
    print(f"[TIMING][CHATTER] {time.perf_counter() - _t1:.2f}s")
    send_event("agent_complete", {"agent": "chatter", "summary": None})
    bundle["terminal_reply"] = reply
    bundle["reply"] = reply
    bundle.setdefault("model_outputs", {})["terminal_reply"] = reply
    session["last_debug"] = debug_payload

    session["last_files"] = result_files
    append_turn(
        session, user_query=user_text, mode="graph_query",
        intent_summary=plan.intent_summary, entity_result=entity_result,
        tool_summary=build_tool_summary_for_mode("graph_query", graph_plan=graph_plan.model_dump()),
        result_payload=graph_result, assistant_reply=reply, bundle_id=bundle_id,
    )
    print(f"[TIMING][TOTAL] {time.perf_counter() - t_total_start:.2f}s")
    return _emit_query_complete(send_event, reply, debug_payload, bundle_id, files=result_files or None)


# DEPRECATED: superseded by pipeline_agent. Left in place for now;
# safe to delete once a release cycle has passed with no rollback.
def _execute_nfcore_wizard(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    user_text: str,
    wizard_params: dict[str, Any],
    log_dir: str,
    send_event: SendEvent,
    artifact_store: ArtifactStore,
    confirmation_reply: str,
) -> dict[str, Any]:
    """Run generate_report_outputs with the wizard's collected pre_supplied_cohorts.

    Synthesizes a minimal ParserPlan + ReporterPlan so we can reuse the existing
    report-generation execution path without going through the parser/reporter
    LLM agents (we already have explicit user answers).
    """
    from .agents import report_writer_agent
    from .schemas import ParserFilters, ParserPlan
    from .schemas.chat import ReporterPlan

    # Empty-uids guard: nothing to build. Bounce the user back to the builder
    # step so they can pick samples, rather than crashing generate_report_outputs.
    uids: list[str] = list(wizard_params.get("uids") or [])
    if not uids:
        state = session.get(nfcore_wizard.WIZARD_KEY) or {}
        state["step"] = nfcore_wizard.STEP_BUILDER
        nfcore_wizard._save_state(session, state)
        reply = (
            "I don't have any samples selected yet — pick samples first "
            "(start with your last search, paste UIDs, or run a new search), "
            "then we can build."
        )
        debug_payload = {"nfcore_wizard": {"empty_uids_bounce": True}}
        session["last_debug"] = debug_payload
        return _emit_query_complete(send_event, reply, debug_payload, None)
    report_type: str = wizard_params.get("report_type") or "NFCORE_RNASEQ"
    cohorts: list[dict[str, Any]] = wizard_params.get("pre_supplied_cohorts") or []
    selector_rationale: str = wizard_params.get("selector_rationale") or ""

    synthetic_parser_plan = ParserPlan(
        mode="reporter",
        report_mode="report_generation",
        report_type=report_type,
        intent_summary=f"Wizard-driven nf-core export ({report_type}).",
        filters=ParserFilters(uids=uids),
    )
    synthetic_reporter_plan = ReporterPlan(
        reporter_mode="report_generation",
        report_type=report_type,
        uids=uids,
        reporter_context={"per_sample_reports": False},
        notes=selector_rationale,
    )

    send_event("agent_started", {"agent": "report_writer", "mode": "report_generation"})
    reporter_result, report_writer_output, saved_files, reply_body = generate_report_outputs(
        config=config,
        user_query=user_text,
        parser_plan=synthetic_parser_plan,
        reporter_plan=synthetic_reporter_plan,
        uids=uids,
        log_dir=log_dir,
        report_writer_fn=report_writer_agent,
        per_sample_reports=False,
        pre_supplied_cohorts=cohorts,
    )
    send_event("agent_complete", {"agent": "report_writer", "summary": None})

    reply = confirmation_reply + "\n\n" + (reply_body or "")
    debug_payload: dict[str, Any] = {
        "wizard_params": wizard_params,
        "reporter_plan": synthetic_reporter_plan.model_dump(),
        "reporter_result": reporter_result,
        "report_writer_output": (
            report_writer_output.model_dump()
            if hasattr(report_writer_output, "model_dump")
            else report_writer_output
        ),
    }
    if saved_files:
        debug_payload["report_saved_files"] = saved_files

    history = session.get("results_history", [])
    bundle_id = len(history) + 1
    result_files = build_saved_report_file_manifest(saved_files)
    bundle = build_metadata_bundle(
        bundle_id=bundle_id,
        mode="reporter",
        user_query=user_text,
        parser_plan=synthetic_parser_plan.model_dump(),
        reporter_plan=synthetic_reporter_plan.model_dump(),
        reporter_result=reporter_result,
        report_writer_output=(
            report_writer_output.model_dump()
            if hasattr(report_writer_output, "model_dump")
            else report_writer_output
        ),
        report_saved_files=saved_files,
        terminal_reply=reply,
        files=result_files,
    )
    history.append(bundle)
    session["results_history"] = history
    session["last_debug"] = debug_payload
    session["last_files"] = result_files

    append_turn(
        session,
        user_query=user_text,
        mode="reporter",
        intent_summary=synthetic_parser_plan.intent_summary,
        tool_summary=build_tool_summary_for_mode(
            "reporter",
            reporter_plan=synthetic_reporter_plan.model_dump(),
        ),
        assistant_reply=reply,
        bundle_id=bundle_id,
        wizard_state={"step": "executed", "pipeline": (cohorts[0].get("pipeline") if cohorts else None)},
    )
    # Wizard state cleared after we persist the run.
    nfcore_wizard.clear(session)
    return _emit_query_complete(
        send_event,
        reply,
        debug_payload,
        bundle_id,
        files=result_files or None,
    )


def _handle_pipeline_agent_turn(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    user_text: str,
    log_dir: str,
    send_event: SendEvent,
    artifact_store: ArtifactStore,
) -> dict[str, Any] | None:
    """If a pipeline_agent session is active, advance it. Returns the
    orchestrator payload, or None if the agent requested passthrough
    (caller should run normal parser).

    This gate runs BEFORE the legacy wizard interceptor so pipeline_agent
    always wins for in-progress NFCORE flows.
    """
    if not pipeline_agent.is_active(session):
        return None
    result = pipeline_agent.handle_turn(session, config, user_text, log_dir=log_dir)
    action = result.get("action")
    if action == "passthrough":
        pipeline_agent.clear(session)
        return None
    if action == "cancel":
        reply = result.get("reply") or ""
        debug_payload = {"pipeline_agent": {"cancelled": True}}
        session["last_debug"] = debug_payload
        append_turn(
            session,
            user_query=user_text,
            mode="pipeline_agent",
            intent_summary="pipeline_agent cancelled",
            assistant_reply=reply,
        )
        return _emit_query_complete(send_event, reply, debug_payload, None)
    # All non-passthrough/cancel actions (ask, build, submit, etc.) get the
    # same chat-log treatment: record the turn with the agent's reply and a
    # snapshot of its state for the debug panel.
    reply = result.get("reply") or ""
    snapshot = pipeline_agent.snapshot_for_chat_log(session)
    debug_payload = {"pipeline_agent": snapshot}
    session["last_debug"] = debug_payload
    append_turn(
        session,
        user_query=user_text,
        mode="pipeline_agent",
        intent_summary="pipeline_agent turn",
        tool_summary={"phase": snapshot.get("phase")},
        assistant_reply=reply,
        wizard_state=snapshot,
    )
    return _emit_query_complete(send_event, reply, debug_payload, None)


def _handle_wizard_turn(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    user_text: str,
    log_dir: str,
    send_event: SendEvent,
    artifact_store: ArtifactStore,
) -> dict[str, Any] | None:
    """If a wizard is active, advance it. Returns the orchestrator payload, or
    None if the wizard requested passthrough (caller should run normal parser).
    """
    if not nfcore_wizard.is_active(session):
        return None
    result = nfcore_wizard.handle_turn(session, config, user_text, log_dir=log_dir)
    action = result.get("action")
    if action == "passthrough":
        nfcore_wizard.clear(session)
        return None
    if action == "ask":
        reply = result.get("reply") or ""
        debug_payload = {"nfcore_wizard": nfcore_wizard.snapshot_for_chat_log(session)}
        session["last_debug"] = debug_payload
        append_turn(
            session,
            user_query=user_text,
            mode="nfcore_wizard",
            intent_summary="nf-core wizard Q&A",
            tool_summary={"step": (session.get(nfcore_wizard.WIZARD_KEY) or {}).get("step")},
            assistant_reply=reply,
            wizard_state=nfcore_wizard.snapshot_for_chat_log(session),
        )
        return _emit_query_complete(send_event, reply, debug_payload, None)
    if action == "cancel":
        reply = result.get("reply") or ""
        debug_payload = {"nfcore_wizard": {"cancelled": True}}
        session["last_debug"] = debug_payload
        append_turn(
            session,
            user_query=user_text,
            mode="nfcore_wizard",
            intent_summary="nf-core wizard cancelled",
            assistant_reply=reply,
        )
        return _emit_query_complete(send_event, reply, debug_payload, None)
    if action == "execute":
        params = result.get("params") or {}
        return _execute_nfcore_wizard(
            session,
            config,
            user_text,
            params,
            log_dir,
            send_event,
            artifact_store,
            confirmation_reply=result.get("reply") or "Building artifacts now…",
        )
    nfcore_wizard.clear(session)
    return None


def run_query(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    user_text: str,
    send_event: SendEvent | None = None,
    *,
    credentials: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Shared query orchestrator for Streamlit, CLI, and async/SSE consumers.
    Runs the full agent pipeline, updates session state, and emits optional progress events.

    credentials — optional dict with keys 'api_user' and/or 'api_pass'.  When provided,
    a shallow copy of config is made so the shared singleton is never mutated; all LLM
    clients, catalogs, and prompts remain shared by reference.
    """
    if credentials:
        config = copy.copy(config)
        if credentials.get("api_user"):
            config.API_USER = credentials["api_user"]
        if credentials.get("api_pass"):
            config.API_PASS = credentials["api_pass"]

    log_dir = _ensure_query_log_dir(session, config)
    artifact_store = ArtifactStore(log_dir)
    current_agent = "catalog"
    _t_total_start = time.perf_counter()
    session["last_files"] = []

    _raw_send_event = send_event

    def send_event(event_name: str, payload: dict) -> None:
        if _raw_send_event:
            _raw_send_event(event_name, payload)

    try:
        # pipeline_agent gates BEFORE the legacy wizard so an in-progress
        # samplesheet build always advances even if a stale wizard state
        # also looks active.
        pipeline_payload = _handle_pipeline_agent_turn(
            session, config, user_text, log_dir, send_event, artifact_store,
        )
        if pipeline_payload is not None:
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return pipeline_payload

        wizard_payload = _handle_wizard_turn(session, config, user_text, log_dir, send_event, artifact_store)
        if wizard_payload is not None:
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return wizard_payload

        send_event("agent_started", {"agent": "catalog", "mode": ""})
        sampletypes_short, assays_short, shortlist_diag = shortlist_catalog(
            user_text,
            config.MIN_SAMPLETYPES or [],
            config.MIN_ASSAYS or [],
            k_st=50,
            k_a=75,
            sampletype_index=getattr(config, "SAMPLETYPE_INDEX", None),
            assay_index=getattr(config, "ASSAY_INDEX", None),
            ratio=getattr(config, "SEMANTIC_RATIO", 0.7),
            min_k=getattr(config, "SEMANTIC_MIN_K", 10),
            max_k=getattr(config, "SEMANTIC_MAX_K", 80),
        )
        if not sampletypes_short:
            sampletypes_short = config.MIN_SAMPLETYPES or []
        if not assays_short:
            assays_short = config.MIN_ASSAYS or []
        send_event("agent_complete", {"agent": "catalog", "summary": None})

        current_agent = "entity"
        send_event("agent_started", {"agent": "entity", "mode": ""})
        _t0 = time.perf_counter()
        entity_result = entity_agent(config, user_text, sampletypes_short, assays_short)
        print(f"[TIMING][ENTITY] {time.perf_counter() - _t0:.2f}s")
        send_event("agent_complete", {"agent": "entity", "summary": entity_result.model_dump()})

        current_agent = "parser"
        send_event("agent_started", {"agent": "parser", "mode": ""})
        _t0 = time.perf_counter()
        plan = parser_agent(session, config, user_text, entity_result)
        print(f"[TIMING][PARSER] {time.perf_counter() - _t0:.2f}s")
        plan = ParserPlan.model_validate(fix_sample_endpoint(plan.model_dump()))
        mode = plan.mode
        send_event(
            "agent_complete",
            {"agent": "parser", "summary": {"mode": mode, "endpoint": plan.target_endpoint}},
        )

        debug_payload: dict[str, Any] = {
            "entity_result": entity_result.model_dump(),
            "parser_plan": plan.model_dump(),
            "shortlist_sampletype_codes": shortlist_diag.get("sampletype_codes", []),
            "shortlist_assay_codes": shortlist_diag.get("assay_codes", []),
            "shortlist_diagnostics": shortlist_diag,
            "api_plan": None,
            "reporter_plan": None,
            "reporter_result": None,
            "report_writer_output": None,
            "reporter_metadata": None,
            "api_result_meta": None,
            "api_result_slim": None,
            "api_result_full": None,
            "raw_json_path": None,
            "error_context": None,
        }

        if mode == "unsupported":
            notes = plan.notes or "No additional notes."
            reply = (
                "I can't turn that request into a valid NExtSEEK operation yet.\n\n"
                f"Reason from parser: {notes}"
            )
            session["last_debug"] = debug_payload
            append_turn(
                session,
                user_query=user_text,
                mode=mode,
                intent_summary=plan.intent_summary,
                entity_result=entity_result,
                assistant_reply=reply,
            )
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return _emit_query_complete(send_event, reply, debug_payload, None)

        if mode == "ask_about_last_results":
            current_agent = "memory"
            send_event("agent_started", {"agent": "memory", "mode": mode})
            history = session.get("results_history", [])
            if not history:
                reply = (
                    "You asked a follow-up question about previous data, but there are no stored results "
                    "in this session yet. Please run a search first."
                )
                session["last_debug"] = debug_payload
                send_event("agent_complete", {"agent": "memory", "summary": None})
                print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                return _emit_query_complete(send_event, reply, debug_payload, None)

            target_id = plan.target_result_id
            if target_id is None:
                # Parser punted on bundle selection — usually because the
                # relevant bundle is outside the recent_results_summary window.
                # Score every bundle in results_history by keyword overlap with
                # the user's current message instead of silently grabbing the
                # latest. (Fixes the case where "how many NDMA mice did the
                # first search return?" defaulted to history[-1].)
                bundle = resolve_bundle_for_recall(session, user_text)
                if bundle is None:
                    reply = (
                        "You asked a follow-up question about previous data, but there are no stored "
                        "results in this session yet. Please run a search first."
                    )
                    session["last_debug"] = debug_payload
                    send_event("agent_complete", {"agent": "memory", "summary": None})
                    print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                    return _emit_query_complete(send_event, reply, debug_payload, None)
                print(
                    f"[DEBUG][MEMORY] parser set target_result_id=None; "
                    f"resolved by keyword overlap → bundle id={bundle.get('id')}, "
                    f"query={bundle.get('user_query')!r}"
                )
            else:
                bundle = next((b for b in history if b.get("id") == target_id), None)
                if bundle is None:
                    reply = (
                        f"You referred to previous results with id={target_id}, but I couldn't find that "
                        "in this session. Please run a new search."
                    )
                    session["last_debug"] = debug_payload
                    send_event("agent_complete", {"agent": "memory", "summary": None})
                    print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                    return _emit_query_complete(send_event, reply, debug_payload, None)

            _t0 = time.perf_counter()
            answer = memory_agent_answer(config, user_text, bundle, log_dir=log_dir)
            print(f"[TIMING][MEMORY] {time.perf_counter() - _t0:.2f}s")
            append_turn(
                session,
                user_query=user_text,
                mode=mode,
                intent_summary=plan.intent_summary,
                entity_result=entity_result,
                tool_summary={"target_bundle": bundle.get("id")},
                assistant_reply=answer,
                bundle_id=bundle.get("id"),
            )
            debug_payload["api_plan"] = bundle.get("api_plan")
            api_full = bundle.get("api_result_full") or {}
            debug_payload["api_result_full"] = api_full
            debug_payload["raw_json_path"] = bundle.get("raw_result_path") or bundle.get("graph_debug_path")
            debug_payload["api_result_meta"] = {
                "ok": api_full.get("ok") if isinstance(api_full, dict) else None,
                "status_code": api_full.get("status_code") if isinstance(api_full, dict) else None,
                "url": api_full.get("url") if isinstance(api_full, dict) else None,
                "bundle_id": bundle.get("id"),
                "source_mode": bundle.get("mode"),
            }
            debug_payload["api_result_slim"] = bundle.get("api_result_slim")
            debug_payload["memory_coder_artifact"] = bundle.get("memory_coder_artifact")
            session["last_debug"] = debug_payload
            send_event("agent_complete", {"agent": "memory", "summary": None})
            session["last_files"] = bundle.get("files") or []
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return _emit_query_complete(
                send_event,
                answer,
                debug_payload,
                bundle.get("id"),
                files=(bundle.get("files") or None),
            )

        if mode == "reporter":
            current_agent = "reporter"
            send_event("agent_started", {"agent": "reporter", "mode": mode})
            _t0 = time.perf_counter()
            reporter_plan = reporter_agent(config, user_text, plan)
            print(f"[TIMING][REPORTER] {time.perf_counter() - _t0:.2f}s")
            debug_payload["reporter_plan"] = reporter_plan.model_dump()
            reporter_mode = reporter_plan.reporter_mode or plan.report_mode or "summary"
            if reporter_mode == "summary_sql":  # legacy alias
                reporter_mode = "summary"
            print("[DEBUG][REPORTER] Mode selected:", reporter_mode, "Report type:", reporter_plan.report_type)
            send_event("agent_complete", {"agent": "reporter", "summary": {"reporter_mode": reporter_mode}})

            reporter_result = None
            report_writer_output = None
            saved_files: dict[str, str] = {}
            per_sample_reports = bool((reporter_plan.reporter_context or {}).get("per_sample_reports", False))

            if reporter_mode == "report_generation":
                report_type_for_branch = (reporter_plan.report_type or plan.report_type or "").upper()
                if report_type_for_branch.startswith("NFCORE"):
                    pa_start = pipeline_agent.start(
                        session,
                        config,
                        user_query=user_text,
                        parser_plan=plan,
                        reporter_plan=reporter_plan,
                        log_dir=log_dir,
                    )
                    reply = pa_start.get("reply") or ""
                    snapshot = pipeline_agent.snapshot_for_chat_log(session)
                    debug_payload["pipeline_agent"] = snapshot
                    session["last_debug"] = debug_payload
                    append_turn(
                        session,
                        user_query=user_text,
                        mode="pipeline_agent",
                        intent_summary="pipeline_agent launched",
                        entity_result=entity_result,
                        tool_summary={"phase": snapshot.get("phase")},
                        assistant_reply=reply,
                        wizard_state=snapshot,
                    )
                    print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                    return _emit_query_complete(send_event, reply, debug_payload, None)

                current_agent = "report_writer"
                send_event("agent_started", {"agent": "report_writer", "mode": reporter_mode})
                uids = reporter_plan.uids or []
                try:
                    parser_uids = plan.filters.uids if hasattr(plan, "filters") else []
                except Exception:
                    parser_uids = []
                if parser_uids:
                    existing = set(uids)
                    uids.extend([u for u in parser_uids if u not in existing])

                print("[DEBUG][REPORTER] Report generation UIDs:", uids)
                reporter_result, report_writer_output, saved_files, reply = generate_report_outputs(
                    config=config,
                    user_query=user_text,
                    parser_plan=plan,
                    reporter_plan=reporter_plan,
                    uids=uids,
                    log_dir=log_dir,
                    report_writer_fn=report_writer_agent,
                    per_sample_reports=per_sample_reports,
                )
                send_event("agent_complete", {"agent": "report_writer", "summary": None})
            else:
                summary_mode = reporter_plan.summary_mode or "samples"
                project = reporter_plan.project
                if isinstance(project, str) and not project.strip():
                    project = None

                current_agent = "search"
                send_event("search_started", {"source": "reporter", "project": project, "summary_mode": summary_mode})
                reporter_result, saved_files, reporter_summary = run_reporter_summary(config, reporter_plan, log_dir)
                send_event(
                    "search_complete",
                    {
                        "source": "reporter",
                        "ok": reporter_result.get("ok"),
                        "rows_returned": reporter_result.get("rows_returned"),
                        "summary_mode": summary_mode,
                    },
                )

                if not reporter_result.get("ok"):
                    reply = (
                        "The reporter agent could not run the project report.\n\n"
                        f"Error: {reporter_result.get('error', 'unknown error')}"
                    )
                    debug_payload["reporter_result"] = reporter_result
                    session["last_debug"] = debug_payload
                    print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                    return _emit_query_complete(send_event, reply, debug_payload, None)

                current_agent = "chatter"
                send_event("agent_started", {"agent": "chatter", "mode": "reporter"})
                try:
                    narrative = chatter_agent_answer(
                        config,
                        user_text,
                        entity_result.model_dump(),
                        plan.model_dump(),
                        reporter_summary=reporter_summary,
                        log_dir=log_dir,
                        session=session,
                    )
                except Exception as e:
                    narrative = (
                        "Project report completed.\n"
                        f"(Summary generation failed: {repr(e)})"
                    )
                send_event("agent_complete", {"agent": "chatter", "summary": None})

                reply_lines = [
                    narrative.strip(),
                    "",
                    f"- **Rows returned:** {reporter_result.get('rows_returned')}",
                    f"- **Download report:** `{reporter_result.get('uuid_report_file')}`",
                ]
                # Surface a clear hint when the connected DB has no data for the
                # requested project (e.g. local dev DB aliased to MYSQL_HOST_PROD).
                diag = reporter_result.get("db_diagnostic") or {}
                if isinstance(diag, dict) and diag.get("likely_missing_data"):
                    if not diag.get("project_exists_in_db"):
                        reply_lines.append(
                            f"- **Note:** project id `{reporter_result.get('project_id')}` "
                            "doesn't exist in the connected database. The reporter may be "
                            "pointed at a local/dev DB without prod data loaded — check "
                            "`MYSQL_HOST_PROD` in `nextseek.env`."
                        )
                    else:
                        reply_lines.append(
                            f"- **Note:** the connected database has zero rows for "
                            f"project id `{reporter_result.get('project_id')}` across "
                            "all time. Likely a local/dev DB without prod data loaded — "
                            "check `MYSQL_HOST_PROD` in `nextseek.env`."
                        )
                reply_lines.extend([
                    "",
                    "_Detailed tables and downloads are available in the **Reporter result** panel._",
                ])
                reply = "\n".join(reply_lines)

            debug_payload["reporter_result"] = reporter_result
            debug_payload["report_writer_output"] = (
                report_writer_output.model_dump()
                if isinstance(report_writer_output, ReportWriterOutput)
                else report_writer_output
            )
            debug_payload["reporter_metadata"] = None
            if isinstance(reporter_result, dict):
                if "metadata" in reporter_result:
                    debug_payload["reporter_metadata"] = reporter_result.get("metadata")
                elif isinstance(reporter_result.get("reports"), list) and reporter_result["reports"]:
                    debug_payload["reporter_metadata"] = {
                        entry.get("uid") or f"item_{idx}": entry.get("metadata")
                        for idx, entry in enumerate(reporter_result["reports"])
                    }
                if saved_files:
                    debug_payload["report_saved_files"] = saved_files

            history = session.get("results_history", [])
            bundle_id = len(history) + 1
            result_files = build_saved_report_file_manifest(saved_files)
            report_writer_output_payload = (
                report_writer_output.model_dump()
                if isinstance(report_writer_output, ReportWriterOutput)
                else report_writer_output
            )
            bundle = build_metadata_bundle(
                bundle_id=bundle_id,
                mode=mode,
                user_query=user_text,
                parser_plan=plan.model_dump(),
                reporter_plan=reporter_plan.model_dump(),
                reporter_result=reporter_result,
                report_writer_output=report_writer_output_payload,
                report_saved_files=saved_files,
                terminal_reply=reply,
                files=result_files,
            )
            history.append(bundle)
            session["results_history"] = history
            session["last_debug"] = debug_payload

            artifacts: list[dict[str, Any]] | None = None
            try:
                from nextseek_api.assistant.excel_export import extract_table_artifacts

                artifacts = extract_table_artifacts(bundle)
            except Exception:
                artifacts = None

            session["last_files"] = result_files
            append_turn(
                session,
                user_query=user_text,
                mode=mode,
                intent_summary=plan.intent_summary,
                entity_result=entity_result,
                tool_summary=build_tool_summary_for_mode("reporter", reporter_plan=reporter_plan.model_dump()),
                assistant_reply=reply,
                bundle_id=bundle_id,
            )
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return _emit_query_complete(
                send_event,
                reply,
                debug_payload,
                bundle_id,
                artifacts=artifacts,
                files=result_files or None,
            )

        if mode == "system_question":
            current_agent = "system"
            send_event("agent_started", {"agent": "system", "mode": mode})
            _t0 = time.perf_counter()
            sys_output = system_agent(config, user_text, entity_result, plan)
            print(f"[TIMING][SYSTEM] {time.perf_counter() - _t0:.2f}s")
            print(f"[DEBUG][SYSTEM] mode={sys_output.mode}")
            print(f"[DEBUG][SYSTEM] entities_consulted={sys_output.entities_consulted}")
            print(f"[DEBUG][SYSTEM] notes={sys_output.notes!r}")
            print(f"[DEBUG][SYSTEM] narrative:\n{sys_output.narrative}")
            send_event("agent_complete", {"agent": "system", "summary": {"mode": sys_output.mode}})
            debug_payload["system_mode"] = sys_output.mode
            debug_payload["debug_info"] = {
                "entity": {
                    "sampletypes": entity_result.model_dump().get("sampletypes", []),
                    "assays": entity_result.model_dump().get("assays", []),
                },
                "parser": {
                    "mode": plan.mode,
                    "intent_summary": plan.intent_summary,
                },
                "system": {
                    "mode": sys_output.mode,
                    "entities_consulted": sys_output.entities_consulted,
                    "notes": sys_output.notes,
                },
            }
            reply = sys_output.narrative
            session["last_debug"] = debug_payload
            append_turn(
                session,
                user_query=user_text,
                mode=mode,
                intent_summary=plan.intent_summary,
                entity_result=entity_result,
                tool_summary={"system_mode": sys_output.mode},
                assistant_reply=reply,
            )
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return _emit_query_complete(send_event, reply, debug_payload, None)

        if mode == "graph_query":
            current_agent = "graph"
            return _execute_graph_turn(
                config=config, session=session, user_text=user_text,
                entity_result=entity_result, plan=plan, log_dir=log_dir,
                artifact_store=artifact_store, send_event=send_event,
                debug_payload=debug_payload, t_total_start=_t_total_start,
            )

        if mode in ("new_search", "refine_last_search"):
            # Graph-origin refines re-run the graph path (with prior Cypher as context);
            # everything below this is REST refine prep.
            if mode == "refine_last_search":
                _history = session.get("results_history", []) or []
                if _history and (_history[-1] or {}).get("mode") == "graph_query":
                    current_agent = "graph"
                    return _execute_graph_turn(
                        config=config, session=session, user_text=user_text,
                        entity_result=entity_result, plan=plan,
                        log_dir=log_dir, artifact_store=artifact_store, send_event=send_event,
                        debug_payload=debug_payload, t_total_start=_t_total_start,
                        refine_context=_build_graph_refine_context(_history[-1]),
                    )
            if mode == "refine_last_search":
                plan_data = plan.model_dump()
                history = session.get("results_history", [])
                if history:
                    last_bundle = history[-1]
                    prev_plan = last_bundle.get("parser_plan", {}) or {}
                    previous_api_plan = last_bundle.get("api_plan")
                    previous_user_query = last_bundle.get("user_query")
                    previous_search_context = last_bundle.get("search_context", {}) or {}

                    if not plan_data.get("target_endpoint"):
                        plan_data["target_endpoint"] = (
                            prev_plan.get("target_endpoint")
                            if isinstance(prev_plan, dict)
                            else None
                        ) or (
                            previous_search_context.get("endpoint")
                            if isinstance(previous_search_context, dict)
                            else None
                        )
                    if not plan_data.get("resolved"):
                        plan_data["resolved"] = prev_plan.get("resolved", {}) if isinstance(prev_plan, dict) else {}
                    if not plan_data.get("filters"):
                        plan_data["filters"] = prev_plan.get("filters", {}) if isinstance(prev_plan, dict) else {}

                    filters = plan_data.get("filters", {}) or {}
                    prev_filters = prev_plan.get("filters", {}) or {}
                    if not prev_filters and isinstance(previous_search_context, dict):
                        prev_filters = previous_search_context.get("filters", {}) or {}
                    for key in ("sampletype_code", "assay_codes", "keywords", "uids"):
                        val = filters.get(key) if isinstance(filters, dict) else None
                        if val in (None, [], "") and isinstance(prev_filters, dict):
                            if key not in filters and isinstance(filters, dict):
                                filters = dict(filters)
                            filters[key] = prev_filters.get(key)
                    plan_data["filters"] = filters

                    match = re.search(r"project id\s*=*\s*(\d+)", user_text, re.IGNORECASE)
                    if match:
                        kw = filters.get("keywords") if isinstance(filters, dict) else []
                        if not isinstance(kw, list):
                            kw = []
                        kw.append(f"project id {match.group(1)}")
                        filters["keywords"] = kw
                        plan_data["filters"] = filters
                    if previous_api_plan:
                        plan_data["previous_api_plan"] = previous_api_plan
                    if previous_user_query:
                        plan_data["previous_user_query"] = previous_user_query

                plan = ParserPlan.model_validate(plan_data)
                debug_payload["parser_plan"] = plan.model_dump()

            endpoint = plan.target_endpoint
            if not endpoint:
                reply = (
                    "The parser recognized this as a database search, but did not specify an endpoint.\n\n"
                    f"Notes: {plan.notes or 'No additional notes.'}"
                )
                session["last_debug"] = debug_payload
                print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                return _emit_query_complete(send_event, reply, debug_payload, None)

            from .agents import api_agent_build_request

            current_agent = "api"
            send_event("agent_started", {"agent": "api", "mode": mode})
            _t0 = time.perf_counter()
            api_plan = api_agent_build_request(config, plan)
            print(f"[TIMING][API_AGENT] {time.perf_counter() - _t0:.2f}s")
            debug_payload["api_plan"] = api_plan.model_dump()
            send_event(
                "agent_complete",
                {"agent": "api", "summary": {"endpoint": api_plan.endpoint, "method": api_plan.method}},
            )

            if not api_plan.endpoint:
                reply = "The API Agent could not construct a valid request for this query."
                session["last_debug"] = debug_payload
                print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                return _emit_query_complete(send_event, reply, debug_payload, None)

            current_agent = "search"
            send_event("search_started", {"source": "api", "endpoint": api_plan.endpoint, "method": api_plan.method})
            api_result_full = tool_nextseek_api_request(
                config=config,
                endpoint=api_plan.endpoint,
                method=api_plan.method,
                requestBody=api_plan.requestBody or {},
                queryParameters=api_plan.queryParameters or {},
            )
            api_plan_dict, api_result_full = _retry_advanced_search_if_empty(
                config, plan.model_dump(), api_plan.model_dump(), api_result_full
            )
            api_plan = APIRequestPlan.model_validate(api_plan_dict)
            debug_payload["api_plan"] = api_plan.model_dump()
            send_event(
                "search_complete",
                {
                    "source": "api",
                    "ok": api_result_full.get("ok"),
                    "status_code": api_result_full.get("status_code"),
                },
            )

            api_result_slim = slim_api_result_for_llm(api_result_full)
            history = session.get("results_history", [])
            bundle_id = len(history) + 1
            raw_json_path = None
            try:
                entry = artifact_store.write_json(
                    key="api_result",
                    label="Full API result JSON",
                    filename=f"api_result_bundle_{bundle_id}.json",
                    payload=api_result_full,
                    kind="api",
                    bundle_id=bundle_id,
                )
                if entry:
                    raw_json_path = entry["path"]
            except Exception as e:
                print("[DEBUG][API_LOG] Failed to write raw API result file:", repr(e))

            debug_payload["api_result_meta"] = {
                "ok": api_result_full.get("ok"),
                "status_code": api_result_full.get("status_code"),
                "url": api_result_full.get("url"),
            }
            debug_payload["api_result_slim"] = api_result_slim
            debug_payload["api_result_full"] = api_result_full
            debug_payload["raw_json_path"] = raw_json_path
            debug_payload["error_context"] = None
            if not api_result_full.get("ok"):
                schema = config.get_schema_for_endpoint(api_plan.endpoint or "")
                req_schema = None
                if isinstance(schema, dict):
                    req_schema = (schema.get("request_schemas") or {}).get(api_plan.method)
                debug_payload["error_context"] = {
                    "ok": api_result_full.get("ok"),
                    "status_code": api_result_full.get("status_code"),
                    "error": api_result_full.get("error"),
                    "url": api_result_full.get("url"),
                    "method": api_result_full.get("method"),
                    "request_body": api_plan.requestBody,
                    "request_query": api_plan.queryParameters,
                    "response_preview": api_result_full.get("data"),
                    "schema_required_paths": _extract_required_paths(req_schema) if req_schema else [],
                }

            result_files: list[dict[str, Any]] = []
            entry = artifact_store.register_path(
                key="api_result",
                label="Full API result JSON",
                path=raw_json_path,
                kind="api",
                bundle_id=bundle_id,
            )
            if entry:
                result_files.append(entry)
            search_context = {
                "endpoint": api_plan.endpoint,
                "method": api_plan.method,
                "request_body": api_plan.requestBody or {},
                "query_params": api_plan.queryParameters or {},
                "filters": plan.model_dump().get("filters"),
            }
            memory_payload = {
                "data": (api_result_full.get("data") if isinstance(api_result_full, dict) else None),
                "api_plan": api_plan.model_dump(),
                "endpoint": api_plan.endpoint,
                "tool": mode,
            }
            bundle = build_metadata_bundle(
                bundle_id=bundle_id,
                mode=mode,
                user_query=user_text,
                parser_plan=plan.model_dump(),
                api_plan=api_plan.model_dump(),
                api_result_full=api_result_full,
                api_result_slim=api_result_slim,
                memory_payload=memory_payload,
                search_context=search_context,
                files=result_files,
                paths={"raw_result_path": raw_json_path},
            )
            history.append(bundle)
            session["results_history"] = history

            log_api_call(
                session,
                user_query=user_text,
                parser_plan=plan.model_dump(),
                api_plan=api_plan.model_dump(),
                api_result_full=api_result_full,
                bundle_id=bundle_id,
            )

            current_agent = "chatter"
            send_event("agent_started", {"agent": "chatter", "mode": "search"})
            _t0 = time.perf_counter()
            answer = chatter_agent_answer(
                config,
                user_text,
                entity_result.model_dump(),
                plan.model_dump(),
                api_plan.model_dump(),
                api_result_slim,
                api_result_full,
                debug_payload["error_context"],
                log_dir=log_dir,
                session=session,
            )
            print(f"[TIMING][CHATTER] {time.perf_counter() - _t0:.2f}s")
            send_event("agent_complete", {"agent": "chatter", "summary": None})
            bundle["terminal_reply"] = answer
            bundle["reply"] = answer
            bundle.setdefault("model_outputs", {})["terminal_reply"] = answer
            session["last_debug"] = debug_payload

            session["last_files"] = result_files
            append_turn(
                session,
                user_query=user_text,
                mode=mode,
                intent_summary=plan.intent_summary,
                entity_result=entity_result,
                tool_summary=build_tool_summary_for_mode(
                    mode,
                    api_plan=api_plan.model_dump(),
                    parser_plan=plan.model_dump(),
                ),
                result_payload=api_result_full,
                assistant_reply=answer,
                bundle_id=bundle_id,
            )
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return _emit_query_complete(send_event, answer, debug_payload, bundle_id, files=result_files or None)

        reply = (
            f"The parser returned an unexpected mode={mode!r}. "
            "I don't yet know how to handle this case."
        )
        session["last_debug"] = debug_payload
        print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
        return _emit_query_complete(send_event, reply, debug_payload, None)

    except LLMFatalError as fatal:
        agent = getattr(fatal, "agent", None) or current_agent
        msg = str(fatal)
        print(f"[FATAL][{(agent or 'unknown').upper()}] Run killed: {msg}")
        if send_event:
            send_event("query_error", {"error": msg, "agent": agent, "fatal": True})
        reply = f"**The request could not be completed.**\n\n{msg}"
        # Persist a chat_log turn so subsequent parser/chatter turns know this
        # query was attempted and failed (otherwise the next turn sees a "hole"
        # in conversational history). Mark mode='error_<agent>' for grep-ability.
        try:
            append_turn(
                session,
                user_query=user_text,
                mode=f"error_{agent or 'unknown'}",
                intent_summary=f"Fatal LLM error in {agent or 'unknown'} agent.",
                tool_summary={"fatal": True, "agent": agent, "error": msg[:240]},
                assistant_reply=reply,
            )
        except Exception as log_err:  # pragma: no cover
            print(f"[FATAL] failed to log chat_log turn: {log_err!r}")
        return _emit_query_complete(send_event, reply, {"fatal_error": msg, "agent": agent}, None)

    except Exception as exc:
        if send_event:
            send_event("query_error", {"error": str(exc), "agent": current_agent})
        raise


def handle_query(session: SessionState | SessionStateProxy, config: ChatConfig, user_text: str) -> str:
    """Convenience wrapper that runs the standard pipeline and returns only the reply text."""
    return run_query(session, config, user_text)["reply"]


def run_query_plan(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    user_text: str,
    send_event: SendEvent | None = None,
    *,
    credentials: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Planner-based orchestrator: entity -> parser -> planner -> executor -> chatter -> evaluator.
    Parallel structure to `run_query`, using the same result contract.

    credentials — same shallow-copy semantics as run_query.
    """
    if credentials:
        config = copy.copy(config)
        if credentials.get("api_user"):
            config.API_USER = credentials["api_user"]
        if credentials.get("api_pass"):
            config.API_PASS = credentials["api_pass"]

    log_dir = _ensure_query_log_dir(session, config)
    artifact_store = ArtifactStore(log_dir)
    _t_total_start = time.perf_counter()
    session["last_files"] = []

    _raw_send_event = send_event

    def send_event(event_name: str, payload: dict) -> None:
        print(f"[DEBUG][EVENT][PLAN] {event_name}: {list(payload.keys())}")
        if _raw_send_event:
            _raw_send_event(event_name, payload)

    try:
        wizard_payload = _handle_wizard_turn(session, config, user_text, log_dir, send_event, artifact_store)
        if wizard_payload is not None:
            print(f"[TIMING][TOTAL][PLAN] {time.perf_counter() - _t_total_start:.2f}s")
            return wizard_payload

        send_event("agent_started", {"agent": "catalog", "mode": "plan"})
        sampletypes_short, assays_short, shortlist_diag = shortlist_catalog(
            user_text,
            config.MIN_SAMPLETYPES or [],
            config.MIN_ASSAYS or [],
            k_st=50,
            k_a=75,
            sampletype_index=getattr(config, "SAMPLETYPE_INDEX", None),
            assay_index=getattr(config, "ASSAY_INDEX", None),
            ratio=getattr(config, "SEMANTIC_RATIO", 0.7),
            min_k=getattr(config, "SEMANTIC_MIN_K", 10),
            max_k=getattr(config, "SEMANTIC_MAX_K", 80),
        )
        sampletypes_short = sampletypes_short or config.MIN_SAMPLETYPES or []
        assays_short = assays_short or config.MIN_ASSAYS or []
        send_event("agent_complete", {"agent": "catalog", "summary": None})

        send_event("agent_started", {"agent": "entity", "mode": "plan"})
        _t0 = time.perf_counter()
        entity_result = entity_agent(config, user_text, sampletypes_short, assays_short)
        print(f"[TIMING][ENTITY] {time.perf_counter() - _t0:.2f}s")
        send_event("agent_complete", {"agent": "entity", "summary": entity_result.model_dump()})

        send_event("agent_started", {"agent": "parser", "mode": "plan"})
        _t0 = time.perf_counter()
        multi_parser_plan = multi_parser_agent(session, config, user_text, entity_result)
        print(f"[TIMING][MULTI_PARSER] {time.perf_counter() - _t0:.2f}s")
        send_event(
            "agent_complete",
            {"agent": "parser", "summary": {"candidates": len(multi_parser_plan.candidates), "intent": multi_parser_plan.intent_summary}},
        )
        print(f"\n[MULTI_PARSER]\n{json.dumps(multi_parser_plan.model_dump(), indent=2)}\n")

        debug_payload: dict[str, Any] = {
            "mode": "plan",
            "parser": multi_parser_plan.model_dump(),
            "planner": None,
            "planner_iterations": [],
            "entity": entity_result.model_dump(),
            "shortlist_sampletype_codes": shortlist_diag.get("sampletype_codes", []),
            "shortlist_assay_codes": shortlist_diag.get("assay_codes", []),
            "shortlist_diagnostics": shortlist_diag,
            "provisional_reply": None,
            "evaluator": None,
            "replan_attempted": False,
            "replan_reason": None,
            "termination_reason": None,
            "step_budget": {"max_steps": 5, "used_steps": 0},
        }

        def _step_summary(sr: dict) -> dict:
            base = {k: v for k, v in sr.items() if k != "output"}
            base["count"] = (sr.get("output") or {}).get("count")
            # Surface api_plan for search steps so test criteria can inspect requestBody
            if sr.get("tool") in {"new_search", "refine_last_search"}:
                api_plan = (sr.get("output") or {}).get("api_plan")
                if api_plan:
                    base["api_plan"] = api_plan
            return base

        max_plan_steps = 5
        executed_steps = []
        step_results: dict[int, dict] = {}
        step_summary: dict[Any, dict] = {}
        enriched_context = {}
        intersection_uids = None
        seen_step_signatures: set[str] = set()
        planner_notes: list[str] = []
        stop_reason: str | None = None
        termination_reason: str | None = None

        for iteration in range(1, max_plan_steps + 1):
            send_event("agent_started", {"agent": "planner", "mode": "plan"})
            _t0 = time.perf_counter()
            planner_decision = planner_agent(
                session,
                config,
                user_text,
                entity_result,
                parser_plan=multi_parser_plan,
                prior_steps=executed_steps,
                step_results=step_results,
                max_steps=max_plan_steps,
            )
            print(f"[TIMING][PLANNER] {time.perf_counter() - _t0:.2f}s")
            debug_payload["planner_iterations"].append(planner_decision.model_dump())
            send_event(
                "agent_complete",
                {
                    "agent": "planner",
                    "summary": {
                        "iteration": iteration,
                        "action": planner_decision.action,
                        "intent": planner_decision.intent_summary,
                        "tool": planner_decision.step.tool if planner_decision.step else None,
                        "termination_reason": planner_decision.termination_reason,
                    },
                },
            )
            print(f"\n[PLANNER_DECISION {iteration}]\n{json.dumps(planner_decision.model_dump(), indent=2)}\n")
            if planner_decision.notes:
                planner_notes.append(planner_decision.notes)

            if planner_decision.action == "halt" or planner_decision.step is None:
                termination_reason = planner_decision.termination_reason or "answered"
                stop_reason = planner_decision.rationale or planner_decision.notes or termination_reason
                break

            next_step = planner_decision.step
            signature = _step_signature(next_step)
            if signature in seen_step_signatures:
                termination_reason = "repeated_strategy"
                stop_reason = "Planner proposed a step equivalent to one already executed."
                debug_payload["repeated_step"] = next_step.model_dump()
                break
            seen_step_signatures.add(signature)

            tool_output, debug_fragment_local, enriched_context, intersection_uids, step_stop_reason = _execute_single_plan_step(
                config,
                session,
                next_step,
                entity_result,
                log_dir,
                send_event,
                parser_plan=multi_parser_plan,
                step_results=step_results,
                enriched_context=enriched_context,
                intersection_uids=intersection_uids,
            )
            step_results[next_step.step_id] = tool_output
            executed_steps.append(next_step)
            for key, value in debug_fragment_local.items():
                if isinstance(value, dict):
                    debug_payload.setdefault(key, {}).update(value)
                else:
                    debug_payload[key] = value
            step_summary[next_step.step_id] = _step_summary(tool_output)
            debug_payload["step_budget"]["used_steps"] = len(executed_steps)

            if step_stop_reason:
                stop_reason = step_stop_reason
                if "missing required inputs" in step_stop_reason.lower():
                    termination_reason = "missing_required_inputs"
                else:
                    termination_reason = "hard_step_failure"
                break
        else:
            termination_reason = "step_budget_exhausted"
            stop_reason = "Planner reached the maximum step budget before halting."

        plan = PlannerOutput(
            intent_summary=multi_parser_plan.intent_summary or user_text,
            steps=executed_steps,
            notes=" | ".join(note for note in planner_notes if note),
        )
        _materialize_intersection_result(executed_steps, step_results, debug_payload, intersection_uids)
        if "intersection" in step_results:
            step_summary["intersection"] = _step_summary(step_results["intersection"])

        debug_payload["planner"] = plan.model_dump()
        debug_payload["step_results"] = step_summary
        debug_payload["termination_reason"] = termination_reason

        send_event("agent_started", {"agent": "chatter", "mode": "plan"})
        _t0 = time.perf_counter()
        terminal_reply = None
        if plan.steps:
            last_step = plan.steps[-1]
            last_output = (step_results.get(last_step.step_id) or {}).get("output") or {}
            if (
                last_step.tool in {"system_question", "ask_about_last_results", "memory_lookup", "unsupported", "report_generation"}
                and isinstance(last_output.get("reply"), str)
                and last_output.get("reply", "").strip()
            ):
                terminal_reply = last_output["reply"].strip()
        provisional_reply = terminal_reply or chatter_agent_plan(config, user_text, plan, step_results, log_dir, session=session)
        print(f"[TIMING][PLAN_CHATTER] {time.perf_counter() - _t0:.2f}s")
        print(f"[DEBUG][PLAN_CHATTER] Reply:\n{provisional_reply}")
        send_event("agent_complete", {"agent": "chatter", "summary": None})
        debug_payload["provisional_reply"] = provisional_reply

        send_event("agent_started", {"agent": "evaluator", "mode": "plan"})
        _t0 = time.perf_counter()
        evaluator_output = plan_evaluator_agent(
            config,
            user_text,
            entity_result,
            multi_parser_plan,
            plan,
            step_results,
            provisional_reply,
            stop_reason=stop_reason,
            log_dir=log_dir,
        )
        print(f"[TIMING][EVALUATOR] {time.perf_counter() - _t0:.2f}s")
        send_event(
            "agent_complete",
            {"agent": "evaluator", "summary": {"status": evaluator_output.overall_status, "answered": evaluator_output.answered_query}},
        )
        debug_payload["evaluator"] = evaluator_output.model_dump()

        evaluator_lines = [
            f"Status: {evaluator_output.overall_status}",
            f"Answered Query: {'yes' if evaluator_output.answered_query else 'no'}",
            f"Execution Consistent: {'yes' if evaluator_output.execution_consistent else 'no'}",
            "Steps Followed: " + " -> ".join(step.get("tool", "?") for step in (plan.model_dump().get("steps") or [])),
            f"Termination Reason: {termination_reason or 'answered'}",
        ]
        if evaluator_output.zero_results_assessment != "not_applicable":
            evaluator_lines.append(f"Zero Results Assessment: {evaluator_output.zero_results_assessment}")
        if evaluator_output.user_safe_summary:
            evaluator_lines.append(f"Summary: {evaluator_output.user_safe_summary}")
        elif evaluator_output.reason:
            evaluator_lines.append(f"Summary: {evaluator_output.reason}")

        reply = (
            "**Evaluator**\n\n"
            + "\n".join(f"- {line}" for line in evaluator_lines)
            + "\n\n"
            + provisional_reply
        )
        if stop_reason and termination_reason not in {None, "answered"} and not evaluator_output.user_safe_summary:
            reply = f"Plan halted: {stop_reason}\n\n{reply}"

        history = session.get("results_history", [])
        bundle_id = len(history) + 1
        result_files: list[dict[str, Any]] = []
        raw_result_paths: dict[int, str] = {}
        graph_debug_paths: dict[int, str] = {}
        plan_debug_path: str | None = None

        try:
            full_debug = {**debug_payload, "step_results_full": step_results}
            entry = artifact_store.write_json(
                key="plan_debug",
                label="Plan debug JSON",
                filename=f"plan_debug_{bundle_id}.json",
                payload=full_debug,
                kind="plan",
                bundle_id=bundle_id,
            )
            if entry:
                plan_debug_path = entry["path"]
                print(f"[PLAN] Debug written to {entry['path']}")
                result_files.append(entry)
        except Exception as e:
            print(f"[PLAN] Failed to write plan debug: {e!r}")

        for step_id, sr in step_results.items():
            if sr.get("tool") in {"new_search", "refine_last_search", "coding_filter"} and sr.get("ok") and sr.get("output"):
                try:
                    entry = artifact_store.write_json(
                        key=f"api_result_step_{step_id}",
                        label=f"Step result (step {step_id})",
                        filename=f"api_result_bundle_{bundle_id}_step_{step_id}.json",
                        payload=sr["output"],
                        kind="api",
                        bundle_id=bundle_id,
                        step_id=step_id,
                    )
                    if entry:
                        raw_result_paths[step_id] = entry["path"]
                        result_files.append(entry)
                except Exception as e:
                    print(f"[PLAN] Failed to write step {step_id} API result: {e!r}")
            if sr.get("tool") == "graph_query" and sr.get("output"):
                try:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_step_{step_id}"
                    graph_debug_path = _write_graph_debug(
                        log_dir,
                        ts,
                        {
                            "graph_plan": sr["output"].get("graph_plan"),
                            "result": {"data": sr["output"].get("data"), "count": sr["output"].get("count")},
                            "error": sr.get("error"),
                        },
                    )
                    if graph_debug_path:
                        graph_debug_paths[step_id] = graph_debug_path
                    entry = artifact_store.register_path(
                        key=f"graph_debug_step_{step_id}",
                        label=f"Graph debug (step {step_id})",
                        path=graph_debug_path,
                        kind="graph",
                        bundle_id=bundle_id,
                        step_id=step_id,
                    )
                    if entry:
                        result_files.append(entry)
                except Exception as e:
                    print(f"[PLAN] Failed to write step {step_id} graph debug: {e!r}")
            if sr.get("tool") in {"reporter", "report_generation"} and sr.get("ok") and sr.get("output"):
                try:
                    result_files.extend(
                        build_saved_report_file_manifest(
                            (sr.get("output") or {}).get("saved_files"),
                            key_prefix=f"step_{step_id}_",
                        )
                    )
                except Exception as e:
                    print(f"[PLAN] Failed to collect step {step_id} report files: {e!r}")

        canonical_api_plan = None
        canonical_api_result = None
        canonical_graph_plan = None
        canonical_graph_result = None
        canonical_reporter_plan = None
        canonical_reporter_result = None
        canonical_report_saved_files = None
        canonical_memory_payload = None
        canonical_search_context = None
        for sr in step_results.values():
            if not isinstance(sr, dict) or not sr.get("ok"):
                continue
            output = sr.get("output") or {}
            if not isinstance(output, dict):
                continue
            tool = sr.get("tool")
            if tool in {"new_search", "refine_last_search"} and canonical_api_plan is None:
                canonical_api_plan = output.get("api_plan")
                rows = output.get("data") if isinstance(output.get("data"), list) else []
                canonical_api_result = {
                    "ok": sr.get("ok"),
                    "data": {"rows": rows, "total": output.get("count", len(rows))},
                    "error": sr.get("error"),
                }
                canonical_memory_payload = {
                    "data": {"rows": rows, "total": output.get("count", len(rows))},
                    "api_plan": canonical_api_plan,
                    "endpoint": output.get("endpoint"),
                    "tool": tool,
                }
                canonical_search_context = {
                    "endpoint": output.get("endpoint"),
                    "method": (canonical_api_plan or {}).get("method") if isinstance(canonical_api_plan, dict) else None,
                    "request_body": (canonical_api_plan or {}).get("requestBody") if isinstance(canonical_api_plan, dict) else {},
                    "query_params": (canonical_api_plan or {}).get("queryParameters") if isinstance(canonical_api_plan, dict) else {},
                }
            elif tool == "coding_filter":
                rows = output.get("data") if isinstance(output.get("data"), list) else []
                canonical_memory_payload = {
                    "data": {"rows": rows, "total": output.get("count", len(rows))},
                    "source_output": output,
                    "tool": tool,
                }
            elif tool == "graph_query" and canonical_graph_plan is None:
                canonical_graph_plan = output.get("graph_plan")
                canonical_graph_result = {
                    "ok": sr.get("ok"),
                    "data": output.get("data") or [],
                    "count": output.get("count", 0),
                    "error": sr.get("error"),
                }
                if canonical_memory_payload is None:
                    canonical_memory_payload = canonical_graph_result
            elif tool in {"reporter", "report_generation"}:
                canonical_reporter_plan = canonical_reporter_plan or output.get("reporter_plan")
                canonical_reporter_result = canonical_reporter_result or output.get("reporter_result")
                canonical_report_saved_files = canonical_report_saved_files or output.get("saved_files")
                if canonical_memory_payload is None:
                    canonical_memory_payload = output

        bundle = build_metadata_bundle(
            bundle_id=bundle_id,
            mode="plan",
            user_query=user_text,
            parser_plan=None,
            api_plan=canonical_api_plan,
            api_result_full=canonical_api_result,
            graph_plan=canonical_graph_plan,
            graph_result=canonical_graph_result,
            reporter_plan=canonical_reporter_plan,
            reporter_result=canonical_reporter_result,
            report_saved_files=canonical_report_saved_files,
            planner_output=plan.model_dump(),
            multi_parser_plan=multi_parser_plan.model_dump(),
            step_results=step_results,
            terminal_reply=reply,
            provisional_reply=provisional_reply,
            memory_payload=canonical_memory_payload,
            search_context=canonical_search_context,
            files=result_files,
            paths={
                "raw_result_path": next(iter(raw_result_paths.values()), None),
                "graph_debug_path": next(iter(graph_debug_paths.values()), None),
                "plan_debug_path": plan_debug_path,
                "raw_result_paths": raw_result_paths,
                "graph_debug_paths": graph_debug_paths,
            },
        )
        history.append(bundle)
        session["results_history"] = history
        session["last_debug"] = debug_payload
        session["last_files"] = result_files

        last_step_tool = plan.steps[-1].tool if plan.steps else None
        plan_tool_summary: dict[str, Any] = {
            "steps": [s.tool for s in plan.steps],
            "termination_reason": termination_reason,
        }
        plan_result_payload: dict | None = None
        if canonical_api_result is not None:
            plan_result_payload = canonical_api_result
        elif canonical_graph_result is not None:
            plan_result_payload = canonical_graph_result
        append_turn(
            session,
            user_query=user_text,
            mode=f"plan:{last_step_tool}" if last_step_tool else "plan",
            intent_summary=multi_parser_plan.intent_summary,
            entity_result=entity_result,
            tool_summary=plan_tool_summary,
            result_payload=plan_result_payload,
            assistant_reply=reply,
            bundle_id=bundle_id,
        )

        print(f"[TIMING][TOTAL][PLAN] {time.perf_counter() - _t_total_start:.2f}s")
        return _emit_query_complete(send_event, reply, debug_payload, bundle_id, files=result_files or None)

    except LLMFatalError as fatal:
        agent = getattr(fatal, "agent", None) or "unknown"
        msg = str(fatal)
        print(f"[FATAL][PLAN][{agent.upper()}] Run killed: {msg}")
        if send_event:
            send_event("query_error", {"error": msg, "agent": agent, "fatal": True})
        reply = f"**The planner pipeline was stopped.**\n\n{msg}"
        try:
            append_turn(
                session,
                user_query=user_text,
                mode=f"error_plan_{agent}",
                intent_summary=f"Fatal LLM error in planner pipeline (agent={agent}).",
                tool_summary={"fatal": True, "agent": agent, "error": msg[:240]},
                assistant_reply=reply,
            )
        except Exception as log_err:
            print(f"[FATAL][PLAN] failed to log chat_log turn: {log_err!r}")
        return _emit_query_complete(send_event, reply, {"fatal_error": msg, "agent": agent}, None)

    except Exception as e:
        import traceback

        print(f"[ERROR][PLAN] run_query_plan unhandled exception: {e!r}")
        traceback.print_exc()
        reply = f"An unexpected error occurred in the planner pipeline: {e}"
        return _emit_query_complete(send_event, reply, {"error": repr(e)}, None)


def handle_query_plan(session: SessionState | SessionStateProxy, config: ChatConfig, user_text: str) -> str:
    """Thin wrapper that runs the planner pipeline and returns only the reply text."""
    return run_query_plan(session, config, user_text)["reply"]

"""
Orchestrates chat_nextseek agent functions with SSE event callbacks.

Replicates the logic of chat_nextseek.agents.handle_query() without
Streamlit dependencies, emitting structured events via a callback
instead of writing to st.session_state.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# chat_nextseek imports are deferred to function bodies so that importing
# this module does not trigger chat_nextseek's eager prompt-file loading
# (which requires external files that may not be present at import time).

logger = logging.getLogger(__name__)

SendEvent = Callable[[str, dict[str, Any]], None]


def run_query(
    session_adapter: Any,
    user_query: str,
    send_event: SendEvent,
) -> None:
    """Orchestrate the chat_nextseek agent pipeline with SSE event callbacks.

    Args:
        session_adapter: DictSessionAdapter wrapping a ChatSession.
        user_query: The user's natural language query.
        send_event: ``send_event(event_type, data)`` — emits an SSE event.
    """
    from chat_nextseek.agents import entity_agent, memory_agent_answer, parser_agent
    from chat_nextseek.config import MIN_ASSAYS, MIN_SAMPLETYPES
    from chat_nextseek.helpers import fix_sample_endpoint, shortlist_catalog
    from chat_nextseek.schemas import ParserPlan

    current_agent = "catalog"
    try:
        # ------------------------------------------------------------------
        # 1. Catalog shortlisting
        # ------------------------------------------------------------------
        send_event("agent_started", {"agent": "catalog", "mode": ""})
        sampletypes_short, assays_short = shortlist_catalog(
            user_query,
            MIN_SAMPLETYPES or [],
            MIN_ASSAYS or [],
            k_st=50,
            k_a=50,
        )
        if not sampletypes_short:
            sampletypes_short = MIN_SAMPLETYPES or []
        if not assays_short:
            assays_short = MIN_ASSAYS or []
        send_event("agent_complete", {"agent": "catalog", "summary": None})

        # ------------------------------------------------------------------
        # 2. Entity agent
        # ------------------------------------------------------------------
        current_agent = "entity"
        send_event("agent_started", {"agent": "entity", "mode": ""})
        entity_result = entity_agent(user_query, sampletypes_short, assays_short)
        send_event("agent_complete", {
            "agent": "entity",
            "summary": entity_result.model_dump(),
        })

        # ------------------------------------------------------------------
        # 3. Parser agent
        # ------------------------------------------------------------------
        current_agent = "parser"
        send_event("agent_started", {"agent": "parser", "mode": ""})
        plan = parser_agent(session_adapter, user_query, entity_result)
        plan = ParserPlan.model_validate(fix_sample_endpoint(plan.model_dump()))
        mode = plan.mode
        send_event("agent_complete", {
            "agent": "parser",
            "summary": {"mode": mode, "endpoint": plan.target_endpoint},
        })

        # Build base debug payload
        debug_payload: dict[str, Any] = {
            "entity_result": entity_result.model_dump(),
            "parser_plan": plan.model_dump(),
            "api_plan": None,
            "reporter_plan": None,
            "reporter_result": None,
            "report_writer_output": None,
            "reporter_metadata": None,
            "api_result_meta": None,
            "api_result_slim": None,
            "api_result_norm": None,
            "api_result_full": None,
            "raw_json_path": None,
            "error_context": None,
        }

        # ------------------------------------------------------------------
        # 4. Branch on mode
        # ------------------------------------------------------------------

        if mode == "unsupported":
            notes = plan.notes or "No additional notes."
            reply = (
                "I can't turn that request into a valid NExtSEEK operation yet.\n\n"
                f"Reason from parser: {notes}"
            )
            session_adapter["last_debug"] = debug_payload
            _emit_complete(send_event, reply, debug_payload, None)
            return

        if mode == "system_question":
            notes = plan.notes or ""
            reply = (
                "This is a system/meta question about NExtSEEK.\n\n"
                f"Intent summary: {plan.intent_summary}\n\n"
                f"Notes: {notes}"
            )
            session_adapter["last_debug"] = debug_payload
            _emit_complete(send_event, reply, debug_payload, None)
            return

        if mode == "ask_about_last_results":
            current_agent = "memory"
            send_event("agent_started", {"agent": "memory", "mode": mode})
            history = session_adapter.get("results_history", [])
            if not history:
                reply = (
                    "You asked a follow-up question about previous data, but there are no stored results "
                    "in this session yet. Please run a search first."
                )
                session_adapter["last_debug"] = debug_payload
                send_event("agent_complete", {"agent": "memory", "summary": None})
                _emit_complete(send_event, reply, debug_payload, None)
                return

            target_id = plan.target_result_id
            if target_id is None:
                bundle = history[-1]
            else:
                bundle = next((b for b in history if b.get("id") == target_id), None)
                if bundle is None:
                    reply = (
                        f"You referred to previous results with id={target_id}, but I couldn't find that "
                        "in this session. Please run a new search."
                    )
                    session_adapter["last_debug"] = debug_payload
                    send_event("agent_complete", {"agent": "memory", "summary": None})
                    _emit_complete(send_event, reply, debug_payload, None)
                    return

            answer = memory_agent_answer(user_query, bundle)

            debug_payload["api_plan"] = bundle.get("api_plan")
            api_full = bundle.get("api_result_full", {})
            debug_payload["api_result_full"] = api_full
            debug_payload["raw_json_path"] = bundle.get("raw_result_path")
            debug_payload["api_result_meta"] = {
                "ok": api_full.get("ok"),
                "status_code": api_full.get("status_code"),
                "url": api_full.get("url"),
                "bundle_id": bundle.get("id"),
            }
            debug_payload["api_result_slim"] = bundle.get("api_result_slim")
            debug_payload["api_result_norm"] = bundle.get("api_result_norm")

            session_adapter["last_debug"] = debug_payload
            send_event("agent_complete", {"agent": "memory", "summary": None})
            _emit_complete(send_event, answer, debug_payload, bundle.get("id"))
            return

        if mode == "reporter":
            _handle_reporter(
                session_adapter, user_query, plan, mode, debug_payload, send_event
            )
            return

        if mode in ("new_search", "refine_last_search"):
            _handle_search(
                session_adapter, user_query, plan, mode, debug_payload, send_event
            )
            return

        # Unexpected mode
        reply = (
            f"The parser returned an unexpected mode={mode!r}. "
            "I don't yet know how to handle this case."
        )
        session_adapter["last_debug"] = debug_payload
        _emit_complete(send_event, reply, debug_payload, None)

    except Exception as exc:
        logger.exception("Pipeline error in agent=%s", current_agent)
        send_event("query_error", {"error": str(exc), "agent": current_agent})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _emit_complete(
    send_event: SendEvent,
    reply: str,
    debug: dict[str, Any],
    bundle_id: int | None,
) -> None:
    send_event("query_complete", {
        "reply": reply,
        "debug": debug,
        "bundle_id": bundle_id,
    })


def _handle_reporter(
    session_adapter: Any,
    user_query: str,
    plan: ParserPlan,
    mode: str,
    debug_payload: dict[str, Any],
    send_event: SendEvent,
) -> None:
    """Handle reporter mode: SQL reports or report generation."""
    from chat_nextseek.agents import (
        chatter_agent_report_answer,
        report_writer_agent,
        reporter_agent,
    )
    from chat_nextseek.config import LOG_DIR
    from chat_nextseek.helpers import generate_report_outputs, persist_report_file, top_items
    from chat_nextseek.schemas import ReportWriterOutput

    current_agent = "reporter"
    send_event("agent_started", {"agent": "reporter", "mode": mode})

    reporter_plan_obj = reporter_agent(user_query, plan)
    debug_payload["reporter_plan"] = reporter_plan_obj.model_dump()

    reporter_mode = reporter_plan_obj.reporter_mode or plan.report_mode or "summary_sql"
    reporter_result = None
    report_writer_output = None
    saved_files: dict[str, str] = {}
    per_sample_reports = bool(
        (reporter_plan_obj.reporter_context or {}).get("per_sample_reports", False)
    )

    send_event("agent_complete", {
        "agent": "reporter",
        "summary": {"reporter_mode": reporter_mode},
    })

    if reporter_mode == "report_generation":
        current_agent = "report_writer"
        send_event("agent_started", {"agent": "report_writer", "mode": reporter_mode})

        uids = reporter_plan_obj.uids or []
        try:
            parser_uids = plan.filters.uids if hasattr(plan, "filters") else []
        except Exception:
            parser_uids = []
        if parser_uids:
            existing = set(uids)
            uids.extend([u for u in parser_uids if u not in existing])

        reporter_result, report_writer_output, saved_files, reply = generate_report_outputs(
            user_query=user_query,
            parser_plan=plan,
            reporter_plan=reporter_plan_obj,
            uids=uids,
            log_dir=LOG_DIR,
            report_writer_fn=report_writer_agent,
            per_sample_reports=per_sample_reports,
        )

        send_event("agent_complete", {"agent": "report_writer", "summary": None})

    else:
        from chat_nextseek.helpers import run_project_sample_report

        project = reporter_plan_obj.project
        if isinstance(project, str) and not project.strip():
            project = None
        years = reporter_plan_obj.years or []
        month_range = reporter_plan_obj.month_range
        day_range = reporter_plan_obj.day_range

        try:
            reporter_result = run_project_sample_report(
                project,
                years=years,
                month_range=month_range,
                day_range=day_range,
                outputs_root=LOG_DIR,
            )
        except Exception as e:
            reporter_result = {"ok": False, "error": repr(e)}

        if not reporter_result.get("ok"):
            reply = (
                "The reporter agent could not run the project report.\n\n"
                f"Error: {reporter_result.get('error', 'unknown error')}"
            )
            debug_payload["reporter_result"] = reporter_result
            session_adapter["last_debug"] = debug_payload
            _emit_complete(send_event, reply, debug_payload, None)
            return

        # Persist SQL report outputs
        uuid_path = reporter_result.get("uuid_report_file")
        if uuid_path:
            uuid_basename = Path(uuid_path).stem
            base_name = uuid_basename.replace(".uuids", "")
            reporter_result_name = f"{base_name}.reporter_result"
        else:
            reporter_result_name = "reporter_result"
        summary_path = persist_report_file(reporter_result_name, reporter_result, LOG_DIR)
        if summary_path:
            saved_files["reporter_result"] = summary_path
        if uuid_path and Path(uuid_path).exists():
            try:
                dest = Path(LOG_DIR) / Path(uuid_path).name
                if dest.resolve() != Path(uuid_path).resolve():
                    shutil.copyfile(uuid_path, dest)
                saved_files["uuid_report_file"] = str(dest)
            except Exception as e:
                logger.warning("Failed to copy uuid report file: %s", repr(e))

        # Build summary for chatter
        reporter_summary = {
            "project": project,
            "project_id": reporter_result.get("project_id"),
            "rows_returned": reporter_result.get("rows_returned"),
            "filters": reporter_result.get("filters") or {},
            "top_sampletypes": top_items(reporter_result.get("sampletypes_table"), 5),
            "top_labs": top_items(reporter_result.get("labs_table"), 5),
            "years": top_items(reporter_result.get("years_table"), 10),
            "top_months": top_items(reporter_result.get("months_table"), 12),
            "uuid_report_file": reporter_result.get("uuid_report_file"),
        }

        current_agent = "chatter"
        send_event("agent_started", {"agent": "chatter", "mode": "reporter"})
        try:
            narrative = chatter_agent_report_answer(user_query, reporter_summary)
        except Exception as e:
            narrative = (
                "Project report completed.\n"
                f"(Summary generation failed: {repr(e)})"
            )
        send_event("agent_complete", {"agent": "chatter", "summary": None})

        reply = "\n".join([
            narrative.strip(),
            "",
            f"- **Rows returned:** {reporter_result.get('rows_returned')}",
            f"- **Download report:** `{reporter_result.get('uuid_report_file')}`",
            "",
            "_Detailed tables and downloads are available in the **Reporter result** panel._",
        ])

    # Update debug + session
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

    history = session_adapter.get("results_history", [])
    bundle_id = len(history) + 1
    bundle = {
        "id": bundle_id,
        "timestamp": datetime.now().isoformat(),
        "user_query": user_query,
        "mode": mode,
        "parser_plan": plan.model_dump(),
        "reporter_plan": reporter_plan_obj.model_dump(),
        "reporter_result": reporter_result,
        "report_writer_output": (
            report_writer_output.model_dump()
            if isinstance(report_writer_output, ReportWriterOutput)
            else report_writer_output
        ),
        "report_saved_files": saved_files,
    }
    history.append(bundle)
    session_adapter["results_history"] = history
    session_adapter["last_debug"] = debug_payload

    _emit_complete(send_event, reply, debug_payload, bundle_id)


def _handle_search(
    session_adapter: Any,
    user_query: str,
    plan: ParserPlan,
    mode: str,
    debug_payload: dict[str, Any],
    send_event: SendEvent,
) -> None:
    """Handle new_search / refine_last_search modes."""
    from chat_nextseek.agents import api_agent_build_request, chatter_agent_search_answer
    from chat_nextseek.config import LOG_DIR, get_schema_for_endpoint
    from chat_nextseek.helpers import (
        _extract_required_paths,
        _retry_advanced_search_if_empty,
        normalize_api_result_for_memory,
        slim_api_result_for_llm,
        tool_nextseek_api_request,
    )
    from chat_nextseek.schemas import APIRequestPlan, ParserPlan

    # Refine: merge previous context
    if mode == "refine_last_search":
        plan_data = plan.model_dump()
        history = session_adapter.get("results_history", [])
        if history:
            last_bundle = history[-1]
            prev_plan = last_bundle.get("parser_plan", {}) or {}
            previous_api_plan = last_bundle.get("api_plan")
            previous_user_query = last_bundle.get("user_query")

            if not plan_data.get("target_endpoint"):
                plan_data["target_endpoint"] = prev_plan.get("target_endpoint")
            if not plan_data.get("resolved"):
                plan_data["resolved"] = prev_plan.get("resolved", {})
            if not plan_data.get("filters"):
                plan_data["filters"] = prev_plan.get("filters", {})

            filters = plan_data.get("filters", {}) or {}
            prev_filters = prev_plan.get("filters", {}) or {}
            for key in ("sampletype_code", "assay_codes", "keywords", "uids"):
                val = filters.get(key) if isinstance(filters, dict) else None
                if val in (None, [], "") and isinstance(prev_filters, dict):
                    if key not in filters and isinstance(filters, dict):
                        filters = dict(filters)
                    filters[key] = prev_filters.get(key)
            plan_data["filters"] = filters

            match = re.search(r"project id\s*=*\s*(\d+)", user_query, re.IGNORECASE)
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

    endpoint = plan.target_endpoint
    if not endpoint:
        reply = (
            "The parser recognized this as a database search, but did not specify an endpoint.\n\n"
            f"Notes: {plan.notes or 'No additional notes.'}"
        )
        session_adapter["last_debug"] = debug_payload
        _emit_complete(send_event, reply, debug_payload, None)
        return

    # API agent
    current_agent = "api"
    send_event("agent_started", {"agent": "api", "mode": mode})
    api_plan = api_agent_build_request(plan)
    debug_payload["api_plan"] = api_plan.model_dump()
    send_event("agent_complete", {
        "agent": "api",
        "summary": {"endpoint": api_plan.endpoint, "method": api_plan.method},
    })

    if not api_plan.endpoint:
        reply = "The API Agent could not construct a valid request for this query."
        session_adapter["last_debug"] = debug_payload
        _emit_complete(send_event, reply, debug_payload, None)
        return

    # Execute HTTP request
    current_agent = "http"
    send_event("agent_started", {"agent": "http", "mode": ""})
    api_result_full = tool_nextseek_api_request(
        endpoint=api_plan.endpoint,
        method=api_plan.method,
        requestBody=api_plan.requestBody or {},
        queryParameters=api_plan.queryParameters or {},
    )
    api_plan_dict, api_result_full = _retry_advanced_search_if_empty(
        plan.model_dump(), api_plan.model_dump(), api_result_full
    )
    api_plan = APIRequestPlan.model_validate(api_plan_dict)
    debug_payload["api_plan"] = api_plan.model_dump()
    send_event("agent_complete", {
        "agent": "http",
        "summary": {
            "ok": api_result_full.get("ok"),
            "status_code": api_result_full.get("status_code"),
        },
    })

    api_result_norm = normalize_api_result_for_memory(api_result_full)
    api_result_slim = slim_api_result_for_llm(api_result_full)

    history = session_adapter.get("results_history", [])
    bundle_id = len(history) + 1
    raw_json_path = None
    try:
        raw_path = Path(LOG_DIR) / f"api_result_bundle_{bundle_id}.json"
        raw_path.write_text(json.dumps(api_result_full, indent=2), encoding="utf-8")
        raw_json_path = str(raw_path)
    except Exception as e:
        logger.warning("Failed to write raw API result file: %s", repr(e))

    debug_payload["api_result_meta"] = {
        "ok": api_result_full.get("ok"),
        "status_code": api_result_full.get("status_code"),
        "url": api_result_full.get("url"),
    }
    debug_payload["api_result_slim"] = api_result_slim
    debug_payload["api_result_norm"] = api_result_norm
    debug_payload["api_result_full"] = api_result_full
    debug_payload["raw_json_path"] = raw_json_path
    debug_payload["error_context"] = None
    if not api_result_full.get("ok"):
        schema = get_schema_for_endpoint(api_plan.endpoint or "")
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

    bundle = {
        "id": bundle_id,
        "timestamp": datetime.now().isoformat(),
        "user_query": user_query,
        "mode": mode,
        "parser_plan": plan.model_dump(),
        "api_plan": api_plan.model_dump(),
        "endpoint": api_plan.endpoint,
        "method": api_plan.method,
        "request_body": api_plan.requestBody or {},
        "query_params": api_plan.queryParameters or {},
        "api_result_full": api_result_full,
        "api_result_norm": api_result_norm,
        "api_result_slim": api_result_slim,
        "raw_result_path": raw_json_path,
    }
    history.append(bundle)
    session_adapter["results_history"] = history

    # Chatter agent
    current_agent = "chatter"
    send_event("agent_started", {"agent": "chatter", "mode": "search"})
    answer = chatter_agent_search_answer(
        user_query,
        plan.model_dump(),
        api_plan.model_dump(),
        api_result_slim,
        api_result_full,
        debug_payload["error_context"],
    )
    send_event("agent_complete", {"agent": "chatter", "summary": None})

    session_adapter["last_debug"] = debug_payload
    _emit_complete(send_event, answer, debug_payload, bundle_id)


# ------------------------------------------------------------------
# DB-backed event callback for async queries
# ------------------------------------------------------------------

def make_db_event_callback(task_id: str, session_id: str) -> SendEvent:
    """Create a send_event callback that persists progress to QueryTask.

    Used by the async query endpoint (POST /assistant/query/async/).
    Each call appends to QueryTask.progress and updates status on
    terminal events (query_complete / query_error).
    """

    def send_event(event_type: str, data: dict[str, Any]) -> None:
        from nextseek_api.assistant.models_db import QueryTask

        if event_type in ("query_complete", "query_error"):
            data.setdefault("session_id", session_id)

        try:
            task = QueryTask.objects.get(task_id=task_id)
        except QueryTask.DoesNotExist:
            logger.error("QueryTask %s not found during event callback", task_id)
            return

        progress = task.progress or []
        progress.append({"event": event_type, "data": data})
        task.progress = progress

        update_fields = ["progress", "updated_at"]
        if event_type == "query_complete":
            task.status = "completed"
            task.result = data
            update_fields.extend(["status", "result"])
        elif event_type == "query_error":
            task.status = "error"
            task.result = data
            update_fields.extend(["status", "result"])

        task.save(update_fields=update_fields)

    return send_event

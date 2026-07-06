from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from ...session import SessionState
from ...config import ChatConfig
from ...artifacts import ArtifactStore
from ...helpers import (
    _retry_advanced_search_if_empty,
    build_memory_data_profile,
    execute_memory_code,
    fix_sample_endpoint,
    generate_report_outputs,
    normalize_report_type,
    run_reporter_summary,
    tool_neo4j_query,
    tool_nextseek_api_request,
)
from ...schemas import (
    APIRequestPlan,
    ContextEngineerOutput,
    EntityAgentOutput,
    MultiParserPlan,
    ParserFilters,
    ParserPlan,
    PlanStep,
)
from ..parser import _infer_report_type_from_query, _resolve_step_inputs
from ..api import api_agent_build_request
from ..graph import graph_agent
from ..memory import memory_agent_answer, memory_coder_agent
from ..reporter import reporter_agent, report_writer_agent
from ..system import system_agent


def _plan_tool_graph_query(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    query: str,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    enriched_context: "dict[int, ContextEngineerOutput]",
) -> dict:
    """Execute a planner graph step, including one retry with error-informed Cypher regeneration."""
    query = step.execution.tool_query or query
    candidate_metadata = dict(step.execution.metadata or {})
    entity_payload = entity_result if isinstance(entity_result, dict) else entity_result.model_dump()
    graph_parser_plan = ParserPlan(
        mode="graph_query",
        target_endpoint=step.execution.target_endpoint,
        intent_summary=query,
        filters=ParserFilters.model_validate(step.execution.filters or {}),
        resolved=EntityAgentOutput.model_validate(entity_payload or {}),
        notes=step.notes or "",
        metadata=candidate_metadata,
    )
    graph_plan = graph_agent(config, query, entity_result, parser_plan=graph_parser_plan)
    print(f"[DEBUG][PLAN_TOOL] cypher={graph_plan.cypher!r}")
    if not graph_plan.cypher:
        return {"ok": False, "tool": "graph_query", "output": {}, "error": "graph_agent produced no cypher"}
    result = tool_neo4j_query(config, graph_plan.cypher, graph_plan.parameters)
    if not result.get("ok"):
        retry_ctx = (
            f"Your previous Cypher failed:\n{result.get('error', '')}\n\n"
            "Check schema, property types, relationship directions, and retry."
        )
        graph_plan = graph_agent(config, query, entity_result, parser_plan=graph_parser_plan, retry_context=retry_ctx)
        if graph_plan.cypher:
            result = tool_neo4j_query(config, graph_plan.cypher, graph_plan.parameters)
    return {
        "ok": result.get("ok", False),
        "tool": "graph_query",
        "output": {
            "data": result.get("data", []),
            "count": result.get("count", 0),
            "graph_plan": graph_plan.model_dump(),
        },
        "error": result.get("error") if not result.get("ok") else None,
    }


def _plan_tool_new_search(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    query: str,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    enriched_context: "dict[int, ContextEngineerOutput]",
    parser_plan: "MultiParserPlan | None" = None,
) -> dict:
    """Execute a planner search step by synthesizing a parser plan and running the API agent/tool."""
    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result
    query = step.execution.tool_query or query
    endpoint = step.execution.target_endpoint or step.target_endpoint or "/nextseek_api/samples/advanced_search/"
    endpoint = fix_sample_endpoint({"target_endpoint": endpoint}).get("target_endpoint", endpoint)
    input_values, _missing = _resolve_step_inputs(step, enriched_context)
    base_filters = dict(step.execution.filters or {})
    previous_api_plan = None
    previous_user_query = None
    if step.tool == "refine_last_search":
        history = session.get("results_history", []) or []
        if history:
            last_bundle = history[-1]
            previous_api_plan = last_bundle.get("api_plan")
            previous_user_query = last_bundle.get("user_query")
            previous_filters: dict[str, Any] = {}
            previous_endpoint = None
            search_context = last_bundle.get("search_context") or {}
            if isinstance(search_context, dict):
                previous_endpoint = search_context.get("endpoint")
                request_body = search_context.get("request_body") or {}
                if isinstance(request_body, dict):
                    sampletype = request_body.get("sampletype") or request_body.get("sample_type") or request_body.get("sampletype_code")
                    if sampletype:
                        previous_filters["sampletype_code"] = sampletype

            prev_parser_plan = last_bundle.get("parser_plan") or {}
            if isinstance(prev_parser_plan, dict):
                previous_filters = dict(prev_parser_plan.get("filters") or {})
                previous_endpoint = previous_endpoint or prev_parser_plan.get("target_endpoint")

            prev_plan = last_bundle.get("plan") or {}
            if isinstance(prev_plan, dict):
                for prev_step in prev_plan.get("steps") or []:
                    if not isinstance(prev_step, dict):
                        continue
                    prev_exec = prev_step.get("execution") or {}
                    if not isinstance(prev_exec, dict):
                        continue
                    prev_mode = prev_exec.get("mode") or prev_step.get("tool")
                    if prev_mode not in {"new_search", "refine_last_search"}:
                        continue
                    if not previous_filters:
                        previous_filters = dict(prev_exec.get("filters") or {})
                    previous_endpoint = previous_endpoint or prev_exec.get("target_endpoint") or prev_step.get("target_endpoint")
                    break

            step_results = last_bundle.get("step_results") or {}
            if isinstance(step_results, dict):
                for sr in step_results.values():
                    if not isinstance(sr, dict):
                        continue
                    output = sr.get("output") or {}
                    api_plan = output.get("api_plan") if isinstance(output, dict) else None
                    if isinstance(api_plan, dict):
                        previous_api_plan = previous_api_plan or api_plan
                        previous_endpoint = previous_endpoint or api_plan.get("endpoint")
                        body = api_plan.get("requestBody") or {}
                        if isinstance(body, dict) and not previous_filters.get("sampletype_code"):
                            sampletype = body.get("sampletype") or body.get("sample_type") or body.get("sampletype_code")
                            if sampletype:
                                previous_filters["sampletype_code"] = sampletype
                        break

            if previous_endpoint and (not step.execution.target_endpoint and not step.target_endpoint):
                endpoint = previous_endpoint
            for key in ("sampletype_code", "assay_codes", "uids"):
                if base_filters.get(key) in (None, "", [], {}) and previous_filters.get(key) not in (None, "", [], {}):
                    base_filters[key] = previous_filters[key]

    for key, value in input_values.items():
        base_filters[key] = value
    candidate = None
    if parser_plan and step.execution.parser_candidate_id:
        candidate = next((c for c in parser_plan.candidates if c.candidate_id == step.execution.parser_candidate_id), None)
    if candidate and candidate.filters:
        candidate_filters = candidate.filters.model_dump()
        for key, value in candidate_filters.items():
            if key not in base_filters or base_filters.get(key) in (None, "", [], {}):
                base_filters[key] = value
    if step.tool == "refine_last_search":
        project_match = re.search(r"project id\s*=*\s*(\d+)", query, re.IGNORECASE)
        if project_match:
            keywords = base_filters.get("keywords") or []
            if not isinstance(keywords, list):
                keywords = []
            project_keyword = f"project id {project_match.group(1)}"
            if project_keyword not in keywords:
                keywords.append(project_keyword)
            base_filters["keywords"] = keywords

    resolved_for_plan = dict(entity_dict)
    if step.combine_mode == "intersect":
        resolved_for_plan["keywords"] = []
        sampletype_code = base_filters.get("sampletype_code")
        if sampletype_code:
            sampletypes = resolved_for_plan.get("sampletypes") or []
            resolved_for_plan["sampletypes"] = [
                item for item in sampletypes
                if isinstance(item, dict) and item.get("code") == sampletype_code
            ] or [{"code": sampletype_code, "name": sampletype_code}]

    synthetic_plan: dict = {
        "mode": "refine_last_search" if step.tool == "refine_last_search" else "new_search",
        "target_endpoint": endpoint,
        "intent_summary": query,
        "filters": {
            "sampletype_code": base_filters.get("sampletype_code"),
            "assay_codes": base_filters.get("assay_codes") or [],
            "keywords": base_filters.get("keywords") or [],
            "uids": base_filters.get("uids") or [],
        },
        "resolved": resolved_for_plan,
        "endpoint_candidates": [endpoint],
        "notes": f"planner step {step.step_id}: {step.notes} | input_fields: {list(input_values.keys())}",
    }
    if previous_api_plan:
        synthetic_plan["previous_api_plan"] = previous_api_plan
    if previous_user_query:
        synthetic_plan["previous_user_query"] = previous_user_query
    print(f"[DEBUG][PLAN_TOOL][new_search] synthetic_plan={json.dumps(synthetic_plan, indent=2)}")
    api_plan = api_agent_build_request(config, synthetic_plan)
    api_result = tool_nextseek_api_request(
        config=config,
        endpoint=api_plan.endpoint,
        method=api_plan.method,
        requestBody=api_plan.requestBody or {},
        queryParameters=api_plan.queryParameters or {},
    )
    api_plan_dict, api_result = _retry_advanced_search_if_empty(
        config, synthetic_plan, api_plan.model_dump(), api_result
    )
    api_plan = APIRequestPlan.model_validate(api_plan_dict)
    api_data = api_result.get("data") or {}
    if isinstance(api_data, dict):
        # Standard search response: {"total": N, "rows": [...]}
        # Admin retrieve response:  {"total_samples": N, "data": [...]}
        # Sample-tree response:     {"total_nodes": N, "nodes": [...]}
        # Assays response:          {"data": [...]}
        rows = (api_data.get("rows")
                or api_data.get("nodes")
                or api_data.get("data")
                or [])
        if not isinstance(rows, list):
            rows = []
        total = (api_data.get("total")
                 or api_data.get("total_samples")
                 or api_data.get("total_nodes")
                 or len(rows))
    else:
        rows = []
        total = 0
    return {
        "ok": api_result.get("ok", False),
        "tool": step.tool,
        "output": {"data": rows, "count": total, "endpoint": api_plan.endpoint, "api_plan": api_plan.model_dump()},
        "error": api_result.get("error") if not api_result.get("ok", True) else None,
    }


def _plan_tool_reporter(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    query: str,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    enriched_context: "dict[int, ContextEngineerOutput]",
    parser_plan: "MultiParserPlan | None" = None,
) -> dict:
    """Execute a planner reporter step — runs the full summary reporter pipeline."""
    query = step.execution.tool_query or query
    input_values, _missing = _resolve_step_inputs(step, enriched_context)
    reporter_filters = dict(step.execution.filters or {})
    candidate = None
    if parser_plan and step.execution.parser_candidate_id:
        candidate = next((c for c in parser_plan.candidates if c.candidate_id == step.execution.parser_candidate_id), None)
    if candidate:
        candidate_filters = candidate.filters.model_dump() if hasattr(candidate.filters, "model_dump") else dict(candidate.filters or {})
        for key, value in candidate_filters.items():
            if reporter_filters.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
                reporter_filters[key] = value
    reporter_uids = input_values.get("uids") or reporter_filters.get("uids") or []
    metadata = dict((candidate.metadata if candidate else {}) or {})
    metadata.update(step.execution.metadata or {})
    parser_context = {
        "mode": "reporter",
        "report_mode": step.execution.report_mode or (candidate.report_mode if candidate else None) or "summary",
        "report_type": step.execution.report_type or (candidate.report_type if candidate else None),
        "filters": {**reporter_filters, "uids": reporter_uids},
        "resolved": entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result,
        "metadata": metadata,
    }
    rplan = reporter_agent(config, query, parser_context)
    if reporter_uids and not rplan.uids:
        rplan = rplan.model_copy(update={"uids": reporter_uids})
    if step.execution.report_mode and not rplan.reporter_mode:
        rplan = rplan.model_copy(update={"reporter_mode": step.execution.report_mode})
    if step.execution.report_type and not rplan.report_type:
        rplan = rplan.model_copy(update={"report_type": step.execution.report_type})
    reporter_updates = {}
    for key in ("project", "years", "month_range", "day_range", "summary_mode", "reporter_context"):
        if metadata.get(key) not in (None, "", [], {}):
            reporter_updates[key] = metadata[key]
    if reporter_updates:
        rplan = rplan.model_copy(update=reporter_updates)
    print(f"[DEBUG][PLAN_TOOL][REPORTER] reporter_mode={rplan.reporter_mode!r} summary_mode={rplan.summary_mode!r} project={rplan.project!r}")
    reporter_result, saved_files, reporter_summary = run_reporter_summary(config, rplan, log_dir)
    ok = bool(reporter_result.get("ok"))
    rows = reporter_result.get("rows_returned", 0) or 0
    print(f"[DEBUG][PLAN_TOOL][REPORTER] ok={ok}, rows={rows}, files={list(saved_files.keys())}")
    return {
        "ok": ok,
        "tool": "reporter",
        "output": {
            "reporter_plan": rplan.model_dump(),
            "reporter_result": reporter_result,
            "reporter_summary": reporter_summary,
            "saved_files": saved_files,
            "count": rows,
        },
        "error": reporter_result.get("error") if not ok else None,
    }


def _plan_tool_refine_last_search(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    query: str,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    enriched_context: "dict[int, ContextEngineerOutput]",
    parser_plan: "MultiParserPlan | None" = None,
) -> dict:
    """Execute a planner refine step by reusing the search tool with refine semantics."""
    return _plan_tool_new_search(
        config,
        session,
        step,
        query,
        entity_result,
        log_dir,
        enriched_context,
        parser_plan=parser_plan,
    )


def _plan_tool_report_generation(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    query: str,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    enriched_context: "dict[int, ContextEngineerOutput]",
    parser_plan: "MultiParserPlan | None" = None,
) -> dict:
    """Execute a planner report-generation step and return saved artifacts plus reply text."""
    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result
    query = step.execution.tool_query or query
    input_values, _missing = _resolve_step_inputs(step, enriched_context)
    step_filters = dict(step.execution.filters or {})
    candidate = None
    if parser_plan and step.execution.parser_candidate_id:
        candidate = next((c for c in parser_plan.candidates if c.candidate_id == step.execution.parser_candidate_id), None)
    if candidate:
        candidate_filters = candidate.filters.model_dump() if hasattr(candidate.filters, "model_dump") else dict(candidate.filters or {})
        for key, value in candidate_filters.items():
            if step_filters.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
                step_filters[key] = value
    kw_uids = [kw for kw in entity_dict.get("keywords", []) if "-" in kw and len(kw) > 8]
    explicit_uids = input_values.get("uids") or step_filters.get("uids") or []
    metadata = dict((candidate.metadata if candidate else {}) or {})
    metadata.update(step.execution.metadata or {})
    parser_context = {
        "mode": "reporter",
        "report_mode": step.execution.report_mode or (candidate.report_mode if candidate else None) or "report_generation",
        "report_type": step.execution.report_type or (candidate.report_type if candidate else None),
        "filters": {**step_filters, "uids": explicit_uids},
        "resolved": entity_dict,
        "metadata": metadata,
    }
    rplan = reporter_agent(config, query, parser_context)
    uids = list(dict.fromkeys(list(explicit_uids) + kw_uids + (rplan.uids or [])))
    if uids and not rplan.uids:
        rplan = rplan.model_copy(update={"uids": uids})
    if not rplan.reporter_mode:
        rplan = rplan.model_copy(update={"reporter_mode": step.execution.report_mode or "report_generation"})
    if not rplan.report_type:
        rplan = rplan.model_copy(update={"report_type": normalize_report_type(step.execution.report_type) or _infer_report_type_from_query(query) or "GEO"})
    else:
        rplan = rplan.model_copy(update={"report_type": normalize_report_type(rplan.report_type)})
    synthetic_parser_plan = {
        "mode": "reporter",
        "report_mode": "report_generation",
        "report_type": rplan.report_type,
        "intent_summary": query,
        "filters": {"uids": uids},
        "resolved": entity_dict,
    }
    reporter_updates = {}
    for key in ("project", "years", "month_range", "day_range", "reporter_context"):
        if metadata.get(key) not in (None, "", [], {}):
            reporter_updates[key] = metadata[key]
    if reporter_updates:
        rplan = rplan.model_copy(update=reporter_updates)
    report_type_value = (rplan.report_type or "").upper()
    if report_type_value.startswith("NFCORE"):
        from chat_nextseek.pipeline import wizard as nfcore_wizard

        wizard_result = nfcore_wizard.start(
            session,
            config,
            user_query=query,
            parser_plan=synthetic_parser_plan,
            reporter_plan=rplan,
        )
        return {
            "ok": True,
            "tool": "report_generation",
            "output": {
                "reply": wizard_result.get("reply") or "",
                "saved_files": {},
                "count": 1,
                "wizard_started": True,
            },
            "error": None,
        }

    try:
        _, _, saved_files, reply = generate_report_outputs(
            config=config,
            user_query=query,
            parser_plan=synthetic_parser_plan,
            reporter_plan=rplan,
            uids=uids,
            log_dir=log_dir or "outputs",
            report_writer_fn=report_writer_agent,
            per_sample_reports=False,
        )
        return {
            "ok": True,
            "tool": "report_generation",
            "output": {"reply": reply, "saved_files": saved_files, "count": len(saved_files)},
            "error": None,
        }
    except Exception as e:
        return {"ok": False, "tool": "report_generation", "output": {}, "error": repr(e)}


def _select_memory_bundle_for_step(
    history: list[dict],
    step: PlanStep,
    query: str,
) -> tuple[dict | None, str | None]:
    """Select the intended prior bundle for a planner memory/follow-up step."""
    if not history:
        return None, "No prior results in session"
    metadata = step.execution.metadata or {}
    target_id = (
        metadata.get("target_result_id")
        or metadata.get("refers_to_result_id")
        or metadata.get("result_id")
        or metadata.get("bundle_id")
    )
    if target_id is not None:
        try:
            target_id_int = int(target_id)
        except (TypeError, ValueError):
            target_id_int = None
        if target_id_int is not None:
            bundle = next((b for b in history if b.get("id") == target_id_int), None)
            if bundle:
                return bundle, None
            return None, f"No prior result bundle with id={target_id_int}"

    target_query = (
        metadata.get("target_query")
        or metadata.get("prior_query")
        or metadata.get("original_query")
        or metadata.get("refers_to_query")
    )
    if isinstance(target_query, str) and target_query.strip():
        target_query_norm = target_query.strip().lower()
        for bundle in reversed(history):
            bundle_query = str(bundle.get("user_query") or "").strip().lower()
            if bundle_query and (bundle_query == target_query_norm or target_query_norm in bundle_query or bundle_query in target_query_norm):
                return bundle, None

    lowered_query = query.lower()
    explicit_latest = any(token in lowered_query for token in ("last result", "latest result", "previous result", "those results", "that search"))
    if explicit_latest or len(history) == 1:
        return history[-1], None
    return history[-1], None


def _plan_tool_memory_lookup(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    query: str,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    enriched_context: "dict[int, ContextEngineerOutput]",
) -> dict:
    """Execute a planner memory-lookup step against the intended stored session bundle."""
    query = step.execution.tool_query or query
    history = session.get("results_history", [])
    bundle, error = _select_memory_bundle_for_step(history, step, query)
    if not bundle:
        return {"ok": False, "tool": "memory_lookup", "output": {}, "error": error or "No prior results in session"}
    reply = memory_agent_answer(config, query, bundle, log_dir=log_dir)
    return {
        "ok": True,
        "tool": "memory_lookup",
        "output": {"reply": reply, "count": 1, "bundle_id": bundle.get("id")},
        "error": None,
    }


def _plan_tool_ask_about_last_results(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    query: str,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    enriched_context: "dict[int, ContextEngineerOutput]",
) -> dict:
    """Planner-visible alias for session-memory questions about prior results."""
    out = _plan_tool_memory_lookup(config, session, step, query, entity_result, log_dir, enriched_context)
    out["tool"] = "ask_about_last_results"
    return out


def _plan_tool_coding_filter(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    query: str,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    enriched_context: "dict[int, ContextEngineerOutput]",
    step_results: dict[int, dict],
) -> dict:
    """Apply a planner-only local code filter to rows from a prior step."""
    post_filter = (step.execution.metadata or {}).get("post_filter") or {}
    source_step_id = step.depends_on
    if source_step_id is None and step.input_mapping:
        source_step_id = next(iter(step.input_mapping.values())).from_step
    if source_step_id is None:
        source_step_id = step.step_id - 1

    source_result = step_results.get(source_step_id)
    if not source_result:
        return {"ok": False, "tool": "coding_filter", "output": {}, "error": f"No source step result found for step {source_step_id}"}

    source_output = source_result.get("output") or {}
    rows = source_output.get("data") or []
    if not isinstance(rows, list):
        rows = []

    data_for_code = {
        "data": {
            "rows": rows,
            "total": len(rows),
        },
        "source_output": source_output,
        "post_filter": post_filter,
    }
    profile = build_memory_data_profile(data_for_code, sample_limit=5)
    filter_query = (
        "Filter the retrieved rows using this post_filter predicate. "
        "Return a dict with key 'filtered_rows' containing the matching row dicts, "
        "and key 'count' containing the number of matches. "
        "If the field is absent or cannot be parsed on a row, exclude that row. "
        f"post_filter={json.dumps(post_filter, default=str)}"
    )
    coder_output = memory_coder_agent(
        config,
        original_query=query,
        user_query=filter_query,
        data_profile=profile,
        log_dir=log_dir,
    )
    computed_result = execute_memory_code(coder_output.extraction_code, data_for_code)
    filtered_rows = (
        computed_result.get("filtered_rows")
        or computed_result.get("rows")
        or computed_result.get("matches")
        or computed_result.get("data")
        or []
    )
    if not isinstance(filtered_rows, list):
        filtered_rows = []

    try:
        store = ArtifactStore(log_dir or config.LOG_DIR)
        store.write_json(
            key="coding_filter_result",
            label="Planner coding filter result",
            filename=f"coding_filter_step_{step.step_id}.json",
            payload={
                "step_id": step.step_id,
                "source_step_id": source_step_id,
                "post_filter": post_filter,
                "memory_coder": coder_output.model_dump(),
                "computed_result": computed_result,
                "count": len(filtered_rows),
            },
            kind="memory",
            bundle_id=step.step_id,
        )
    except Exception as artifact_err:
        print(f"[DEBUG][CODING_FILTER] Failed to save artifact: {artifact_err!r}")

    return {
        "ok": True,
        "tool": "coding_filter",
        "output": {
            "data": filtered_rows,
            "count": len(filtered_rows),
            "computed_result": computed_result,
            "post_filter": post_filter,
            "source_step_id": source_step_id,
        },
        "error": None,
    }


def _plan_tool_system_question(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    query: str,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    enriched_context: "dict[int, ContextEngineerOutput]",
) -> dict:
    """Execute a planner system-question step using a lightweight synthetic parser plan."""
    query = step.execution.tool_query or query
    stub_plan = ParserPlan(mode="system_question", intent_summary=query)
    sys_out = system_agent(config, query, entity_result, stub_plan)
    return {"ok": True, "tool": "system_question", "output": {"reply": sys_out.narrative, "count": 1}, "error": None}


def _plan_tool_unsupported(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    query: str,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    enriched_context: "dict[int, ContextEngineerOutput]",
) -> dict:
    """Planner-visible terminal path for out-of-scope or unsupported requests."""
    query = step.execution.tool_query or query
    reply = (
        "I can't turn that request into a valid NExtSEEK operation yet.\n\n"
        f"Reason from planner: {step.notes or query}"
    )
    return {"ok": True, "tool": "unsupported", "output": {"reply": reply, "count": 1}, "error": None}


_PLAN_TOOL_DISPATCH: dict[str, Any] = {
    "graph_query":       _plan_tool_graph_query,
    "new_search":        _plan_tool_new_search,
    "refine_last_search": _plan_tool_refine_last_search,
    "ask_about_last_results": _plan_tool_ask_about_last_results,
    "reporter":          _plan_tool_reporter,
    "report_generation": _plan_tool_report_generation,
    "memory_lookup":     _plan_tool_memory_lookup,
    "coding_filter":     _plan_tool_coding_filter,
    "system_question":   _plan_tool_system_question,
    "unsupported":       _plan_tool_unsupported,
}

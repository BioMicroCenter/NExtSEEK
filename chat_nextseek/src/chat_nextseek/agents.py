from __future__ import annotations

import json
import re
import calendar
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from .session import SessionState

from .config import ChatConfig
from .artifacts import ArtifactStore

from .llm_clients import LLMAPIConnectionError, LLMRateLimitError, LLMTimeoutError
from .helpers import (
    _retry_advanced_search_if_empty,
    build_memory_data_profile,
    build_recent_results_summary,
    collect_bundle_files,
    execute_memory_code,
    fix_sample_endpoint,
    load_json_for_memory,
    load_file_for_memory,
    log_prompt,
    log_usage,
    run_reporter_summary,
    safe_parse_json,
    strip_html,
    strip_html_recursive,
    tool_nextseek_api_request,
    tool_neo4j_query,
    generate_report_outputs,
    normalize_report_type,
)
from .schemas.schema_helper import StructuredOutputError, call_llm_structured
from .schemas import (
    APIRequestPlan,
    ContextEngineerOutput,
    EntityAgentOutput,
    GraphAgentPlan,
    MemoryCoderOutput,
    MultiParserPlan,
    PipelineCohort,
    PlannerDecisionOutput,
    PlanEvaluatorOutput,
    ParserCandidate,
    ParserFilters,
    ParserPlan,
    PlannerOutput,
    PlanStep,
    ReporterPlan,
    ReportWriterOutput,
    ReportWriterPlan,
    SeqeraLaunchPlan,
    StepExecutionPayload,
    StepInputRef,
    StepOutcome,
    StepOutputMapping,
    SystemAgentOutput,
    WizardAgentOutput,
)
from .seqera.catalog import get_pipeline_entry

_PLAN_TOOL_AGENT_NAMES: dict[str, str] = {
    "graph_query": "graph",
    "new_search": "api",
    "refine_last_search": "api",
    "ask_about_last_results": "memory",
    "reporter": "reporter",
    "report_generation": "report_writer",
    "memory_lookup": "memory",
    "coding_filter": "memory_coder",
    "system_question": "system",
    "unsupported": "unsupported",
}

_PLAN_TOOL_SEARCH_SOURCES: dict[str, str] = {
    "graph_query": "neo4j",
    "new_search": "api",
    "refine_last_search": "api",
    "reporter": "reporter",
}

_TERMINAL_REPLY_TOOLS = {
    "system_question",
    "ask_about_last_results",
    "memory_lookup",
    "unsupported",
    "report_generation",
}





def _extract_uids_from_output(tool_output: dict) -> set[str]:
    """
    Extract a set of UID strings from a standardized _run_plan_tool output dict.
    Handles both graph results (uid field = plain string) and API results
    (uuid/title = plain UID; uid field = HTML link, skip it).
    """
    output = tool_output.get("output") or {}
    rows = output.get("data") or []
    if not isinstance(rows, list):
        return set()
    uids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Prefer uuid/title (plain strings in API results) over uid (may be HTML)
        for key in ("uuid", "uid", "UID", "s.UID", "s.uid", "title"):
            val = row.get(key)
            if val and isinstance(val, str) and "<" not in val:
                uids.add(val)
                break
    return uids


def _extract_fields_from_output(step: PlanStep, tool_output: dict) -> dict[str, Any]:
    extracted: dict[str, Any] = {}
    output = tool_output.get("output") or {}
    rows = output.get("data") or []
    for field_name, mapping in (step.output_mapping or {}).items():
        if mapping.source == "output":
            value = output
            for key in mapping.keys:
                if isinstance(value, dict):
                    value = value.get(key)
                else:
                    value = None
                    break
            if value not in (None, "", [], {}):
                extracted[field_name] = value
            continue
        if mapping.source == "rows" and isinstance(rows, list):
            values: list[Any] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key in mapping.keys:
                    val = row.get(key)
                    if val and (not isinstance(val, str) or "<" not in val):
                        values.append(val)
                        break
            if mapping.value_type.startswith("list"):
                if values:
                    extracted[field_name] = list(dict.fromkeys(values))
            elif values:
                extracted[field_name] = values[0]
    return extracted


def _inject_context(step: PlanStep, enriched_context: dict[int, ContextEngineerOutput]) -> str:
    """
    Replace {context_from_step_N} placeholders in step.context_prompt with
    the extracted JSON from the corresponding prior step's ContextEngineerOutput.
    """
    prompt = step.context_prompt
    for step_id, ce_out in enriched_context.items():
        placeholder = f"{{context_from_step_{step_id}}}"
        if placeholder in prompt:
            replacement = json.dumps(ce_out.enriched_context, separators=(",", ":"))
            prompt = prompt.replace(placeholder, replacement)
    return prompt


def _step_signature(step: PlanStep) -> str:
    execution = step.execution
    payload = {
        "tool": step.tool,
        "combine_mode": step.combine_mode,
        "candidate_id": execution.parser_candidate_id,
        "endpoint": execution.target_endpoint,
        "filters": execution.filters,
        "query": execution.tool_query,
        "input_mapping": step.input_mapping,
    }
    return json.dumps(payload, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Per-tool executor functions — each calls the relevant agent(s) and returns
# a standardised {ok, tool, output, error} dict.
# ---------------------------------------------------------------------------



def _run_plan_tool(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    query_for_tool: str,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    enriched_context: "dict[int, ContextEngineerOutput] | None" = None,
    parser_plan: "MultiParserPlan | None" = None,
    step_results: dict[int, dict] | None = None,
) -> dict:
    """Thin dispatcher — logs inputs, looks up the tool function, calls it."""
    entity_dict_log = entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result
    print(f"\n[DEBUG][PLAN_TOOL] Dispatching step {step.step_id}: tool={step.tool}")
    print(f"[DEBUG][PLAN_TOOL] query_for_tool={query_for_tool!r}")
    print(f"[DEBUG][PLAN_TOOL] full_step_input={json.dumps({'step': step.model_dump(), 'query_for_tool': query_for_tool, 'entity_result': entity_dict_log, 'enriched_context_keys': list((enriched_context or {}).keys())}, indent=2)}")

    fn = _PLAN_TOOL_DISPATCH.get(step.tool)
    if fn is None:
        return {"ok": False, "tool": step.tool, "output": {}, "error": f"Unknown tool: {step.tool}"}
    try:
        if step.tool in {"new_search", "refine_last_search"}:
            return fn(config, session, step, query_for_tool, entity_result, log_dir, enriched_context or {}, parser_plan=parser_plan)
        if step.tool in {"reporter", "report_generation"}:
            return fn(config, session, step, query_for_tool, entity_result, log_dir, enriched_context or {}, parser_plan=parser_plan)
        if step.tool == "coding_filter":
            return fn(config, session, step, query_for_tool, entity_result, log_dir, enriched_context or {}, step_results or {})
        return fn(config, session, step, query_for_tool, entity_result, log_dir, enriched_context or {})
    except Exception as e:
        print(f"[DEBUG][PLAN_TOOL] tool={step.tool} raised: {e!r}")
        return {"ok": False, "tool": step.tool, "output": {}, "error": repr(e)}



def plan_evaluator_agent(
    config: ChatConfig,
    user_query: str,
    entity_result: EntityAgentOutput | dict,
    parser_plan: MultiParserPlan | dict,
    planner_output: PlannerOutput | dict,
    step_results: dict[int, dict] | dict[str, Any],
    provisional_reply: str | None = None,
    *,
    stop_reason: str | None = None,
    log_dir: str | None = None,
) -> PlanEvaluatorOutput:
    """Advisory evaluator for the completed planner run."""
    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else (entity_result or {})
    parser_dict = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else (parser_plan or {})
    planner_dict = planner_output.model_dump() if hasattr(planner_output, "model_dump") else (planner_output or {})
    parser_candidates = (parser_dict or {}).get("candidates") or []

    condensed_results: dict[str, Any] = {}
    for step_id, result in (step_results or {}).items():
        if step_id == "intersection":
            output = result.get("output") or {}
            condensed_results[str(step_id)] = {
                "tool": "intersection",
                "ok": True,
                "count": output.get("count"),
                "preview": (output.get("data") or [])[:5] if isinstance(output.get("data"), list) else output.get("data"),
            }
            continue
        if not isinstance(result, dict):
            continue
        output = result.get("output") or {}
        preview: Any
        if result.get("tool") in {"reporter", "report_generation"}:
            preview = {
                "reporter_summary": output.get("reporter_summary"),
                "saved_files": output.get("saved_files"),
                "reporter_plan": output.get("reporter_plan"),
            }
        else:
            data = output.get("data")
            preview = data[:5] if isinstance(data, list) else data
        condensed_results[str(step_id)] = {
            "tool": result.get("tool"),
            "ok": result.get("ok"),
            "count": output.get("count"),
            "error": result.get("error"),
            "endpoint": output.get("endpoint"),
            "api_plan": output.get("api_plan"),
            "graph_plan": output.get("graph_plan"),
            "preview": preview,
        }

    # Deterministic pre-checks for obvious strategy/execution failures before asking the LLM.
    candidate_by_id = {
        candidate.get("candidate_id"): candidate
        for candidate in parser_candidates
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }

    # Fix 3: Detect hard step failures — any step with ok=False should always be failure.
    # Surface error string verbatim so the replan has actionable feedback.
    for _step in (planner_dict.get("steps") or []):
        _sid = str((_step or {}).get("step_id", ""))
        _sr = condensed_results.get(_sid) or {}
        if _sr.get("ok") is False and _sr.get("error"):
            return PlanEvaluatorOutput(
                answered_query=False,
                execution_consistent=False,
                overall_status="failure",
                zero_results_assessment="not_applicable",
                failure_stage="executor",
                reason=f"Step {_sid} failed with an error.",
                what_went_wrong=str(_sr["error"])[:400],
                user_safe_summary="A step in the plan failed to execute. See error details.",
                confidence=0.99,
            )

    covered_scopes: set[str] = set()
    for step in (planner_dict.get("steps") or []):
        if not isinstance(step, dict):
            continue
        execution = step.get("execution") or {}
        candidate = candidate_by_id.get(execution.get("parser_candidate_id"))
        if candidate and candidate.get("criterion_scope"):
            covered_scopes.add(str(candidate.get("criterion_scope")))
        step_result = condensed_results.get(str(step.get("step_id"))) or {}
        if not candidate or step_result.get("tool") != "graph_query":
            continue

        metadata = candidate.get("metadata") or {}
        keyword_filter = metadata.get("keyword_filter")
        graph_plan = step_result.get("graph_plan") or {}
        cypher = (graph_plan.get("cypher") or "").lower()
        parameters = graph_plan.get("parameters") or {}

        if keyword_filter:
            keyword_lower = str(keyword_filter).strip().lower()
            cypher_has_keyword = keyword_lower and keyword_lower in cypher
            params_have_keyword = any(keyword_lower in str(value).lower() for value in parameters.values())
            if not cypher_has_keyword and not params_have_keyword:
                # In a valid intersect plan the keyword is intentionally absent from the graph Cypher —
                # it is handled by the REST attribute step instead. Only fire failure if no other
                # non-graph step in the plan covers this keyword in its filters or tool_query.
                all_plan_steps = planner_dict.get("steps") or []
                keyword_covered_elsewhere = any(
                    keyword_lower in [str(k).lower() for k in ((s.get("execution") or {}).get("filters") or {}).get("keywords", [])]
                    or keyword_lower in ((s.get("execution") or {}).get("tool_query") or "").lower()
                    for s in all_plan_steps
                    if s.get("step_id") != step.get("step_id")
                    and (s.get("execution") or {}).get("mode") not in ("graph_query", None, "")
                )
                if not keyword_covered_elsewhere:
                    return PlanEvaluatorOutput(
                        answered_query=False,
                        execution_consistent=False,
                        overall_status="failure",
                        zero_results_assessment="not_applicable",
                        failure_stage="executor",
                        reason="The executed plan did not enforce all requested constraints.",
                        what_went_wrong=(
                            "The graph step did not filter by the required keyword and no other step covered it. "
                            "The plan should combine structural scope and attribute filtering."
                        ),
                        user_safe_summary=(
                            "This did not fully answer the request: the executed plan covered only part of the requested constraints."
                        ),
                        confidence=0.99,
                    )

    available_scopes = {
        str(candidate.get("criterion_scope"))
        for candidate in parser_candidates
        if isinstance(candidate, dict)
        and candidate.get("criterion_scope") in {"structural", "attribute"}
        and candidate.get("can_intersect") is True
    }
    if available_scopes == {"structural", "attribute"} and covered_scopes in ({"structural"}, {"attribute"}):
        return PlanEvaluatorOutput(
            answered_query=False,
            execution_consistent=False,
            overall_status="failure",
            zero_results_assessment="not_applicable",
            failure_stage="planner",
            reason="The planner used only one part of a multi-constraint solution.",
            what_went_wrong=(
                "The parser provided complementary candidates for different parts of the request, "
                "but the planner did not combine them."
            ),
            user_safe_summary=(
                "This did not fully answer the request: the planner should combine multiple candidates rather than using only one."
            ),
            confidence=0.95,
        )

    # Fix 2: Detect intersect-collapse — every step returned results but the intersection is empty.
    # This reliably indicates a UID format mismatch or over-composition.
    intersection_entry = condensed_results.get("intersection") or {}
    if intersection_entry.get("count") == 0:
        intersect_step_counts = [
            v.get("count") or 0
            for k, v in condensed_results.items()
            if k != "intersection" and isinstance(v, dict) and v.get("count") is not None
        ]
        if intersect_step_counts and all(c > 0 for c in intersect_step_counts):
            return PlanEvaluatorOutput(
                answered_query=False,
                execution_consistent=False,
                overall_status="failure",
                zero_results_assessment="suspicious",
                failure_stage="planner",
                reason="Intersect plan collapsed to zero despite each individual step returning results.",
                what_went_wrong=(
                    "Each step returned results independently but their intersection is empty. "
                    "This indicates a UID format mismatch between data sources, or that the query "
                    "does not require multi-source intersection. Re-run using only the primary "
                    "candidate (candidate 0) as a single step."
                ),
                user_safe_summary="",
                confidence=0.95,
            )

    messages = [
        {"role": "system", "content": config.EVALUATOR_V1_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"USER_QUERY:\n{user_query}\n\n"
                f"ENTITY_RESULT:\n{json.dumps(entity_dict, indent=2)}\n\n"
                f"CANONICAL_MULTI_PARSER:\n{json.dumps(parser_dict, indent=2)}\n\n"
                f"PLANNER_OUTPUT:\n{json.dumps(planner_dict, indent=2)}\n\n"
                f"CONDENSED_STEP_RESULTS:\n{json.dumps(condensed_results, indent=2, default=str)}\n\n"
                f"STOP_REASON:\n{stop_reason or '[none]'}\n\n"
                f"PROVISIONAL_REPLY:\n{provisional_reply or '[not generated yet]'}"
            ),
        },
    ]

    evaluator_client, evaluator_model, evaluator_budget = config.get_agent_model("evaluator")
    try:
        result = call_llm_structured(
            config=config,
            prompt=user_query,
            model=PlanEvaluatorOutput,
            system=config.EVALUATOR_V1_SYSTEM_PROMPT,
            messages=messages,
            model_name=evaluator_model,
            temperature=0,
            log_label="plan_evaluator",
            log_payload_extra={"user_query": user_query, "stop_reason": stop_reason},
            usage_label="PLAN_EVALUATOR",
            thinking_budget=evaluator_budget,
            client=evaluator_client,
        )
        print(
            f"[DEBUG][EVALUATOR] status={result.overall_status} "
            f"answered={result.answered_query} consistent={result.execution_consistent}"
        )
        return result
    except Exception as e:
        print(f"[DEBUG][EVALUATOR] failed: {e!r}")
        fallback_status = "partial" if stop_reason else "success"
        fallback_summary = (
            f"This may not fully answer your request: {stop_reason}"
            if stop_reason else ""
        )
        fallback = PlanEvaluatorOutput(
            answered_query=not bool(stop_reason),
            execution_consistent=not bool(stop_reason),
            overall_status=fallback_status,
            zero_results_assessment="not_applicable",
            failure_stage="unknown" if stop_reason else None,
            reason=f"plan evaluator failed: {e}",
            what_went_wrong=str(stop_reason or ""),
            user_safe_summary=fallback_summary,
            confidence=None,
        )
        log_prompt(
            log_dir or config.LOG_DIR,
            "plan_evaluator",
            {"messages": messages, "response": fallback.model_dump()},
        )
        return fallback


def _execute_single_plan_step(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    step: PlanStep,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    send_event: "SendEvent | None",
    *,
    parser_plan: "MultiParserPlan | None" = None,
    step_results: dict[int, dict] | None = None,
    enriched_context: dict[int, ContextEngineerOutput] | None = None,
    intersection_uids: set[str] | None = None,
    next_step: PlanStep | None = None,
) -> tuple[dict, dict[str, Any], dict[int, ContextEngineerOutput], set[str] | None, str | None]:
    step_results = step_results or {}
    enriched_context = dict(enriched_context or {})
    debug_fragment: dict[str, Any] = {}
    query_for_tool = _step_query(step)
    input_values, missing_inputs = _resolve_step_inputs(step, enriched_context)
    ec_for_tool = enriched_context if step.combine_mode == "sequential" else {}
    step_agent = _PLAN_TOOL_AGENT_NAMES.get(step.tool, step.tool)
    search_source = _PLAN_TOOL_SEARCH_SOURCES.get(step.tool)

    if missing_inputs and step.required:
        stop_reason = f"Step {step.step_id} is missing required inputs: {', '.join(missing_inputs)}"
        print(f"[PLANNER] Halting before step {step.step_id}: {stop_reason}")
        debug_fragment.setdefault("executor", {})[step.step_id] = {
            "missing_inputs": missing_inputs,
            "query_for_tool": query_for_tool,
        }
        return {"ok": False, "tool": step.tool, "output": {}, "error": stop_reason}, debug_fragment, enriched_context, intersection_uids, stop_reason

    if send_event:
        send_event(
            "agent_started",
            {"agent": step_agent, "mode": "plan", "step_id": step.step_id, "tool": step.tool},
        )
        if search_source:
            search_payload = {"source": search_source, "step_id": step.step_id, "tool": step.tool}
            if step.tool == "new_search" and step.target_endpoint:
                search_payload["endpoint"] = step.target_endpoint
            send_event("search_started", search_payload)

    _t0 = time.perf_counter()
    tool_output = _run_plan_tool(
        config,
        session,
        step,
        query_for_tool,
        entity_result,
        log_dir,
        enriched_context=ec_for_tool,
        parser_plan=parser_plan,
        step_results=step_results,
    )
    print(f"[TIMING][STEP {step.step_id}] {time.perf_counter() - _t0:.2f}s ok={tool_output.get('ok')}")

    if send_event:
        if search_source:
            search_complete_payload = {
                "source": search_source,
                "step_id": step.step_id,
                "tool": step.tool,
                "ok": tool_output.get("ok"),
                "count": (tool_output.get("output") or {}).get("count"),
            }
            if step.tool == "new_search":
                search_complete_payload["endpoint"] = (tool_output.get("output") or {}).get("endpoint")
            send_event("search_complete", search_complete_payload)
        send_event("agent_complete", {"agent": step_agent, "summary": {
            "ok": tool_output.get("ok"),
            "count": (tool_output.get("output") or {}).get("count"),
            "step_id": step.step_id,
            "tool": step.tool,
        }})

    step_output = tool_output.get("output") or {}
    step_count = step_output.get("count") if isinstance(step_output, dict) else None
    hard_error = not tool_output.get("ok", False) and step.required and step.outcome.halt_on_error
    empty_required = bool(step.outcome.halt_on_empty and step.required and (step_count == 0))
    if hard_error or empty_required:
        stop_reason = tool_output.get("error") or f"Step {step.step_id} returned no usable results"
        print(f"[PLANNER] Halting at step {step.step_id}: {stop_reason}")
        return tool_output, debug_fragment, enriched_context, intersection_uids, stop_reason

    extracted_fields = _extract_fields_from_output(step, tool_output)
    if extracted_fields:
        enriched_context[step.step_id] = ContextEngineerOutput(
            extraction_code="result = {}",
            enriched_context=extracted_fields,
            method="code",
            notes="deterministic mapping",
        )
        debug_fragment.setdefault("context_engineer", {})[step.step_id] = enriched_context[step.step_id].model_dump()

    should_run_ce = (
        step.needs_context_engineer
        or bool(step.transformation_hint or step.extraction_hint)
        or (step.combine_mode == "intersect" and "uids" not in extracted_fields)
    )
    if should_run_ce:
        _t0 = time.perf_counter()
        if send_event:
            send_event(
                "agent_started",
                {"agent": "context_engineer", "mode": "plan", "step_id": step.step_id, "tool": step.tool},
            )
        ce_out = context_engineer_step(config, step, tool_output, next_step)
        print(f"[TIMING][CTX_ENG step {step.step_id}] {time.perf_counter() - _t0:.2f}s method={ce_out.method}")
        merged_context = dict((enriched_context.get(step.step_id) or ContextEngineerOutput(
            extraction_code="result = {}",
            enriched_context={},
            method="code",
            notes="",
        )).enriched_context)
        merged_context.update(ce_out.enriched_context)
        enriched_context[step.step_id] = ce_out.model_copy(update={"enriched_context": merged_context})
        debug_fragment.setdefault("context_engineer", {})[step.step_id] = enriched_context[step.step_id].model_dump()
        if send_event:
            send_event(
                "agent_complete",
                {
                    "agent": "context_engineer",
                    "summary": {"step_id": step.step_id, "tool": step.tool, "method": ce_out.method},
                },
            )

    if step.combine_mode == "intersect":
        ce_uids = set()
        if step.step_id in enriched_context:
            raw_ce_uids = enriched_context[step.step_id].enriched_context.get("uids") or []
            ce_uids = {u for u in raw_ce_uids if u and isinstance(u, str)}
        step_uids = ce_uids if ce_uids else _extract_uids_from_output(tool_output)
        intersection_uids = step_uids if intersection_uids is None else intersection_uids & step_uids
        print(f"[DEBUG][INTERSECT] step {step.step_id} contributed {len(step_uids)} UIDs (ce={len(ce_uids)}); running intersection={len(intersection_uids)}")

    return tool_output, debug_fragment, enriched_context, intersection_uids, None


def _materialize_intersection_result(
    executed_steps: list[PlanStep],
    step_results: dict[int, dict],
    debug_fragment: dict[str, Any],
    intersection_uids: set[str] | None,
) -> None:
    if not any(step.combine_mode == "intersect" for step in executed_steps):
        return
    intersection_list = sorted(intersection_uids or set())
    debug_fragment["intersection"] = {"uids": intersection_list, "count": len(intersection_list)}
    step_results["intersection"] = {
        "ok": True,
        "tool": "intersection",
        "output": {"data": [{"uid": u} for u in intersection_list], "count": len(intersection_list)},
        "error": None,
    }
    print(f"[DEBUG][INTERSECT] Final intersection: {len(intersection_list)} UIDs")


def _execute_plan_steps(
    config: ChatConfig,
    session: "SessionState | SessionStateProxy",
    plan: PlannerOutput,
    entity_result: "EntityAgentOutput | dict",
    log_dir: str | None,
    send_event: "SendEvent | None",
    parser_plan: "MultiParserPlan | None" = None,
) -> "tuple[dict[int, dict], dict[str, Any], str | None]":
    """
    Core step loop: runs each PlanStep in order with deterministic halt rules
    and bridges context via the Context Engineer when needed.

    Returns (step_results, debug_payload_fragment, stop_reason).
    """
    step_results: dict[int, dict] = {}
    enriched_context: dict[int, ContextEngineerOutput] = {}
    debug_fragment: dict[str, Any] = {}
    stop_reason: str | None = None

    has_intersect_steps = any(s.combine_mode == "intersect" for s in plan.steps)
    intersection_uids: set[str] | None = None

    for i, step in enumerate(plan.steps):
        query_for_tool = _step_query(step)
        input_values, missing_inputs = _resolve_step_inputs(step, enriched_context)
        ec_for_tool = enriched_context if step.combine_mode == "sequential" else {}
        step_agent = _PLAN_TOOL_AGENT_NAMES.get(step.tool, step.tool)
        search_source = _PLAN_TOOL_SEARCH_SOURCES.get(step.tool)

        if missing_inputs and step.required:
            stop_reason = f"Step {step.step_id} is missing required inputs: {', '.join(missing_inputs)}"
            print(f"[PLANNER] Halting before step {step.step_id}: {stop_reason}")
            debug_fragment.setdefault("executor", {})[step.step_id] = {
                "missing_inputs": missing_inputs,
                "query_for_tool": query_for_tool,
            }
            break

        if send_event:
            send_event(
                "agent_started",
                {"agent": step_agent, "mode": "plan", "step_id": step.step_id, "tool": step.tool},
            )
            if search_source:
                search_payload = {"source": search_source, "step_id": step.step_id, "tool": step.tool}
                if step.tool == "new_search" and step.target_endpoint:
                    search_payload["endpoint"] = step.target_endpoint
                send_event("search_started", search_payload)
        _t0 = time.perf_counter()
        tool_output = _run_plan_tool(
            config,
            session,
            step,
            query_for_tool,
            entity_result,
            log_dir,
            enriched_context=ec_for_tool,
            parser_plan=parser_plan,
            step_results=step_results,
        )
        print(f"[TIMING][STEP {step.step_id}] {time.perf_counter() - _t0:.2f}s ok={tool_output.get('ok')}")
        step_results[step.step_id] = tool_output
        if send_event:
            if search_source:
                search_complete_payload = {
                    "source": search_source,
                    "step_id": step.step_id,
                    "tool": step.tool,
                    "ok": tool_output.get("ok"),
                    "count": (tool_output.get("output") or {}).get("count"),
                }
                if step.tool == "new_search":
                    search_complete_payload["endpoint"] = (tool_output.get("output") or {}).get("endpoint")
                send_event("search_complete", search_complete_payload)
            send_event("agent_complete", {"agent": step_agent, "summary": {
                "ok": tool_output.get("ok"),
                "count": (tool_output.get("output") or {}).get("count"),
                "step_id": step.step_id,
                "tool": step.tool,
            }})

        is_last_step = (i == len(plan.steps) - 1)

        ce_already_ran = False

        # 1. Executor-owned hard-stop rules
        step_output = tool_output.get("output") or {}
        step_count = step_output.get("count") if isinstance(step_output, dict) else None
        hard_error = not tool_output.get("ok", False) and step.required and step.outcome.halt_on_error
        empty_required = bool(step.outcome.halt_on_empty and step.required and (step_count == 0))
        if hard_error or empty_required:
            stop_reason = tool_output.get("error") or f"Step {step.step_id} returned no usable results"
            print(f"[PLANNER] Halting at step {step.step_id}: {stop_reason}")
            break

        # 2. Deterministic extraction first
        extracted_fields = _extract_fields_from_output(step, tool_output)
        if extracted_fields:
            enriched_context[step.step_id] = ContextEngineerOutput(
                extraction_code="result = {}",
                enriched_context=extracted_fields,
                method="code",
                notes="deterministic mapping",
            )
            debug_fragment.setdefault("context_engineer", {})[step.step_id] = enriched_context[step.step_id].model_dump()

        # 4. Context engineer — intersect extraction fallback or explicit transformation only
        should_run_ce = (
            step.needs_context_engineer
            or bool(step.transformation_hint or step.extraction_hint)
            or (step.combine_mode == "intersect" and "uids" not in extracted_fields)
        )
        if should_run_ce and (step.combine_mode == "intersect" or not is_last_step or step.needs_context_engineer):
            _t0 = time.perf_counter()
            _next_step = plan.steps[i + 1] if not is_last_step else None
            if send_event:
                send_event(
                    "agent_started",
                    {"agent": "context_engineer", "mode": "plan", "step_id": step.step_id, "tool": step.tool},
                )
            ce_out = context_engineer_step(config, step, tool_output, _next_step)
            print(f"[TIMING][CTX_ENG step {step.step_id}] {time.perf_counter() - _t0:.2f}s method={ce_out.method}")
            merged_context = dict((enriched_context.get(step.step_id) or ContextEngineerOutput(
                extraction_code="result = {}",
                enriched_context={},
                method="code",
                notes="",
            )).enriched_context)
            merged_context.update(ce_out.enriched_context)
            enriched_context[step.step_id] = ce_out.model_copy(update={"enriched_context": merged_context})
            debug_fragment.setdefault("context_engineer", {})[step.step_id] = enriched_context[step.step_id].model_dump()
            if send_event:
                send_event(
                    "agent_complete",
                    {
                        "agent": "context_engineer",
                        "summary": {"step_id": step.step_id, "tool": step.tool, "method": ce_out.method},
                    },
                )
            ce_already_ran = True

        # 5. Intersect accumulation
        if step.combine_mode == "intersect":
            ce_uids = set()
            if step.step_id in enriched_context:
                raw_ce_uids = enriched_context[step.step_id].enriched_context.get("uids") or []
                ce_uids = {u for u in raw_ce_uids if u and isinstance(u, str)}
            step_uids = ce_uids if ce_uids else _extract_uids_from_output(tool_output)
            intersection_uids = step_uids if intersection_uids is None else intersection_uids & step_uids
            print(f"[DEBUG][INTERSECT] step {step.step_id} contributed {len(step_uids)} UIDs (ce={len(ce_uids)}); running intersection={len(intersection_uids)}")

    # Finalise intersection result
    if has_intersect_steps and intersection_uids is not None:
        intersection_list = sorted(intersection_uids)
        debug_fragment["intersection"] = {"uids": intersection_list, "count": len(intersection_list)}
        step_results["intersection"] = {
            "ok": True,
            "tool": "intersection",
            "output": {"data": [{"uid": u} for u in intersection_list], "count": len(intersection_list)},
            "error": None,
        }
        print(f"[DEBUG][INTERSECT] Final intersection: {len(intersection_list)} UIDs")

    return step_results, debug_fragment, stop_reason




# Symbols moved to agents_new in Phase 3 — re-exported for backward compat
from .agents_new.entity import entity_agent  # noqa: E402,F401
from .agents_new.api import api_agent_build_request  # noqa: E402,F401
from .agents_new.reporter import reporter_agent, report_writer_agent  # noqa: E402,F401
from .agents_new.chatter import chatter_agent_answer, chatter_agent_plan  # noqa: E402,F401
from .agents_new.memory import (  # noqa: E402,F401
    _strip_python_code_fences,
    _load_memory_json_payload,
    memory_coder_agent,
    _format_memory_coder_answer,
    _legacy_memory_agent_answer,
    memory_agent_answer,
)
from .agents_new.system import system_agent  # noqa: E402,F401
from .agents_new.graph import graph_agent  # noqa: E402,F401
from .agents_new.seqera import seqera_agent  # noqa: E402,F401
from .agents_new.wizard import (  # noqa: E402,F401
    WizardToolLoopError,
    _wizard_agent_builder,
    _build_wizard_builder_system_prompt,
    wizard_agent,
)
from .agents_new.planner.agent import (  # noqa: E402,F401
    _normalize_planner_output,
    _planner_runtime_state,
    _normalize_planner_decision,
    multi_parser_agent,
    planner_agent,
    context_engineer_step,
)
from .agents_new.planner.tools import (  # noqa: E402,F401
    _plan_tool_graph_query,
    _plan_tool_new_search,
    _plan_tool_reporter,
    _plan_tool_refine_last_search,
    _plan_tool_report_generation,
    _select_memory_bundle_for_step,
    _plan_tool_memory_lookup,
    _plan_tool_ask_about_last_results,
    _plan_tool_coding_filter,
    _plan_tool_system_question,
    _plan_tool_unsupported,
    _PLAN_TOOL_DISPATCH,
)
from .agents_new.parser import (  # noqa: E402,F401
    _infer_report_type_from_query,
    _filters_have_any_value,
    _is_unscoped_bulk_export_request,
    _unsupported_bulk_export_candidate,
    _candidate_output_mapping,
    _fill_candidate_defaults,
    _build_step_from_candidate,
    _normalize_plan_step,
    _finalize_plan_steps,
    _resolve_step_inputs,
    _step_query,
    _fallback_multi_parser_plan,
    _canonical_multi_parse,
    _candidate_to_parser_plan,
    _apply_parser_guardrails,
    _apply_multi_parser_guardrails,
    parser_agent,
    _synthesize_top_candidate_plan,
    _filters_have_substance,
    _scope_only_graph_candidate,
    _find_mixed_scope_intersection_plan,
    _append_coding_filter_step_if_needed,
)

# Pipeline step shims — moved to pipeline/steps/* in Phase 1
from .pipeline.steps.directive import _pipeline_directive_parse  # noqa: E402,F401
from .pipeline.steps.sanity import _pipeline_sanity_check  # noqa: E402,F401
from .pipeline.steps.groupby import _pipeline_groupby_resolution  # noqa: E402,F401
from .pipeline.steps.edit import _pipeline_edit_step  # noqa: E402,F401
from .pipeline.steps.question import _pipeline_question_step  # noqa: E402,F401

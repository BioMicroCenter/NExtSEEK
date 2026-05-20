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
from .agents_new.planner.execution import (  # noqa: E402,F401
    _extract_uids_from_output,
    _extract_fields_from_output,
    _inject_context,
    _step_signature,
    _run_plan_tool,
    _execute_single_plan_step,
    _materialize_intersection_result,
    _execute_plan_steps,
    _PLAN_TOOL_AGENT_NAMES,
    _PLAN_TOOL_SEARCH_SOURCES,
    _TERMINAL_REPLY_TOOLS,
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

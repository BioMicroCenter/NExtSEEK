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




def _normalize_planner_output(
    result: PlannerOutput,
    parser_plan: MultiParserPlan | None,
    user_query: str,
) -> PlannerOutput:
    """Apply deterministic planner guardrails after the LLM emits a plan."""
    normalized_steps = [_normalize_plan_step(step, parser_plan, user_query) for step in result.steps]
    if not parser_plan or not parser_plan.candidates:
        return result.model_copy(update={"steps": _finalize_plan_steps(normalized_steps)})

    top_candidate = _fill_candidate_defaults(parser_plan.candidates[0])
    candidate_ids = {c.candidate_id for c in parser_plan.candidates if c.candidate_id}
    protected_modes = {
        "reporter",
        "report_generation",
        "system_question",
        "unsupported",
        "ask_about_last_results",
        "refine_last_search",
    }

    if not normalized_steps:
        synthesized = _synthesize_top_candidate_plan(parser_plan, user_query, notes="planner emitted no steps; synthesized from candidate 0")
        return synthesized

    mixed_scope_plan = _find_mixed_scope_intersection_plan(parser_plan, user_query)
    if mixed_scope_plan and len(normalized_steps) == 1:
        only_step = normalized_steps[0]
        if only_step.execution.parser_candidate_id == top_candidate.candidate_id and top_candidate.criterion_scope == "structural":
            return mixed_scope_plan

    if top_candidate.mode in protected_modes and len(normalized_steps) > 1:
        synthesized = _synthesize_top_candidate_plan(parser_plan, user_query, notes=f"collapsed to protected top candidate mode={top_candidate.mode}")
        return synthesized

    invalid_candidate_reference = any(
        step.execution.parser_candidate_id and step.execution.parser_candidate_id not in candidate_ids
        for step in normalized_steps
    )
    if invalid_candidate_reference:
        synthesized = _synthesize_top_candidate_plan(parser_plan, user_query, notes="planner referenced unknown parser candidate; synthesized from candidate 0")
        return synthesized

    first_step = normalized_steps[0]
    first_candidate_id = first_step.execution.parser_candidate_id
    first_step_depends = bool(first_step.input_mapping)
    top_confident = (top_candidate.confidence or 0) >= 0.80
    if (
        top_candidate.mode in protected_modes
        and first_candidate_id
        and first_candidate_id != top_candidate.candidate_id
    ):
        synthesized = _synthesize_top_candidate_plan(parser_plan, user_query, notes=f"rewrote protected top candidate mode={top_candidate.mode}")
        return synthesized
    if (
        top_confident
        and first_candidate_id
        and first_candidate_id != top_candidate.candidate_id
        and not first_step_depends
        and len(normalized_steps) == 1
    ):
        synthesized = _synthesize_top_candidate_plan(parser_plan, user_query, notes="high-confidence candidate 0 preserved over lower-ranked single-step plan")
        return synthesized

    step_by_candidate = {c.candidate_id: _fill_candidate_defaults(c) for c in parser_plan.candidates if c.candidate_id}
    for step in normalized_steps:
        if step.tool == "coding_filter":
            continue
        candidate = step_by_candidate.get(step.execution.parser_candidate_id or "")
        if not candidate:
            continue
        tool_for_candidate = "report_generation" if candidate.mode == "reporter" and candidate.report_mode == "report_generation" else candidate.mode
        endpoint_mismatch = (
            candidate.target_endpoint
            and step.execution.target_endpoint
            and step.execution.target_endpoint != candidate.target_endpoint
        )
        tool_mismatch = step.tool != tool_for_candidate
        if endpoint_mismatch or tool_mismatch:
            synthesized = _synthesize_top_candidate_plan(
                parser_plan,
                user_query,
                notes=f"planner step diverged from parser candidate {candidate.candidate_id}; synthesized from candidate 0",
            )
            return synthesized

    normalized_steps = _append_coding_filter_step_if_needed(normalized_steps, parser_plan, user_query)
    finalized_steps = _finalize_plan_steps(normalized_steps)
    return result.model_copy(update={"steps": finalized_steps})


def _planner_runtime_state(
    user_query: str,
    parser_plan: MultiParserPlan | None,
    prior_steps: list[PlanStep],
    step_results: dict[int, dict],
    max_steps: int,
) -> dict[str, Any]:
    completed_steps: list[dict[str, Any]] = []
    executed_candidate_ids: list[str] = []
    for step in prior_steps:
        result = step_results.get(step.step_id) or {}
        output = result.get("output") or {}
        completed_steps.append(
            {
                "step_id": step.step_id,
                "tool": step.tool,
                "combine_mode": step.combine_mode,
                "parser_candidate_id": step.execution.parser_candidate_id,
                "query": _step_query(step),
                "ok": result.get("ok"),
                "count": output.get("count"),
                "error": result.get("error"),
            }
        )
        if step.execution.parser_candidate_id:
            executed_candidate_ids.append(step.execution.parser_candidate_id)

    available_candidates: list[dict[str, Any]] = []
    for candidate in (parser_plan.candidates if parser_plan else []):
        available_candidates.append(
            {
                "candidate_id": candidate.candidate_id,
                "mode": candidate.mode,
                "criterion_scope": candidate.criterion_scope,
                "composition_role": candidate.composition_role,
                "can_intersect": candidate.can_intersect,
                "fully_answers_query": candidate.fully_answers_query,
                "covers_constraints": candidate.covers_constraints,
                "missing_constraints": candidate.missing_constraints,
                "compatible_with": candidate.compatible_with,
                "already_used": candidate.candidate_id in executed_candidate_ids,
            }
        )

    return {
        "user_query": user_query,
        "intent_summary": parser_plan.intent_summary if parser_plan else user_query,
        "step_budget": {"used": len(prior_steps), "remaining": max(max_steps - len(prior_steps), 0), "max_steps": max_steps},
        "completed_steps": completed_steps,
        "available_candidates": available_candidates,
    }


def _normalize_planner_decision(
    result: PlannerDecisionOutput,
    parser_plan: MultiParserPlan | None,
    user_query: str,
    prior_steps: list[PlanStep],
) -> PlannerDecisionOutput:
    if result.action == "halt":
        termination_reason = result.termination_reason or ("answered" if not result.uncovered_constraints else "no_viable_next_step")
        return result.model_copy(update={"step": None, "termination_reason": termination_reason})

    step = result.step
    if step is None:
        if parser_plan and parser_plan.candidates:
            top_candidate = _fill_candidate_defaults(parser_plan.candidates[0])
            step = _build_step_from_candidate(top_candidate, step_id=len(prior_steps) + 1, user_query=user_query)
        else:
            return result.model_copy(update={"action": "halt", "step": None, "termination_reason": "no_viable_next_step"})

    step = _normalize_plan_step(step, parser_plan, user_query)
    execution = step.execution
    candidate = None
    if parser_plan and execution.parser_candidate_id:
        candidate = next((c for c in parser_plan.candidates if c.candidate_id == execution.parser_candidate_id), None)
        if candidate is None:
            return result.model_copy(update={"action": "halt", "step": None, "termination_reason": "no_viable_next_step"})
        candidate = _fill_candidate_defaults(candidate)

    if candidate:
        expected_tool = "report_generation" if candidate.mode == "reporter" and candidate.report_mode == "report_generation" else candidate.mode
        execution = step.execution.model_copy(
            update={
                "mode": candidate.mode,
                "target_endpoint": candidate.target_endpoint or step.execution.target_endpoint,
                "filters": step.execution.filters or (candidate.filters.model_dump() if hasattr(candidate.filters, "model_dump") else dict(candidate.filters or {})),
                "report_mode": step.execution.report_mode or candidate.report_mode,
                "report_type": step.execution.report_type or candidate.report_type,
                "tool_query": step.execution.tool_query or candidate.tool_query or user_query,
                "metadata": {**(candidate.metadata or {}), **(step.execution.metadata or {})},
            }
        )
        step = step.model_copy(
            update={
                "tool": expected_tool,
                "target_endpoint": execution.target_endpoint or step.target_endpoint,
                "context_prompt": step.context_prompt or execution.tool_query,
                "execution": execution,
            }
        )

    step = step.model_copy(update={"step_id": len(prior_steps) + 1})
    return result.model_copy(update={"step": step})



# ======================================================
# System Agent
# ======================================================

# ======================================================
# Planner Pipeline
# ======================================================


def multi_parser_agent(
    session: "SessionState | SessionStateProxy",
    config: ChatConfig,
    user_query: str,
    entity_result: EntityAgentOutput | dict,
) -> MultiParserPlan:
    """Canonical multi-parser entry point used by the planner pipeline."""
    return _canonical_multi_parse(session, config, user_query, entity_result)


def planner_agent(
    session: "SessionState | SessionStateProxy",
    config: ChatConfig,
    user_query: str,
    entity_result: EntityAgentOutput | dict,
    parser_plan: MultiParserPlan | None = None,
    prior_steps: list[PlanStep] | None = None,
    step_results: dict[int, dict] | None = None,
    max_steps: int = 5,
    retry_feedback: str | None = None,
) -> PlannerDecisionOutput:
    """
    Planner controller: given parser candidates plus runtime execution state, emit either the
    next executable step or a halt decision.
    """
    print("\n[DEBUG][PLANNER] User query:", user_query)

    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result
    recent_summary = build_recent_results_summary(session)
    prior_steps = prior_steps or []
    step_results = step_results or {}
    runtime_state = _planner_runtime_state(user_query, parser_plan, prior_steps, step_results, max_steps)

    if parser_plan is not None:
        candidates_json = json.dumps([c.model_dump() for c in parser_plan.candidates], indent=2)
        parser_context = f"PARSER_CANDIDATES (from Multi-Path Parser, ordered by preference):\n{candidates_json}"
    else:
        parser_context = "PARSER_CANDIDATES: (none — planner must infer routing independently)"

    messages = [
        {"role": "system", "content": config.PLANNER_SYSTEM_PROMPT},
        {"role": "system", "content": "RECENT_CONTEXT (prior session results):\n" + recent_summary},
        {"role": "system", "content": "ENTITY_RESULT (from Entity Agent):\n" + json.dumps(entity_dict, indent=2)},
        {"role": "system", "content": parser_context},
        {"role": "system", "content": "PLANNER_RUNTIME_STATE:\n" + json.dumps(runtime_state, indent=2)},
    ]
    if retry_feedback:
        messages.append(
            {
                "role": "system",
                "content": (
                    "PLANNER_RETRY_FEEDBACK:\n"
                    f"{retry_feedback}\n\n"
                    "Revise the plan to address this specific failure. You have exactly one retry."
                ),
            }
        )
    messages.append({"role": "user", "content": user_query})

    planner_client, planner_model, planner_budget = config.get_agent_model("planner")
    if planner_budget:
        print(f"[DEBUG][PLANNER] Extended thinking: budget={planner_budget}, model={planner_model}")

    try:
        result = call_llm_structured(
            config=config,
            prompt=user_query,
            model=PlannerDecisionOutput,
            system=config.PLANNER_SYSTEM_PROMPT,
            messages=messages,
            model_name=planner_model,
            temperature=0,
            log_label="planner",
            log_payload_extra={"user_query": user_query},
            usage_label="PLANNER",
            thinking_budget=planner_budget,
            client=planner_client,
        )
        result = _normalize_planner_decision(result, parser_plan, user_query, prior_steps)
        if result.action == "halt":
            print(f"[DEBUG][PLANNER] halt intent={result.intent_summary!r} reason={result.termination_reason!r}")
        elif result.step is not None:
            print(f"[DEBUG][PLANNER] next step {result.step.step_id}: tool={result.step.tool}, required={result.step.required}")
        return result
    except Exception as e:
        print(f"[DEBUG][PLANNER] planner_agent failed: {e!r}; falling back to next step from candidate 0")
        fallback_step = None
        if parser_plan and parser_plan.candidates:
            fallback_step = _build_step_from_candidate(
                _fill_candidate_defaults(parser_plan.candidates[0]),
                step_id=len(prior_steps) + 1,
                user_query=user_query,
            )
        else:
            fallback_step = _build_step_from_candidate(
                _fill_candidate_defaults(
                    ParserCandidate(
                        mode="new_search",
                        target_endpoint="/nextseek_api/samples/advanced_search/",
                        tool_query=user_query,
                        rationale=f"fallback after planner error: {e}",
                        confidence=0.5,
                    )
                ),
                step_id=len(prior_steps) + 1,
                user_query=user_query,
            )
        return PlannerDecisionOutput(
            intent_summary=user_query,
            action="execute_step",
            step=fallback_step,
            rationale=f"planner fallback after error: {e}",
            notes=f"planner_agent failed: {e}",
        )


def context_engineer_step(
    config: ChatConfig,
    step: PlanStep,
    tool_output: dict,
    next_step: PlanStep | None = None,
) -> ContextEngineerOutput:
    """
    Flash specialist that inspects the head of `tool_output` and writes a Python snippet
    to extract the needed context for the next step. Executes the snippet and returns
    a ContextEngineerOutput with the populated enriched_context.
    Falls back to an LLM-direct extraction when code exec fails.
    """
    print(f"\n[DEBUG][CTX_ENG] step={step.step_id} hint={step.transformation_hint or step.extraction_hint!r} intersect={step.combine_mode == 'intersect'}")

    # For intersect steps with no hint, default to UID extraction (used for intersection)
    extraction_hint = step.transformation_hint or step.extraction_hint
    if not extraction_hint and step.combine_mode == "intersect":
        extraction_hint = (
            "Extract all sample UID values from result rows. "
            "Try keys 'uid', 'UID', 's.UID', 'uuid', 'title' in that order. "
            'Return as {"uids": [list of uid strings]}. Filter out None/empty/HTML values.'
        )

    # Build a trimmed data sample for the prompt (head only)
    output_section = tool_output.get("output") or {}
    data_rows = output_section.get("data") or []
    if isinstance(data_rows, list):
        data_sample = data_rows[:10]
    else:
        data_sample = data_rows
    sample_text = json.dumps(data_sample, indent=2)[:2000]

    next_step_prompt = next_step.context_prompt if next_step else "(last step — extract UIDs for final intersection result)"

    messages = [
        {"role": "system", "content": config.CONTEXT_ENGINEER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"STEP_DEFINITION:\n{json.dumps(step.model_dump(), indent=2)}\n\n"
                f"DATA_SAMPLE (first ~10 records):\n{sample_text}\n\n"
                f"NEXT_STEP_CONTEXT_PROMPT:\n{next_step_prompt}\n\n"
                f"EXTRACTION_HINT:\n{extraction_hint}"
            ),
        },
    ]

    ce_client, ce_model, ce_budget = config.get_agent_model("context_engineer")
    try:
        ce_out = call_llm_structured(
            config=config,
            prompt=extraction_hint,
            model=ContextEngineerOutput,
            system=config.CONTEXT_ENGINEER_SYSTEM_PROMPT,
            messages=messages,
            model_name=ce_model,
            temperature=0,
            log_label="context_engineer",
            thinking_budget=ce_budget,
            client=ce_client,
        )
    except Exception as e:
        print(f"[DEBUG][CTX_ENG] structured call failed: {e!r}; returning empty context")
        return ContextEngineerOutput(
            extraction_code="result = {}",
            enriched_context={},
            method="llm",
            notes=f"call failed: {e}",
        )

    # Execute the generated code.
    # Provide `rows` as a convenience variable pointing directly at the data list,
    # so generated code doesn't need to know the full nesting structure.
    output_section = tool_output.get("output") or {}
    data_rows = output_section.get("data") or []
    rows_for_exec = data_rows if isinstance(data_rows, list) else []

    try:
        exec_scope: dict = {
            "data": tool_output,     # full {ok, tool, output, error} dict
            "rows": rows_for_exec,   # convenience: the actual data list
            "json": json,
            "re": re,
        }
        exec(ce_out.extraction_code, exec_scope)  # noqa: S102
        extracted = exec_scope.get("result", {})
        if not isinstance(extracted, dict):
            extracted = {"value": extracted}
        # Reject if the result looks like a ContextEngineerOutput (LLM confused the format)
        ce_keys = {"extraction_code", "enriched_context", "method", "notes"}
        if ce_keys.issubset(extracted.keys()):
            raise ValueError("exec result looks like a CE schema object, not extracted data")
        print(f"[DEBUG][CTX_ENG] extracted keys={list(extracted.keys())}")
        return ContextEngineerOutput(
            extraction_code=ce_out.extraction_code,
            enriched_context=extracted,
            method="code",
            notes=ce_out.notes,
        )
    except Exception as exec_err:
        print(f"[DEBUG][CTX_ENG] exec failed ({exec_err!r}); falling back to LLM extraction")
        # Fallback: ask the LLM to produce ONLY the extracted values dict directly.
        try:
            fallback_messages = [
                {
                    "role": "user",
                    "content": (
                        f"Extract the following from the data and return ONLY a JSON object "
                        f"with the extracted values. Do NOT return extraction_code or any "
                        f"ContextEngineerOutput fields — only the data itself.\n\n"
                        f"EXTRACTION_HINT:\n{extraction_hint}\n\n"
                        f"DATA (rows):\n{json.dumps(rows_for_exec[:20], default=str)[:2000]}\n\n"
                        "Return a single flat JSON object, e.g. {\"uids\": [\"TIS-001\", ...]}."
                    ),
                },
            ]
            fallback_resp = ce_client.chat(
                model=ce_model,
                temperature=0,
                messages=fallback_messages,
                thinking_budget=ce_budget,
            )
            raw = fallback_resp.content or "{}"
            extracted = safe_parse_json(raw)
            if not isinstance(extracted, dict):
                extracted = {}
            # Same guard: reject if it looks like a CE object
            if ce_keys.issubset(extracted.keys()):
                extracted = extracted.get("enriched_context") or {}
            print(f"[DEBUG][CTX_ENG] fallback extracted keys={list(extracted.keys())}")
            return ContextEngineerOutput(
                extraction_code=ce_out.extraction_code,
                enriched_context=extracted,
                method="llm",
                notes=f"exec failed ({exec_err!r}); used LLM fallback",
            )
        except Exception as fb_err:
            print(f"[DEBUG][CTX_ENG] fallback also failed: {fb_err!r}")
            return ContextEngineerOutput(
                extraction_code=ce_out.extraction_code,
                enriched_context={},
                method="llm",
                notes=f"exec failed: {exec_err!r}; fallback failed: {fb_err!r}",
            )


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
        from . import nfcore_wizard

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


def seqera_agent(
    config: ChatConfig,
    user_query: str,
    pipeline: str,
    samplesheet_preview: dict | None = None,
    reporter_context_summary: dict | None = None,
) -> SeqeraLaunchPlan:
    """Produce a SeqeraLaunchPlan (run name + params + revision/profile overrides).

    Falls back to a minimal catalog-default plan on failure so emission still
    proceeds. The emitter wires workspace/compute-env/work-dir from env vars.
    """
    entry = get_pipeline_entry(pipeline)
    fallback = SeqeraLaunchPlan(
        run_name=f"{pipeline}-run-{int(time.time())}",
        params={"genome": entry.get("default_genome")} if entry.get("default_genome") else {},
        outdir_suffix="",
        work_dir_suffix="",
        pipeline_revision=None,
        profile=entry.get("default_profile"),
        notes="catalog default",
    )

    messages = [
        {"role": "system", "content": config.SEQERA_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": f"PIPELINE: {pipeline}"},
        {"role": "system", "content": f"PIPELINE_ENTRY:\n{json.dumps(entry, indent=2)}"},
        {"role": "system", "content": f"SAMPLESHEET_PREVIEW:\n{json.dumps(samplesheet_preview or {}, indent=2)}"},
        {"role": "system", "content": f"REPORTER_CONTEXT_SUMMARY:\n{json.dumps(reporter_context_summary or {}, indent=2)}"},
        {"role": "user", "content": user_query},
    ]

    print(f"\n[DEBUG][SEQERA_AGENT] pipeline={pipeline} cohort_label={(samplesheet_preview or {}).get('cohort_label') or '(single)'}")

    client, model_name, budget = config.get_agent_model("seqera_agent")
    try:
        result = call_llm_structured(
            config=config,
            prompt="Produce a Tower launch plan for the chosen pipeline.",
            model=SeqeraLaunchPlan,
            messages=messages,
            model_name=model_name,
            temperature=0,
            log_label="seqera_agent",
            usage_label="SEQERA_AGENT",
            thinking_budget=budget,
            client=client,
        )
        # Strip any forbidden keys the LLM might have emitted.
        params = dict(result.params or {})
        for forbidden in ("input", "outdir"):
            params.pop(forbidden, None)
        final = result.model_copy(update={"params": params})
        print(
            f"[DEBUG][SEQERA_AGENT] Parsed plan: run_name={final.run_name!r} "
            f"params={final.params} revision={final.pipeline_revision} profile={final.profile}"
        )
        return final
    except Exception as e:
        print(f"[DEBUG][SEQERA_AGENT] LLM failed: {e!r}; using fallback plan.")
        return fallback


class WizardToolLoopError(Exception):
    """Raised when the builder-step tool-use loop exceeds MAX_TOOL_ITERATIONS,
    when the LLM stops without calling finalize_turn, or when the resolved
    model client lacks function-calling support."""


MAX_TOOL_ITERATIONS = 5


def _wizard_agent_builder(
    *,
    config: ChatConfig,
    session: SessionState | SessionStateProxy,
    user_text: str,
    chat_history: str = "",
    history_messages: list[dict] | None = None,
) -> WizardAgentOutput:
    """Run one builder-step turn through an Anthropic-style function-calling loop.

    The LLM may call any of the tools in BUILDER_TOOL_SCHEMAS; it MUST call
    finalize_turn as its last tool. We dispatch each non-finalize tool via
    `dispatch_tool_call`, feed the result back as a user message, and exit
    when finalize_turn is encountered.

    If `history_messages` is provided, those alternating user/assistant turns
    are prepended to the messages list before the current `user_text`, so
    the builder LLM sees prior builder-step turns and can resolve pronouns
    / follow-ups without re-prompting the user. Defaults to None (no
    history; behavior identical to the pre-history call shape).

    Cap: MAX_TOOL_ITERATIONS=5 per turn. Beyond that, raise WizardToolLoopError.
    """
    from .builder_tools import BUILDER_TOOL_SCHEMAS, dispatch_tool_call

    print(f"[DEBUG][WIZARD_BUILDER] enter user_text={user_text!r} prior_turns={len(history_messages) // 2 if history_messages else 0}")
    client, model_name, _budget = config.get_agent_model("wizard_builder")
    print(f"[DEBUG][WIZARD_BUILDER] resolved model_name={model_name!r} client_type={type(client).__name__}")
    if not callable(getattr(client, "chat_with_tools", None)):
        print(f"[DEBUG][WIZARD_BUILDER] FAIL: client has no callable chat_with_tools")
        raise WizardToolLoopError(
            f"Resolved LLM client {type(client).__name__!r} does not support "
            "chat_with_tools; the wizard_builder agent must be mapped to a "
            "function-calling-capable model (e.g. Anthropic via BedrockClient)."
        )

    system_prompt = _build_wizard_builder_system_prompt(
        session=session, config=config, chat_history=chat_history,
    )

    messages: list[dict] = list(history_messages or [])
    messages.append({"role": "user", "content": user_text})

    for i in range(MAX_TOOL_ITERATIONS):
        print(f"[DEBUG][WIZARD_BUILDER] iter={i} messages_len={len(messages)}")
        resp = client.chat_with_tools(
            messages=messages,
            tools=BUILDER_TOOL_SCHEMAS,
            system=system_prompt,
            model=model_name,
        )
        print(f"[DEBUG][WIZARD_BUILDER] iter={i} stop_reason={resp.get('stop_reason')!r} content_blocks={len(resp.get('content', []))}")
        if resp.get("stop_reason") != "tool_use":
            # LLM produced a plain-text answer and stopped without finalize_turn.
            # Treat text-only end_turn as an implicit finalize_turn(action="stay")
            # — the chat_log only stores reply previews (not the underlying
            # finalize_turn tool_use), so when history is replayed the LLM
            # naturally mimics that "just answer in text" pattern. action=advance
            # and action=cancel still need an explicit finalize_turn signal.
            text_blocks = [b for b in resp.get("content", []) if b.get("type") == "text"]
            if resp.get("stop_reason") == "end_turn" and text_blocks:
                reply_text = "".join(b.get("text", "") for b in text_blocks).strip()
                print(f"[DEBUG][WIZARD_BUILDER] implicit finalize_turn (end_turn with text, len={len(reply_text)})")
                return WizardAgentOutput(
                    action="stay", selection_updates={}, reply=reply_text,
                )
            print(f"[DEBUG][WIZARD_BUILDER] FAIL: stop_reason={resp.get('stop_reason')!r} content={resp.get('content', [])!r}")
            raise WizardToolLoopError(
                "Builder LLM stopped without calling finalize_turn "
                f"(stop_reason={resp.get('stop_reason')!r})."
            )

        tool_use_blocks = [b for b in resp.get("content", []) if b.get("type") == "tool_use"]
        # Track the raw assistant message for the next iteration's history.
        assistant_content = resp.get("content", [])
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results: list[dict] = []
        finalize_payload: dict | None = None
        for block in tool_use_blocks:
            name = block.get("name")
            tool_input = block.get("input", {})
            tool_use_id = block.get("id")
            if name == "finalize_turn":
                # finalize_turn is the control-flow signal; do NOT dispatch.
                finalize_payload = tool_input
                print(f"[DEBUG][WIZARD_BUILDER] finalize_turn action={finalize_payload.get('action')!r} selection_keys={list((finalize_payload.get('selection_updates') or {}).keys())}")
                continue
            print(f"[DEBUG][WIZARD_BUILDER] dispatching tool={name!r} input_keys={list(tool_input.keys()) if isinstance(tool_input, dict) else 'non-dict'}")
            result = dispatch_tool_call(
                config=config, session=session,
                tool_name=name, tool_input=tool_input,
            )
            # The Bedrock Converse path expects tool_result content; the
            # normalized shape we return from chat_with_tools is anthropic-
            # native, so we feed it back in anthropic-native shape.
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result if isinstance(result, str) else str(result),
            })

        if finalize_payload is not None:
            return WizardAgentOutput(
                action=finalize_payload.get("action", "stay"),
                selection_updates=finalize_payload.get("selection_updates") or {},
                reply=finalize_payload.get("reply", ""),
            )

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    print(f"[DEBUG][WIZARD_BUILDER] FAIL: hit MAX_TOOL_ITERATIONS={MAX_TOOL_ITERATIONS} without finalize_turn")
    raise WizardToolLoopError(
        f"Builder LLM did not call finalize_turn within {MAX_TOOL_ITERATIONS} iterations."
    )


def _build_wizard_builder_system_prompt(
    *,
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    chat_history: str = "",
) -> str:
    """Compose the builder-step system prompt: base prompt + current state + chat history."""
    state = session.get("nfcore_wizard") or {}
    pipeline = state.get("pipeline") or "?"
    selection = state.get("selection") or {}
    pinned = state.get("pinned_context") or {}
    base_prompt = config.WIZARD_AGENT_SYSTEM_PROMPT or ""

    state_block = (
        "\n\n# CURRENT BUILDER STATE\n"
        f"pipeline: {pipeline}\n"
        f"selection.uids: {len(selection.get('uids') or [])} UIDs\n"
        f"selection.cohort_criteria: {selection.get('cohort_criteria') or []}\n"
        f"selection.enrichment_fields: {selection.get('enrichment_fields') or []}\n"
        f"pinned_context.source: {pinned.get('source', 'none')}\n"
        f"pinned_context.bundle_id: {pinned.get('bundle_id')}\n"
        "pinned_context.metadata_summary: "
        + (str(pinned.get('metadata_summary')) if pinned.get('metadata_summary') else "(not prefetched yet)")
    )
    parts = [base_prompt, state_block]
    if chat_history:
        parts.append("\n\n" + chat_history)
    return "".join(parts)


def wizard_agent(
    config: ChatConfig,
    *,
    step: str,
    user_text: str,
    wizard_state: dict,
    step_context: dict,
    chat_history: str = "",
    original_query: str = "",
    log_dir: str | None = None,
) -> WizardAgentOutput:
    """Per-turn LLM dispatcher for the nf-core wizard.

    Decides whether the user's reply answers the current step's question, is
    exploratory, or wants cancel/restart. Returns a `WizardAgentOutput` whose
    `extracted` shape depends on the step (see schema docstring).

    Falls back to a deterministic 'stay' answer if the LLM call fails so the
    wizard never crashes a user turn.
    """
    print(f"\n[DEBUG][WIZARD_AGENT] step={step!r} user_text={user_text!r}")

    state_json = json.dumps(
        {k: v for k, v in wizard_state.items() if k not in ("available_pipelines",)},
        default=str,
        indent=2,
    )
    context_json = json.dumps(step_context or {}, default=str, indent=2)
    if len(context_json) > 18000:
        context_json = context_json[:18000] + "\n…[step_context truncated]"

    messages: list[dict[str, str]] = [
        {"role": "system", "content": config.WIZARD_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": f"CURRENT_STEP: {step}"},
        {"role": "system", "content": f"WIZARD_STATE:\n{state_json}"},
        {"role": "system", "content": f"STEP_CONTEXT:\n{context_json}"},
    ]
    if chat_history:
        messages.append({"role": "system", "content": chat_history})
    if original_query:
        messages.append(
            {"role": "system", "content": f"ORIGINAL_USER_QUERY (what triggered the wizard):\n{original_query}"}
        )
    messages.append({"role": "user", "content": user_text})

    client, model_name, budget = config.get_agent_model("wizard_agent")
    try:
        result = call_llm_structured(
            config=config,
            prompt=user_text,
            model=WizardAgentOutput,
            system=config.WIZARD_AGENT_SYSTEM_PROMPT,
            messages=messages,
            model_name=model_name,
            temperature=0,
            log_label="wizard_agent",
            usage_label="WIZARD_AGENT",
            thinking_budget=budget,
            client=client,
        )
        print(
            f"[DEBUG][WIZARD_AGENT] action={result.action} "
            f"extracted_keys={list(result.extracted.keys())} notes={result.notes!r}"
        )
        log_prompt(
            log_dir or config.LOG_DIR,
            "wizard_agent",
            {
                "step": step,
                "user_text": user_text,
                "wizard_state": wizard_state,
                "step_context_keys": list((step_context or {}).keys()),
                "messages": messages,
                "response": result.model_dump(),
            },
        )
        return result
    except Exception as e:
        print(f"[DEBUG][WIZARD_AGENT] LLM failed: {e!r}; falling back to stay")
        fallback = WizardAgentOutput(
            action="stay",
            extracted={},
            reply=(
                "I hit a snag interpreting that. Could you rephrase your answer "
                "for the current step?"
            ),
            notes=f"llm_error: {e!r}",
        )
        log_prompt(
            log_dir or config.LOG_DIR,
            "wizard_agent",
            {
                "step": step,
                "user_text": user_text,
                "error": repr(e),
                "messages": messages,
                "response": fallback.model_dump(),
            },
        )
        return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline agent LLM helpers
# ─────────────────────────────────────────────────────────────────────────────


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

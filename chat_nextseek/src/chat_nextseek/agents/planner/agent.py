from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from ...session import SessionState
from ...config import ChatConfig
from ...helpers import (
    build_recent_results_summary,
    safe_parse_json,
)
from ...schemas.schema_helper import call_llm_structured
from ...schemas import (
    ContextEngineerOutput,
    EntityAgentOutput,
    MultiParserPlan,
    ParserCandidate,
    PlannerDecisionOutput,
    PlannerOutput,
    PlanStep,
)
from ..parser import (
    _append_coding_filter_step_if_needed,
    _canonical_multi_parse,
    _fill_candidate_defaults,
    _finalize_plan_steps,
    _find_mixed_scope_intersection_plan,
    _normalize_plan_step,
    _synthesize_top_candidate_plan,
    parser_agent,
)


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


from __future__ import annotations

import json
from typing import Any

from ...config import ChatConfig
from ...helpers import (
    log_prompt,
)
from ...schemas.schema_helper import call_llm_structured
from ...schemas import (
    EntityAgentOutput,
    MultiParserPlan,
    PlanEvaluatorOutput,
    PlannerOutput,
)


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

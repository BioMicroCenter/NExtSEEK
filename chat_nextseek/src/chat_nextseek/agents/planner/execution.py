from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from ...session import SessionState
from ...config import ChatConfig
from ...schemas import (
    ContextEngineerOutput,
    EntityAgentOutput,
    MultiParserPlan,
    PlannerOutput,
    PlanStep,
)
from ..parser import (
    _resolve_step_inputs,
    _step_query,
)
from .agent import context_engineer_step
from .tools import _PLAN_TOOL_DISPATCH


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

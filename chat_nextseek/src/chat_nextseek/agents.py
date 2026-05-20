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


_BULK_EXPORT_INTENT_RE = re.compile(r"\b(download|export|dump|spreadsheet|csv|xlsx|excel)\b", re.IGNORECASE)
_BULK_SCOPE_RE = re.compile(
    r"\b(all|every|entire|whole)\b.{0,40}\b(sample|samples|record|records|database|db)\b|"
    r"\b(sample|samples|record|records|database|db)\b.{0,40}\b(all|every|entire|whole)\b",
    re.IGNORECASE,
)


def _infer_report_type_from_query(query: str) -> str | None:
    lowered = query.lower()
    if "nf-core" in lowered or "nfcore" in lowered:
        if "scrnaseq" in lowered or "sc rnaseq" in lowered or "single cell" in lowered or "single-cell" in lowered:
            return "NFCORE_SCRNASEQ"
        if "rnaseq" in lowered or "rna-seq" in lowered or "bulk rna" in lowered or "bulk-rna" in lowered:
            return "NFCORE_RNASEQ"
    return next((rt for rt in ("GEO", "PRIDE", "SRA") if rt.lower() in lowered), None)


def _filters_have_any_value(filters: ParserFilters | dict | None) -> bool:
    if hasattr(filters, "model_dump"):
        filters = filters.model_dump()
    filters = filters or {}
    return any(
        filters.get(key) not in (None, "", [], {})
        for key in ("sampletype_code", "assay_codes", "keywords", "uids")
    )


def _is_unscoped_bulk_export_request(user_query: str, mode: str, filters: ParserFilters | dict | None) -> bool:
    if mode != "new_search":
        return False
    query = user_query or ""
    return (
        bool(_BULK_EXPORT_INTENT_RE.search(query))
        and bool(_BULK_SCOPE_RE.search(query))
        and not _filters_have_any_value(filters)
        and _infer_report_type_from_query(query) is None
    )


def _unsupported_bulk_export_candidate(user_query: str, rationale: str = "") -> ParserCandidate:
    return _fill_candidate_defaults(ParserCandidate(
        mode="unsupported",
        target_endpoint=None,
        tool_query=user_query,
        criterion_scope="unsupported",
        output_fields=["reply"],
        rationale=(
            rationale
            or "Unscoped bulk export/download requests are unsupported without filters, UIDs, "
            "project-reporting scope, or a supported repository/report-generation target."
        ),
        confidence=1.0,
    ))


def _candidate_output_mapping(candidate: ParserCandidate) -> dict[str, StepOutputMapping]:
    if candidate.mode in {"graph_query", "new_search", "refine_last_search"}:
        return {
            "uids": StepOutputMapping(
                field="uids",
                source="rows",
                keys=["uid", "uuid", "UID", "s.UID", "s.uid", "title"],
                value_type="list[str]",
            )
        }
    if candidate.mode in {"memory_lookup", "ask_about_last_results", "system_question", "unsupported"}:
        return {"reply": StepOutputMapping(field="reply", source="output", keys=["reply"], value_type="str")}
    if candidate.mode == "report_generation":
        return {"saved_files": StepOutputMapping(field="saved_files", source="output", keys=["saved_files"], value_type="dict")}
    return {}


def _fill_candidate_defaults(candidate: ParserCandidate) -> ParserCandidate:
    updates: dict[str, Any] = {}
    if not candidate.candidate_id:
        endpoint_slug = (candidate.target_endpoint or candidate.mode or "candidate").strip("/").replace("/", "_")
        updates["candidate_id"] = endpoint_slug or f"candidate_{candidate.mode}"
    if not candidate.tool_query:
        if candidate.mode in {"reporter", "system_question", "memory_lookup", "ask_about_last_results", "unsupported"}:
            updates["tool_query"] = candidate.rationale or candidate.mode
        else:
            updates["tool_query"] = candidate.rationale or candidate.target_endpoint or candidate.mode
    if not candidate.criterion_scope:
        scope_map = {
            "graph_query": "structural",
            "new_search": "attribute",
            "refine_last_search": "attribute",
            "reporter": "aggregate",
            "report_generation": "aggregate",
            "memory_lookup": "memory",
            "ask_about_last_results": "memory",
            "system_question": "system",
            "unsupported": "unsupported",
        }
        updates["criterion_scope"] = scope_map.get(candidate.mode, "")
    if candidate.mode in {"graph_query", "new_search", "refine_last_search"} and not candidate.output_fields:
        updates["output_fields"] = ["uids"]
    elif candidate.mode in {"memory_lookup", "ask_about_last_results", "system_question", "unsupported"} and not candidate.output_fields:
        updates["output_fields"] = ["reply"]
    elif candidate.report_mode == "report_generation" and not candidate.output_fields:
        updates["output_fields"] = ["saved_files"]
    if candidate.mode in {"graph_query", "new_search", "refine_last_search"} and not candidate.can_intersect:
        updates["can_intersect"] = True
    if not candidate.composition_role:
        role_map = {
            "graph_query": "scope",
            "new_search": "filter",
            "refine_last_search": "filter",
            "reporter": "report",
            "report_generation": "report",
            "memory_lookup": "memory",
            "ask_about_last_results": "memory",
            "system_question": "system",
            "unsupported": "terminal",
        }
        updates["composition_role"] = role_map.get(candidate.mode, "")
    return candidate.model_copy(update=updates) if updates else candidate


def _build_step_from_candidate(candidate: ParserCandidate, step_id: int, user_query: str) -> PlanStep:
    tool = candidate.mode
    if candidate.mode == "reporter" and candidate.report_mode == "report_generation":
        tool = "report_generation"
    query = candidate.tool_query or user_query
    execution = StepExecutionPayload(
        mode=candidate.mode,
        target_endpoint=candidate.target_endpoint,
        filters=candidate.filters.model_dump() if hasattr(candidate.filters, "model_dump") else dict(candidate.filters or {}),
        report_mode=candidate.report_mode,
        report_type=candidate.report_type,
        tool_query=query,
        parser_candidate_id=candidate.candidate_id,
        metadata=candidate.metadata or {},
    )
    halt_on_empty = candidate.mode in {"graph_query", "new_search", "refine_last_search"} and not candidate.can_intersect
    return PlanStep(
        step_id=step_id,
        tool=tool,
        context_prompt=query,
        target_endpoint=candidate.target_endpoint or "",
        combine_mode="sequential",
        required=True,
        execution=execution,
        output_mapping=_candidate_output_mapping(candidate),
        needs_context_engineer=False,
        outcome=StepOutcome(proceed=True, halt_on_empty=halt_on_empty, halt_on_error=True),
        notes=candidate.rationale or "",
    )


def _normalize_plan_step(step: PlanStep, parser_plan: MultiParserPlan | None, user_query: str) -> PlanStep:
    execution = step.execution
    candidate = None
    if parser_plan and execution.parser_candidate_id:
        candidate = next((c for c in parser_plan.candidates if c.candidate_id == execution.parser_candidate_id), None)
    if candidate is None and parser_plan:
        candidate = next(
            (
                c for c in parser_plan.candidates
                if c.mode == execution.mode or c.mode == step.tool or (c.target_endpoint and c.target_endpoint == step.target_endpoint)
            ),
            None,
        )

    execution_updates: dict[str, Any] = {}
    if not execution.mode:
        execution_updates["mode"] = candidate.mode if candidate else step.tool
    if not execution.tool_query:
        execution_updates["tool_query"] = step.context_prompt or (candidate.tool_query if candidate else user_query)
    if not execution.target_endpoint and step.target_endpoint:
        execution_updates["target_endpoint"] = step.target_endpoint
    if candidate:
        if not execution.target_endpoint and candidate.target_endpoint:
            execution_updates["target_endpoint"] = candidate.target_endpoint
        if not execution.filters:
            execution_updates["filters"] = candidate.filters.model_dump() if hasattr(candidate.filters, "model_dump") else dict(candidate.filters or {})
        if not execution.report_mode and candidate.report_mode:
            execution_updates["report_mode"] = candidate.report_mode
        if not execution.report_type and candidate.report_type:
            execution_updates["report_type"] = candidate.report_type
        if not execution.parser_candidate_id:
            execution_updates["parser_candidate_id"] = candidate.candidate_id
        if not execution.metadata and candidate.metadata:
            execution_updates["metadata"] = candidate.metadata
    execution = execution.model_copy(update=execution_updates) if execution_updates else execution

    updates: dict[str, Any] = {
        "execution": execution,
        "context_prompt": step.context_prompt or execution.tool_query or user_query,
        "target_endpoint": step.target_endpoint or execution.target_endpoint or "",
    }
    if not step.output_mapping and candidate:
        updates["output_mapping"] = _candidate_output_mapping(candidate)
    if not step.outcome:
        updates["outcome"] = StepOutcome(proceed=True, halt_on_empty=False, halt_on_error=step.required)
    if step.extraction_hint and not step.transformation_hint:
        updates["transformation_hint"] = step.extraction_hint
    if step.extraction_hint and not step.needs_context_engineer:
        updates["needs_context_engineer"] = True
    if step.tool in _TERMINAL_REPLY_TOOLS:
        updates["needs_context_engineer"] = False
    return step.model_copy(update=updates)


def _finalize_plan_steps(steps: list[PlanStep]) -> list[PlanStep]:
    finalized: list[PlanStep] = []
    for index, step in enumerate(steps):
        updates: dict[str, Any] = {}
        if (
            step.combine_mode == "sequential"
            and index > 0
            and not step.input_mapping
            and step.tool in {"new_search", "refine_last_search", "reporter", "report_generation"}
        ):
            prev_step = finalized[index - 1]
            if "uids" in (prev_step.output_mapping or {}):
                updates["input_mapping"] = {
                    "uids": StepInputRef(from_step=prev_step.step_id, field="uids", required=step.required)
                }
        if (
            step.combine_mode == "sequential"
            and step.extraction_hint
            and "uids" not in (step.output_mapping or {})
            and ("uid" in step.extraction_hint.lower() or "uids" in step.extraction_hint.lower())
        ):
            output_mapping = dict(step.output_mapping or {})
            output_mapping["uids"] = StepOutputMapping(
                field="uids",
                source="rows",
                keys=["uid", "uuid", "UID", "s.UID", "s.uid", "title"],
                value_type="list[str]",
                notes="inferred from legacy extraction_hint",
            )
            updates["output_mapping"] = output_mapping
            updates["needs_context_engineer"] = False
        finalized.append(step.model_copy(update=updates) if updates else step)
    return finalized


def _resolve_step_inputs(step: PlanStep, enriched_context: dict[int, ContextEngineerOutput]) -> tuple[dict[str, Any], list[str]]:
    resolved: dict[str, Any] = {}
    missing: list[str] = []
    for field_name, mapping in (step.input_mapping or {}).items():
        ce_out = enriched_context.get(mapping.from_step)
        value = (ce_out.enriched_context or {}).get(mapping.field) if ce_out else None
        if value in (None, "", [], {}):
            if mapping.required:
                missing.append(field_name)
            continue
        resolved[field_name] = value
    return resolved, missing


def _step_query(step: PlanStep) -> str:
    return step.execution.tool_query or step.context_prompt

def entity_agent(
    config: ChatConfig,
    user_query: str,
    sampletypes: list[dict] | None = None,
    assays: list[dict] | None = None,
    projects: list[dict] | None = None,
) -> EntityAgentOutput:
    """
    Run the Entity agent to resolve sampletypes, assays, and keywords from a user query.
    Accepts optional pre-shortlisted catalogs to reduce prompt size and falls back to min catalogs when absent.
    Returns a validated `EntityAgentOutput`, degrading gracefully when structured parsing fails.
    """
    print("\n[DEBUG][ENTITY] User query:", user_query)

    sampletypes = sampletypes if sampletypes is not None else config.MIN_SAMPLETYPES
    assays = assays if assays is not None else config.MIN_ASSAYS
    projects = projects if projects is not None else config.MIN_PROJECTS

    sampletypes_json = json.dumps(sampletypes, indent=2) if sampletypes else "[]"
    assays_json = json.dumps(assays, indent=2) if assays else "[]"
    projects_json = json.dumps(projects, indent=2) if projects else "[]"

    messages = [
        {"role": "system", "content": config.ENTITY_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": "SAMPLE TYPES CATALOG (JSON array from min_sampletypes.json):\n" + sampletypes_json,
        },
        {
            "role": "system",
            "content": "ASSAYS CATALOG (JSON array from min_assays.json):\n" + assays_json,
        },
        {
            "role": "system",
            "content": "PROJECTS CATALOG (JSON array from projects_db.json):\n" + projects_json,
        },
        {"role": "user", "content": user_query},
    ]

    entity_client, entity_model, entity_budget = config.get_agent_model("entity")
    try:
        result = call_llm_structured(
            config=config,
            prompt=user_query,
            model=EntityAgentOutput,
            system=config.ENTITY_SYSTEM_PROMPT,
            messages=messages,
            model_name=entity_model,
            temperature=0,
            response_format={"type": "json_object"},
            log_label="entity",
            log_payload_extra={"user_query": user_query},
            usage_label="ENTITY",
            thinking_budget=entity_budget,
            client=entity_client,
        )
    except Exception as e:
        print("[DEBUG][ENTITY] Exception or parse error (structured):", repr(e))
        # If the structured call timed out (common with Gemini), retry once and skip raw fallback.
        if isinstance(e, LLMTimeoutError):
            try:
                print("[DEBUG][ENTITY] Retrying structured call after timeout.")
                result = call_llm_structured(
                    config=config,
                    prompt=user_query,
                    model=EntityAgentOutput,
                    system=config.ENTITY_SYSTEM_PROMPT,
                    messages=messages,
                    model_name=entity_model,
                    temperature=0,
                    response_format={"type": "json_object"},
                    log_label="entity",
                    log_payload_extra={"user_query": user_query, "retry_after_timeout": True},
                    usage_label="ENTITY",
                    retries=0,
                    timeout_retries=0,
                    thinking_budget=entity_budget,
                    client=entity_client,
                )
            except Exception as e_retry:
                print("[DEBUG][ENTITY] Retry after timeout failed:", repr(e_retry))
                result = EntityAgentOutput()
        else:
            # Fallback: raw call without forced response_format (guarded by timeout)
            try:
                from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

                def _do_fallback_call():
                    return entity_client.chat(
                        model=entity_model,
                        temperature=0,
                        messages=messages,
                        thinking_budget=entity_budget,
                    )

                executor = ThreadPoolExecutor(max_workers=1)
                future = executor.submit(_do_fallback_call)
                try:
                    resp = future.result(timeout=180)
                except FuturesTimeoutError as te:
                    try:
                        future.cancel()
                    except Exception:
                        pass
                    try:
                        executor.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass
                    raise LLMTimeoutError("Raw fallback timed out after 180 seconds") from te
                finally:
                    try:
                        executor.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass

                log_usage(resp, "ENTITY_FALLBACK")
                raw_content = resp.content or ""
                parsed = safe_parse_json(raw_content)
                if isinstance(parsed, list):
                    parsed = {"sampletypes": parsed, "assays": [], "keywords": []}
                if not isinstance(parsed, dict):
                    parsed = {}
                result = EntityAgentOutput.model_validate(parsed)
            except Exception as e_fallback:
                print("[DEBUG][ENTITY] Fallback exception:", repr(e_fallback))
                result = EntityAgentOutput()

    print("[DEBUG][ENTITY] Parsed entity result:", json.dumps(result.model_dump(), indent=2))
    return result


def _fallback_multi_parser_plan(
    user_query: str,
    entity_result: EntityAgentOutput | dict,
    error: Exception | str,
) -> MultiParserPlan:
    entity_out = entity_result if isinstance(entity_result, EntityAgentOutput) else EntityAgentOutput()
    return MultiParserPlan(
        intent_summary=user_query,
        resolved=entity_out,
        candidates=[
            _fill_candidate_defaults(ParserCandidate(
                mode="new_search",
                target_endpoint="/nextseek_api/samples/advanced_search/",
                tool_query=user_query,
                rationale=f"fallback after multi_parser error: {error}",
                confidence=0.5,
            ))
        ],
        notes=f"multi_parser_agent failed: {error}",
    )


def _canonical_multi_parse(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    user_query: str,
    entity_result: EntityAgentOutput | dict,
) -> MultiParserPlan:
    """Run the canonical routing pass used by both the standard parser and planner pipeline."""
    print("\n[DEBUG][MULTI_PARSER] User query:", user_query)

    from .chat_memory import history_block

    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result
    recent_summary = build_recent_results_summary(session)
    chat_history = history_block(session)
    endpoints_json = json.dumps(config.MIN_API_ENDPOINTS, indent=2)
    graph_schema_json = json.dumps(config.MIN_GRAPH_SCHEMA, indent=2) if config.MIN_GRAPH_SCHEMA else "{}"

    messages: list[dict[str, str]] = [
        {"role": "system", "content": config.MULTI_PARSER_SYSTEM_PROMPT},
    ]
    if chat_history:
        messages.append({"role": "system", "content": chat_history})
    messages.extend([
        {"role": "system", "content": "RECENT_CONTEXT (prior session results):\n" + recent_summary},
        {"role": "system", "content": "ENTITY_RESULT (from Entity Agent):\n" + json.dumps(entity_dict, indent=2)},
        {"role": "system", "content": "API_ENDPOINT_CATALOG:\n" + endpoints_json},
        {"role": "system", "content": "GRAPH_SCHEMA:\n" + graph_schema_json},
        {"role": "user", "content": user_query},
    ])

    mp_client, mp_model, mp_budget = config.get_agent_model("multi_parser")
    if mp_budget:
        print(f"[DEBUG][MULTI_PARSER] Extended thinking: budget={mp_budget}, model={mp_model}")
    try:
        result = call_llm_structured(
            config=config,
            prompt=user_query,
            model=MultiParserPlan,
            system=config.MULTI_PARSER_SYSTEM_PROMPT,
            messages=messages,
            model_name=mp_model,
            temperature=0,
            log_label="multi_parser",
            log_payload_extra={"user_query": user_query},
            usage_label="MULTI_PARSER",
            thinking_budget=mp_budget,
            client=mp_client,
        )
        normalized_candidates = [_fill_candidate_defaults(c) for c in result.candidates]
        result = result.model_copy(update={"candidates": normalized_candidates})
        result = _apply_multi_parser_guardrails(user_query, result)
        print(f"[DEBUG][MULTI_PARSER] intent={result.intent_summary!r}, candidates={len(result.candidates)}")
        for c in result.candidates:
            print(f"[DEBUG][MULTI_PARSER]   candidate: mode={c.mode}, endpoint={c.target_endpoint}, confidence={c.confidence}")
        return result
    except Exception as e:
        print(f"[DEBUG][MULTI_PARSER] Failed: {e!r}; falling back to single new_search candidate")
        return _fallback_multi_parser_plan(user_query, entity_result, e)


def _candidate_to_parser_plan(
    session: SessionState | SessionStateProxy,
    user_query: str,
    parser_plan: MultiParserPlan,
) -> ParserPlan:
    """Project canonical candidate 0 into the legacy ParserPlan shape used by the standard pipeline."""
    candidate = _fill_candidate_defaults(parser_plan.candidates[0]) if parser_plan.candidates else _fill_candidate_defaults(
        ParserCandidate(
            mode="new_search",
            target_endpoint="/nextseek_api/samples/advanced_search/",
            tool_query=user_query,
            rationale="fallback candidate missing from canonical parser output",
            confidence=0.5,
        )
    )
    metadata = candidate.metadata or {}
    notes_parts = [part for part in [parser_plan.notes, candidate.rationale] if part]

    target_result_id = metadata.get("target_result_id")
    if target_result_id is None and candidate.mode == "ask_about_last_results":
        history = session.get("results_history", []) or []
        if history:
            target_result_id = history[-1].get("id")

    previous_api_plan = metadata.get("previous_api_plan")
    previous_user_query = metadata.get("previous_user_query")
    if candidate.mode == "refine_last_search" and (previous_api_plan is None or previous_user_query is None):
        history = session.get("results_history", []) or []
        if history:
            last_bundle = history[-1]
            previous_api_plan = previous_api_plan or last_bundle.get("api_plan")
            previous_user_query = previous_user_query or last_bundle.get("user_query")

    return ParserPlan(
        mode=candidate.mode,
        target_endpoint=candidate.target_endpoint,
        intent_summary=parser_plan.intent_summary or user_query,
        filters=candidate.filters if hasattr(candidate.filters, "model_dump") else ParserFilters.model_validate(candidate.filters or {}),
        resolved=parser_plan.resolved,
        target_result_id=target_result_id,
        endpoint_candidates=[candidate.target_endpoint] if candidate.target_endpoint else [],
        notes=" | ".join(notes_parts),
        previous_api_plan=previous_api_plan,
        previous_user_query=previous_user_query,
        report_mode=candidate.report_mode,
        report_type=candidate.report_type,
    )


def _apply_parser_guardrails(user_query: str, plan: ParserPlan) -> ParserPlan:
    """Apply narrow deterministic safety checks after LLM routing."""
    if _is_unscoped_bulk_export_request(user_query, plan.mode, plan.filters):
        return ParserPlan(
            mode="unsupported",
            target_endpoint=None,
            intent_summary=plan.intent_summary or user_query,
            filters=ParserFilters(),
            resolved=plan.resolved,
            target_result_id=plan.target_result_id,
            endpoint_candidates=[],
            notes=(
                (plan.notes + " | ") if plan.notes else ""
            ) + "Unscoped bulk export/download is unsupported without filters, UIDs, project-reporting scope, or a supported report-generation target.",
            previous_api_plan=plan.previous_api_plan,
            previous_user_query=plan.previous_user_query,
            report_mode=None,
            report_type=None,
        )
    return plan


def _apply_multi_parser_guardrails(user_query: str, plan: MultiParserPlan) -> MultiParserPlan:
    """Apply narrow deterministic safety checks to parser candidates."""
    if not plan.candidates:
        return plan
    top = _fill_candidate_defaults(plan.candidates[0])
    if not _is_unscoped_bulk_export_request(user_query, top.mode, top.filters):
        return plan
    unsupported = _unsupported_bulk_export_candidate(user_query)
    return plan.model_copy(update={
        "candidates": [unsupported],
        "notes": (
            (plan.notes + " | ") if plan.notes else ""
        ) + "Guardrail: unscoped bulk export/download routed to unsupported.",
    })


def parser_agent(session: SessionState | SessionStateProxy, config: ChatConfig, user_query: str, entity_result: EntityAgentOutput | dict) -> ParserPlan:
    """
    Invoke the single-path parser used by the standard pipeline.
    Embeds recent session context plus catalog endpoints into the prompt and returns a ParserPlan.
    """
    from .chat_memory import history_block

    recent_summary = build_recent_results_summary(session)
    chat_history = history_block(session)
    if isinstance(entity_result, EntityAgentOutput):
        entity_payload = entity_result.model_dump()
    else:
        entity_payload = entity_result or {}
    entity_json = json.dumps(entity_payload, indent=2)
    endpoints_json = json.dumps(config.MIN_API_ENDPOINTS, indent=2)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": config.PARSER_SYSTEM_PROMPT},
    ]
    if chat_history:
        messages.append({"role": "system", "content": chat_history})
    messages.extend([
        {
            "role": "system",
            "content": "Recent search context:\n" + recent_summary,
        },
        {
            "role": "system",
            "content": "ENTITY_RESULT (from Entity Agent):\n" + entity_json,
        },
        {
            "role": "system",
            "content": "API ENDPOINT CATALOG (JSON array from min_api_endpoints_enriched.json):\n" + endpoints_json,
        },
        {
            "role": "system",
            "content": "GRAPH_SCHEMA (consult to determine graph_query vs. API routing):\n"
            + json.dumps(config.MIN_GRAPH_SCHEMA, indent=2),
        },
        {"role": "user", "content": user_query},
    ])

    parser_client, parser_model_name, parser_thinking_budget = config.get_agent_model("parser")
    if parser_thinking_budget:
        print(f"[DEBUG][PARSER] Extended thinking enabled: budget={parser_thinking_budget} tokens, model={parser_model_name}")

    print("\n[DEBUG][PARSER] User query:", user_query)
    try:
        plan_model = call_llm_structured(
            config=config,
            prompt=user_query,
            model=ParserPlan,
            system=config.PARSER_SYSTEM_PROMPT,
            messages=messages,
            model_name=parser_model_name,
            temperature=0,
            log_label="parser",
            log_payload_extra={"user_query": user_query},
            usage_label="PARSER",
            thinking_budget=parser_thinking_budget,
            client=parser_client,
        )
    except Exception as e:
        print("[DEBUG][PARSER] Exception or parse error:", repr(e))
        plan_model = ParserPlan(notes="Parser could not produce valid structured output.")

    print("[DEBUG][PARSER] Parsed plan:", json.dumps(plan_model.model_dump(), indent=2))
    plan_model = _apply_parser_guardrails(user_query, plan_model)
    if plan_model.mode == "unsupported":
        print("[DEBUG][PARSER] Guardrailed plan:", json.dumps(plan_model.model_dump(), indent=2))
    return plan_model


def _synthesize_top_candidate_plan(
    parser_plan: MultiParserPlan,
    user_query: str,
    notes: str = "",
) -> PlannerOutput:
    """Build a one-step planner output from canonical candidate 0."""
    top_candidate = _fill_candidate_defaults(parser_plan.candidates[0]) if parser_plan.candidates else _fill_candidate_defaults(
        ParserCandidate(
            mode="new_search",
            target_endpoint="/nextseek_api/samples/advanced_search/",
            tool_query=user_query,
            rationale="planner synthesis fallback without parser candidates",
            confidence=0.5,
        )
    )
    synthesized_step = _build_step_from_candidate(top_candidate, step_id=1, user_query=user_query)
    return PlannerOutput(
        intent_summary=parser_plan.intent_summary or user_query,
        steps=[synthesized_step],
        notes=notes or "synthesized from canonical candidate 0",
    )


def _filters_have_substance(filters: ParserFilters | dict | None) -> bool:
    if hasattr(filters, "model_dump"):
        filters = filters.model_dump()
    filters = filters or {}
    return any(
        filters.get(key) not in (None, "", [], {})
        for key in ("sampletype_code", "assay_codes", "keywords", "uids")
    )


def _scope_only_graph_candidate(candidate: ParserCandidate, user_query: str) -> ParserCandidate:
    """Strip clearly non-structural attribute filters from a graph candidate when composing with search."""
    filters = candidate.filters.model_dump() if hasattr(candidate.filters, "model_dump") else dict(candidate.filters or {})
    filters["keywords"] = []
    metadata = dict(candidate.metadata or {})
    metadata.pop("keyword_filter", None)
    scope_query = (
        "Using only graph-structural criteria from this request: "
        f"{user_query}. "
        "Include investigation/study membership, lineage (DERIVED_FROM), and assay-relationship "
        "traversals such as a parent sample having an assay child. Return UIDs for the requested "
        "sample type/scope, not child assay-node UIDs unless the user explicitly asks for assay records. "
        "Do NOT filter by free-text keywords, sample markers, or metadata attributes "
        "(those are handled by a separate REST step)."
    )
    return candidate.model_copy(
        update={
            "filters": ParserFilters.model_validate(filters),
            "metadata": metadata,
            "tool_query": scope_query,
            "rationale": (candidate.rationale or "") + " Structural scope isolated for intersection planning.",
        }
    )


def _find_mixed_scope_intersection_plan(
    parser_plan: MultiParserPlan,
    user_query: str,
) -> PlannerOutput | None:
    """
    Build a two-step intersect plan when the parser identified distinct structural and attribute candidates
    that together satisfy the query better than any single candidate.
    """
    if not parser_plan.candidates:
        return None

    # If candidate 0 is a high-confidence specialized endpoint (not advanced_search), it handles
    # all criteria natively. Forcing a graph intersect adds UID format risk with no benefit.
    _ADVANCED_SEARCH = "/nextseek_api/samples/advanced_search/"
    _top = _fill_candidate_defaults(parser_plan.candidates[0])
    if (
        _top.mode == "new_search"
        and _top.target_endpoint
        and _top.target_endpoint != _ADVANCED_SEARCH
        and (_top.confidence or 0) >= 0.80
    ):
        return None

    normalized_candidates = [_fill_candidate_defaults(candidate) for candidate in parser_plan.candidates]
    structural_candidates = [
        candidate for candidate in normalized_candidates
        if candidate.criterion_scope == "structural" and candidate.can_intersect
    ]
    attribute_candidates = [
        candidate for candidate in normalized_candidates
        if candidate.criterion_scope == "attribute" and candidate.can_intersect and _filters_have_substance(candidate.filters)
    ]
    if not structural_candidates or not attribute_candidates:
        return None

    top_structural = structural_candidates[0]
    top_attribute = attribute_candidates[0]
    if top_structural.mode != "graph_query":
        return None
    if top_structural.confidence is not None and top_structural.confidence < 0.60:
        return None

    graph_scope_candidate = _scope_only_graph_candidate(top_structural, user_query)
    graph_step = _build_step_from_candidate(graph_scope_candidate, step_id=1, user_query=user_query).model_copy(
        update={
            "combine_mode": "intersect",
            "outcome": StepOutcome(proceed=True, halt_on_empty=True, halt_on_error=True),
            "notes": "Structural candidate isolated for intersection with attribute filters.",
        }
    )
    attribute_step = _build_step_from_candidate(top_attribute, step_id=2, user_query=user_query).model_copy(
        update={
            "combine_mode": "intersect",
            "outcome": StepOutcome(proceed=True, halt_on_empty=True, halt_on_error=True),
            "notes": "Attribute candidate intersects with the structural scope candidate.",
        }
    )
    return PlannerOutput(
        intent_summary=parser_plan.intent_summary or user_query,
        steps=_finalize_plan_steps([graph_step, attribute_step]),
        notes=(
            "Synthesized mixed-scope plan from parser candidates: "
            "intersect structural graph scope with attribute/search filters."
        ),
    )


def _append_coding_filter_step_if_needed(
    steps: list[PlanStep],
    parser_plan: MultiParserPlan,
    user_query: str,
) -> list[PlanStep]:
    """Ensure candidate metadata.post_filter becomes a real planner post-processing step."""
    if any(step.tool == "coding_filter" for step in steps):
        return steps

    candidates_by_id = {
        candidate.candidate_id: _fill_candidate_defaults(candidate)
        for candidate in parser_plan.candidates
        if candidate.candidate_id
    }
    source_step: PlanStep | None = None
    source_candidate: ParserCandidate | None = None
    for step in steps:
        candidate = candidates_by_id.get(step.execution.parser_candidate_id or "")
        if candidate and isinstance(candidate.metadata, dict) and candidate.metadata.get("post_filter"):
            source_step = step
            source_candidate = candidate
            break
    if source_step is None or source_candidate is None:
        return steps

    post_filter = source_candidate.metadata.get("post_filter")
    tool_query = (
        "Apply the parser-provided post_filter to the retrieved rows for this query: "
        f"{user_query}"
    )
    coding_step = PlanStep(
        step_id=max(step.step_id for step in steps) + 1,
        tool="coding_filter",
        context_prompt=tool_query,
        target_endpoint=None,
        combine_mode="sequential",
        extraction_hint="",
        depends_on=source_step.step_id,
        required=True,
        execution=StepExecutionPayload(
            mode="coding_filter",
            target_endpoint=None,
            filters={},
            tool_query=tool_query,
            parser_candidate_id=None,
            metadata={"post_filter": post_filter},
        ),
        input_mapping={
            "rows": StepInputRef(
                from_step=source_step.step_id,
                field="rows",
                required=True,
                notes="coding_filter reads full source step rows from executor step_results",
            )
        },
        output_mapping={},
        transformation_hint="",
        needs_context_engineer=False,
        outcome=StepOutcome(proceed=True, halt_on_empty=False, halt_on_error=True),
        notes="Planner-added local post-filter for parser metadata.post_filter.",
    )
    return [*steps, coding_step]


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


def reporter_agent(config: ChatConfig, user_query: str, parser_plan: ParserPlan | dict | None = None) -> ReporterPlan:
    """
    Map a user query to reporting inputs for run_project_sample_report.
    Detects relative date hints, folds in parser context, and fills missing mode/type defaults.
    Returns a structured ReporterPlan that gracefully handles parse failures.
    """
    now = datetime.now()
    parser_context = parser_plan.model_dump() if isinstance(parser_plan, ParserPlan) else parser_plan or {}
    suggested_report_mode = parser_context.get("report_mode") if isinstance(parser_context, dict) else None
    suggested_report_type = parser_context.get("report_type") if isinstance(parser_context, dict) else None
    valid_reporter_modes = {"summary", "summary_sql", "report_generation"}

    def _normalize_reporter_mode(value: str | None) -> str | None:
        if isinstance(value, str) and value in valid_reporter_modes:
            return value
        return None

    suggested_report_mode = _normalize_reporter_mode(suggested_report_mode)
    suggested_report_type = normalize_report_type(suggested_report_type)

    def _month_bounds(dt: datetime) -> tuple[str, str]:
        last_day = calendar.monthrange(dt.year, dt.month)[1]
        start = dt.replace(day=1).date().isoformat()
        end = dt.replace(day=last_day).date().isoformat()
        return start, end

    def _previous_month_bounds(dt: datetime) -> tuple[str, str]:
        first_of_month = dt.replace(day=1)
        end_prev = first_of_month - timedelta(days=1)
        start_prev, end_prev_month = _month_bounds(end_prev)
        return start_prev, end_prev_month

    def _week_bounds(dt: datetime) -> tuple[str, str]:
        # ISO weeks start on Monday
        start = (dt - timedelta(days=dt.weekday())).date()
        end = start + timedelta(days=6)
        return start.isoformat(), end.isoformat()

    def _detect_relative_date_hint(text: str, anchor: datetime) -> dict:
        lowered = text.lower()
        hint = {"day_range": None, "month_range": None, "years": [], "reason": ""}

        if "yesterday" in lowered:
            target = (anchor - timedelta(days=1)).date().isoformat()
            hint.update({"day_range": [target, target], "reason": "yesterday relative to today"})
            return hint

        if "today" in lowered or "right now" in lowered:
            target = anchor.date().isoformat()
            hint.update({"day_range": [target, target], "reason": "today's date"})
            return hint

        match_last_ndays = re.search(r"last\s+(\d+)\s+days", lowered) or re.search(
            r"past\s+(\d+)\s+days", lowered
        )
        if match_last_ndays:
            days = int(match_last_ndays.group(1))
            start = (anchor - timedelta(days=days - 1)).date().isoformat()
            end = anchor.date().isoformat()
            hint.update({"day_range": [start, end], "reason": f"last {days} days"})
            return hint

        if "last week" in lowered or "previous week" in lowered:
            start_current, _ = _week_bounds(anchor)
            start_prev = datetime.fromisoformat(start_current) - timedelta(days=7)
            start, end = _week_bounds(start_prev)
            hint.update({"day_range": [start, end], "reason": "previous calendar week"})
            return hint

        if "this week" in lowered:
            start, end = _week_bounds(anchor)
            hint.update({"day_range": [start, end], "reason": "current calendar week"})
            return hint

        if "last month" in lowered or "previous month" in lowered:
            start, end = _previous_month_bounds(anchor)
            hint.update({"day_range": [start, end], "reason": "previous calendar month"})
            return hint

        if "this month" in lowered or "current month" in lowered:
            start, end = _month_bounds(anchor)
            hint.update({"day_range": [start, end], "reason": "current calendar month"})
            return hint

        if "last year" in lowered or "previous year" in lowered:
            hint.update({"years": [anchor.year - 1], "reason": "previous calendar year"})
            return hint

        if "this year" in lowered or "current year" in lowered:
            hint.update({"years": [anchor.year], "reason": "current calendar year"})
            return hint

        return hint

    relative_hint = _detect_relative_date_hint(user_query, now)
    date_context = {
        "current_datetime": now.isoformat(),
        "today": now.date().isoformat(),
        "yesterday": (now - timedelta(days=1)).date().isoformat(),
        "this_month_range": _month_bounds(now),
        "last_month_range": _previous_month_bounds(now),
        "this_week_range": _week_bounds(now),
    }

    messages = [
        {"role": "system", "content": config.REPORTER_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                "Parser context:\n"
                f"{json.dumps(parser_context, indent=2)}"
            ),
        },
        {
            "role": "system",
            "content": (
                "Current date/time context for resolving relative phrases:\n"
                f"{json.dumps(date_context, indent=2)}"
            ),
        },
        {
            "role": "system",
            "content": (
                "Auto-detected relative date hint from the user query (use only if helpful):\n"
                f"{json.dumps(relative_hint, indent=2)}"
            ),
        },
        {"role": "user", "content": user_query},
    ]

    reporter_client, reporter_model, reporter_budget = config.get_agent_model("reporter")
    try:
        plan_model = call_llm_structured(
            config=config,
            prompt=user_query,
            model=ReporterPlan,
            system=config.REPORTER_SYSTEM_PROMPT,
            messages=messages,
            model_name=reporter_model,
            temperature=0,
            response_format={"type": "json_object"},
            thinking_budget=reporter_budget,
            log_label="reporter",
            log_payload_extra={"user_query": user_query},
            usage_label="REPORTER",
            client=reporter_client,
        )
    except Exception as e:
        print("[DEBUG][REPORTER] Exception or parse error:", repr(e))
        fallback_uids: list[str] = []
        if isinstance(parser_context, dict):
            filters = parser_context.get("filters") or {}
            if isinstance(filters, dict):
                parser_uids = filters.get("uids") or []
                if isinstance(parser_uids, list):
                    fallback_uids = [uid for uid in parser_uids if isinstance(uid, str) and uid.strip()]

        plan_model = ReporterPlan(
            reporter_mode=suggested_report_mode,
            report_type=suggested_report_type,
            uids=fallback_uids,
            notes="Reporter could not produce a structured plan.",
        )

    # If the LLM did not set date filters but we detected a clear relative range, fill it in.
    updates: dict = {}
    if relative_hint.get("day_range") and not plan_model.day_range:
        updates["day_range"] = relative_hint["day_range"]
        updates.setdefault("month_range", None)
        updates.setdefault("years", [])
    elif relative_hint.get("month_range") and not plan_model.month_range and not plan_model.day_range:
        updates["month_range"] = relative_hint["month_range"]
        updates.setdefault("years", [])
    elif relative_hint.get("years") and relative_hint["years"] and not plan_model.years:
        updates["years"] = relative_hint["years"]

    if updates:
        note_prefix = plan_model.notes + " | " if plan_model.notes else ""
        updates["notes"] = note_prefix + (relative_hint.get("reason") or "Applied relative date hint")
        plan_model = plan_model.model_copy(update=updates)

    normalized_reporter_mode = _normalize_reporter_mode(getattr(plan_model, "reporter_mode", None))
    if normalized_reporter_mode != getattr(plan_model, "reporter_mode", None):
        plan_model = plan_model.model_copy(update={"reporter_mode": normalized_reporter_mode})

    # Apply parser hints for report mode/type if missing
    if not getattr(plan_model, "reporter_mode", None):
        plan_model = plan_model.model_copy(update={"reporter_mode": suggested_report_mode or "summary"})
    normalized_report_type = normalize_report_type(getattr(plan_model, "report_type", None))
    if normalized_report_type != getattr(plan_model, "report_type", None):
        plan_model = plan_model.model_copy(update={"report_type": normalized_report_type})
    if not getattr(plan_model, "report_type", None) and suggested_report_type:
        plan_model = plan_model.model_copy(update={"report_type": suggested_report_type})
    if not plan_model.uids and isinstance(parser_context, dict):
        filters = parser_context.get("filters") or {}
        if isinstance(filters, dict):
            parser_uids = filters.get("uids") or []
            if parser_uids:
                plan_model = plan_model.model_copy(update={"uids": parser_uids})

    print("[DEBUG][REPORTER] Parsed reporter plan:", json.dumps(plan_model.model_dump(), indent=2))
    return plan_model

def report_writer_agent(
    config: ChatConfig,
    user_query: str,
    plan: ReportWriterPlan,
    template: dict | None = None,
) -> ReportWriterOutput:
    """
    Generate a repository-style report JSON using reporter_context and a type-specific template.
    Selects a specialized output model (e.g., GEO) when needed and logs the structured response.
    Falls back to a minimal output with notes if structured parsing fails.
    """
    template = template or {}
    canonical_report_type = normalize_report_type(plan.report_type)
    model_cls = ReportWriterOutput
    if canonical_report_type == "GEO":
        from .schemas import ReportWriterOutputGEO
        model_cls = ReportWriterOutputGEO
    messages = [
        {"role": "system", "content": config.REPORT_WRITER_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                f"Report type: {canonical_report_type or 'unknown'}\n"
                "Report template JSON (may be empty if unavailable):\n"
                f"{json.dumps(template, indent=2)}"
            ),
        },
        {
            "role": "system",
            "content": (
                "Reporter context JSON (metadata to use; do NOT fetch anything new):\n"
                f"{json.dumps(plan.reporter_context or {}, indent=2)}"
            ),
        },
    ]
    if canonical_report_type and canonical_report_type.startswith("NFCORE"):
        ctx = plan.reporter_context or {}
        passthrough = template.get("pipeline_optional_columns_passthrough") or []
        cohorts_ctx = ctx.get("nfcore_cohorts") or []
        chosen_fields = sorted({
            f for c in cohorts_ctx for f in (c.get("enrichment_metadata_fields") or [])
        })
        cohort_summary_text = "; ".join(
            f"{c.get('label')}={c.get('pipeline')} (criterion={c.get('cohort_criterion') or {}})"
            for c in cohorts_ctx
        ) or "(single default cohort)"
        nfcore_note = (
            "NFCORE SAMPLESHEET RULES:\n"
            "\n"
            "DIVISION OF LABOR — READ FIRST:\n"
            "- You produce `report.samplesheet`: a list of row dicts (ONE FLAT LIST covering ALL samples).\n"
            "- The DOWNSTREAM EMITTER rewrites `fastq_1` / `fastq_2` to ENA HTTPS URLs "
            "for every row that has an `accession` key, stamps enrichment columns from the "
            "actual metadata, and partitions rows into per-cohort samplesheets based on "
            "the cohort criterion. So:\n"
            "  - You MUST set `accession` to the most specific run accession (SRR/ERR/DRR) "
            "for each sample, taken from the sample's metadata (fields like SRA_accession, "
            "Run_accession, sra_run, or any 'SRR…' string in the metadata).\n"
            "  - Leave `fastq_1` and `fastq_2` empty — they WILL be overwritten. Do NOT use "
            "`File_PrimaryData`, `File_SecondaryData`, or any local filename for fastq "
            "columns — those are not the actual FASTQs and will be discarded.\n"
            "  - If a sample has no run accession in its metadata, OMIT the row entirely.\n"
            "\n"
            f"COHORT PARTITIONING (downstream): {cohort_summary_text}\n"
            "- Do NOT filter rows yourself — emit ONE row per biological sample with a real "
            "accession, regardless of which cohort it belongs to. The emitter applies each "
            "cohort's criterion to partition rows into separate samplesheets.\n"
            "\n"
            "ROW SELECTION:\n"
            "- One row per biological sample that has a real run accession.\n"
            "- Same `sample` value across rows tells nf-core to concatenate them.\n"
            "- The emitter automatically expands accessions that resolve to multiple runs.\n"
            "\n"
            "REQUIRED COLUMNS (must appear on every row, even when blank): see "
            "template.pipeline.required_columns.\n"
            "\n"
            f"ENRICHMENT COLUMNS (across all cohorts): {chosen_fields or '(none — emit only required columns + accession)'}.\n"
            "- These were chosen by the user during the nf-core wizard. The emitter stamps "
            "values authoritatively from the metadata, so do not feel obligated to populate "
            "them per row — but DO include them in your row keys so the column appears.\n"
            "- Use EXACT field names from the list.\n"
            "\n"
            f"PIPELINE OPTIONAL COLUMNS (include only when clearly supported by metadata): {passthrough or '(none)'}.\n"
        )
        messages.append({"role": "system", "content": nfcore_note})
    messages.append({"role": "user", "content": user_query})

    writer_client, writer_model, writer_budget = config.get_agent_model("report_writer")
    try:
        result = call_llm_structured(
            config=config,
            prompt="Write the structured report JSON using the template and reporter_context.",
            model=model_cls,
            system=config.REPORT_WRITER_SYSTEM_PROMPT,
            messages=messages,
            model_name=writer_model,
            temperature=0,
            response_format={"type": "json_object"},
            log_label="report_writer",
            log_payload_extra={"user_query": user_query, "report_type": canonical_report_type},
            usage_label="REPORT_WRITER",
            timeout_seconds=600,
            thinking_budget=writer_budget,
            client=writer_client,
        )
    except Exception as e:
        print("[DEBUG][REPORT_WRITER] Exception or parse error:", repr(e))
        result = model_cls(
            report_type=canonical_report_type,
            report=getattr(model_cls, "report", {}) or {},
            narrative=None,
            notes="Report writer could not produce structured output.",
        )

    print("[DEBUG][REPORT_WRITER] Parsed report writer output:", json.dumps(result.model_dump(), indent=2))
    return result

def api_agent_build_request(config: ChatConfig, plan: ParserPlan | dict) -> APIRequestPlan:
    """
    Use the API agent to convert a parser plan into a concrete APIRequestPlan with method, endpoint, and payloads.
    Injects endpoint schema hints, JSON:API requirements, and prior request context to improve validity.
    Returns a normalized plan with safe fallbacks for missing methods or invalid fields.
    """
    plan_dict = plan.model_dump() if isinstance(plan, ParserPlan) else plan or {}
    endpoint = plan_dict.get("target_endpoint")
    if not endpoint:
        return APIRequestPlan(
            endpoint=None,
            method=None,
            requestBody={},
            queryParameters={},
            notes="No endpoint specified in parser plan.",
        )

    schema = config.get_schema_for_endpoint(endpoint)
    methods = (schema or {}).get("methods") or []
    default_method = (schema or {}).get("method") or (methods[0] if methods else "POST")
    # Also pull enriched catalog entry (has request_body, llm_hint, requires_uids, etc.)
    enriched_entry = next((ep for ep in config.MIN_API_ENDPOINTS if ep.get("path") == endpoint), None)
    schema_text = (
        f"Schema for endpoint {endpoint}:\n"
        f"{json.dumps(schema, indent=2) if schema else 'No schema is registered for this endpoint.'}"
    )
    if enriched_entry:
        schema_text += f"\n\nEnriched endpoint definition (authoritative — use request_body as the body template):\n{json.dumps(enriched_entry, indent=2)}"

    filters = plan_dict.get("filters", {})
    resolved = plan_dict.get("resolved", {})
    intent_summary = plan_dict.get("intent_summary", "")
    previous_api_plan = plan_dict.get("previous_api_plan")
    previous_user_query = plan_dict.get("previous_user_query")

    # Build JSON:API hint if present in schema
    jsonapi_hint = ""
    req_schema = (schema or {}).get("request_schemas", {}).get(default_method)
    if isinstance(req_schema, dict):
        data_node = (req_schema.get("properties") or {}).get("data")
        data_required = set(data_node.get("required", [])) if isinstance(data_node, dict) else set()
        data_props = (data_node.get("properties") or {}) if isinstance(data_node, dict) else {}
        const_type = None
        if isinstance(data_props, dict):
            const_type = (data_props.get("type") or {}).get("const")
        attr_required = set()
        if isinstance(data_props.get("attributes"), dict):
            attr_required = set(data_props["attributes"].get("required", []) or [])
        rel_required = set()
        if isinstance(data_props.get("relationships"), dict):
            rel_required = set(data_props["relationships"].get("required", []) or [])

        if data_node:
            parts: list[str] = ["Detected JSON:API payload requirements:"]
            parts.append(f"- data.type must be set to {const_type!r}" if const_type else "- data.type is required.")
            if attr_required:
                parts.append(f"- data.attributes requires: {sorted(attr_required)}")
            if rel_required:
                parts.append(f"- data.relationships requires: {sorted(rel_required)} (use empty collections if no values are provided).")
            jsonapi_hint = "\n".join(parts)


    messages = [
        {"role": "system", "content": config.API_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": schema_text},
        {
            "role": "system",
            "content": (
                "Available HTTP methods for this endpoint: "
                f"{methods if methods else [default_method] if default_method else '[not specified]'}.\n"
                "IMPORTANT: Always use the method listed above — it is determined by the endpoint schema and overrides "
                "any general preference. Do not substitute GET for POST or vice versa."
            ),
        },
        {"role": "system", "content": jsonapi_hint or "No additional JSON:API hints."},
        {
            "role": "system",
            "content": (
                "Previous request context (if refining):\n"
                f"Prior user query: {previous_user_query or '[none]'}\n"
                f"Prior API plan:\n{json.dumps(previous_api_plan, indent=2) if previous_api_plan else '[none]'}"
            ),
        },
        {
          "role": "user",
          "content": (
            "Build a valid request.\n\n"
            f"Intent summary: {intent_summary}\n\n"
            f"Resolved entities JSON:\n{json.dumps(resolved, indent=2)}\n\n"
            f"Filters JSON:\n{json.dumps(filters, indent=2)}"
          ),
        }

    ]

    api_client, api_model, api_budget = config.get_agent_model("api")
    print("[DEBUG][API_AGENT] Calling API Agent with endpoint:", endpoint)
    try:
        api_plan_model = call_llm_structured(
            config=config,
            prompt="Construct the API request using the provided context.",
            model=APIRequestPlan,
            system=config.API_AGENT_SYSTEM_PROMPT,
            messages=messages,
            model_name=api_model,
            temperature=0,
            log_label="api_agent",
            log_payload_extra={"parser_plan": plan_dict},
            usage_label="API_AGENT",
            thinking_budget=api_budget,
            client=api_client,
        )
    except Exception as e:
        print("[DEBUG][API_AGENT] Exception or parse error:", repr(e))
        api_plan_model = APIRequestPlan(
            endpoint=endpoint,
            method=default_method,
            requestBody={},
            queryParameters={},
            notes="Fallback minimal plan; structured parsing failed.",
        )

    api_plan = api_plan_model.model_copy(
        update={
            "endpoint": api_plan_model.endpoint or endpoint,
            "method": api_plan_model.method or default_method,
            "requestBody": api_plan_model.requestBody or {},
            "queryParameters": api_plan_model.queryParameters or {},
            "notes": api_plan_model.notes or "",
        }
    )

    # Normalize filter_searchText to a single string when a list is returned
    rb = dict(api_plan.requestBody or {})
    fst = rb.get("filter_searchText")
    if isinstance(fst, list):
        strings = [s for s in fst if isinstance(s, str) and s.strip()]
        if strings:
            rb["filter_searchText"] = " ".join(strings)
        else:
            rb["filter_searchText"] = ""
    api_plan = api_plan.model_copy(update={"requestBody": rb})

    if api_plan.endpoint == "/nextseek_api/samples/advanced_search/":
        rb = dict(api_plan.requestBody or {})
        rb.pop("attribute", None)
        if rb.get("filter_searchText") is None:
            rb["filter_searchText"] = ""
        api_plan = api_plan.model_copy(update={"requestBody": rb})

    # If the agent selected a method not in the allowed list, fall back to default
    if methods and api_plan.method not in methods:
        print(
            f"[DEBUG][API_AGENT] Method {api_plan.method} not in allowed {methods}; "
            f"falling back to {default_method}"
        )
        api_plan = api_plan.model_copy(update={"method": default_method})

    print("[DEBUG][API_AGENT] Parsed API plan:", json.dumps(api_plan.model_dump(), indent=2))
    return api_plan

def chatter_agent_answer(
    config: ChatConfig,
    user_query: str,
    entity_result: dict,
    parser_plan: dict,
    api_plan: dict | None = None,
    api_result_slim: dict | None = None,
    api_result_full: dict | None = None,
    error_context: dict | None = None,
    reporter_summary: dict | None = None,
    graph_plan: dict | None = None,
    graph_result: dict | None = None,
    log_dir: str | None = None,
    session: "SessionState | SessionStateProxy | None" = None,
) -> str:
    """
    Unified chatter agent: produces a narrative answer for search, reporter, and graph results,
    followed by a structured debug JSON block showing key inter-agent data.
    Pass reporter_summary for reporter mode, graph_plan+graph_result for graph mode,
    or API params for search/refine mode.
    Falls back to informative messages when the LLM hits rate or connection limits.
    """
    is_reporter = reporter_summary is not None
    is_graph = graph_plan is not None

    # ---------- Build mode-appropriate debug JSON ----------
    if is_graph:
        debug_info = {
            "entity": {
                "sampletypes": entity_result.get("sampletypes", []),
                "assays": entity_result.get("assays", []),
                "projects": entity_result.get("projects", []),
            },
            "parser": {
                "mode": parser_plan.get("mode"),
                "intent_summary": parser_plan.get("intent_summary"),
            },
            "graph": {
                "cypher": graph_plan.get("cypher"),
                "explanation": graph_plan.get("explanation"),
                "parameters": graph_plan.get("parameters"),
            },
            "neo4j": {
                "ok": (graph_result or {}).get("ok"),
                "count": (graph_result or {}).get("count"),
                "error": (graph_result or {}).get("error"),
            },
        }
    elif is_reporter:
        debug_info = {
            "entity": {
                "sampletypes": entity_result.get("sampletypes", []),
                "assays": entity_result.get("assays", []),
                "projects": entity_result.get("projects", []),
            },
            "parser": {
                "mode": parser_plan.get("mode"),
                "report_mode": parser_plan.get("report_mode"),
                "report_type": parser_plan.get("report_type"),
            },
        }
    else:
        search_total = None
        search_preview_count = 0
        if isinstance(api_result_full, dict):
            api_data_full = api_result_full.get("data")
            if isinstance(api_data_full, dict):
                search_total = (
                    api_data_full.get("total")
                    or api_data_full.get("total_samples")
                    or api_data_full.get("total_nodes")
                )
                preview_items = (
                    api_data_full.get("rows")
                    if isinstance(api_data_full.get("rows"), list)
                    else api_data_full.get("nodes")
                    if isinstance(api_data_full.get("nodes"), list)
                    else api_data_full.get("data")
                    if isinstance(api_data_full.get("data"), list)
                    else []
                )
                search_preview_count = len(preview_items)

        debug_info = {
            "entity": {
                "sampletypes": entity_result.get("sampletypes", []),
                "assays": entity_result.get("assays", []),
                "projects": entity_result.get("projects", []),
                "filters": parser_plan.get("filters", {}),
            },
            "parser": {
                "mode": parser_plan.get("mode"),
                "target_endpoint": parser_plan.get("target_endpoint"),
                "endpoint_candidates": parser_plan.get("endpoint_candidates", []),
            },
            "api_agent": {
                "requestBody": (api_plan or {}).get("requestBody") or {},
                "queryParameters": (api_plan or {}).get("queryParameters") or {},
            },
        }
    debug_json = json.dumps(debug_info, indent=2)

    # ---------- Build messages ----------
    if is_graph:
        graph_plan_json = json.dumps(graph_plan, separators=(",", ":"))
        records = ((graph_result or {}).get("data") or [])[:20]
        preview_json = json.dumps(records, separators=(",", ":"), default=str)
        count = (graph_result or {}).get("count", 0)
        ok = (graph_result or {}).get("ok", False)
        error_str = (graph_result or {}).get("error", "")
        user_content = (
            "User question:\n"
            f"{user_query}\n\n"
            "Graph query plan (Cypher + explanation + parameters):\n"
            f"{graph_plan_json}\n\n"
            f"Query status: {'success' if ok else 'failed'}\n"
            f"Records returned: {count}\n"
            f"{'Error: ' + error_str if error_str else ''}\n\n"
            "Result preview (up to 20 records):\n"
            f"{preview_json}\n\n"
            "MODE: graph_query — Summarize what the graph query found."
        )
        log_label = "chatter_graph"
    elif is_reporter:
        summary_json = json.dumps(reporter_summary, separators=(",", ":"))
        user_content = (
            "User question:\n"
            f"{user_query}\n\n"
            "Reporter summary JSON:\n"
            f"{summary_json}\n\n"
            "MODE: reporter — Write a concise narrative summary using ONLY the reporter summary JSON."
        )
        log_label = "chatter_report"
    else:
        plan_json = json.dumps(parser_plan, separators=(",", ":"))
        api_plan_json = json.dumps(api_plan, separators=(",", ":"))
        api_json = json.dumps(api_result_slim, separators=(",", ":"))
        error_json = json.dumps(error_context, separators=(",", ":")) if error_context else ""
        user_content = (
            "User question:\n"
            f"{user_query}\n\n"
            "Parser plan JSON:\n"
            f"{plan_json}\n\n"
            "API plan JSON:\n"
            f"{api_plan_json}\n\n"
            "NExtSEEK API result JSON (slimmed):\n"
            f"{api_json}\n\n"
            "Deterministic result stats:\n"
            f"total_matches={search_total}\n"
            f"preview_items={search_preview_count}\n\n"
            f"Error context (if any):\n{error_json}\n\n"
            "MODE: search — Answer the question using ONLY the information from the API result.\n"
            "Treat the deterministic result stats above as authoritative for whether results exist."
        )
        log_label = "chatter"

    from .chat_memory import history_block

    messages: list[dict] = [{"role": "system", "content": config.CHATTER_SYSTEM_PROMPT}]
    chat_history = history_block(session) if session is not None else ""
    if chat_history:
        messages.append({"role": "system", "content": chat_history})
    if not is_reporter and not is_graph and error_context:
        messages.append({
            "role": "system",
            "content": (
                "If error_context is present (API failure), summarize the likely cause and request any missing "
                "values explicitly (e.g., project IDs). Offer a short placeholder the user can fill, without inventing "
                "values. Keep the answer concise."
            ),
        })
    messages.append({"role": "user", "content": user_content})

    # ---------- LLM Call ----------
    chatter_client, chatter_model, chatter_budget = config.get_agent_model("chatter")
    try:
        resp = chatter_client.chat(
            model=chatter_model,
            temperature=0,
            messages=messages,
            thinking_budget=chatter_budget,
        )
        log_usage(resp, "CHATTER")
        answer = resp.content or ""
        print("[DEBUG][CHATTER] Raw answer:", answer)
        log_prompt(
            log_dir or config.LOG_DIR,
            log_label,
            {
                "user_query": user_query,
                "messages": messages,
                "response": answer,
            },
        )

    except LLMAPIConnectionError as e:
        print("[DEBUG][CHATTER] APIConnectionError:", repr(e))
        if is_reporter:
            return "Reporter completed, but had a connection issue summarizing the results."
        if is_graph:
            count = (graph_result or {}).get("count", 0)
            return f"Graph query returned {count} record(s), but had a connection issue summarizing the results."
        data = (api_result_slim or {}).get("data", {})
        total = data.get("total") if isinstance(data, dict) else None
        return (
            "I successfully queried NExtSEEK, but had a connection issue talking to the LLM to "
            "summarize the results.\n\n"
            f"Basic info:\n- endpoint: {parser_plan.get('target_endpoint')}\n"
            f"- intent: {parser_plan.get('intent_summary')}\n"
            f"- total matches: {total if total is not None else 'unknown'}\n\n"
            "You can re-run the query or refine it (e.g. by project or study) to narrow the results."
        )
    except LLMRateLimitError as e:
        print("[DEBUG][CHATTER] RateLimitError:", repr(e))
        if is_reporter:
            return "Reporter completed, but the summarization call hit the model's token/throughput limit."
        if is_graph:
            count = (graph_result or {}).get("count", 0)
            return f"Graph query returned {count} record(s), but hit the rate limit while summarizing. Try again shortly."
        data = (api_result_slim or {}).get("data", {})
        total = data.get("total") if isinstance(data, dict) else None
        return (
            "I pulled the NExtSEEK results, but the summarization call hit the model's token/throughput limit. "
            "Try again with a narrower query or after a short pause.\n\n"
            f"Basic info:\n- endpoint: {parser_plan.get('target_endpoint')}\n"
            f"- intent: {parser_plan.get('intent_summary')}\n"
            f"- total matches: {total if total is not None else 'unknown'}"
        )

    # ---------- Clean answer ----------
    answer_no_links = re.sub(r"https?://\S+", "", answer)
    answer_no_links = re.sub(r"\n{3,}", "\n\n", answer_no_links).strip()

    # ---------- Debug block ----------
    debug_block = (
        "**Debug info**\n\n"
        "```json\n"
        f"{debug_json}\n"
        "```"
    )

    final_answer = answer_no_links + "\n\n" + debug_block
    print("[DEBUG][CHATTER] Final answer (post-processed):", final_answer)
    return final_answer


def _strip_python_code_fences(code: str) -> str:
    """Remove common markdown code fences from generated Python snippets."""
    text = (code or "").strip()
    match = re.match(r"^```(?:python)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _load_memory_json_payload(result_bundle: dict) -> tuple[str, Any, list[tuple[str, str]]]:
    """
    Load the most relevant JSON payload for memory analysis.
    Prefers canonical in-memory payloads, then saved JSON artifacts, then inline bundle data.
    """
    file_entries = collect_bundle_files(result_bundle)
    memory_payload = result_bundle.get("memory_payload")
    if isinstance(memory_payload, dict) and memory_payload:
        return "bundle memory_payload", strip_html_recursive(memory_payload), file_entries

    def _memory_file_priority(entry: tuple[str, str]) -> int:
        label, path = entry
        text = f"{label} {path}".lower()
        if "api result" in text or "/api/" in text:
            return 0
        if "graph" in text:
            return 1
        if "report" in text and "plan debug" not in text:
            return 2
        if "plan debug" in text:
            return 9
        return 5

    for label, path in sorted(file_entries, key=_memory_file_priority):
        if not str(path).lower().endswith(".json"):
            continue
        try:
            return label, load_json_for_memory(path), file_entries
        except Exception as e:
            print(f"[DEBUG][MEMORY_CODER] Failed to load JSON artifact {path}: {e!r}")

    step_results = result_bundle.get("step_results") or {}
    if isinstance(step_results, dict):
        for step_id, sr in step_results.items():
            if not isinstance(sr, dict) or not sr.get("ok"):
                continue
            output = sr.get("output") or {}
            if not isinstance(output, dict):
                continue
            rows = output.get("data")
            if isinstance(rows, list):
                return (
                    f"inline planner step {step_id} rows",
                    {
                        "data": {
                            "rows": strip_html_recursive(rows),
                            "total": output.get("count", len(rows)),
                        },
                        "api_plan": output.get("api_plan"),
                        "endpoint": output.get("endpoint"),
                        "tool": sr.get("tool"),
                    },
                    file_entries,
                )
            reply = output.get("reply")
            if isinstance(reply, str) and reply.strip():
                return (
                    f"inline planner step {step_id} reply",
                    {"reply": reply, "count": output.get("count"), "tool": sr.get("tool")},
                    file_entries,
                )

    inline = (
        result_bundle.get("memory_payload")
        or result_bundle.get("model_outputs", {}).get("memory_payload")
        or result_bundle.get("graph_result")
        or result_bundle.get("api_result_full")
        or result_bundle.get("api_result_slim")
        or {}
    )
    return "inline bundle data", strip_html_recursive(inline), file_entries


def memory_coder_agent(
    config: ChatConfig,
    *,
    original_query: str,
    user_query: str,
    data_profile: dict,
    log_dir: str | None = None,
) -> MemoryCoderOutput:
    """Ask the memory coder to write a deterministic extraction snippet for prior JSON results."""
    profile_text = json.dumps(data_profile, indent=2, default=str)
    if len(profile_text) > 50000:
        profile_text = profile_text[:50000] + "\n...[profile truncated]"

    messages = [
        {"role": "system", "content": config.MEMORY_CODER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Original search question:\n"
                f"{original_query}\n\n"
                "Follow-up question about those results:\n"
                f"{user_query}\n\n"
                "JSON skeleton and examples:\n"
                f"{profile_text}\n\n"
                "Write Python extraction code that computes the answer from the full `data` object. "
                "Assign the final JSON-serializable answer to `result`."
            ),
        },
    ]

    coder_client, coder_model, coder_budget = config.get_agent_model("memory_coder")
    coder_output = call_llm_structured(
        config=config,
        prompt=user_query,
        model=MemoryCoderOutput,
        system=config.MEMORY_CODER_SYSTEM_PROMPT,
        messages=messages,
        model_name=coder_model,
        temperature=0,
        log_label="memory_coder",
        thinking_budget=coder_budget,
        client=coder_client,
    )
    coder_output = coder_output.model_copy(update={"extraction_code": _strip_python_code_fences(coder_output.extraction_code)})
    log_prompt(
        log_dir or config.LOG_DIR,
        "memory_coder",
        {
            "user_query": user_query,
            "messages": messages,
            "response": coder_output.model_dump(),
        },
    )
    return coder_output


def _format_memory_coder_answer(
    config: ChatConfig,
    *,
    original_query: str,
    user_query: str,
    source_label: str,
    coder_output: MemoryCoderOutput,
    computed_result: dict,
    row_count: int | None,
    log_dir: str | None = None,
) -> str:
    """Use the chatter route to turn deterministic memory-code output into a user-facing answer."""
    computed_text = json.dumps(computed_result, indent=2, default=str)
    if len(computed_text) > 60000:
        computed_text = computed_text[:60000] + "\n...[computed result truncated]"

    messages = [
        {"role": "system", "content": config.CHATTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "MODE: memory_computed\n\n"
                "You are answering a follow-up question using a deterministic computation over prior NExtSEEK results. "
                "Use ONLY the computed result below. Do not claim you queried an external database.\n\n"
                f"Original search question:\n{original_query}\n\n"
                f"Follow-up question:\n{user_query}\n\n"
                f"Source data label:\n{source_label}\n"
                f"Rows convenience count, if known:\n{row_count}\n\n"
                f"Computation description:\n{coder_output.result_description}\n"
                f"Fields used:\n{json.dumps(coder_output.fields_used, default=str)}\n"
                f"Coder notes:\n{coder_output.notes}\n\n"
                f"Computed result JSON:\n{computed_text}\n\n"
                "Write a concise answer. If the computed result indicates missing fields or no matches, say that directly."
            ),
        },
    ]

    chatter_client, chatter_model, chatter_budget = config.get_agent_model("chatter")
    resp = chatter_client.chat(
        model=chatter_model,
        temperature=0,
        messages=messages,
        thinking_budget=chatter_budget,
    )
    log_usage(resp, "MEMORY_CODER_CHATTER")
    answer = resp.content
    log_prompt(
        log_dir or config.LOG_DIR,
        "memory_coder_chatter",
        {
            "user_query": user_query,
            "messages": messages,
            "response": answer,
        },
    )
    return answer


def _legacy_memory_agent_answer(config: ChatConfig, user_query: str, result_bundle: dict, log_dir: str | None = None) -> str:
    """
    Existing fallback path: pass saved result content directly to the memory model.
    """
    orig_query = result_bundle.get("user_query", "")
    memory_client, memory_model, memory_budget = config.get_agent_model("memory")

    # --- Build result context from saved files ---
    file_entries = collect_bundle_files(result_bundle)
    context_parts: list[str] = []
    for label, path in file_entries:
        try:
            content = load_file_for_memory(path)
            context_parts.append(f"=== {label} ===\n{content}")
            print(f"[DEBUG][MEMORY] Loaded file: {label} ({path}), {len(content)} chars")
        except Exception as e:
            print(f"[DEBUG][MEMORY] Failed to read file {path}: {repr(e)}")

    # Fallback: use inline bundle data if no files were found
    if not context_parts:
        inline = (
            result_bundle.get("graph_result")
            or result_bundle.get("api_result_full")
            or result_bundle.get("api_result_slim")
            or {}
        )
        context_parts.append(json.dumps(inline, indent=2))
        print(f"[DEBUG][MEMORY] No files found; using inline bundle data ({len(context_parts[0])} chars)")

    result_context = "\n\n".join(context_parts)

    messages = [
        {"role": "system", "content": config.MEMORY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Original search question:\n"
                f"{orig_query}\n\n"
                "Follow-up user question (about those results):\n"
                f"{user_query}\n\n"
                "Prior result data (from saved files):\n"
                f"{result_context}\n\n"
                "Answer the follow-up question using ONLY this result data."
            ),
        },
    ]

    try:
        resp = memory_client.chat(
            model=memory_model,
            temperature=0,
            messages=messages,
            thinking_budget=memory_budget,
        )
        log_usage(resp, "MEMORY")
        answer = resp.content
        print("[DEBUG][MEMORY][MEMORY] Answer:", answer)
        log_prompt(
            log_dir or config.LOG_DIR,
            "memory",
            {
                "user_query": user_query,
                "messages": messages,
                "response": answer,
                "bundle_id": result_bundle.get("id"),
                "files_loaded": [label for label, _ in file_entries],
            },
        )
        return answer
    except LLMAPIConnectionError as e:
        print("[DEBUG][MEMORY] APIConnectionError:", repr(e))

    # Degraded response if LLM call fails
    if isinstance(result_bundle.get("graph_result"), dict):
        count = result_bundle["graph_result"].get("count", "unknown")
        return (
            "I have the prior graph query results loaded, but had a connection issue. "
            f"The result contained {count} records."
        )
    return (
        "I have the prior results loaded, but had a connection issue. "
        "Please try your follow-up question again."
    )


def memory_agent_answer(config: ChatConfig, user_query: str, result_bundle: dict, log_dir: str | None = None) -> str:
    """
    Answer follow-up questions about prior results.
    Fast path: profile JSON shape, generate guarded Python extraction code, execute it locally,
    and ask chatter to phrase the computed result. Any failure falls back to the legacy memory path.
    """
    orig_query = result_bundle.get("user_query", "")
    try:
        source_label, memory_data, file_entries = _load_memory_json_payload(result_bundle)
        profile = build_memory_data_profile(memory_data, sample_limit=5)
        row_count = None
        if isinstance(memory_data, dict):
            rows = ((memory_data.get("data") or {}).get("rows") if isinstance(memory_data.get("data"), dict) else None)
            if isinstance(rows, list):
                row_count = len(rows)

        coder_output = memory_coder_agent(
            config,
            original_query=orig_query,
            user_query=user_query,
            data_profile=profile,
            log_dir=log_dir,
        )
        computed_result = execute_memory_code(coder_output.extraction_code, memory_data)

        artifact_entry = None
        try:
            # Each follow-up gets a unique filename so multiple memory queries against
            # the same source bundle don't overwrite each other's debug trail.
            # Format: memory_coder_bundle_<bid>_<UTC-ts>.json
            from datetime import datetime, timezone

            artifact_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            artifact_name = f"memory_coder_bundle_{result_bundle.get('id', 'latest')}_{artifact_ts}.json"
            store = ArtifactStore(log_dir or config.LOG_DIR)
            artifact_entry = store.write_json(
                key=f"memory_coder_result_{artifact_ts}",
                label="Memory coder execution result",
                filename=artifact_name,
                payload={
                    "bundle_id": result_bundle.get("id"),
                    "source_label": source_label,
                    "original_query": orig_query,
                    "followup_query": user_query,
                    "profile": profile,
                    "memory_coder": coder_output.model_dump(),
                    "computed_result": computed_result,
                    "row_count": row_count,
                    "files_loaded": [label for label, _ in file_entries],
                    "artifact_ts": artifact_ts,
                },
                kind="memory",
                bundle_id=result_bundle.get("id"),
            )
            print(f"[DEBUG][MEMORY_CODER] Saved artifact: {artifact_entry}")
            if artifact_entry:
                existing_files = result_bundle.setdefault("files", [])
                if isinstance(existing_files, list) and artifact_entry not in existing_files:
                    existing_files.append(artifact_entry)
                result_bundle["memory_coder_artifact"] = artifact_entry
        except Exception as artifact_err:
            print(f"[DEBUG][MEMORY_CODER] Failed to save artifact: {artifact_err!r}")

        answer = _format_memory_coder_answer(
            config,
            original_query=orig_query,
            user_query=user_query,
            source_label=source_label,
            coder_output=coder_output,
            computed_result=computed_result,
            row_count=row_count,
            log_dir=log_dir,
        )
        log_prompt(
            log_dir or config.LOG_DIR,
            "memory_coder_execution",
            {
                "user_query": user_query,
                "bundle_id": result_bundle.get("id"),
                "source_label": source_label,
                "coder_output": coder_output.model_dump(),
                "computed_result": computed_result,
                "artifact": artifact_entry,
                "answer": answer,
            },
        )
        return answer
    except Exception as e:
        print(f"[DEBUG][MEMORY_CODER] Fast path failed; falling back to legacy memory agent: {e!r}")
        try:
            return _legacy_memory_agent_answer(config, user_query, result_bundle, log_dir=log_dir)
        except Exception as legacy_err:
            print(f"[DEBUG][MEMORY] Legacy fallback failed: {legacy_err!r}")
            return (
                "I found the prior results, but the follow-up analysis failed before I could produce a reliable answer. "
                f"Fast-path error: {e!r}; fallback error: {legacy_err!r}"
            )


# ======================================================
# System Agent
# ======================================================

def system_agent(
    config: ChatConfig,
    user_query: str,
    entity_result: EntityAgentOutput | dict,
    parser_plan: ParserPlan | dict,
) -> SystemAgentOutput:
    """
    Answer meta questions about the system: capabilities, catalog entity details, and search options.
    Determines its own sub-mode (get_capabilities, get_entities, get_searches) from context.
    Returns a SystemAgentOutput with a narrative answer ready for direct display.
    Falls back to a canned capabilities answer on failure.
    """
    print("\n[DEBUG][SYSTEM] User query:", user_query)

    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result
    plan_dict = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else (parser_plan or {})

    # Build full entity details for any resolved catalog codes using pre-built maps from config
    entity_details: dict = {}
    for st in entity_dict.get("sampletypes", []):
        code = st.get("code")
        if code and code in config.FULL_SAMPLETYPES_MAP:
            entity_details[code] = config.FULL_SAMPLETYPES_MAP[code]
    for assay in entity_dict.get("assays", []):
        code = assay.get("code")
        if code and code in config.FULL_ASSAYS_MAP:
            entity_details[code] = config.FULL_ASSAYS_MAP[code]
    for project_name in entity_dict.get("projects", []):
        if project_name and project_name in config.FULL_PROJECTS_MAP:
            entity_details[project_name] = config.FULL_PROJECTS_MAP[project_name]

    caps_doc = config.CAPABILITIES_DOC or "(No capabilities document loaded — describe general NExtSEEK capabilities.)"
    endpoints_json = json.dumps(config.MIN_API_ENDPOINTS, indent=2)
    schema_json = json.dumps(config.NEO4J_SCHEMA, indent=2) if config.NEO4J_SCHEMA else "{}"
    entity_details_json = json.dumps(entity_details, indent=2) if entity_details else "{}"

    messages = [
        {"role": "system", "content": config.SYSTEM_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": f"CAPABILITIES_DOCUMENT:\n{caps_doc}"},
        {"role": "system", "content": f"ENDPOINT_CATALOG:\n{endpoints_json}"},
        {"role": "system", "content": f"GRAPH_SCHEMA:\n{schema_json}"},
        {"role": "system", "content": f"ENTITY_RESULT (from entity agent):\n{json.dumps(entity_dict, indent=2)}"},
        {"role": "system", "content": f"ENTITY_DETAILS (full catalog data for resolved entities):\n{entity_details_json}"},
        {"role": "system", "content": f"PARSER_INTENT:\n{json.dumps(plan_dict, indent=2)}"},
        {"role": "user", "content": user_query},
    ]

    sys_client, sys_model, sys_budget = config.get_agent_model("system")
    try:
        result = call_llm_structured(
            config=config,
            prompt=user_query,
            model=SystemAgentOutput,
            messages=messages,
            model_name=sys_model,
            temperature=0,
            log_label="system_agent",
            thinking_budget=sys_budget,
            client=sys_client,
        )
        print(f"[DEBUG][SYSTEM] mode={result.mode}, entities={result.entities_consulted}")
        return result
    except Exception as e:
        print(f"[DEBUG][SYSTEM] system_agent failed: {e!r}")
        return SystemAgentOutput(
            mode="get_capabilities",
            narrative=(
                f"I encountered an issue answering your question.\n\n"
                f"Parser intent: {plan_dict.get('intent_summary', '')}"
            ),
            entities_consulted=[],
            notes=f"error: {e}",
        )


def graph_agent(
    config: ChatConfig,
    user_query: str,
    entity_result: EntityAgentOutput | dict,
    parser_plan: ParserPlan | dict | None = None,
    retry_context: str | None = None,
) -> GraphAgentPlan:
    """
    Generate a Cypher query for the given user query using the live graph schema.
    Receives resolved entities from the Entity Agent and optional parser filters,
    then calls the LLM to produce a GraphAgentPlan (cypher + explanation + parameters).
    Falls back to an empty plan on structured-output failure.
    """
    print("\n[DEBUG][GRAPH] User query:", user_query)

    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result
    plan_dict = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else (parser_plan or {})

    schema_json = json.dumps(config.NEO4J_SCHEMA, indent=2) if config.NEO4J_SCHEMA else "{}"

    # Conditionally include protocol vocabulary if query mentions protocol-related terms
    protocol_keywords = ("protocol", "method", "procedure", "technique")
    include_protocol = any(kw in user_query.lower() for kw in protocol_keywords)
    protocol_context = ""
    if include_protocol and getattr(config, "PROTOCOL_SCHEMA", None):
        protocol_titles = config.PROTOCOL_SCHEMA.get("protocol_titles", [])
        if protocol_titles:
            protocol_context = "PROTOCOL VOCABULARY (DERIVED_FROM.protocol_title values):\n" + json.dumps(protocol_titles, indent=2)
            print(f"[DEBUG][GRAPH] Including protocol vocabulary ({len(protocol_titles)} titles)")

    # Conditionally include assay-sample connections when query involves assays or data types
    assay_conn_keywords = ("assay", "sequencing", "cytometry", "spectrometry", "imaging", "data", "processed", "associated", "underwent", "via", "collection", "extraction")
    include_assay_conn = any(kw in user_query.lower() for kw in assay_conn_keywords)
    assay_conn_context = ""
    if include_assay_conn and getattr(config, "ASSAY_SAMPLE_CONNECTIONS", None):
        connections = config.ASSAY_SAMPLE_CONNECTIONS.get("connections", [])
        if connections:
            assay_conn_context = "ASSAY-SAMPLE CONNECTIONS (assay → parent_type → child_type, use to determine which side a sample type sits on for a given assay):\n" + json.dumps(connections, indent=2)
            print(f"[DEBUG][GRAPH] Including assay-sample connections ({len(connections)} entries)")

    # Use full parser plan when available (contains resolved entities + routing intent + filters)
    # Fall back to raw entity dict when called without a parser plan
    if plan_dict:
        upstream_context = "PARSER PLAN (from Parser Agent — routing intent + resolved entities + filters):\n" + json.dumps(plan_dict, indent=2)
    else:
        entity_json = json.dumps(entity_dict, indent=2)
        upstream_context = "RESOLVED ENTITIES (from Entity Agent — no parser plan available):\n" + entity_json

    messages = [
        {"role": "system", "content": config.GRAPH_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": "GRAPH SCHEMA (node labels, relationships, properties, vocabulary):\n" + schema_json},
        {"role": "system", "content": upstream_context},
    ]
    if protocol_context:
        messages.append({"role": "system", "content": protocol_context})
    if assay_conn_context:
        messages.append({"role": "system", "content": assay_conn_context})
    if retry_context:
        messages.append({"role": "system", "content": retry_context})
    messages.append({"role": "user", "content": user_query})

    graph_client, graph_model, graph_budget = config.get_agent_model("graph")
    try:
        result = call_llm_structured(
            config=config,
            prompt=user_query,
            model=GraphAgentPlan,
            system=config.GRAPH_AGENT_SYSTEM_PROMPT,
            messages=messages,
            model_name=graph_model,
            temperature=0,
            response_format={"type": "json_object"},
            log_label="graph_agent",
            thinking_budget=graph_budget,
            client=graph_client,
        )
        print(f"[DEBUG][GRAPH] Generated cypher: {result.cypher!r}")
        return result
    except Exception as e:
        print(f"[DEBUG][GRAPH] graph_agent failed: {e!r}")
        return GraphAgentPlan(cypher="", explanation=f"Graph agent error: {e}", parameters={})

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


def chatter_agent_plan(
    config: ChatConfig,
    user_query: str,
    plan: PlannerOutput,
    step_results: dict[int, dict],
    log_dir: str | None = None,
    session: "SessionState | SessionStateProxy | None" = None,
) -> str:
    """
    Chatter variant for the planner pipeline. Receives the full plan + all step results
    and produces a narrative weaving together what was found at each step.
    Appends a debug block with the plan JSON and per-step ok/count summary.
    """
    # Build step summaries for the debug block
    step_summary = []
    for step in plan.steps:
        sr = step_results.get(step.step_id, {})
        step_summary.append({
            "step_id": step.step_id,
            "tool": step.tool,
            "combine_mode": step.combine_mode,
            "ok": sr.get("ok"),
            "count": (sr.get("output") or {}).get("count"),
            "error": sr.get("error"),
        })
    intersection = step_results.get("intersection")
    if intersection:
        step_summary.append({
            "step_id": "intersection",
            "tool": "intersection",
            "combine_mode": "intersect",
            "ok": True,
            "count": (intersection.get("output") or {}).get("count"),
            "error": None,
        })

    debug_info = {
        "planner": {
            "intent_summary": plan.intent_summary,
            "step_count": len(plan.steps),
            "notes": plan.notes,
        },
        "steps": step_summary,
    }
    debug_json = json.dumps(debug_info, indent=2)

    # Build a step-by-step summary for the narrative prompt
    steps_for_prompt = []
    for step in plan.steps:
        sr = step_results.get(step.step_id, {})
        output = sr.get("output") or {}
        count = output.get("count", 0)
        ok = sr.get("ok", False)
        # Build a meaningful preview depending on tool type
        if step.tool == "reporter":
            reporter_summary = output.get("reporter_summary") or {}
            data_preview: Any = reporter_summary if reporter_summary else output.get("reporter_plan", {})
        elif isinstance(output.get("reply"), str) and output.get("reply", "").strip():
            data_preview = {"reply": output.get("reply")}
        else:
            data = output.get("data", [])
            data_preview = data[:10] if isinstance(data, list) else data
        steps_for_prompt.append(
            f"Step {step.step_id} [{step.tool}] combine_mode={step.combine_mode}: ok={ok}, count={count}\n"
            f"  query: {_step_query(step)}\n"
            f"  preview: {json.dumps(data_preview, default=str)[:2000]}"
        )
    if step_results.get("intersection"):
        isr = step_results["intersection"].get("output") or {}
        idata = isr.get("data", [])
        steps_for_prompt.append(
            f"INTERSECTION RESULT: count={isr.get('count', 0)}\n"
            f"  preview: {json.dumps(idata[:5], default=str)[:800]}"
        )

    steps_text = "\n\n".join(steps_for_prompt)

    from .chat_memory import history_block

    chatter_client, chatter_model, chatter_budget = config.get_agent_model("chatter")
    messages: list[dict] = [
        {"role": "system", "content": config.CHATTER_SYSTEM_PROMPT},
    ]
    chat_history = history_block(session) if session is not None else ""
    if chat_history:
        messages.append({"role": "system", "content": chat_history})
    messages.extend([
        {
            "role": "system",
            "content": (
                "You are summarizing the results of a multi-step plan.\n\n"
                f"PLAN INTENT: {plan.intent_summary}\n\n"
                f"STEP RESULTS:\n{steps_text}"
            ),
        },
        {
            "role": "system",
            "content": f"```json\n{debug_json}\n```",
        },
        {"role": "user", "content": user_query},
    ])

    try:
        resp = chatter_client.chat(
            model=chatter_model,
            temperature=0.3,
            messages=messages,
            thinking_budget=chatter_budget,
        )
        narrative = resp.content or "(no response)"
    except Exception as e:
        print(f"[DEBUG][PLAN_CHATTER] failed: {e!r}")
        narrative = (
            f"I executed a {len(plan.steps)}-step plan for your query: {plan.intent_summary}.\n\n"
            + "\n".join(
                f"Step {s['step_id']} ({s['tool']}): {'✓' if s['ok'] else '✗'} "
                f"— {s['count'] or 0} result(s)"
                for s in step_summary
            )
        )

    log_prompt(
        log_dir or config.LOG_DIR,
        "plan_chatter",
        {"messages": messages, "response": narrative},
    )
    return f"{narrative}\n\n```json\n{debug_json}\n```"


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


def _pipeline_groupby_resolution(
    *,
    config: "ChatConfig",
    pipeline_key: str,
    group_by_phrase: str,
    metadata_summary: dict,
    user_hint: str = "",
) -> "GroupByResolution":
    """Third LLM step of pipeline_agent: function-calling loop to resolve a
    group-by phrase to a canonical metadata field.

    Uses up to MAX_ITER=5 iterations of tool_use/tool_result exchange.
    The LLM MUST call finalize_groupby before the cap; otherwise raises RuntimeError.
    """
    from .schemas.pipeline import GroupByResolution, FieldRef
    from .pipeline_tools import GROUPBY_TOOL_SCHEMAS, dispatch_groupby_tool_call

    MAX_ITER = 5

    prompt_template = config._load_prompt("pipeline_agent_groupby.txt")
    system_prompt = (
        prompt_template
        .replace("{group_by_phrase}", group_by_phrase or "")
        .replace("{pipeline_key}", pipeline_key or "")
        .replace("{user_hint}", user_hint or "")
    )

    client, model_name, _budget = config.get_agent_model("pipeline_groupby")

    if not callable(getattr(client, "chat_with_tools", None)):
        raise RuntimeError(
            f"[PIPELINE_GROUPBY] Resolved LLM client {type(client).__name__!r} does not "
            "support chat_with_tools; pipeline_groupby must be mapped to a "
            "function-calling-capable model."
        )

    messages: list[dict] = [
        {"role": "user", "content": f"Resolve group-by phrase: {group_by_phrase!r}"}
    ]

    print(
        f"[DEBUG][PIPELINE_GROUPBY] enter phrase={group_by_phrase!r} "
        f"hint={user_hint!r} model={model_name!r}"
    )

    try:
        for i in range(MAX_ITER):
            print(f"[DEBUG][PIPELINE_GROUPBY] iter={i} messages_len={len(messages)}")
            resp = client.chat_with_tools(
                messages=messages,
                tools=GROUPBY_TOOL_SCHEMAS,
                system=system_prompt,
                model=model_name,
            )
            stop_reason = resp.get("stop_reason")
            print(
                f"[DEBUG][PIPELINE_GROUPBY] iter={i} stop_reason={stop_reason!r} "
                f"content_blocks={len(resp.get('content', []))}"
            )

            if stop_reason != "tool_use":
                raise RuntimeError(
                    f"[PIPELINE_GROUPBY] LLM stopped without calling finalize_groupby "
                    f"(stop_reason={stop_reason!r})"
                )

            tool_use_blocks = [
                b for b in resp.get("content", []) if b.get("type") == "tool_use"
            ]
            assistant_content = resp.get("content", [])
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results: list[dict] = []
            finalize_payload: dict | None = None

            for block in tool_use_blocks:
                name = block.get("name")
                tool_input = block.get("input", {})
                tool_use_id = block.get("id")

                if name == "finalize_groupby":
                    finalize_payload = tool_input
                    print(
                        f"[DEBUG][PIPELINE_GROUPBY] finalize_groupby "
                        f"requires_clarification={tool_input.get('requires_clarification')} "
                        f"field={tool_input.get('field_name')!r}"
                    )
                    continue

                print(
                    f"[DEBUG][PIPELINE_GROUPBY] dispatching tool={name!r} "
                    f"input_keys={list(tool_input.keys()) if isinstance(tool_input, dict) else 'non-dict'}"
                )
                result = dispatch_groupby_tool_call(
                    name=name,
                    tool_input=tool_input,
                    bundle=metadata_summary,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result if isinstance(result, str) else str(result),
                })

            if finalize_payload is not None:
                requires_clarification = bool(finalize_payload.get("requires_clarification"))
                if requires_clarification:
                    raw_candidates = finalize_payload.get("candidates") or []
                    candidates = [
                        FieldRef(
                            sample_type=c.get("sample_type", ""),
                            field_name=c.get("field_name", ""),
                        )
                        for c in raw_candidates
                    ]
                    return GroupByResolution(
                        requires_clarification=True,
                        candidates=candidates,
                        clarifying_question=finalize_payload.get("clarifying_question") or "",
                        rationale=finalize_payload.get("rationale") or "",
                    )

                field_ref = FieldRef(
                    sample_type=finalize_payload.get("sample_type", ""),
                    field_name=finalize_payload.get("field_name", ""),
                )
                return GroupByResolution(
                    field=field_ref,
                    distinct_values=finalize_payload.get("distinct_values") or [],
                    rationale=finalize_payload.get("rationale") or "",
                )

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(
            f"[PIPELINE_GROUPBY] LLM did not call finalize_groupby within "
            f"{MAX_ITER} iterations."
        )

    except RuntimeError:
        raise
    except Exception as exc:
        print(f"[DEBUG][PIPELINE_GROUPBY] unexpected error: {exc!r}")
        raise RuntimeError(
            f"[PIPELINE_GROUPBY] Tool-use loop failed: {exc!r}"
        ) from exc


def _pipeline_edit_step(
    *,
    config: "ChatConfig",
    pipeline_key: str,
    current_rows: list[dict],
    user_text: str,
) -> "EditDiffOutput":
    """Fourth LLM step of pipeline_agent. Applies a free-text edit message
    to the in-memory samplesheet rows."""
    import json as _json
    from .schemas.pipeline import EditDiffOutput
    from .seqera.catalog import NFCORE_PIPELINE_CATALOG

    catalog_entry = NFCORE_PIPELINE_CATALOG.get(pipeline_key, {})
    required_columns = catalog_entry.get("required_columns") or []

    prompt = (
        config._load_prompt("pipeline_agent_edit.txt")
        .replace("{pipeline_key}", pipeline_key)
        .replace("{current_rows_json}", _json.dumps(current_rows, indent=2))
        .replace("{required_columns}", _json.dumps(required_columns))
        .replace("{user_text}", user_text or "")
    )

    print(f"[DEBUG][PIPELINE_EDIT] pipeline={pipeline_key} rows={len(current_rows)} edit={user_text!r}")

    client, model_name, budget = config.get_agent_model("pipeline_edit")
    try:
        return call_llm_structured(
            config,
            prompt,
            EditDiffOutput,
            system="You are the pipeline_agent's edit step. Return only the JSON object.",
            model_name=model_name,
            temperature=0,
            log_label="pipeline_edit",
            usage_label="PIPELINE_EDIT",
            thinking_budget=budget,
            client=client,
        )
    except Exception as exc:
        print(f"[DEBUG][PIPELINE_EDIT] LLM call failed: {exc!r}")
        return EditDiffOutput(
            action="ask",
            ask_reply=f"Couldn't apply that edit (LLM error: {exc!r}). Please rephrase.",
        )


def _pipeline_question_step(
    *,
    config: "ChatConfig",
    user_query: str,
    pinned_bundle_summary: str = "",
) -> str:
    """Fifth LLM step. Answers a pipeline-domain question from catalog + capabilities.

    Returns a plain-text answer (no Pydantic wrapping). Falls back to a short
    error string on LLM failure so the caller can still return a useful reply.
    """
    from .seqera.catalog import NFCORE_PIPELINE_CATALOG

    catalog_block_lines = []
    for k, entry in NFCORE_PIPELINE_CATALOG.items():
        catalog_block_lines.append(
            f"- {k}: {entry.get('pipeline_kind_description', '')} "
            f"(input={entry.get('samplesheet_input_kind', '?')}, "
            f"leaf_types={entry.get('accepted_leaf_sample_types', [])})"
        )
    catalog_block = "\n".join(catalog_block_lines)

    prompt = (
        config._load_prompt("pipeline_agent_question.txt")
        .replace("{user_query}", user_query)
        .replace("{catalog_block}", catalog_block)
        .replace("{pinned_bundle_summary}", pinned_bundle_summary or "(none)")
    )

    print(f"[DEBUG][PIPELINE_QUESTION] user_query={user_query!r}")

    client, model_name, budget = config.get_agent_model("pipeline_question")
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_query},
    ]
    try:
        resp = client.chat(
            model=model_name,
            temperature=0,
            messages=messages,
            thinking_budget=budget,
        )
        log_usage(resp, "PIPELINE_QUESTION")
        answer = (resp.content or "").strip()
        log_prompt(
            config.LOG_DIR,
            "pipeline_question",
            {
                "user_query": user_query,
                "pinned_bundle_summary": pinned_bundle_summary,
                "messages": messages,
                "response": answer,
            },
        )
        return answer or "I don't have a clear answer for that. Try the regular assistant."
    except Exception as exc:
        print(f"[DEBUG][PIPELINE_QUESTION] LLM call failed: {exc!r}")
        return f"Couldn't answer that (LLM error: {exc!r}). Try asking the regular assistant."


# Pipeline step shims — moved to pipeline/steps/* in Phase 1
from .pipeline.steps.directive import _pipeline_directive_parse  # noqa: E402,F401
from .pipeline.steps.sanity import _pipeline_sanity_check  # noqa: E402,F401

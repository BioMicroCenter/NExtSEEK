from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from ..session import SessionState
from ..config import ChatConfig
from ..helpers import (
    build_recent_results_summary,
)
from ..llm_clients import LLMTimeoutError
from ..schemas.schema_helper import call_llm_structured, empty_output_problem
from ..schemas import (
    ContextEngineerOutput,
    EntityAgentOutput,
    MultiParserPlan,
    ParserCandidate,
    ParserFilters,
    ParserPlan,
    PlannerOutput,
    PlanStep,
    StepExecutionPayload,
    StepInputRef,
    StepOutcome,
    StepOutputMapping,
)


def _endpoints_for_prompt(config, user_text: str) -> str:
    """Render the API endpoints catalog as JSON for the parser prompt.

    With SEMANTIC_ENDPOINTS_ENABLED=true, semantically shortlists endpoints
    against user_text using the cached embedding index. Otherwise dumps
    the full catalog (today's behavior, byte-identical).
    """
    idx = getattr(config, "ENDPOINT_INDEX", None)
    if idx is not None and idx.ready:
        from chat_nextseek.helpers.tools.catalog_semantic import dynamic_cutoff  # noqa: PLC0415
        scored = idx.query(user_text, top_n=getattr(config, "SEMANTIC_MAX_K", 80))
        kept = dynamic_cutoff(
            [(item, score) for item, score, _ in scored],
            ratio=getattr(config, "SEMANTIC_RATIO", 0.7),
            min_k=getattr(config, "SEMANTIC_MIN_K", 10),
            max_k=getattr(config, "SEMANTIC_MAX_K", 80),
        )
        return json.dumps(kept, indent=2)
    return json.dumps(config.MIN_API_ENDPOINTS, indent=2)


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
    # Function-local import: planner.execution imports this module at top level,
    # so a top-level `from .planner.execution import _TERMINAL_REPLY_TOOLS`
    # would be a circular import. Import is cached after first call.
    from .planner.execution import _TERMINAL_REPLY_TOOLS

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
                mode="graph_query",
                target_endpoint=None,
                tool_query=user_query,
                rationale=f"fallback after multi_parser error: {error}",
                confidence=0.5,
            ))
        ],
        notes=f"multi_parser_agent failed: {error}",
    )


def _empty_multi_plan_problem(plan: MultiParserPlan) -> str | None:
    """Why ``plan`` is not a routing decision, or None when it is one.

    The multi-parser's counterpart of ``_empty_plan_problem``. Every MultiParserPlan
    field has a default, so ``{}`` and a plan nested under an unknown key validate to a
    plan with no candidates; so does an output that names every key and fills none. A
    plan with neither a candidate, nor the user's intent, nor a note carries no
    decision, and goes back through the repair turn. When no attempt carries one, the
    caller's existing fallback plan runs.
    """
    problem = empty_output_problem(plan)
    if problem:
        return problem
    if plan.candidates or (plan.intent_summary or "").strip() or (plan.notes or "").strip():
        return None
    return (
        "The output carried no plan: no candidates, an empty intent_summary and empty "
        "notes. Return the complete MultiParserPlan with every key filled, including at "
        "least one candidate and the intent_summary."
    )


def _canonical_multi_parse(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    user_query: str,
    entity_result: EntityAgentOutput | dict,
) -> MultiParserPlan:
    """Run the canonical routing pass used by both the standard parser and planner pipeline."""
    print("\n[DEBUG][MULTI_PARSER] User query:", user_query)

    from ..chat_memory import history_block

    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result
    recent_summary = build_recent_results_summary(session)
    chat_history = history_block(session)
    endpoints_json = _endpoints_for_prompt(config, user_query)
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
            timeout_seconds=35,
            timeout_retry_seconds=60,
            result_check=_empty_multi_plan_problem,
        )
        normalized_candidates = [_fill_candidate_defaults(c) for c in result.candidates]
        result = result.model_copy(update={"candidates": normalized_candidates})
        result = _apply_multi_parser_guardrails(user_query, result)
        print(f"[DEBUG][MULTI_PARSER] intent={result.intent_summary!r}, candidates={len(result.candidates)}")
        for c in result.candidates:
            print(f"[DEBUG][MULTI_PARSER]   candidate: mode={c.mode}, endpoint={c.target_endpoint}, confidence={c.confidence}")
        return result
    except Exception as e:
        print(f"[DEBUG][MULTI_PARSER] Failed: {e!r}; falling back to single graph_query candidate")
        return _fallback_multi_parser_plan(user_query, entity_result, e)


def _candidate_to_parser_plan(
    session: SessionState | SessionStateProxy,
    user_query: str,
    parser_plan: MultiParserPlan,
) -> ParserPlan:
    """Project canonical candidate 0 into the legacy ParserPlan shape used by the standard pipeline."""
    candidate = _fill_candidate_defaults(parser_plan.candidates[0]) if parser_plan.candidates else _fill_candidate_defaults(
        ParserCandidate(
            mode="graph_query",
            target_endpoint=None,
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

    projected = ParserPlan(
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
    # The multi-parser path degrades the same way the single-path parser does.
    return _note_refine_without_bundle(session, projected)


# A well-formed NExtSEEK UID: <TYPE>-<YYMMDD><LAB>-<INC>[-PUB<n>].
# `NHP-22052-1` does not match — the date block must be exactly 6 digits.
_WELL_FORMED_UID_RE = re.compile(r"\b[A-Z][A-Z.]{1,6}-\d{6}[A-Z]{3}-\d+(?:-PUB\d*)?\b")

# Phrases that make the question about a *relationship* from the named UID rather
# than about the record itself.
_UID_RELATION_RE = re.compile(
    r"\b(?:deriv(?:e|es|ed|ing)\s+from|came?\s+from|from\s+these|from\s+those"
    r"|child(?:ren)?\s+of|parents?\s+of|lineage|ancestor|descendant"
    r"|associated\s+with|linked\s+to|related\s+to|generated\s+from)\b",
    re.IGNORECASE,
)

# "<some other entity> for/from/of <UIDs>" — the answer is a different set of records
# than the ones named. Safe to act on ONLY in combination with the 2-UID floor below;
# on its own it also matches legitimate single-UID REST work ("Build an SRA metadata
# file for <UID>", "Make me an nfcore samplesheet for the sequencing samples
# associated with <UID>").
_UID_DERIVED_ENTITY_RE = re.compile(
    r"\b(?:sequencing\s+data|data|assays?|samples?|files?|reads?|bams?|fastqs?|results?)"
    r"\s+(?:for|from|of)\b",
    re.IGNORECASE,
)

# Asking for the named record itself. REST answers this correctly and cheaply.
_UID_RECORD_LOOKUP_RE = re.compile(
    r"\b(?:full\s+details|details|metadata|record|info(?:rmation)?)\s+(?:for|on|about|of)\b",
    re.IGNORECASE,
)

UID_LINEAGE_ROUTE_NOTE = (
    "forced to graph_query: lineage across MULTIPLE named UIDs, which no single REST "
    "endpoint serves (sample-tree is GET-per-UID, and a child record does not contain "
    "its parent's UID as text)"
)

# How many UIDs it takes before REST stops being able to answer. Deliberately 2.
#
# A SINGLE-UID lineage question is served by REST and must stay there: sample-tree
# returns the whole bidirectional tree around one UID, `retrieve` returns everything
# associated with one UID, and the reporter builds a submission file for one UID. The
# corpus has 20 such turns across the search_tree, retrieve, reporting and
# pipeline_nfcore families, all asserting a REST endpoint.
#
# TWO OR MORE UIDs is the shape that has no REST answer, and is exactly task 797:
# the api agent fused both UIDs into one filter_searchText, got total 0 (a child
# record does not contain its parent's UID as text), and fell into the retry ladder
# that eventually "succeeded" on the term "1" with 2,057 unrelated rows.
_MIN_UIDS_FOR_FORCED_GRAPH = 2


def _force_graph_for_uid_lineage(user_query: str, plan: ParserPlan) -> ParserPlan:
    """
    Route multi-UID lineage questions to the graph, deterministically.

    `repro.cypher_uid_dot` went graph_query in the baseline (task 733, returning
    exactly the correct six D.SEQ-220823SHA-1..6-PUB) and new_search post-fix
    (task 797, wrong) with no code change on that path. The parser samples rather
    than decides, and the prompt contradicted itself — parser_core_routing.txt said
    do NOT use graph_query for "lineage from a known UID" in one place and DO for
    "derivation chains, lineage, ancestor/descendant" in another.

    Runs AFTER the LLM call so the model's choice is respected everywhere else.
    """
    if plan.mode == "graph_query":
        return plan
    query = user_query or ""
    uids = list(dict.fromkeys(_WELL_FORMED_UID_RE.findall(query)))
    if len(uids) < _MIN_UIDS_FOR_FORCED_GRAPH:
        return plan
    if _UID_RECORD_LOOKUP_RE.search(query):
        return plan  # "full details for <UID>" is a plain record lookup
    if not (_UID_RELATION_RE.search(query) or _UID_DERIVED_ENTITY_RE.search(query)):
        return plan

    merged = list(dict.fromkeys(list(plan.filters.uids or []) + uids))
    print(f"[DEBUG][PARSER] {UID_LINEAGE_ROUTE_NOTE} (uids={merged})")
    return plan.model_copy(update={
        "mode": "graph_query",
        "target_endpoint": None,
        "filters": plan.filters.model_copy(update={"uids": merged}),
        "notes": ((plan.notes + " | ") if plan.notes else "") + UID_LINEAGE_ROUTE_NOTE,
    })


REFINE_WITHOUT_BUNDLE_NOTE = (
    "previous turn ran outside the NExtSEEK search path (no result bundle to refine); "
    "re-ran as a fresh search"
)


def _note_refine_without_bundle(
    session: "SessionState | SessionStateProxy | None", plan: ParserPlan
) -> ParserPlan:
    """
    Make the refine -> new_search degrade visible.

    A Container CC turn writes no result bundle (795 bid=None, 796 opened bid=1), so
    `results_history` is empty, the refine plan is built with Nones and the turn
    quietly becomes a fresh search. The router had explicitly asked for a memory
    follow-up — 796's reasoning names `memory_lookup` — and nothing detected the
    mismatch.

    Deliberately NOT fixed by synthesising a bundle. A bundle's contract is a real
    REST call plus its response; fabricating one would let the parser re-POST an
    invented api_plan and return confidently wrong rows, which is strictly worse than
    today. `ask_about_last_results` over a CC turn is legitimate and is unblocked
    separately by carrying the CC reply preview into chat_log.

    So: keep the fallback, but say so. `notes` flows into the reply debug block and
    can be asserted on, converting a silent defect into a detectable one.
    """
    if plan.mode != "refine_last_search" or session is None:
        return plan
    try:
        history = session.get("results_history", []) or []
    except Exception:
        return plan
    if history:
        return plan

    print(f"[DEBUG][PARSER] refine_last_search with no result bundle; {REFINE_WITHOUT_BUNDLE_NOTE}")
    return plan.model_copy(update={
        "mode": "new_search",
        "notes": ((plan.notes + " | ") if plan.notes else "") + REFINE_WITHOUT_BUNDLE_NOTE,
        "previous_api_plan": None,
        "previous_user_query": None,
    })


# The evaluation switch (graph_search Nessie POC, spec 4.6 and E2). The harness pins
# the same marker phrase to prove a forced turn really was forced.
FORCE_NOTE_MARKER = "by the evaluation switch"
FORCE_MODES = ("graph", "api")
ADVANCED_SEARCH_PATH = "/nextseek_api/samples/advanced_search/"
_REST_ENDPOINT_PREFIX = "/nextseek_api/"
_FORCEABLE_MODES = ("new_search", "graph_query")


def _first_rest_candidate(plan: ParserPlan) -> str | None:
    """The first of the parser's endpoint candidates that is a NExtSEEK REST path."""
    for candidate in plan.endpoint_candidates or []:
        endpoint = candidate if isinstance(candidate, str) else getattr(candidate, "endpoint", None)
        if isinstance(endpoint, str) and endpoint.startswith(_REST_ENDPOINT_PREFIX):
            return endpoint
    return None


def _force_parser_mode(plan: ParserPlan, force_mode: str | None) -> ParserPlan:
    """Evaluation only: force a single-turn retrieval question to the graph or the API path, deterministically.

    Runs LAST in _apply_parser_guardrails, after the LLM call, so the parser's own choice is kept in the note.
    graph: new_search -> graph_query.
    api:   graph_query -> new_search on the first REST endpoint candidate, else advanced_search.
    Every other mode, and force_mode None, returns the plan unchanged (the same object).

    "The parser's choice" is the mode as it reaches this function, after the product's
    own guardrails: what the unforced product would have run. A retrieval plan whose
    mode already matches the arm keeps its mode and still gets the note, so every
    forced retrieval turn shows that the switch landed. Filters are kept either way.
    The switch is set only on a per-request config copy (``FORCE_PARSER_MODE``, see
    ``NessieAI/cc/turn.py::_with_parser_force``); the planner's multi-parser path
    ignores it.
    """
    if force_mode not in FORCE_MODES or plan.mode not in _FORCEABLE_MODES:
        return plan
    chosen = plan.mode
    updates: dict[str, Any] = {}
    if force_mode == "graph" and chosen == "new_search":
        updates = {"mode": "graph_query", "target_endpoint": None}
    elif force_mode == "api" and chosen == "graph_query":
        updates = {
            "mode": "new_search",
            "target_endpoint": _first_rest_candidate(plan) or ADVANCED_SEARCH_PATH,
        }
    note = f"forced to {force_mode} {FORCE_NOTE_MARKER} (parser chose {chosen})"
    updates["notes"] = ((plan.notes + " | ") if plan.notes else "") + note
    print(f"[DEBUG][PARSER] {note}")
    return plan.model_copy(update=updates)


#: The REST sample searches the parser may no longer route to (routing review 6a, 2026-09-24): every sample
#: question goes to the graph. None of them is in the catalog the parser reads any more, but nothing checks its
#: chosen endpoint against that catalog, and an old chat's recent-results summary still shows these paths.
#: Only this set and "no endpoint" are caught, not "anything outside the catalog": the old retrieve alias must
#: stay readable so saved chats replay.
RETIRED_SAMPLE_SEARCH_ENDPOINTS = frozenset({
    "/nextseek_api/samples/advanced_search/",
    "/nextseek_api/sample_types/get_parents/parents_by_child_types/",
    "/nextseek_api/samples/graph_search/",   # code still sets it after this guard (the scope fallback)
})
#: Names no retired endpoint: a note naming advanced_search once steered the graph agent wrong.
RETIRED_SEARCH_NOTE = "sent to graph_query: every sample question goes to the graph (the REST sample searches are retired)"


def _refine_prior_endpoint(
    session: "SessionState | SessionStateProxy | None", plan: ParserPlan
) -> str | None:
    """The endpoint of the stored result a refine modifies, or None.

    Read as the orchestrator's refine branch reads it: the bundle ``select_refine_bundle`` picks (the one the
    parser named in ``target_result_id``, else the newest), then that bundle's ``parser_plan.target_endpoint``,
    else its ``search_context.endpoint``. No session, an unreadable one, or no bundle is None.
    """
    if session is None:
        return None
    try:
        history = session.get("results_history", []) or []
    except Exception:
        return None
    from ..chat_memory import select_refine_bundle

    prior, _chosen = select_refine_bundle(history, plan.target_result_id)
    if not prior:
        return None
    prev_plan = prior.get("parser_plan") or {}
    search_context = prior.get("search_context") or {}
    return (
        (prev_plan.get("target_endpoint") if isinstance(prev_plan, dict) else None)
        or (search_context.get("endpoint") if isinstance(search_context, dict) else None)
    )


def _route_retired_sample_search(
    session: "SessionState | SessionStateProxy | None", plan: ParserPlan
) -> ParserPlan:
    """Send a sample search the REST path no longer serves to the graph, keeping its filters.

    A new_search that names a retired sample search, or no endpoint at all, becomes graph_query. A refine of a
    stored retired-search result (named on the plan, else the stored bundle's endpoint) keeps its mode and is
    marked ``refine_engine="graph"``, so the orchestrator's graph refine re-runs it with the prior turn's filters.
    Everything else is returned as the same object.
    """
    note = ((plan.notes + " | ") if plan.notes else "") + RETIRED_SEARCH_NOTE
    if plan.mode == "new_search" and (not plan.target_endpoint or plan.target_endpoint in RETIRED_SAMPLE_SEARCH_ENDPOINTS):
        print(f"[DEBUG][PARSER] {RETIRED_SEARCH_NOTE} (endpoint={plan.target_endpoint!r})")
        return plan.model_copy(update={"mode": "graph_query", "target_endpoint": None, "notes": note})
    if plan.mode == "refine_last_search" and plan.refine_engine != "graph":
        endpoint = plan.target_endpoint or _refine_prior_endpoint(session, plan)
        if endpoint in RETIRED_SAMPLE_SEARCH_ENDPOINTS:
            print(f"[DEBUG][PARSER] refine of a retired sample search re-run on the graph (endpoint={endpoint!r})")
            return plan.model_copy(update={"refine_engine": "graph", "target_endpoint": None, "notes": note})
    return plan


#: Modes the parser may emit that the orchestrator dispatches under another name.
#: ``schemas/router.py`` has documented ``memory_lookup`` as an alias of
#: ``ask_about_last_results`` since it was added, and nothing ever performed the
#: normalisation, so a parser that took the schema at its word produced a mode with no
#: branch: "The parser returned an unexpected mode='memory_lookup'. I don't yet know how
#: to handle this case." The planner's own step mapping is separate and already correct.
_MODE_ALIASES: dict[str, str] = {"memory_lookup": "ask_about_last_results"}


def _normalise_mode_aliases(plan: ParserPlan) -> ParserPlan:
    """Rewrite an aliased mode to the one the orchestrator dispatches on."""
    target = _MODE_ALIASES.get(plan.mode)
    if target is None:
        return plan
    return plan.model_copy(update={
        "mode": target,
        "notes": ((plan.notes + " | ") if plan.notes else "") + f"mode {plan.mode} normalised to {target}",
    })


def _apply_parser_guardrails(
    user_query: str,
    plan: ParserPlan,
    session: "SessionState | SessionStateProxy | None" = None,
    force_mode: str | None = None,
) -> ParserPlan:
    """Apply narrow deterministic safety checks after LLM routing.

    After the bulk-export check, a sample search the REST path no longer serves goes to the
    graph (``_route_retired_sample_search``). ``force_mode`` is the evaluation switch
    (``_force_parser_mode``); it runs last, after every product guardrail, and is None
    outside an evaluation run.
    """
    plan = _normalise_mode_aliases(plan)
    plan = _note_refine_without_bundle(session, plan)
    plan = _force_graph_for_uid_lineage(user_query, plan)
    if _is_unscoped_bulk_export_request(user_query, plan.mode, plan.filters):
        plan = ParserPlan(
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
            metadata=plan.metadata,
            previous_api_plan=plan.previous_api_plan,
            previous_user_query=plan.previous_user_query,
            report_mode=None,
            report_type=None,
        )
    plan = _route_retired_sample_search(session, plan)
    return _force_parser_mode(plan, force_mode)


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


def _empty_plan_problem(plan: ParserPlan) -> str | None:
    """Why ``plan`` is not a routing decision, or None when it is one.

    Every ParserPlan field has a default and the default mode is "unsupported", so
    ``{}``, a plan wrapped under an unknown key and a bare ``{"mode": "unsupported"}``
    all validate to an unsupported plan with nothing in it. The prompt asks for every
    key; an unsupported plan that states neither the user's intent nor a reason is
    what an output without a plan looks like, and it must not reach the user as
    "your request is not supported" (CI 2026-09-18, task ed4b2e3b).
    """
    if plan.mode != "unsupported":
        return None
    if (plan.intent_summary or "").strip() or (plan.notes or "").strip():
        return None
    return (
        "The output carried no plan: mode is 'unsupported' with an empty intent_summary "
        "and empty notes. Return the complete ParserPlan object with every key filled, "
        "including intent_summary; if the request really cannot be served, say why in notes."
    )


def parser_agent(session: SessionState | SessionStateProxy, config: ChatConfig, user_query: str, entity_result: EntityAgentOutput | dict) -> ParserPlan:
    """
    Invoke the single-path parser used by the standard pipeline.
    Embeds recent session context plus catalog endpoints into the prompt and returns a ParserPlan.
    """
    from ..chat_memory import history_block

    recent_summary = build_recent_results_summary(session)
    chat_history = history_block(session)
    if isinstance(entity_result, EntityAgentOutput):
        entity_payload = entity_result.model_dump()
    else:
        entity_payload = entity_result or {}
    entity_json = json.dumps(entity_payload, indent=2)
    endpoints_json = _endpoints_for_prompt(config, user_query)

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
    # Log the *effective* temperature, not the requested one. Opus 4.7 and Mythos are
    # adaptive-thinking-only (llm_clients.py), so temperature is never sent and routing
    # samples between runs. Without this line a routing flip that was really a sample
    # gets attributed to a code change — which is exactly what happened between the
    # 2026-07-24 and 2026-07-27 runs for repro.cypher_uid_dot.
    _adaptive_only = any(tag in parser_model_name for tag in ("opus-4-7", "mythos"))
    print(
        f"[DEBUG][PARSER] model={parser_model_name} "
        f"temperature={'UNSET (adaptive-thinking-only; routing may vary run to run)' if _adaptive_only else 0} "
        f"thinking_budget={parser_thinking_budget}"
    )
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
            timeout_seconds=35,
            timeout_retry_seconds=60,
            result_check=_empty_plan_problem,
        )
    except LLMTimeoutError as e:
        # Never reached the model at all. Keep this distinct from a parse failure:
        # mode stays "unsupported" so downstream routing is unchanged, but the notes
        # and metadata record what actually happened so the user is not told their
        # question is invalid when the real cause was a dead connection.
        print("[DEBUG][PARSER] Timed out reaching the model:", repr(e))
        plan_model = ParserPlan(
            notes=(
                "The query planner could not reach the language model in time "
                "(transport timeout). This is a temporary system fault, not a "
                "limitation of your question. Please try again."
            ),
            metadata={"failure": "transport_timeout", "error": repr(e)},
        )
    except Exception as e:
        print("[DEBUG][PARSER] Exception or parse error:", repr(e))
        plan_model = ParserPlan(
            notes=f"Parser could not produce valid structured output ({type(e).__name__}).",
            metadata={"failure": "parse_error", "error": repr(e)},
        )

    print("[DEBUG][PARSER] Parsed plan:", json.dumps(plan_model.model_dump(), indent=2))
    plan_model = _apply_parser_guardrails(
        user_query, plan_model, session=session,
        force_mode=getattr(config, "FORCE_PARSER_MODE", None),
    )
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
            mode="graph_query",
            target_endpoint=None,
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

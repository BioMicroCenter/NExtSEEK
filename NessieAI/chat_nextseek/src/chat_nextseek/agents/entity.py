from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from ..session import SessionState

from ..config import ChatConfig
from ..llm_clients import LLMAPIConnectionError, LLMRateLimitError, LLMTimeoutError
from ..helpers import (
    log_usage,
    safe_parse_json,
)
from ..helpers.lab_code import resolve_labs
from ..schemas.schema_helper import StructuredOutputError, call_llm_structured, call_llm_text, empty_output_problem
from ..schemas import (
    EntityAgentOutput,
)
from ..schemas.entity import LabMatch, LabNearMiss


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _resolve_lab_names(
    config: ChatConfig,
    user_query: str,
    result: EntityAgentOutput,
    sampletypes: Any,
    assays: Any,
    projects: Any,
) -> None:
    """Resolve the LLM's lab and person names against SEEK's lab records, in place.

    ``labs`` becomes the matched labs' surnames as SEEK spells them, ``lab_codes`` only
    matched codes, ``lab_matches`` what matched and how, and ``scientists`` gains person
    names that matched no lab (also appended to ``keywords``). ``config.LABS`` that is not
    a list means no labs document: ``labs`` then passes through unresolved, with no codes.
    Spec: docs/superpowers/specs/2026-09-18-projects-labs-context.md section 7.
    """
    records = getattr(config, "LABS", None)
    # M5 reads the whole catalogs the config holds; a caller's shortlist is only a fallback.
    catalog_sampletypes = getattr(config, "MIN_SAMPLETYPES", None)
    catalog_assays = getattr(config, "MIN_ASSAYS", None)
    catalogs = (
        (catalog_sampletypes if isinstance(catalog_sampletypes, list) else _as_list(sampletypes))
        + (catalog_assays if isinstance(catalog_assays, list) else _as_list(assays))
    )
    try:
        resolution = resolve_labs(
            user_query,
            result.labs,
            records=records,
            llm_scientists=result.scientists,
            llm_keywords=result.keywords,
            catalogs=catalogs,
            projects=_as_list(projects),
        )
    except Exception as exc:  # a matcher bug must not fail the turn, nor guess a code
        print(f"[WARN][ENTITY] Lab resolution failed, labs left unresolved: {exc!r}")
        result.lab_codes = []
        result.lab_matches = []
        result.lab_near_misses = []
        return
    if not resolution.available:
        print(
            "[WARN][ENTITY] No lab records (config.LABS is "
            f"{type(records).__name__}): labs passed through unresolved, no lab codes."
        )
    result.labs = resolution.labs
    result.lab_codes = resolution.lab_codes
    result.lab_matches = [LabMatch(**match) for match in resolution.lab_matches]
    result.lab_near_misses = [LabNearMiss(**miss) for miss in resolution.near_misses]
    result.scientists = resolution.scientists
    result.keywords = resolution.keywords


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
            result_check=empty_output_problem,
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
                    result_check=empty_output_problem,
                )
            except Exception as e_retry:
                print("[DEBUG][ENTITY] Retry after timeout failed:", repr(e_retry))
                result = EntityAgentOutput()
        else:
            # Fallback: raw call without forced response_format. Through the recovery
            # ladder, so a timeout, a 503 or an empty body moves to the entity's next
            # provider; it used to call the SDK directly and never moved. The first
            # attempt keeps the 180 s this call always had, and the retry (usually on
            # the fallback model) gets 60 s, so the worst case stays near the old 180 s.
            try:
                raw_content = call_llm_text(
                    config,
                    messages=messages,
                    model_name=entity_model,
                    client=entity_client,
                    agent_label="entity",
                    log_label="entity_raw",
                    temperature=0,
                    thinking_budget=entity_budget,
                    timeout_seconds=180,
                    timeout_retry_seconds=60,
                    usage_label="ENTITY_FALLBACK",
                )
                parsed = safe_parse_json(raw_content)
                if isinstance(parsed, list):
                    parsed = {"sampletypes": parsed, "assays": [], "keywords": []}
                if not isinstance(parsed, dict):
                    parsed = {}
                result = EntityAgentOutput.model_validate(parsed)
            except Exception as e_fallback:
                print("[DEBUG][ENTITY] Fallback exception:", repr(e_fallback))
                result = EntityAgentOutput()

    _resolve_lab_names(config, user_query, result, sampletypes, assays, projects)

    print("[DEBUG][ENTITY] Parsed entity result:", json.dumps(result.model_dump(), indent=2))
    return result

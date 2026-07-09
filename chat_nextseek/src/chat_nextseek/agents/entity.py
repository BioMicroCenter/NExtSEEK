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
from ..helpers.lab_code import lab_code
from ..schemas.schema_helper import StructuredOutputError, call_llm_structured
from ..schemas import (
    EntityAgentOutput,
)


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

    seen: set[str] = set()
    codes: list[str] = []
    for name in result.labs:
        code = lab_code(name)
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    result.lab_codes = codes

    print("[DEBUG][ENTITY] Parsed entity result:", json.dumps(result.model_dump(), indent=2))
    return result

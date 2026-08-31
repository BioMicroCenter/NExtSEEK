from __future__ import annotations

import json
from typing import Any

from ..config import ChatConfig
from ..schemas.schema_helper import call_llm_structured
from ..schemas import (
    EntityAgentOutput,
    ParserPlan,
    SystemAgentOutput,
)


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

    # The full catalogs, not just the codes the entity agent happened to resolve.
    # Without these the only enumerable list in context is the representative
    # table in capabilities.md (25 sample types, 15 assays), and the agent
    # answers "how many D.* types exist" with that table's row count instead of
    # the catalog's. Every other catalog-answering agent already passes these;
    # see agents/entity.py:38-40.
    sampletypes_json = json.dumps(config.MIN_SAMPLETYPES, indent=2)
    assays_json = json.dumps(config.MIN_ASSAYS, indent=2)

    caps_doc = config.CAPABILITIES_DOC or "(No capabilities document loaded — describe general NExtSEEK capabilities.)"
    endpoints_json = json.dumps(config.MIN_API_ENDPOINTS, indent=2)
    schema_json = json.dumps(config.NEO4J_SCHEMA, indent=2) if config.NEO4J_SCHEMA else "{}"
    entity_details_json = json.dumps(entity_details, indent=2) if entity_details else "{}"

    messages = [
        {"role": "system", "content": config.SYSTEM_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": f"CAPABILITIES_DOCUMENT:\n{caps_doc}"},
        {"role": "system", "content": f"ENDPOINT_CATALOG:\n{endpoints_json}"},
        {"role": "system", "content":
            "SAMPLETYPE_CATALOG (COMPLETE — this is the authoritative list; "
            "count and enumerate from here, never from the representative table "
            f"in the capabilities document):\n{sampletypes_json}"},
        {"role": "system", "content":
            "ASSAY_CATALOG (COMPLETE — authoritative, same rule as the sampletype "
            f"catalog):\n{assays_json}"},
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


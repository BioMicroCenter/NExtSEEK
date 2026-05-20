from __future__ import annotations

import json
from typing import Any

from ..config import ChatConfig
from ..schemas.schema_helper import call_llm_structured
from ..schemas import (
    EntityAgentOutput,
    GraphAgentPlan,
    ParserPlan,
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

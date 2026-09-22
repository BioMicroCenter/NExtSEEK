from __future__ import annotations

import json
from typing import Any

from .. import graph_catalog
from ..config import ChatConfig
from ..context_rows import is_investigation_row, is_project_row
from ..schemas.schema_helper import call_llm_structured
from ..schemas import (
    EntityAgentOutput,
    ParserPlan,
    SystemAgentOutput,
)
from .graph import live_catalog_context


def _as_map(value) -> dict:
    """`value` when it is a dict, else an empty one."""
    return value if isinstance(value, dict) else {}



def _lab_details(config, entity_dict: dict, projects_map: dict) -> dict:
    """ENTITY_DETAILS entries for the SEEK labs the question names, from ``config.LABS``.

    "Who is Engelward?" was answered from the model's general knowledge (a professorship, a
    directorship) and never from the lab record NExtSEEK holds (local run 2026-09-22,
    bucket5.who_is_a_person): the labs never reached this agent. A lab is matched by a code the
    entity step resolved (``lab_codes``, ``lab_matches``) or by a name it read as a scientist,
    lab or keyword that equals a lab's name. Nothing when ``LABS`` is not a list.
    """
    labs = getattr(config, "LABS", None)
    if not isinstance(labs, list):
        return {}
    codes = {c.upper() for c in entity_dict.get("lab_codes") or [] if isinstance(c, str)}
    for match in entity_dict.get("lab_matches") or []:
        code = match.get("code") if isinstance(match, dict) else getattr(match, "code", None)
        if isinstance(code, str):
            codes.add(code.upper())
    names = {
        n.strip().lower()
        for key in ("scientists", "labs", "keywords")
        for n in entity_dict.get(key) or []
        if isinstance(n, str) and n.strip()
    }
    project_names = {
        row.get("project_id"): name for name, row in projects_map.items() if isinstance(row, dict)
    }
    details: dict = {}
    for record in labs:
        if not isinstance(record, dict):
            continue
        code, name = record.get("code"), record.get("name")
        if not (isinstance(code, str) and isinstance(name, str)):
            continue
        if code.upper() not in codes and name.strip().lower() not in names:
            continue
        project_ids = list(record.get("project_ids") or [])
        details[f"{name} lab ({code})"] = {
            "entity_type": "lab",
            "code": code,
            "name": name,
            "title": record.get("title"),
            "affiliation": record.get("affiliation"),
            "project_ids": project_ids,
            "projects": [project_names[pid] for pid in project_ids if pid in project_names],
        }
    return details


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
    # A project and an investigation may share a name (the real CSBC and MetNet do), so
    # they live in two maps and both are sent, the investigation under its own label.
    # Each row is sent as what chat_nextseek.context_rows says it is, whichever map it is in:
    # production's legacy project rows are typed 'investigation' with no parent_project and
    # are projects, never "<name> (investigation)"; a 'study' row is neither and is not sent.
    # A map that is not a dict (a MagicMock config in tests) reads as empty.
    projects_map = _as_map(getattr(config, "FULL_PROJECTS_MAP", None))
    investigations_map = _as_map(getattr(config, "FULL_INVESTIGATIONS_MAP", None))
    for project_name in entity_dict.get("projects", []):
        if not project_name:
            continue
        project = projects_map.get(project_name)
        if is_project_row(project):
            entity_details[project_name] = project
        investigation = investigations_map.get(project_name)
        if is_investigation_row(investigation):
            entity_details[f"{project_name} (investigation)"] = investigation

    entity_details.update(_lab_details(config, entity_dict, projects_map))

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
    # The graph agent's own rendering when the v1.1 catalog is live (structure, type index, the resolved types and
    # the question's vocabulary blocks); the committed JSON schema otherwise.
    catalog = live_catalog_context(config, user_query, entity_dict, plan_dict, reader="system agent")
    if catalog is not None:
        schema_json = catalog.schema + (f"\n{catalog.vocabulary}\n" if catalog.vocabulary else "")
    else:
        # The committed schema, without its vocabulary for a caller who is not an admin (graph_catalog).
        committed = graph_catalog.committed_schema(config)
        schema_json = json.dumps(committed, indent=2) if committed else "{}"
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


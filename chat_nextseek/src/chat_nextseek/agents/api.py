from __future__ import annotations

import json
from typing import Any

from ..config import ChatConfig
from ..schemas.schema_helper import call_llm_structured
from ..schemas import (
    APIRequestPlan,
    ParserPlan,
)


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

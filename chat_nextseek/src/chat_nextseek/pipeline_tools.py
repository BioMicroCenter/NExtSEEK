"""Group-by tool schemas, library functions, and dispatcher for pipeline_agent.

Exposes:
- GROUPBY_TOOL_SCHEMAS  — 4 Anthropic-style tool schema dicts (list_metadata_fields,
  field_distribution_by_sample_type, list_distinct_values, finalize_groupby)
- list_metadata_fields(bundle, sample_types) → dict
- field_distribution_by_sample_type(bundle, field_name) → dict
- list_distinct_values(bundle, sample_type, field_name) → dict
- dispatch_groupby_tool_call(name, tool_input, bundle) → str | dict
"""
from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# Tool schemas (Anthropic-native format)
# ---------------------------------------------------------------------------

GROUPBY_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_metadata_fields",
        "description": (
            "Return the metadata field names available for each sample type "
            "(or the requested subset). Use this to discover what fields exist "
            "before deciding which one matches the user's group-by phrase."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Filter to these sample types only. Pass an empty list "
                        "to get all sample types."
                    ),
                },
            },
            "required": ["sample_types"],
        },
    },
    {
        "name": "field_distribution_by_sample_type",
        "description": (
            "For a named field, return example values and population counts per "
            "sample type. Use this to validate that a candidate field actually "
            "carries the values you expect (e.g., treatment groups, tissue types)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field_name": {
                    "type": "string",
                    "description": "Exact field name to inspect.",
                },
            },
            "required": ["field_name"],
        },
    },
    {
        "name": "list_distinct_values",
        "description": (
            "Return the deduplicated list of values found in a specific field "
            "for a specific sample type. Use this to confirm the cohort split "
            "is what the user intends (e.g., ['NDMA', 'vehicle'])."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_type": {
                    "type": "string",
                    "description": "The sample type to query (e.g. 'NHP').",
                },
                "field_name": {
                    "type": "string",
                    "description": "The field name to query.",
                },
            },
            "required": ["sample_type", "field_name"],
        },
    },
    {
        "name": "finalize_groupby",
        "description": (
            "Commit the group-by resolution. Call this once you have identified "
            "the correct field and confirmed its distinct values. If the phrase "
            "is genuinely ambiguous across multiple fields, set "
            "requires_clarification=true and populate candidates + clarifying_question "
            "instead of field/distinct_values."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_type": {
                    "type": "string",
                    "description": "Sample type that owns the resolved field (omit when requires_clarification=true).",
                },
                "field_name": {
                    "type": "string",
                    "description": "Exact field name chosen (omit when requires_clarification=true).",
                },
                "distinct_values": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Deduplicated values for cohort splitting.",
                },
                "rationale": {
                    "type": "string",
                    "description": "One sentence: why this field matches the user's phrase.",
                },
                "requires_clarification": {
                    "type": "boolean",
                    "description": "Set true when the phrase is ambiguous and you need user input.",
                },
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sample_type": {"type": "string"},
                            "field_name": {"type": "string"},
                        },
                        "required": ["sample_type", "field_name"],
                    },
                    "description": "Candidate fields when requires_clarification=true.",
                },
                "clarifying_question": {
                    "type": "string",
                    "description": "Question to ask the user when requires_clarification=true.",
                },
            },
            "required": ["rationale"],
        },
    },
]


# ---------------------------------------------------------------------------
# Deterministic library functions
# ---------------------------------------------------------------------------

_MAX_DISTINCT_VALUES = 200


def list_metadata_fields(
    bundle: dict[str, Any],
    sample_types: list[str],
) -> dict[str, list[str]]:
    """Return field names keyed by sample type.

    Args:
        bundle: The metadata summary dict with shape
                {"by_sample_type": {<st>: {"fields": {<name>: {...}}}}}
        sample_types: If non-empty, restrict to these sample types.
                      Empty list → return all.

    Returns:
        {sample_type: [field_name, ...]}
    """
    by_st = (bundle or {}).get("by_sample_type") or {}
    result: dict[str, list[str]] = {}
    for st, st_data in by_st.items():
        if sample_types and st not in sample_types:
            continue
        fields = (st_data or {}).get("fields") or {}
        result[st] = sorted(fields.keys())
    return result


def field_distribution_by_sample_type(
    bundle: dict[str, Any],
    field_name: str,
) -> dict[str, dict[str, Any]]:
    """Return examples and n_populated for a field across all sample types.

    Args:
        bundle: Metadata summary dict.
        field_name: Exact field name.

    Returns:
        {sample_type: {"examples": [...], "n_populated": int}}
    """
    by_st = (bundle or {}).get("by_sample_type") or {}
    result: dict[str, dict[str, Any]] = {}
    for st, st_data in by_st.items():
        fields = (st_data or {}).get("fields") or {}
        if field_name not in fields:
            continue
        fd = fields[field_name] or {}
        result[st] = {
            "examples": fd.get("examples") or [],
            "n_populated": fd.get("n_populated") or 0,
        }
    return result


def list_distinct_values(
    bundle: dict[str, Any],
    sample_type: str,
    field_name: str,
) -> dict[str, Any]:
    """Return deduplicated values for a field within a sample type.

    Caps at _MAX_DISTINCT_VALUES distinct values to avoid overwhelming the LLM.

    Args:
        bundle: Metadata summary dict.
        sample_type: The sample type to query.
        field_name: The field name to query.

    Returns:
        {"values": [...], "truncated": bool}
    """
    by_st = (bundle or {}).get("by_sample_type") or {}
    st_data = by_st.get(sample_type) or {}
    fields = st_data.get("fields") or {}
    fd = fields.get(field_name) or {}

    raw_values: list[str] = fd.get("_all_values") or fd.get("examples") or []
    distinct = sorted(set(str(v) for v in raw_values if v is not None))
    truncated = len(distinct) > _MAX_DISTINCT_VALUES
    return {
        "values": distinct[:_MAX_DISTINCT_VALUES],
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def dispatch_groupby_tool_call(
    name: str,
    tool_input: dict[str, Any],
    bundle: dict[str, Any],
) -> str:
    """Route a tool_use block to the appropriate library function.

    ``finalize_groupby`` is handled by the caller (the tool-use loop);
    calling dispatch on it returns an error string.

    Returns a JSON-serialisable string for feeding back as tool_result content.
    """
    if name == "list_metadata_fields":
        result = list_metadata_fields(bundle, tool_input.get("sample_types") or [])
    elif name == "field_distribution_by_sample_type":
        result = field_distribution_by_sample_type(bundle, tool_input.get("field_name", ""))
    elif name == "list_distinct_values":
        result = list_distinct_values(
            bundle,
            tool_input.get("sample_type", ""),
            tool_input.get("field_name", ""),
        )
    elif name == "finalize_groupby":
        result = {"error": "finalize_groupby must be handled by the caller, not dispatched."}
    else:
        result = {"error": f"Unknown tool: {name!r}"}

    return json.dumps(result)

"""JSON parsing and schema utility helpers. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import json


def _extract_required_paths(schema: dict, prefix: str = "") -> list[str]:
    """
    Traverse a JSON schema dict and return dot-separated required paths.
    Recurses through nested properties so callers can surface missing fields clearly.
    Returns an empty list when the schema is not a dict to keep validation tolerant.
    """
    if not isinstance(schema, dict):
        return []

    paths: list[str] = []
    required = schema.get("required", [])
    props = schema.get("properties", {})

    if isinstance(required, list) and isinstance(props, dict):
        for key in required:
            if not isinstance(key, str):
                continue
            path = f"{prefix}.{key}" if prefix else key
            paths.append(path)
            sub_schema = props.get(key)
            paths.extend(_extract_required_paths(sub_schema, path))

    return paths


def estimate_tokens_from_text(text: str, chars_per_token: int = 4) -> int:
    """
    Rough token estimate: chars / chars_per_token (default 4 for GPT-style models).
    Returns at least 1 to avoid zero-token edge cases when text is empty.
    Useful for budgeting prompt sizes without exact tokenization.
    """
    if not text:
        return 1
    return max(1, len(text) // chars_per_token)


def safe_parse_json(text: str | None):
    """
    Extract and parse JSON from text, handling markdown code blocks and stray prose.
    Attempts direct parse first, then extracts inner {...} blocks before giving up.
    """
    if not text:
        return None

    text = text.strip()
    if not text:
        return None

    # Handle markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n", 1)
        # Strip opening ``` and optional language hint
        if len(lines) > 1:
            text = lines[1].rsplit("```", 1)[0].strip()
        else:
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()

    # Fast path: try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: extract innermost {...}
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None

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


# Beyond a handful of unclosed structures we are guessing at shape rather than
# recovering it, so the repair refuses instead.
_MAX_REPAIR_CLOSERS = 8


def _is_escaped(text: str, index: int) -> bool:
    """True when the character at `index` is preceded by an odd run of backslashes."""
    backslashes = 0
    i = index - 1
    while i >= 0 and text[i] == "\\":
        backslashes += 1
        i -= 1
    return backslashes % 2 == 1


def _trim_dangling(body: str) -> str | None:
    """
    Drop a trailing separator or a key whose value was never written.

    `{"a": 1,` -> `{"a": 1` and `{"a": 1, "b":` -> `{"a": 1`. Only punctuation and
    an already-complete key are removed, so no value is ever invented or lost.
    """
    s = body.rstrip()
    for _ in range(_MAX_REPAIR_CLOSERS):
        if s.endswith(","):
            s = s[:-1].rstrip()
            continue
        if s.endswith(":"):
            without_colon = s[:-1].rstrip()
            if not without_colon.endswith('"'):
                return None
            # Walk back over the complete key literal to its opening quote.
            i = len(without_colon) - 2
            while i >= 0 and not (without_colon[i] == '"' and not _is_escaped(without_colon, i)):
                i -= 1
            if i < 0:
                return None
            s = without_colon[:i].rstrip()
            continue
        break
    return s


def _close_truncated_json(text: str):
    """
    Recover an object that was cut off mid-write by appending the missing closers.

    Model output truncated part-way through a JSON object leaves `rfind("}")` pointing
    at an *inner* brace, so the slice fallback above stays unbalanced and gives up even
    though the payload is complete enough to parse.

    Append-only: this adds `}` and `]` and never a value. Truncation inside a string
    literal returns None rather than closing the quote, because a valid-but-invented
    object is worse than a clean failure.
    """
    start = text.find("{")
    if start == -1:
        return None

    body = text[start:]
    stack: list[str] = []
    in_string = False
    escaped = False
    for ch in body:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack:
            stack.pop()

    if in_string or not stack or len(stack) > _MAX_REPAIR_CLOSERS:
        return None

    trimmed = _trim_dangling(body)
    if trimmed is None:
        return None

    try:
        parsed = json.loads(trimmed + "".join(reversed(stack)))
    except json.JSONDecodeError:
        return None

    print(f"[JSON_REPAIR] closed {len(stack)} structures")
    return parsed


def safe_parse_json(text: str | None):
    """
    Extract and parse JSON from text, handling markdown code blocks and stray prose.
    Attempts direct parse first, then extracts inner {...} blocks, then repairs
    output truncated mid-object before giving up.
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

    # Last resort: the payload was cut off mid-object. Close what is still open.
    return _close_truncated_json(text)

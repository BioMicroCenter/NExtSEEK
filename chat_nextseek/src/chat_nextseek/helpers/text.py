"""HTML stripping and JSON file-loading helpers. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def strip_html(value: str | None) -> str | None:
    """
    Remove HTML tags from a string while preserving content for display or logging.
    Returns None when input is None so callers can propagate optional fields cleanly.
    """
    if value is None:
        return None
    return re.sub(r"<[^>]+>", "", value)


def strip_html_recursive(obj):
    """Recursively strip HTML tags from all string values in a JSON-like structure."""
    if isinstance(obj, str):
        return strip_html(obj)
    if isinstance(obj, dict):
        return {k: strip_html_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_html_recursive(v) for v in obj]
    return obj


def load_file_for_memory(path: str) -> str:
    """Read a result file, stripping HTML from parsed JSON payloads before returning text."""
    content = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(content)
        cleaned = strip_html_recursive(data)
        return json.dumps(cleaned, indent=2)
    except Exception:
        return content


def load_json_for_memory(path: str) -> Any:
    """Read a JSON artifact and recursively strip HTML from string values."""
    content = Path(path).read_text(encoding="utf-8")
    return strip_html_recursive(json.loads(content))

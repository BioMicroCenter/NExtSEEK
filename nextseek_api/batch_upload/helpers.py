"""Shared helpers for the batch upload pipeline."""
from __future__ import annotations

import re
from typing import List

# UID format: optional single-letter dotted prefix (A./D./M./…), 2+ uppercase
# type letters, 6-digit date, 2-5 uppercase lab abbreviation, dash, index,
# optional -PUB suffix
UID_RE = re.compile(
    r"\A([A-Z]\.)?[A-Z]{2,}-\d{6}[A-Z]{2,5}-\d+(-PUB\d*)?\Z"
)

# Semicolons only — names may contain spaces, commas, hyphens
_PARENT_SPLIT_RE = re.compile(r";")


def split_parent_field(parent_raw: str) -> List[str]:
    """Split a Parent metadata field into individual tokens.

    Splits on semicolons only. Strips whitespace from each token.
    Returns only non-empty tokens.
    """
    return [t.strip() for t in _PARENT_SPLIT_RE.split(parent_raw.strip()) if t.strip()]


def collect_parent_tokens(meta: dict) -> List[str]:
    """Collect parent tokens from all keys containing 'parent' (case-insensitive).

    Scans all keys in the metadata dict. For each key whose name contains
    'parent' (case-insensitive), splits the value on semicolons using
    ``split_parent_field`` and collects the tokens.

    Returns a deduplicated list preserving first-seen order.
    """
    seen: set = set()
    result: List[str] = []
    for key, value in meta.items():
        if "parent" not in key.lower():
            continue
        if not value or not isinstance(value, str):
            continue
        for token in split_parent_field(value):
            if token not in seen:
                seen.add(token)
                result.append(token)
    return result

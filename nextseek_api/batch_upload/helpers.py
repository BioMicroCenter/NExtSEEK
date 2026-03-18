"""Shared helpers for the batch upload pipeline."""
from __future__ import annotations

import re
from typing import List

# UID format: optional A./D. prefix, 3+ uppercase letters, 6-digit date,
# 2-5 uppercase lab abbreviation, dash, index, optional -PUB suffix
UID_RE = re.compile(r"^([AD]\.)?[A-Z]{3,}-\d{6}[A-Z]{2,5}-\d+(-PUB\d*)?$")

# Semicolons only — names may contain spaces, commas, hyphens
_PARENT_SPLIT_RE = re.compile(r";")


def split_parent_field(parent_raw: str) -> List[str]:
    """Split a Parent metadata field into individual tokens.

    Splits on semicolons only. Strips whitespace from each token.
    Returns only non-empty tokens.
    """
    return [t.strip() for t in _PARENT_SPLIT_RE.split(parent_raw.strip()) if t.strip()]

"""Trio assertion: UI text ≡ console.txt ≡ MySQL chat_log latest reply.

normalize() runs HTML-strip via BeautifulSoup.get_text() + whitespace-collapse.
trio_match() compares all three normalized strings and emits a unified diff
on failure for trio_diff.txt.
"""
from __future__ import annotations

import difflib

from bs4 import BeautifulSoup


def normalize(s: str | None) -> str:
    """HTML-strip + whitespace-collapse. Returns '' for None/empty."""
    if not s:
        return ""
    text = BeautifulSoup(s, "html.parser").get_text()
    return " ".join(text.split())


def trio_match(
    ui_text: str,
    console_text: str,
    mysql_text: str,
) -> tuple[bool, str, str | None]:
    """Compare three strings after normalize().

    Returns (passed, reason, diff_text):
      - passed: True iff all three normalize to the same string
      - reason: 'all three equal' or 'UI ≠ CONSOLE', 'CONSOLE ≠ MYSQL', etc.
      - diff_text: None on pass; unified diff of the three labeled blocks on fail.
    """
    nu = normalize(ui_text)
    nc = normalize(console_text)
    nm = normalize(mysql_text)

    if nu == nc == nm:
        return True, "all three equal", None

    mismatches = []
    if nu != nc:
        mismatches.append("UI ≠ CONSOLE")
    if nc != nm:
        mismatches.append("CONSOLE ≠ MYSQL")
    if nu != nm:
        mismatches.append("UI ≠ MYSQL")
    reason = "; ".join(mismatches)

    diff = "\n".join(difflib.unified_diff(
        [f"UI: {nu}"],
        [f"CONSOLE: {nc}"],
        lineterm="",
    )) + "\n" + "\n".join(difflib.unified_diff(
        [f"CONSOLE: {nc}"],
        [f"MYSQL: {nm}"],
        lineterm="",
    ))
    return False, reason, diff

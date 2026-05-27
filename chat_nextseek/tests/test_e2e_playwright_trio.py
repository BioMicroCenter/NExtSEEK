"""normalize() + trio_match() unit tests."""
import json
from pathlib import Path

import pytest

from e2e.playwright.trio import normalize, trio_match


FIXTURES = Path(__file__).parent / "e2e" / "playwright" / "fixtures" / "normalize_cases.json"


@pytest.mark.parametrize("case", json.loads(FIXTURES.read_text()))
def test_normalize_golden(case):
    assert normalize(case["input"]) == case["expected"], f"case {case['name']!r}"


def test_normalize_handles_none_like_empty():
    assert normalize("") == ""


def test_trio_match_all_equal():
    passed, reason, diff = trio_match("hello world", "hello world", "hello world")
    assert passed is True
    assert diff is None


def test_trio_match_ui_differs():
    passed, reason, diff = trio_match("hello WORLD", "hello world", "hello world")
    assert passed is False
    assert "UI" in reason or "ui" in reason
    assert diff is not None and "hello" in diff


def test_trio_match_mysql_differs():
    passed, reason, diff = trio_match("hello world", "hello world", "hello earth")
    assert passed is False
    assert "MYSQL" in reason or "mysql" in reason


def test_trio_match_uses_normalize():
    """All three are equal AFTER normalize: HTML + whitespace differences must wash out."""
    passed, reason, diff = trio_match(
        "<p>hello   world</p>",
        "hello\n\nworld  ",
        "  hello world",
    )
    assert passed is True

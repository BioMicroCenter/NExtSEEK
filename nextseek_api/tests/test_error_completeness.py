"""Ensures every 4xx/5xx HttpResponse site in services/*.py is wrapped in maybe_v2_error.

Uses AST parsing so adding a new unwrapped error site anywhere in the 3 scoped
files turns this test red — prevents silent regressions.
"""
import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]  # nextseek_api/tests/ → up to repo root
SCOPED_FILES = [
    REPO_ROOT / "nextseek_api" / "services" / "samples.py",
    REPO_ROOT / "nextseek_api" / "services" / "data_files.py",
    REPO_ROOT / "nextseek_api" / "services" / "assays.py",
]


def _iter_http_response_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "HttpResponse":
            yield node


def _extract_status_values(call):
    for kw in call.keywords:
        if kw.arg == "status":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                yield kw.value.value
            else:
                # variable — treat as potentially 4xx/5xx
                yield "variable"


def _is_wrapped_in_maybe_v2_error(call, tree):
    """Walk up: the nearest enclosing Call that is `maybe_v2_error(...)`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
           and node.func.id == "maybe_v2_error":
            for arg in node.args:
                if arg is call:
                    return True
    return False


@pytest.mark.parametrize("path", SCOPED_FILES, ids=[p.name for p in SCOPED_FILES])
def test_no_unwrapped_4xx_5xx_httpresponse(path):
    src = path.read_text()
    tree = ast.parse(src)
    unwrapped = []
    for call in _iter_http_response_calls(tree):
        statuses = list(_extract_status_values(call))
        # Only care about 4xx/5xx literals or variable statuses
        matches = [s for s in statuses if s == "variable" or (isinstance(s, int) and 400 <= s < 600)]
        if not matches:
            continue
        if not _is_wrapped_in_maybe_v2_error(call, tree):
            unwrapped.append((path.name, call.lineno, statuses))
    assert unwrapped == [], (
        f"Unwrapped 4xx/5xx HttpResponse sites:\n" +
        "\n".join(f"  {f}:{ln} status={s}" for f, ln, s in unwrapped)
    )

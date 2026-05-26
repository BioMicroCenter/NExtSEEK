"""Assert that every evaluator module has a module docstring (DD-46)."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

MODULES = [
    "chat_nextseek.evaluator",
    "chat_nextseek.evaluator.__main__",
    "chat_nextseek.evaluator.runner",
    "chat_nextseek.evaluator.workflow",
    "chat_nextseek.evaluator.reports",
    "chat_nextseek.evaluator.client",
    "chat_nextseek.evaluator.normalization",
    "chat_nextseek.evaluator.dashboard",
]


def _read_module_docstring(name: str) -> str:
    spec = importlib.util.find_spec(name)
    assert spec and spec.origin, f"Could not resolve module source for {name}"
    source = Path(spec.origin).read_text(encoding="utf-8")
    return ast.get_docstring(ast.parse(source, filename=spec.origin)) or ""


def test_every_module_has_a_docstring():
    missing = []
    too_long = []
    for name in MODULES:
        doc = _read_module_docstring(name).strip()
        if not doc:
            missing.append(name)
            continue
        if len(doc.splitlines()) > 7:
            too_long.append(name)
    assert not missing, f"Missing module docstrings: {missing}"
    assert not too_long, f"Docstrings exceed 7-line cap: {too_long}"


def test_argparse_epilog_contains_example():
    from chat_nextseek.evaluator.runner import build_parser

    parser = build_parser()
    assert parser.description, "argparse description is required"
    epilog = parser.epilog or ""
    assert "--eval-batch" in epilog
    assert "Examples" in epilog

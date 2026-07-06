"""Regression test for BUG-1 (2026-07-05 T5.5 shakeout).

`planner/agent.py` calls `_canonical_multi_parse(...)` but the
`from ..parser import (...)` block omitted it, so every multi-step planner
query raised `NameError: name '_canonical_multi_parse' is not defined` at
runtime.

Verified statically (AST) rather than by import: importing the planner pulls in
`mysql.connector`, unavailable in the hermetic venv. This pins that any name the
planner references from `parser` is (a) imported into the planner module and
(b) actually defined in `parser`.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "chat_nextseek" / "agents"
_PLANNER = _SRC / "planner" / "agent.py"
_PARSER = _SRC / "parser.py"


def _names_imported_from_parser(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("parser"):
            names.update(alias.name for alias in node.names)
    return names


def _module_level_defs(tree: ast.Module) -> set[str]:
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_canonical_multi_parse_imported_by_planner():
    imported = _names_imported_from_parser(ast.parse(_PLANNER.read_text(encoding="utf-8")))
    assert "_canonical_multi_parse" in imported, (
        "planner/agent.py references _canonical_multi_parse but does not import "
        "it from ..parser -> runtime NameError on the multi-step path"
    )


def test_canonical_multi_parse_defined_in_parser():
    defs = _module_level_defs(ast.parse(_PARSER.read_text(encoding="utf-8")))
    assert "_canonical_multi_parse" in defs, "parser.py must define _canonical_multi_parse"

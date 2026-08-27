"""Regression test for BUG-1 (2026-07-05 T5.5 shakeout).

`planner/agent.py` called parser helpers (`_canonical_multi_parse`,
`_build_step_from_candidate`, `_step_query`) that its
`from ..parser import (...)` block omitted, so multi-step planner queries raised
`NameError` at runtime — one name at a time as each code path was hit.

Rather than pin individual names (whack-a-mole), assert the GENERAL invariant:
every underscore-prefixed name the planner *references* is either imported or
bound locally, and any such name that lives in `parser` is imported from it.

Verified statically (AST) rather than by import: importing the planner pulls in
`mysql.connector`, unavailable in the hermetic venv.
"""
from __future__ import annotations

import ast
from pathlib import Path

_AGENTS = Path(__file__).resolve().parents[1] / "src" / "chat_nextseek" / "agents"
_PLANNER = _AGENTS / "planner" / "agent.py"
_PARSER = _AGENTS / "parser.py"


def _analyze(tree: ast.Module):
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            imported |= {a.asname or a.name for a in n.names}
        elif isinstance(n, ast.Import):
            imported |= {a.asname or a.name.split(".")[0] for a in n.names}
    bound: set[str] = set(imported)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            bound.add(n.id)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
    loads = {
        n.id for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id.startswith("_")
    }
    return imported, bound, loads


def test_planner_references_no_unresolved_underscore_names():
    imported, bound, loads = _analyze(ast.parse(_PLANNER.read_text(encoding="utf-8")))
    unresolved = sorted(name for name in loads if name not in bound)
    assert not unresolved, (
        f"planner/agent.py references undefined underscore names {unresolved} "
        "-> runtime NameError. Import them from ..parser (or define locally)."
    )


def test_planner_underscore_helpers_are_defined_in_parser():
    """The specific helpers BUG-1 missed must exist in parser to import."""
    parser_defs = {
        n.name for n in ast.parse(_PARSER.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in ("_canonical_multi_parse", "_build_step_from_candidate", "_step_query"):
        assert name in parser_defs, f"parser.py must define {name}"

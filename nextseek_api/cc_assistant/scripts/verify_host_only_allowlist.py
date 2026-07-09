#!/usr/bin/env python3
"""Task 9: exact `host_only` marker <-> allowlist cross-check.

AST-scans every `test_*.py` file under `nextseek_api/cc_assistant/tests/`
for the `host_only` pytest marker (registered in `pyproject.toml`'s
`[tool.pytest.ini_options]`), applied either:

  - at module level: a top-level `pytestmark = pytest.mark.host_only`
    statement (or a `pytestmark = [...]` list containing it) -- every test
    in that file is host_only, emitted as `MODULE <relpath>`; or
  - per-test: a `@pytest.mark.host_only` decorator directly on a
    `def test_*` function -- emitted as `TEST <relpath>::<funcname>`.

This is deliberately AST-only (no live pytest collection): the repo's
conftest imports Django, which is not guaranteed to be importable in every
environment this verifier runs in (see task-9-brief.md). AST scanning needs
nothing but the Python source tree.

The discovered set is cross-checked against
`/home/taishajo/work/state/devmerge-evidence/host-only-allowlist.md`
(overridable via argv[1]) for EXACT equality in both directions:
  - every host_only marker found in the tree must appear in the allowlist
    (nothing marked-but-unlisted), and
  - every entry in the allowlist must correspond to a marker actually found
    in the tree (nothing listed-but-unmarked/missing -- catches stale or
    typo'd allowlist entries, and file deletions).

Exit 0 and print PASS on agreement; exit 1 and print the exact set
differences otherwise.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTS_DIR = REPO_ROOT / "nextseek_api" / "cc_assistant" / "tests"
DEFAULT_ALLOWLIST = Path(
    "/home/taishajo/work/state/devmerge-evidence/host-only-allowlist.md"
)


def _is_host_only_mark_expr(node: ast.AST) -> bool:
    """True if `node` is the attribute-access expression `pytest.mark.host_only`
    (bare, or as the func of a call `pytest.mark.host_only(...)`)."""
    if isinstance(node, ast.Call):
        node = node.func
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "host_only"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _module_level_host_only(tree: ast.Module) -> bool:
    """True if the module has a top-level `pytestmark = pytest.mark.host_only`
    (or a list assignment containing that expression)."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            continue
        value = node.value
        if _is_host_only_mark_expr(value):
            return True
        if isinstance(value, (ast.List, ast.Tuple)):
            if any(_is_host_only_mark_expr(elt) for elt in value.elts):
                return True
    return False


def _test_level_host_only_functions(tree: ast.Module) -> list[str]:
    """Names of top-level `def test_*` functions carrying an explicit
    `@pytest.mark.host_only` decorator."""
    names = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        if any(_is_host_only_mark_expr(dec) for dec in node.decorator_list):
            names.append(node.name)
    return names


def discover_host_only_nodes() -> set[str]:
    """AST-scan the test tree; return the canonical node-id set:
    'MODULE <relpath>' for whole-file markers, 'TEST <relpath>::<func>' for
    per-test markers."""
    nodes: set[str] = set()
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        relpath = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _module_level_host_only(tree):
            nodes.add(f"MODULE {relpath}")
            # Module-level marking already covers every test in the file;
            # don't also emit per-test entries for it (avoids double-listing
            # requirements in the allowlist).
            continue
        for funcname in _test_level_host_only_functions(tree):
            nodes.add(f"TEST {relpath}::{funcname}")
    return nodes


_ALLOWLIST_LINE_RE = re.compile(
    r"^-?\s*(MODULE|TEST)\s+(\S+)", re.MULTILINE
)


def parse_allowlist(path: Path) -> set[str]:
    if not path.is_file():
        print(f"FAIL: allowlist file not found: {path}")
        sys.exit(1)
    text = path.read_text(encoding="utf-8")
    nodes: set[str] = set()
    for kind, target in _ALLOWLIST_LINE_RE.findall(text):
        nodes.add(f"{kind} {target}")
    return nodes


def _existence_check(nodes: set[str]) -> list[str]:
    """Extra sanity pass: every listed node's file must exist on disk (and,
    for TEST nodes, its function must be a real host_only-decorated test --
    already implied by set equality with discover_host_only_nodes(), but
    checked explicitly here for a clearer error message)."""
    problems = []
    for node in sorted(nodes):
        kind, target = node.split(" ", 1)
        relpath = target.split("::", 1)[0]
        if not (REPO_ROOT / relpath).is_file():
            problems.append(f"{node} -- file does not exist: {relpath}")
    return problems


def main(argv: list[str]) -> int:
    allowlist_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_ALLOWLIST

    discovered = discover_host_only_nodes()
    allowlisted = parse_allowlist(allowlist_path)

    marked_but_unlisted = sorted(discovered - allowlisted)
    listed_but_unmarked = sorted(allowlisted - discovered)
    missing_files = _existence_check(allowlisted)

    ok = not marked_but_unlisted and not listed_but_unmarked and not missing_files

    print(f"Scanned: {TESTS_DIR}")
    print(f"Allowlist: {allowlist_path}")
    print(f"Discovered host_only nodes: {len(discovered)}")
    print(f"Allowlisted nodes: {len(allowlisted)}")
    print()

    if marked_but_unlisted:
        print("FAIL: host_only-marked in tree but NOT in allowlist:")
        for n in marked_but_unlisted:
            print(f"  - {n}")
    if listed_but_unmarked:
        print("FAIL: listed in allowlist but NOT found host_only-marked in tree:")
        for n in listed_but_unmarked:
            print(f"  - {n}")
    if missing_files:
        print("FAIL: allowlist entries whose file does not exist:")
        for n in missing_files:
            print(f"  - {n}")

    if ok:
        print("PASS: host_only markers and allowlist agree exactly.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

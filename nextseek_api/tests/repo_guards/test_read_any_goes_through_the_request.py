"""Every read-any decision asks the request, so a Container-CC turn pass switches it off (spec piece 1).

``may_read_any_users_data`` takes a user and cannot see a pass; ``may_read_any`` takes the request. Hermetic: an
AST walk of the application code, no database.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCANNED = ("nextseek_api", "NessieAI/cc", "NessieAI/ns", "NessieAI/router")


def _offenders(source: str, rel: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "may_read_any_users_data":
                found.append(f"{rel}:{node.lineno}")
        elif isinstance(node, ast.ImportFrom) and any(a.name == "may_read_any_users_data" for a in node.names):
            found.append(f"{rel}:{node.lineno} (import)")  # an alias would hide the call
    return found


def test_the_scan_sees_calls_and_aliased_imports():
    assert _offenders("may_read_any_users_data(u)", "x") == ["x:1"]
    assert _offenders("from p import may_read_any_users_data as _ra\n_ra(u)", "x") == ["x:1 (import)"]
    assert _offenders("from p import may_read_any\nmay_read_any(r)", "x") == []


def test_nothing_but_the_request_helper_calls_may_read_any_users_data():
    offenders = []
    for top in SCANNED:
        for path in (REPO_ROOT / top).rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if "/tests/" in rel or "/migrations/" in rel or rel == "nextseek_api/permissions.py":
                continue
            offenders += _offenders(path.read_text(encoding="utf-8"), rel)
    assert offenders == [], f"call may_read_any(request) instead: {offenders}"

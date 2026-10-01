"""Every read-any decision asks the request, so a Container-CC turn pass switches it off (spec piece 1).

``may_read_any_users_data`` takes a user and cannot see a pass; ``may_read_any`` takes the request. Hermetic: an
AST walk of the application code, no database.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCANNED = ("nextseek_api", "NessieAI/cc", "NessieAI/ns", "NessieAI/router")


def test_nothing_but_the_request_helper_calls_may_read_any_users_data():
    offenders = []
    for top in SCANNED:
        for path in (REPO_ROOT / top).rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if "/tests/" in rel or "/migrations/" in rel or rel == "nextseek_api/permissions.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                    if name == "may_read_any_users_data":
                        offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"call may_read_any(request) instead: {offenders}"

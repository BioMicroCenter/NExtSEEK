"""Report-code sandbox: executes LLM-generated report-building Python with a
restricted-but-function-capable AST subset. Sibling of memory_code.py; the key
difference is that helper `def`s are allowed (report row-mapping benefits from
small helpers), while import/exec/open/dunder access remain hard-blocked."""
from __future__ import annotations

import ast
import json
import re
import signal
from typing import Any

from .memory_code import (
    _MEMORY_ALLOWED_BUILTINS,
    _MEMORY_ALLOWED_METHODS,
    _MEMORY_ALLOWED_RE_METHODS,
    _MEMORY_ALLOWED_JSON_METHODS,
)


class ReportCodeSafetyError(ValueError):
    """Raised when generated report code uses syntax outside the allowed subset."""


class ReportCodeTimeoutError(TimeoutError):
    """Raised when generated report code exceeds the execution timeout."""


_REPORT_ALLOWED_BUILTINS = {
    **_MEMORY_ALLOWED_BUILTINS,
    "abs": abs,
    "round": round,
}

_REPORT_ALLOWED_METHODS = _MEMORY_ALLOWED_METHODS | {
    "find", "rfind", "rstrip", "lstrip", "title", "count", "setdefault", "pop", "index",
}

_REPORT_ALLOWED_RUNTIME_HELPERS = {"strip_html"}
_REPORT_BLOCKED_NAMES = {"eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "dir", "help", "input"}

# Note: ast.FunctionDef is intentionally NOT blocked (helper functions allowed).
_REPORT_BLOCKED_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.With,
    ast.AsyncWith,
    ast.Delete,
    ast.Global,
    ast.Nonlocal,
    ast.Raise,
    ast.While,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.GeneratorExp,
)


def _validate_report_code(tree: ast.AST) -> None:
    # Collect user-defined function names so calls to them are permitted.
    # Reject any helper that shadows a builtin or blocked name (keeps the
    # allow-list reasoning sound and prevents shadowing tricks).
    local_funcs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name in _REPORT_BLOCKED_NAMES or node.name in _REPORT_ALLOWED_BUILTINS:
                raise ReportCodeSafetyError(
                    f"Helper function may not shadow a builtin/blocked name: {node.name}"
                )
            local_funcs.add(node.name)

    for node in ast.walk(tree):
        if isinstance(node, _REPORT_BLOCKED_NODES):
            raise ReportCodeSafetyError(f"Disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id in _REPORT_BLOCKED_NAMES:
            raise ReportCodeSafetyError(f"Disallowed name: {node.id}")
        if isinstance(node, ast.Attribute):
            # Every attribute access (read or call) must be on the allow-list.
            # This blocks frame/internal attributes (gi_frame, f_back, f_globals,
            # f_builtins, ...) that enable sandbox escape via frame walking.
            attr = node.attr
            if attr.startswith("__"):
                raise ReportCodeSafetyError("Dunder attribute access is not allowed")
            value = node.value
            if isinstance(value, ast.Name) and value.id == "re":
                if attr not in _MEMORY_ALLOWED_RE_METHODS:
                    raise ReportCodeSafetyError(f"Disallowed re method: {attr}")
            elif isinstance(value, ast.Name) and value.id == "json":
                if attr not in _MEMORY_ALLOWED_JSON_METHODS:
                    raise ReportCodeSafetyError(f"Disallowed json method: {attr}")
            elif attr not in _REPORT_ALLOWED_METHODS:
                raise ReportCodeSafetyError(f"Disallowed attribute access: {attr}")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in _REPORT_ALLOWED_RUNTIME_HELPERS:
                    continue
                if func.id in local_funcs:
                    continue
                if func.id not in _REPORT_ALLOWED_BUILTINS:
                    raise ReportCodeSafetyError(f"Disallowed function call: {func.id}")
            elif isinstance(func, ast.Attribute):
                # The attribute itself was already validated by the ast.Attribute
                # branch above (name must be on the method allow-list).
                continue
            else:
                raise ReportCodeSafetyError("Dynamic calls are not allowed")

    if not any(
        isinstance(node, ast.Name) and node.id == "result" and isinstance(node.ctx, ast.Store)
        for node in ast.walk(tree)
    ):
        raise ReportCodeSafetyError("Code must assign the final report body to `result`")


def execute_report_code(code: str, data: Any, *, timeout_seconds: int = 15) -> dict[str, Any]:
    """Execute LLM-generated report-building code. Code must assign a
    JSON-serializable report body dict to `result`."""
    tree = ast.parse(code, mode="exec")
    _validate_report_code(tree)

    def _timeout_handler(signum, frame):
        raise ReportCodeTimeoutError(f"Report code exceeded {timeout_seconds}s timeout")

    old_handler = None
    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_seconds)
    except Exception:
        old_handler = None

    def _strip_html_helper(value: Any) -> str:
        if value is None:
            return ""
        return re.sub(r"<[^>]+>", "", str(value)).strip()

    exec_scope: dict[str, Any] = {
        "__builtins__": _REPORT_ALLOWED_BUILTINS,
        "data": data,
        "re": re,
        "json": json,
        "strip_html": _strip_html_helper,
        "result": {},
    }
    try:
        exec(compile(tree, "<report_coder>", "exec"), exec_scope)  # noqa: S102
    finally:
        try:
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)
        except Exception:
            pass

    result = exec_scope.get("result", {})
    if not isinstance(result, dict):
        result = {"value": result}
    json.dumps(result, default=str)
    return result

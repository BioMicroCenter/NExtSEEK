"""Report-code sandbox: executes LLM-generated report-building Python with a
restricted-but-function-capable AST subset. Sibling of memory_code.py; the key
difference is that helper `def`s are allowed (report row-mapping benefits from
small helpers), while import/exec/open/dunder access remain hard-blocked.

``execute_report_code`` checks the code here and runs it in a separate, limited
process (``row_compute.run_in_child``), which holds its time limit from any thread.
Report code imports nothing (the checker refuses an import), and this module and
``memory_code`` use only the standard library, which is all that process has. One
report runs at a time on the machine, across every server process (a lock file,
``REPORT_LOCK_PATH``); another that cannot start within ``REPORT_WAIT_S`` fails, and
its caller falls back to the report writer."""
from __future__ import annotations

import ast
import fcntl
import json
import os
import re
import signal
import tempfile
import time
from contextlib import contextmanager
from typing import Any

from .memory_code import (
    _MEMORY_ALLOWED_BUILTINS,
    _MEMORY_ALLOWED_METHODS,
    _MEMORY_ALLOWED_RE_METHODS,
    _MEMORY_ALLOWED_JSON_METHODS,
)
from .row_compute import TIME_LIMIT, run_in_child

#: The report coder's process limits. A report reads a whole submission's metadata, so it gets more memory and
#: a larger input than a follow-up computation, and time on top of its own limit to hand the data over and back.
REPORT_MEM_MB = 4096
#: The largest metadata, as JSON, a report hands to its process. The process parses it inside REPORT_MEM_MB, and
#: metadata-shaped JSON takes about four times its text once parsed (3.97x measured), on top of the text itself
#: while it is parsed: about five times the input must fit. A fifth of 4,096 MiB is 819 MiB; 768 MiB leaves the
#: interpreter and the code's first allocations room.
REPORT_INPUT_MAX_BYTES = 768 << 20
#: The largest report body the process may send back. A body is one row per sample of the fields a submission
#: names, far smaller than the metadata it reads; this process holds about five times the reply while reading it.
REPORT_REPLY_MAX_BYTES = 64 << 20
REPORT_TRANSFER_S = 30
#: One report at a time on the machine: each can take REPORT_MEM_MB, and the server runs several worker
#: processes under one memory cap. The lock is a file every worker opens; another report waits this long, then fails.
REPORT_WAIT_S = 5.0
#: The lock file; None means ``nextseek-report-code.lock`` in the system temp directory, found when first needed:
#: this module is also imported inside the limited process, which can create no file, so not at import.
REPORT_LOCK_PATH: str | None = None
_LOCK_NAME = "nextseek-report-code.lock"
_LOCK_POLL_S = 0.05


class ReportCodeSafetyError(ValueError):
    """Raised when generated report code uses syntax outside the allowed subset."""


class ReportCodeTimeoutError(TimeoutError):
    """Raised when generated report code exceeds the execution timeout."""


class ReportCodeError(RuntimeError):
    """Raised when generated report code fails for any other reason (an error in the code, the memory limit)."""


_REPORT_ALLOWED_BUILTINS = {
    **_MEMORY_ALLOWED_BUILTINS,
    "abs": abs,
    "round": round,
}

_REPORT_ALLOWED_METHODS = _MEMORY_ALLOWED_METHODS | {
    "find", "rfind", "rstrip", "lstrip", "title", "count", "setdefault", "pop", "index",
}

_REPORT_ALLOWED_RUNTIME_HELPERS = {"strip_html"}
_REPORT_BLOCKED_NAMES = {"eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "dir", "help", "input", "__builtins__"}

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
    # NOTE: `while` is intentionally allowed — report code legitimately needs it to
    # walk lineage parent-chains. It is control flow, not an RCE vector (escape is
    # blocked by the attribute allow-list + import/exec/dunder blocks). Runaway loops
    # are bounded by the execution timeout in execute_report_code.
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
    """Execute LLM-generated report-building code in a separate, limited process. Code must
    assign a JSON-serializable report body dict to `result`.

    Raises ``ReportCodeSafetyError`` for code outside the allowed subset (before any process
    starts), ``ReportCodeTimeoutError`` when it runs past ``timeout_seconds``, and
    ``ReportCodeError`` for any other failure, including another report already running for
    longer than ``REPORT_WAIT_S``. The caller's ``data`` is never changed: the process gets a
    JSON copy."""
    tree = ast.parse(code, mode="exec")
    _validate_report_code(tree)
    with _one_report_at_a_time():
        run = run_in_child("report", code, data, cpu_s=timeout_seconds, mem_mb=REPORT_MEM_MB,
                           wall_s=timeout_seconds + REPORT_TRANSFER_S, input_max=REPORT_INPUT_MAX_BYTES,
                           reply_max=REPORT_REPLY_MAX_BYTES)
    if run["ok"]:
        result = run["result"]
        return result if isinstance(result, dict) else {"value": result}
    if run["error"] == TIME_LIMIT:
        raise ReportCodeTimeoutError(f"Report code exceeded {timeout_seconds}s timeout")
    raise ReportCodeError(run["error"])


@contextmanager
def _one_report_at_a_time():
    """Hold the machine-wide report lock (an exclusive ``flock`` on ``REPORT_LOCK_PATH``) for the block.

    It is waited for up to ``REPORT_WAIT_S``; then, or when the lock file cannot be opened, ``ReportCodeError``.
    Each call opens the file itself, so two threads of one process exclude each other as two processes do, and
    closing the file releases the lock even when the block raises."""
    try:
        path = REPORT_LOCK_PATH or os.path.join(tempfile.gettempdir(), _LOCK_NAME)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise ReportCodeError(f"the report lock could not be opened: {type(exc).__name__}") from exc
    try:
        deadline = time.monotonic() + REPORT_WAIT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ReportCodeError("another report's code is already running, so this one did not run")
                time.sleep(_LOCK_POLL_S)
        yield
    finally:
        os.close(fd)


def run_report_code_here(code: str, data: Any, *, timeout_seconds: int = 15) -> dict[str, Any]:
    """Run report-building code in the calling process: the body of ``execute_report_code``,
    called inside its separate process (``row_compute_child.py``)."""
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
        "__builtins__": dict(_REPORT_ALLOWED_BUILTINS),
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

"""Run model-written code over rows in a separate, limited process.

``run_code_isolated`` checks the code against the memory-code subset here (``_validate_memory_code``), then runs it
in a fresh interpreter (``row_compute_child.py``) started with ``-I -S -B``, no environment and an empty working
directory. The child gets the code and a JSON copy of the data, nothing else, and sets limits on its own CPU time,
memory, file size and open files; this process ends it at a wall-clock limit, which works from any thread. The
reply comes back as JSON, and the child refuses to send one larger than the caller's limit. It never raises: every
failure is ``ok`` false with a short error.

``run_in_child`` is the shared step, which the report coder's executor (``report_code.execute_report_code``) uses
with its own checks and limits.
"""
from __future__ import annotations

import ast
import json
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .memory_code import _validate_memory_code

CHILD = Path(__file__).with_name("row_compute_child.py")

#: The largest request, code and data as JSON, handed to a child for a memory-code computation. The child parses
#: it inside its memory limit: about five times this (the text, and about four times it once parsed).
INPUT_MAX_BYTES = 16 << 20
#: The largest reply a memory-code computation may send back. This process holds the reply's bytes and what they
#: parse to, about five times the reply, so it is bounded like the input: a filter returns at most its input rows.
REPLY_MAX_BYTES = 16 << 20

TIME_LIMIT = "the computation hit its time limit"
NO_RESULT = "the computation stopped without a result (it may have run out of memory)"
#: Errors the executors raise inside the child when their own timer fires.
_TIMEOUT_ERRORS = ("MemoryCodeTimeoutError", "ReportCodeTimeoutError")
_KILLED = (-signal.SIGXCPU, -signal.SIGKILL)
#: What a reply may exceed ``reply_max`` by: the child caps the reply it writes, and this is its own envelope.
_REPLY_SLACK = 4096


def _reply(t0: float, *, ok: bool, result: Any = None, error: str | None = None, **extra: Any) -> dict:
    return {"ok": ok, "result": result if ok else None, "error": None if ok else error,
            "elapsed_ms": int((time.monotonic() - t0) * 1000), **extra}


def run_code_isolated(code: str, data: Any, *, cpu_s: int = 3, mem_mb: int = 512, wall_s: int = 5) -> dict:
    """Run memory-code ``code`` over ``data`` in a separate, limited process.

    Returns ``{"ok", "result", "error", "elapsed_ms"}``: ``result`` is the dict the code assigned to ``result`` (a
    non-dict is wrapped as ``{"value": ...}``). Code outside the allowed subset is refused here, before any process
    starts. ``cpu_s`` bounds the code's CPU time, ``mem_mb`` the child's memory and ``wall_s`` the whole run. A
    result larger than ``REPLY_MAX_BYTES`` is not sent back: ``ok`` is false and ``result_bytes`` gives its size.
    """
    t0 = time.monotonic()
    try:
        _validate_memory_code(ast.parse(code, mode="exec"))
    except Exception as exc:  # SyntaxError, MemoryCodeSafetyError, or code that is not a string
        return _reply(t0, ok=False, error=f"{type(exc).__name__}: {exc}")
    return run_in_child("memory", code, data, cpu_s=cpu_s, mem_mb=mem_mb, wall_s=wall_s,
                        input_max=INPUT_MAX_BYTES, reply_max=REPLY_MAX_BYTES, t0=t0)


def run_in_child(kind: str, code: str, data: Any, *, cpu_s: int, mem_mb: int, wall_s: float, input_max: int,
                 reply_max: int, t0: float | None = None) -> dict:
    """Run already checked ``code`` of ``kind`` ("memory" or "report") in the child. Never raises.

    The limits go on the command line, so the child sets its memory limit before it parses the request."""
    t0 = time.monotonic() if t0 is None else t0
    try:
        request = json.dumps({"code": code, "data": data}, default=str).encode("utf-8")
    except Exception as exc:
        return _reply(t0, ok=False, error=f"the data could not be handed over: {type(exc).__name__}: {exc}")
    if len(request) > input_max:
        return _reply(t0, ok=False, error=(f"the data is too large to compute over here ({len(request):,} bytes; "
                                           f"the limit is {input_max:,})"))
    try:
        with tempfile.TemporaryDirectory(prefix="row-compute-") as cwd:
            proc = subprocess.run(
                [sys.executable, "-I", "-S", "-B", str(CHILD), kind, str(int(cpu_s)), str(int(mem_mb)),
                 str(int(reply_max))],
                input=request, capture_output=True, timeout=wall_s,
                env={"LANG": "C.UTF-8"}, cwd=cwd, close_fds=True,
            )
    except subprocess.TimeoutExpired:
        return _reply(t0, ok=False, error=TIME_LIMIT)
    except Exception as exc:
        return _reply(t0, ok=False, error=f"the computation could not start: {type(exc).__name__}: {exc}")
    finally:
        del request
    if proc.returncode in _KILLED:
        return _reply(t0, ok=False, error=TIME_LIMIT)
    if len(proc.stdout) > reply_max + _REPLY_SLACK:
        return _reply(t0, ok=False, error=(f"the result is too large to return ({len(proc.stdout):,} bytes; "
                                           f"the limit is {reply_max:,})"), result_bytes=len(proc.stdout))
    try:
        reply = json.loads(proc.stdout)
    except Exception:
        reply = None
    if not isinstance(reply, dict):
        tail = proc.stderr.decode("utf-8", "replace")[-300:] if proc.stderr else ""
        print(f"[DEBUG][ROW_COMPUTE] no reply from the child (exit {proc.returncode}): {tail!r}")
        return _reply(t0, ok=False, error=NO_RESULT)
    if reply.get("ok"):
        return _reply(t0, ok=True, result=reply.get("result"))
    error = str(reply.get("error") or "the computation failed")
    if error.split(":", 1)[0] in _TIMEOUT_ERRORS:
        error = TIME_LIMIT
    extra = {"result_bytes": reply["result_bytes"]} if isinstance(reply.get("result_bytes"), int) else {}
    return _reply(t0, ok=False, error=error, **extra)

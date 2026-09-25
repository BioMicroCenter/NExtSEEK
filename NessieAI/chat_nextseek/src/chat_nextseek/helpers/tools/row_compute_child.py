"""The child side of ``row_compute``: runs one piece of model-written code in a separate, limited process.

Started as ``python -I -S -B row_compute_child.py <kind> <cpu_s> <mem_mb> <reply_max>`` with no environment and an
empty working directory, so it imports only the standard library, and it reads the executor module it needs from
beside this file by path. It reads one JSON request from stdin, ``{code, data}``, parses it under its memory limit,
limits its CPU time, runs the code and writes one JSON reply to stdout, at most ``reply_max`` bytes:
``{"ok": true, "result": ...}`` or ``{"ok": false, "error": "<Type>: <message>"}``.
"""
import importlib
import json
import resource
import sys
import types
from pathlib import Path

#: The executor each kind runs, as (module beside this file, function). Each takes (code, data, timeout_seconds=).
EXECUTORS = {
    "memory": ("memory_code", "execute_memory_code"),
    "report": ("report_code", "run_report_code_here"),
}
#: The name the executor modules are loaded under, so that one module's relative import finds the other.
PACKAGE = "_row_compute_code"
ERROR_MAX = 300
MEMORY_ERROR = "MemoryError: the computation used more memory than it is allowed"


def _executor(kind: str):
    module_name, function = EXECUTORS[kind]
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(Path(__file__).resolve().parent)]
    sys.modules[PACKAGE] = package
    return getattr(importlib.import_module(f"{PACKAGE}.{module_name}"), function)


def _limit_memory_and_files(mem_mb: int) -> None:
    resource.setrlimit(resource.RLIMIT_AS, (mem_mb << 20, mem_mb << 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))


def _limit_cpu(cpu_s: int) -> None:
    """CPU time is counted from the start of this process, so the time reading the input took is added to it."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    spent = int(usage.ru_utime + usage.ru_stime) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (spent + cpu_s + 1, spent + cpu_s + 2))


def _run(kind: str, cpu_s: int, mem_mb: int) -> dict:
    try:
        raw = sys.stdin.buffer.read()
        _limit_memory_and_files(mem_mb)
        request = json.loads(raw)
        del raw
        _limit_cpu(cpu_s)
        run = _executor(kind)
        return {"ok": True, "result": run(request["code"], request["data"], timeout_seconds=cpu_s)}
    except MemoryError:
        return {"ok": False, "error": MEMORY_ERROR}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:ERROR_MAX]}


def _encode(reply: dict, reply_max: int) -> str:
    try:
        text = json.dumps(reply, default=str)
    except MemoryError:
        return json.dumps({"ok": False, "error": MEMORY_ERROR})
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:ERROR_MAX]})
    if len(text) > reply_max:  # json.dumps writes ASCII, so characters are bytes
        size = len(text)
        del text
        return json.dumps({"ok": False, "result_bytes": size,
                           "error": f"the result is too large to return ({size:,} bytes; the limit is {reply_max:,})"})
    return text


def main() -> None:
    try:
        kind, cpu_s, mem_mb, reply_max = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    except (IndexError, ValueError):
        sys.stdout.write(json.dumps({"ok": False, "error": "the computation was started without its limits"}))
        return
    sys.stdout.write(_encode(_run(kind, cpu_s, mem_mb), reply_max))
    sys.stdout.flush()


if __name__ == "__main__":
    main()

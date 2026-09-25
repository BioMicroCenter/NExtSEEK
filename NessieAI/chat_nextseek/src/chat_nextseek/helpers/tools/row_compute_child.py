"""The child side of ``row_compute``: runs one piece of model-written code in a separate, limited process.

Started as ``python -I -S -B row_compute_child.py`` with no environment and an empty working directory, so it
imports only the standard library, and it reads the executor module it needs from beside this file by path. It
reads one JSON request from stdin, ``{kind, code, data, cpu_s, mem_mb}``, sets its own limits (CPU time, address
space, file size, open files), runs the code and writes one JSON reply to stdout: ``{"ok": true, "result": ...}``
or ``{"ok": false, "error": "<Type>: <message>"}``.
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


def _set_limits(cpu_s: int, mem_mb: int) -> None:
    """CPU time is counted from the start of this process, so the time reading the input took is added to it."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    spent = int(usage.ru_utime + usage.ru_stime) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (spent + cpu_s + 1, spent + cpu_s + 2))
    resource.setrlimit(resource.RLIMIT_AS, (mem_mb << 20, mem_mb << 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))


def _run() -> dict:
    try:
        request = json.loads(sys.stdin.buffer.read())
        kind = request["kind"]
        cpu_s = int(request["cpu_s"])
        _set_limits(cpu_s, int(request["mem_mb"]))
        run = _executor(kind)
        return {"ok": True, "result": run(request["code"], request["data"], timeout_seconds=cpu_s)}
    except MemoryError:
        return {"ok": False, "error": MEMORY_ERROR}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:ERROR_MAX]}


def main() -> None:
    reply = _run()
    try:
        text = json.dumps(reply, default=str)
    except MemoryError:
        text = json.dumps({"ok": False, "error": MEMORY_ERROR})
    except Exception as exc:
        text = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:ERROR_MAX]})
    sys.stdout.write(text)
    sys.stdout.flush()


if __name__ == "__main__":
    main()

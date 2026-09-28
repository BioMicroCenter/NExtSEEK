"""What one NS turn, or one Container-CC op, has learned about its models (operator ruling 2026-09-28, F3).

One ``CallScope`` per turn or op, held in a ContextVar beside the cost collector (``turn_spend``). It remembers every
model that failed in this turn, keyed by (catalog provider, model id), so a stall is paid once per turn instead of once
per agent: a later call whose primary failed earlier starts on its fallback without asking the primary, and a call
whose primary and fallback both failed earlier fails at once without calling either (``schema_helper._Failover``).

* Who opens it: ``turn_spend.collects_turn`` (every NS entry point: ``run_query``, ``run_query_plan``,
  ``run_pipeline_launch``) and ``NessieAI/ns/granular.run_op`` (each CC op). A turn inside a turn shares the outer
  scope. A call made outside any scope asks its primary as before.
* What marks a model: a failure the ladder moves on and that says the model itself is failing: a timeout, a 5xx, a
  429, a dropped connection or a refused model. Not an empty body (one response), a 400 or bad output. The parsers'
  own timeout does not mark either (``call_budgets``: their 35 s is a speed preference, not a stall test). One
  strike: the first such failure marks (ruling D4). A timeout the op's deadline cut short marks nothing.
* What resets it: only the end of the turn or op. A marked model gets no further call in this scope, so nothing
  could show it healthy again; the next turn starts clean, on a fresh socket.
* Threads: marks and reads take a lock. Only the caller's thread marks (the wall-clock worker threads never touch the
  scope, so an abandoned call that answers late cannot). The aggregate op's parts run on pool threads and get this
  same object through ``contextvars.copy_context``.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from typing import Iterator

__all__ = ["CallScope", "current", "scope"]

_CURRENT: contextvars.ContextVar["CallScope | None"] = contextvars.ContextVar(
    "chat_nextseek_call_scope", default=None,
)

#: The clock, one name so tests can stand in for it.
_monotonic = time.monotonic

ModelKey = tuple[str, str]


class CallScope:
    """The models that failed in one turn or op."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failed: dict[ModelKey, dict] = {}

    def mark_failed(self, key: ModelKey, *, reason: str, agent: str | None) -> None:
        """Remember that ``key`` failed with ``reason``; the first mark of a model stands."""
        with self._lock:
            self._failed.setdefault(key, {"reason": reason, "agent": agent, "at": _monotonic()})

    def failed(self, key: ModelKey) -> dict | None:
        """``{"reason", "agent", "at"}`` when ``key`` failed earlier in this scope, else None."""
        with self._lock:
            mark = self._failed.get(key)
            return dict(mark) if mark else None

    def failed_models(self) -> list[dict]:
        """Every mark, as ``{"provider", "model", "reason", "agent"}``, in the order they were made."""
        with self._lock:
            items = sorted(self._failed.items(), key=lambda kv: kv[1]["at"])
        return [{"provider": k[0], "model": k[1], "reason": v["reason"], "agent": v["agent"]} for k, v in items]


def current() -> CallScope | None:
    """The scope of the turn or op running in this context, if any."""
    return _CURRENT.get()


@contextlib.contextmanager
def scope() -> Iterator[CallScope]:
    """Open a scope for a turn or an op; inside another scope, share that one."""
    outer = _CURRENT.get()
    if outer is not None:
        yield outer
        return
    opened = CallScope()
    token = _CURRENT.set(opened)
    try:
        yield opened
    finally:
        _CURRENT.reset(token)

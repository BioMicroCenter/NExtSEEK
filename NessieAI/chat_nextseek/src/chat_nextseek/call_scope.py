"""What one NS turn, or one Container-CC op, has learned about its models, and by when it must be done (2026-09-28).

One ``CallScope`` per turn or op, held in a ContextVar beside the cost collector (``turn_spend``). It remembers every
model that failed in this turn, keyed by (catalog provider, model id), so a stall is paid once per turn instead of once
per agent: a later call whose primary failed earlier starts on its fallback without asking the primary, and a call
whose primary and fallback both failed earlier fails at once without calling either (``schema_helper._Failover``).

* Who opens it: ``turn_spend.collects_turn`` (every NS entry point: ``run_query``, ``run_query_plan``,
  ``run_pipeline_launch``) and ``NessieAI/ns/granular.run_op`` (each CC op). A turn inside a turn shares the outer
  scope. A call made outside any scope asks its primary as before.
* How it shows: a ``model_fallback`` item carries ``remembered: true`` when the call skipped a primary that failed
  earlier (a skip, not a new failure) and ``not_called: true`` when it did not ask a fallback that failed earlier;
  the ledger has ``fallback_remembered`` and ``not_called`` records.
* What marks a model: a failure the ladder moves on and that says the model itself is failing: a timeout, a 5xx, a
  429, a dropped connection or a refused model. Not an empty body (one response), a 400 or bad output. The parsers'
  own timeout does not mark either (``call_budgets``: their 35 s is a speed preference, not a stall test). One
  strike: the first such failure marks (ruling D4). A timeout the op's deadline cut short marks nothing.
* What resets it: only the end of the turn or op. A marked model gets no further call in this scope, so nothing
  could show it healthy again; the next turn starts clean, on a fresh socket.
* The deadline (F4, ruling D5): a Container-CC op opens its scope with one (55 s, inside the sidecar's 60 s;
  the aggregate op tightens it to its own 50 s), and the ladder cuts every attempt's wall clock to fit it. A first
  try that can still move gets at most what is left minus ``MOVE_RESERVE_S``, never below ``MIN_FIRST_TRY_S``,
  except for the agents whose budget says ``op_move_reserve=False`` (the graph agent and the report writer, whose
  move could not redo their work in that time), which get what is left; the moved call gets at most what is left;
  with ``DEADLINE_FLOOR_S`` or less left no call starts. An NS turn opens
  its scope with no deadline, so nothing is cut.
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

__all__ = ["CallScope", "current", "scope", "limit_current", "MOVE_RESERVE_S", "MIN_FIRST_TRY_S", "DEADLINE_FLOOR_S"]

_CURRENT: contextvars.ContextVar["CallScope | None"] = contextvars.ContextVar(
    "chat_nextseek_call_scope", default=None,
)

#: The clock, one name so tests can stand in for it.
_monotonic = time.monotonic

#: Under a deadline, what a first try leaves for the one move: enough for a Sonnet 4.6 call on these prompts.
MOVE_RESERVE_S = 20.0
#: Under a deadline, the shortest first try: a nearly spent op must not cut every healthy call.
MIN_FIRST_TRY_S = 5.0
#: Under a deadline, no call starts with this much or less left: nobody would read its answer.
DEADLINE_FLOOR_S = 2.0

ModelKey = tuple[str, str]


class CallScope:
    """The models that failed in one turn or op, and the deadline it must answer by (None: no deadline)."""

    def __init__(self, deadline_s: float | None = None) -> None:
        self._lock = threading.Lock()
        self._failed: dict[ModelKey, dict] = {}
        self.total_s: float | None = None
        self.deadline: float | None = None
        if deadline_s is not None:
            self.limit(deadline_s)

    def limit(self, seconds: float) -> None:
        """Answer within ``seconds`` from now; a deadline is only ever brought forward, never pushed back."""
        deadline = _monotonic() + seconds
        with self._lock:
            if self.deadline is None or deadline < self.deadline:
                self.deadline = deadline
                self.total_s = seconds

    def remaining(self) -> float | None:
        """Seconds until the deadline (negative once it has passed), or None with no deadline."""
        with self._lock:
            deadline = self.deadline
        return None if deadline is None else deadline - _monotonic()

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


def limit_current(seconds: float) -> CallScope | None:
    """Bring the running scope's deadline forward to ``seconds`` from now; the scope, or None when there is none."""
    current_scope = _CURRENT.get()
    if current_scope is not None:
        current_scope.limit(seconds)
    return current_scope


@contextlib.contextmanager
def scope(deadline_s: float | None = None) -> Iterator[CallScope]:
    """Open a scope for a turn or an op; inside another scope, share that one (and bring its deadline forward)."""
    outer = _CURRENT.get()
    if outer is not None:
        if deadline_s is not None:
            outer.limit(deadline_s)
        yield outer
        return
    opened = CallScope(deadline_s)
    token = _CURRENT.set(opened)
    try:
        yield opened
    finally:
        _CURRENT.reset(token)

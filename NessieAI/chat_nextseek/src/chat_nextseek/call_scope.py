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
  own timeout does not mark either (``call_budgets``: their first try is a speed preference, not a stall test). One
  strike: the first such failure marks (ruling D4). A timeout the op's deadline cut short marks nothing.
* What resets it: only the end of the turn or op. A marked model gets no further call in this scope, so nothing
  could show it healthy again; the next turn starts clean, on a fresh socket.
* The deadline (F4, ruling D5): a Container-CC op opens its scope with one (90 s for graph, parse and
  aggregate, 55 s for entity and the API ops; on the sidecar road 55 s, inside the sidecar's 60 s; the aggregate op
  tightens it to its own 85 s), and the ladder cuts every attempt's wall clock to fit it. A first
  try that can still move gets at most what is left minus ``MOVE_RESERVE_S``, never below ``MIN_FIRST_TRY_S``,
  except for the agents whose budget says ``op_move_reserve=False`` (the report writer, whose
  move could not redo its work in that time), which get what is left; the moved call gets at most what is left;
  with ``DEADLINE_FLOOR_S`` or less left no call starts. Inside an op
  (``scope(..., op=True)``, only ``run_op``) an agent's ``op_first_try_s`` caps its first try further. An NS turn opens
  its scope with no deadline, so nothing is cut. The waits that are not
  model calls (Neo4j, Django's own REST calls) read it through ``time_left_for``.
* Threads: marks and reads take a lock. Only the caller's thread marks (the wall-clock worker threads never touch the
  scope, so an abandoned call that answers late cannot). The aggregate op's parts run on pool threads and get this
  same object through ``contextvars.copy_context``.
* Across a turn's ops: NessieAI/ns/granular.run_op seeds each op's scope from the turn's stored strikes and stores
  its new ones back (seed / strikes); an NS turn seeds its scope with its vocabulary pre-run's (chat_nextseek/vocabulary.take).
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from typing import Any, Iterator

__all__ = ["CallScope", "current", "scope", "limit_current", "mark", "time_left_for", "MOVE_RESERVE_S", "MIN_FIRST_TRY_S", "DEADLINE_FLOOR_S"]

_CURRENT: contextvars.ContextVar["CallScope | None"] = contextvars.ContextVar(
    "chat_nextseek_call_scope", default=None,
)

#: The clock, one name so tests can stand in for it.
_monotonic = time.monotonic

#: Under a deadline, what a first try leaves for the one move: enough for a Sonnet 5.5 call on these prompts.
MOVE_RESERVE_S = 20.0
#: Under a deadline, the shortest first try: a nearly spent op must not cut every healthy call.
MIN_FIRST_TRY_S = 5.0
#: Under a deadline, no call starts with this much or less left: nobody would read its answer.
DEADLINE_FLOOR_S = 2.0

ModelKey = tuple[str, str]


class CallScope:
    """The models that failed in one turn or op, and the deadline it must answer by (None: no deadline)."""

    def __init__(self, deadline_s: float | None = None, *, op: bool = False) -> None:
        self._lock = threading.Lock()
        self._failed: dict[ModelKey, dict] = {}
        self.marks: list[tuple[str, float]] = []
        self.total_s: float | None = None
        self.deadline: float | None = None
        #: True for a Container-CC op's scope (``NessieAI/ns/granular.run_op``): some agents cut their first try
        #: further inside an op (``CallBudget.op_first_try_s``). A deadline alone (the pre-run, a nested NS turn)
        #: is not an op.
        self.is_op = op
        if deadline_s is not None:
            self.limit(deadline_s)

    def mark(self, name: str) -> None:
        """Record when a named step of the op happened (round 6 timing log); parts on pool threads share it."""
        with self._lock:
            self.marks.append((name, _monotonic()))

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

    def seed(self, strikes: Any) -> None:
        """Mark the models an earlier op or the pre-run of this turn found failing, each ``[provider, model,
        reason]`` as a turn stores it (``NessieAI/ns/turn_memory.py``). Malformed items are skipped; a model this
        scope already marked keeps its own mark."""
        for item in strikes or ():
            if (isinstance(item, (list, tuple)) and len(item) >= 3
                    and all(isinstance(part, str) and part for part in item[:3])):
                self.mark_failed((item[0], item[1]), reason=item[2], agent=None)

    def strikes(self) -> list[list[str]]:
        """Every mark as ``[provider, model, reason]``, in the order they were made: what a turn stores."""
        return [[m["provider"], m["model"], m["reason"]] for m in self.failed_models()]


def current() -> CallScope | None:
    """The scope of the turn or op running in this context, if any."""
    return _CURRENT.get()


def mark(name: str) -> None:
    """Mark a step on the running scope; nothing outside a scope."""
    current_scope = _CURRENT.get()
    if current_scope is not None:
        current_scope.mark(name)


def limit_current(seconds: float) -> CallScope | None:
    """Bring the running scope's deadline forward to ``seconds`` from now; the scope, or None when there is none."""
    current_scope = _CURRENT.get()
    if current_scope is not None:
        current_scope.limit(seconds)
    return current_scope


def time_left_for(base_s: float) -> float | None:
    """How long a wait may last now: ``base_s`` outside a deadline; inside one, ``base_s`` or what is left if that is
    less; None when ``DEADLINE_FLOOR_S`` or less is left, and then nothing should start.

    For the waits that are not model calls (a Neo4j statement, a REST call Django makes to itself): an op's limit caps
    them too (approach 1, piece 2), so no request is still running after the op has answered.
    """
    current_scope = _CURRENT.get()
    left = None if current_scope is None else current_scope.remaining()
    if left is None:
        return base_s
    if left <= DEADLINE_FLOOR_S:
        return None
    return min(base_s, left)


@contextlib.contextmanager
def scope(deadline_s: float | None = None, *, op: bool = False) -> Iterator[CallScope]:
    """Open a scope for a turn or an op (``op=True`` only from run_op); inside another scope, share that one (and
    bring its deadline forward, and mark it an op when ``op``)."""
    outer = _CURRENT.get()
    if outer is not None:
        if deadline_s is not None:
            outer.limit(deadline_s)
        if op:
            outer.is_op = True
        yield outer
        return
    opened = CallScope(deadline_s, op=op)
    token = _CURRENT.set(opened)
    try:
        yield opened
    finally:
        _CURRENT.reset(token)

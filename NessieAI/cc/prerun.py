"""The vocabulary pre-run: the turn's vocabulary, resolved as the question arrives, beside the router (2026-09-28).

``start_task`` (``NessieAI/cc/turn.py``) starts one per question on a small pool, on the NS turn's own config bound
to the caller's own login and project scope (never the prod-credential swap), unless the switch is off or the turn
will not use it. The pool thread only computes: ``resolve_vocabulary`` (and the parser, with
``NESSIE_PARSER_START=early``) inside its own cost collector and failed-model scope, closing its database connection
at start and end. It sends no event: ``QueryTask.progress`` is read, changed and saved without a lock, so only the turn
thread reports. "Vocabulary ready" goes out from ``result``, which only the turn thread calls.

An NS turn takes the result through ``chat_nextseek.vocabulary.take`` (its spend and failed models become the turn's).
A Container-CC turn waits for it up to ``CC_WAIT_S`` past staging, then ``hand_to_turn`` stores it on the ``CCTurn``,
now or, when it finishes later, from the pool thread.

Limits (2026-09-30): at most ``PRERUN_WORKERS`` run and ``PRERUN_QUEUE`` wait; past that ``start_prerun`` returns a
skipped pre-run at once and the engine resolves the vocabulary itself, as it does with the switch off. Each runs under
``PRERUN_DEADLINE_S``. One still queued is cancelled at hand-off and at the turn's end (``cancel``), and every pre-run
ends with one of ``OUTCOMES`` (``final_outcome``), which the turn reports with its duplicate entity calls.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

from django.conf import settings
from django.db import close_old_connections

from chat_nextseek import call_scope, turn_spend
from chat_nextseek import vocabulary as vocabulary_mod
from chat_nextseek.llm_clients import LLMFatalError
from chat_nextseek.schemas import EntityAgentOutput

logger = logging.getLogger(__name__)

#: The prelude_step labels (brain change: routing progress events, reviewed by the operator).
READING_YOUR_QUESTION = "Reading your question"
CHOOSING_AN_ENGINE = "Choosing an engine"
VOCABULARY_READY = "Vocabulary ready"

PRERUN_SETTING = "NESSIE_VOCAB_PRERUN"
PARSER_START_SETTING = "NESSIE_PARSER_START"

#: How long a Container-CC turn's start waits for the pre-run past staging (spec piece 3).
CC_WAIT_S = 3.0

#: At most this many pre-runs run at once, and at most PRERUN_QUEUE wait behind them; one more is skipped, never
#: queued, and its turn resolves the vocabulary itself (2026-09-30).
PRERUN_WORKERS = 4
PRERUN_QUEUE = 4
#: The pre-run's own CallScope deadline: every model call in it is cut to fit, so a running pre-run ends by then.
# 60 s, not 30: since origin/dev caa55340 the entity step's own first try is 30 s (medium thinking), and F3-F5 keeps a
# 20 s move reserve inside a deadline, so a 30 s deadline would cut that first try to about 10 s.
PRERUN_DEADLINE_S = 60.0

#: How a pre-run ended, reported once per turn (Task 6's "vocabulary_prerun" event). completed: its vocabulary was in
#: before the turn stopped waiting; late: the turn went on without it (it may still reach a Container-CC row later);
#: skipped: never started (switch, route, login, or the pool full); failed: finished without a vocabulary;
#: cancelled: dropped while still queued.
COMPLETED, LATE, SKIPPED, FAILED, CANCELLED = "completed", "late", "skipped", "failed", "cancelled"
OUTCOMES = (COMPLETED, LATE, SKIPPED, FAILED, CANCELLED)

_POOL = ThreadPoolExecutor(max_workers=PRERUN_WORKERS, thread_name_prefix="nessie-vocab")
#: Admission: running plus waiting. Taken in start_prerun without blocking, given back when the future is done
#: (finished or cancelled).
_ADMIT = threading.BoundedSemaphore(PRERUN_WORKERS + PRERUN_QUEUE)


def enabled() -> bool:
    """``NESSIE_VOCAB_PRERUN``: anything but ``off`` is on."""
    return str(getattr(settings, PRERUN_SETTING, "on") or "on").strip().lower() != "off"


def parser_start() -> str:
    """``NESSIE_PARSER_START``: ``early`` or (anything else) ``after_route``."""
    value = str(getattr(settings, PARSER_START_SETTING, "after_route") or "").strip().lower()
    return "early" if value == "early" else "after_route"


class Prerun:
    """One question's pre-run. Built unstarted; ``start_prerun`` submits it."""

    def __init__(self) -> None:
        self._future = None
        self._announced = False
        #: An outcome the turn fixed (late, cancelled); the others are read off the future (``outcome``).
        self._fixed: str | None = None
        #: True once the pool thread began the body (the entity step ran, or was at least started).
        self._ran = False
        #: How many times the NS turn resolved the vocabulary itself (``note_resolved_in_turn``, from
        #: ``vocabulary.take``).
        self.turn_resolutions = 0
        #: Called once, by ``result``, the first time it hands out a vocabulary; set by the turn thread.
        self.announce: Callable[[], None] | None = None
        self.elapsed_s: float | None = None
        self.diagnostics: dict[str, Any] = {}
        #: The early parser plan (``model_dump(mode="json")``), NESSIE_PARSER_START=early only.
        self.plan: dict[str, Any] | None = None
        #: The models that failed in the pre-run, as ``[provider, model, reason]``.
        self.strikes: list[list[str]] = []
        #: The pre-run's own cost collector (``turn_spend.TurnSpend``).
        self.spend: turn_spend.TurnSpend | None = None

    @property
    def started(self) -> bool:
        return self._future is not None

    def done(self) -> bool:
        return self._future is not None and self._future.done()

    @property
    def ran(self) -> bool:
        """Whether the pool thread began the body: False when skipped, cancelled in the queue, or not yet started."""
        return self._ran

    def _fix(self, outcome: str) -> None:
        if self._fixed is None:
            self._fixed = outcome

    def cancel(self) -> bool:
        """Drop the pre-run if it is still queued; True when it was dropped (its place in the pool is given back by
        the future's done-callback). One already running goes on, inside ``PRERUN_DEADLINE_S``. Turn thread only."""
        if self._future is None or self._future.running() or self._future.done():
            return False
        if not self._future.cancel():
            return False
        self._fix(CANCELLED)
        return True

    @property
    def outcome(self) -> str | None:
        """One of ``OUTCOMES``, or None while it runs and the turn has not given up on it."""
        if self._fixed is not None:
            return self._fixed
        future = self._future
        if future is None:
            return SKIPPED
        if not future.done():
            return None
        if future.cancelled():
            return CANCELLED
        if future.exception() is not None or not isinstance(future.result(), EntityAgentOutput):
            return FAILED
        return COMPLETED

    def final_outcome(self) -> str:
        """The outcome at the turn's end: one still running then is late."""
        out = self.outcome
        if out is None:
            self._fix(LATE)
            return LATE
        return out

    def note_resolved_in_turn(self) -> None:
        """The NS turn resolved the vocabulary itself (``vocabulary.take`` handed out nothing)."""
        self.turn_resolutions += 1

    def duplicate_entity_calls(self, turn_resolutions: int) -> int:
        """Vocabulary resolutions for this turn after the first: the pre-run's (when it ran) plus the turn's own
        (``turn_resolutions``: the NS turn's, or a Container-CC turn's ops', ``turn_memory.vocabulary_resolutions``)."""
        return max(0, (1 if self._ran else 0) + int(turn_resolutions or 0) - 1)

    def _vocabulary(self, timeout_s: float | None) -> EntityAgentOutput | None:
        if self._future is None:
            return None
        try:
            out = self._future.result(timeout=timeout_s)
        except FutureTimeout:
            return None
        except CancelledError:
            return None
        except LLMFatalError as fatal:
            logger.info("vocabulary pre-run ended without a vocabulary: %s", fatal)
            return None
        except Exception:  # noqa: BLE001 - the turn resolves it itself
            logger.warning("vocabulary pre-run failed", exc_info=True)
            return None
        return out if isinstance(out, EntityAgentOutput) else None

    def result(self, timeout_s: float) -> EntityAgentOutput | None:
        """The vocabulary, waiting at most ``timeout_s``; None when skipped, failed or not ready in time.

        Call it only from the turn thread: the first vocabulary it hands out runs ``announce``. When it gives up on
        one still queued or running, the pre-run is late (the turn goes on without it)."""
        out = self._vocabulary(timeout_s)
        if out is None and self._future is not None and not self._future.done():
            self._fix(LATE)
        if out is not None and not self._announced:
            self._announced = True
            if self.announce is not None:
                try:
                    self.announce()
                except Exception:  # noqa: BLE001 - a progress event never fails a turn
                    logger.warning("could not announce the vocabulary", exc_info=True)
        return out

    @property
    def spend_usd(self) -> float:
        record = self.spend.summary() if self.spend is not None else {}
        total = record.get("total_cost_usd")
        return float(total) if isinstance(total, (int, float)) and not isinstance(total, bool) else 0.0

    @property
    def spend_partial(self) -> bool:
        if self.spend is None:
            return False
        record = self.spend.summary()
        return bool(record.get("cost_partial")) or record.get("total_cost_usd") is None

    def on_done(self, fn: Callable[["Prerun"], None]) -> None:
        """Run ``fn(self)`` once the pre-run has finished: now, in this thread, when it already has; else in the pool
        thread, which then closes its database connection before and after."""
        if self._future is None:
            return
        caller = threading.get_ident()

        def _callback(_future) -> None:
            in_pool = threading.get_ident() != caller
            if in_pool:
                close_old_connections()
            try:
                fn(self)
            except Exception:  # noqa: BLE001 - bookkeeping never fails a turn
                logger.warning("vocabulary pre-run hand-off failed", exc_info=True)
            finally:
                if in_pool:
                    close_old_connections()

        self._future.add_done_callback(_callback)

    def _body(self, config: Any, query: str, session: Any, early_parser: bool) -> EntityAgentOutput:
        self._ran = True
        close_old_connections()
        started = time.monotonic()
        scope = None
        try:
            # Its own deadline: every model call (and the early parser) is cut to fit PRERUN_DEADLINE_S.
            with turn_spend.collecting() as spend, call_scope.scope(deadline_s=PRERUN_DEADLINE_S) as scope:
                self.spend = spend
                out = vocabulary_mod.resolve_vocabulary(None, config, query, diagnostics=self.diagnostics)
                if early_parser:
                    try:
                        from chat_nextseek.portable import parser_agent
                        plan = parser_agent(session, config, query, out)
                        self.plan = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else None
                    except Exception:  # noqa: BLE001 - the turn's own parser runs as it always did
                        logger.warning("early parser failed; the turn parses after routing", exc_info=True)
                return out
        finally:
            self.elapsed_s = round(time.monotonic() - started, 3)
            self.strikes = scope.strikes() if scope is not None else []
            close_old_connections()


def start_prerun(session: Any, config: Any, query: str, *, skip: bool, early_parser: bool = False) -> Prerun:
    """Submit the pre-run for ``query``, or return one that never starts (``skip``, no config, an empty question, or
    ``PRERUN_WORKERS + PRERUN_QUEUE`` already admitted: never waits for a place).

    ``session`` is read only by the early parser, and must be a snapshot (``session_snapshot``), never the live
    session."""
    prerun = Prerun()
    if skip or config is None or not str(query or "").strip():
        return prerun
    admit = _ADMIT  # bound now: the done-callback must give back to the semaphore it took from (SC-10)
    if not admit.acquire(blocking=False):
        logger.info("vocabulary pre-run skipped: %d already running or waiting", PRERUN_WORKERS + PRERUN_QUEUE)
        return prerun
    try:
        future = _POOL.submit(prerun._body, config, str(query), session, early_parser)
    except Exception:  # noqa: BLE001 - a pool that cannot take work: the turn resolves it itself
        admit.release()
        logger.warning("vocabulary pre-run could not be submitted", exc_info=True)
        return prerun
    # The place is given back once the future is done: finished, failed or cancelled in the queue.
    future.add_done_callback(lambda _f: admit.release())
    prerun._future = future
    return prerun


def session_snapshot(session: Any) -> dict[str, Any]:
    """What the early parser reads of the chat (``results_history`` and ``chat_log``), copied in the turn thread so
    the pool thread never touches the live session."""
    return {"results_history": list(session.get("results_history") or []),
            "chat_log": list(session.get("chat_log") or [])}


def hand_to_turn(prerun: Prerun, turn: Any, *, user_question: str, store_early_plan: bool) -> threading.Event:
    """Store the pre-run's vocabulary, failed models and spend on the Container-CC turn's row, and its early plan
    under the user's question when ``store_early_plan``; now if it has finished, else when it does. The returned
    event is set once that is done (the turn's cost is partial until then). A pre-run still queued is cancelled
    here: the first op that needs the vocabulary resolves it (hand-off, 2026-09-30)."""
    from NessieAI.ns import turn_memory

    settled = threading.Event()
    if not prerun.started or prerun.cancel():
        settled.set()
        return settled

    def _settle(p: Prerun) -> None:
        try:
            out = p._vocabulary(0)
            if out is not None:
                turn_memory.store_vocabulary(turn, out.model_dump(mode="json"))
            if p.strikes:
                turn_memory.merge_strikes(turn, p.strikes)
            turn_memory.add_spend(turn, p.spend_usd, partial=p.spend_partial)
            if store_early_plan and isinstance(p.plan, dict):
                turn_memory.store_plan(turn, user_question, p.plan)
        finally:
            settled.set()

    prerun.on_done(_settle)
    return settled

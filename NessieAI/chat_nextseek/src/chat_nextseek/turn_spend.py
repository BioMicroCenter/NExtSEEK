"""What one NS turn's model calls cost, which models answered, and what fell back.

One collector per turn, held in a ContextVar. ``collects_turn`` starts it on the
orchestrator's entry points (``run_query``, ``run_query_plan``, ``run_pipeline_launch``)
and ends it when the turn returns; a turn inside a turn adds to the outer one. Every
ledger write of the recovery ladder (``_call_with_recovery``) and of the tool loop
(``tool_loop.call_tools``) is handed to ``record_call`` right after it is written, in
the thread that made the call, so the ContextVar is the turn's own. With no collector
(a CC op answered by NS agents, a script) ``record_call`` does nothing.
``_emit_query_complete`` reads ``turn_record()`` into the turn's ``query_complete``.

Spend is summed from USAGE, never by counting ledger records:

* a call that returned usage is priced from it (``model_prices.call_cost``). An empty
  body writes two records, ``empty_completion`` with the usage and then
  ``service_unavailable`` without it, and is one billed call;
* an attempt the wall clock abandoned (a timeout) may still be billed and its usage is
  never seen; a connection error may have reached the provider. Each is an unobserved
  call and makes the turn's cost partial;
* a 5xx, a 429 and a 400 are not billed: nothing is recorded;
* a model with no price is named in ``unpriced_models`` and makes the cost partial.

``total_cost_usd`` is the sum of the priced calls, 0.0 for a turn with no model call,
and None for a turn whose calls were all unpriced or unobserved: spend that was never
seen is never reported as zero.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import threading
from typing import Any, Callable, Iterator, TypeVar

from . import model_prices
from .llm_clients import LLMAPIConnectionError, LLMTimeoutError

__all__ = ["TurnSpend", "current", "record_call", "collecting", "collects_turn", "turn_record", "cost_fields"]

_F = TypeVar("_F", bound=Callable[..., Any])

_CURRENT: contextvars.ContextVar["TurnSpend | None"] = contextvars.ContextVar(
    "chat_nextseek_turn_spend", default=None,
)

# The usage fields a call row keeps, as the clients write them (llm_clients.py).
_USAGE_FIELDS = (
    "prompt_tokens", "completion_tokens", "thoughts_tokens", "cached_tokens",
    "cache_read_tokens", "cache_write_tokens", "cache_write_5m_tokens",
    "cache_write_1h_tokens", "cache_ttl",
)
_BILLED_KEYS = ("input", "cache_read", "cache_write_5m", "cache_write_1h", "output")


def _usage_dict(usage: Any) -> dict | None:
    """The call's usage as a plain dict: the clients write one, OpenAI's SDK an object."""
    if usage is None or isinstance(usage, dict):
        return usage
    try:
        return usage.model_dump()
    except Exception:
        try:
            return dict(usage)
        except Exception:
            return None


def _has_counts(usage: dict | None) -> bool:
    return isinstance(usage, dict) and any(
        isinstance(usage.get(k), (int, float)) and not isinstance(usage.get(k), bool)
        for k in ("prompt_tokens", "completion_tokens")
    )


class TurnSpend:
    """The model calls of one turn, as the ledger saw them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self.unobserved: list[dict[str, Any]] = []
        self.fallbacks: list[dict[str, Any]] = []
        self.answered: list[str] = []

    def record(self, entry: dict, *, resp: Any = None, err: BaseException | None = None) -> None:
        """Take one ledger record (``_ledger_entry``) and the response or error behind it."""
        agent, model = entry.get("agent"), entry.get("model")
        who = {"agent": agent, "provider": entry.get("provider"), "model": model,
               "attempt": entry.get("attempt"), "outcome": entry.get("outcome")}
        with self._lock:
            if entry.get("fallback_from") is not None:
                self.fallbacks.append({"agent": agent, "from": entry.get("fallback_from"), "to": model,
                                       "reason": entry.get("fallback_reason")})
            if resp is None:
                if isinstance(err, LLMTimeoutError):
                    self.unobserved.append({**who, "why": "timed out: abandoned while it may still be billed"})
                elif isinstance(err, LLMAPIConnectionError):
                    self.unobserved.append({**who, "why": "connection error: whether it was billed is unknown"})
                return
            if entry.get("outcome") == "ok" and model and model not in self.answered:
                self.answered.append(model)
            usage = _usage_dict(getattr(resp, "usage", None))
            if not _has_counts(usage):
                self.unobserved.append({**who, "why": "the response reported no usage"})
                return
            row = {**who, "usage": {k: usage[k] for k in _USAGE_FIELDS if usage.get(k) is not None}}
            try:
                day = model_prices.today()
                priced = model_prices.call_cost(model, usage, on=day)
                row.update(billed=priced.tokens, cost_usd=priced.cost_usd, priced_on=day.isoformat())
                if priced.cost_usd is None:
                    row["unpriced"] = priced.reason
            except Exception as exc:  # a missing or malformed table: the turn goes on, partial
                row.update(billed={}, cost_usd=None, unpriced=f"price table: {type(exc).__name__}")
            self.calls.append(row)

    def summary(self) -> dict[str, Any]:
        """The turn record: ``total_cost_usd``, ``cost_partial``, ``models_used``,
        ``model_fallback`` and the ``cost`` breakdown for ``debug``."""
        with self._lock:
            calls = [dict(c) for c in self.calls]
            unobserved = [dict(u) for u in self.unobserved]
            fallbacks = [dict(f) for f in self.fallbacks]
            answered = list(self.answered)
        priced = [c for c in calls if c.get("cost_usd") is not None]
        unpriced_models = sorted({str(c.get("model")) for c in calls if c.get("cost_usd") is None})
        if priced:
            total: float | None = round(sum(c["cost_usd"] for c in priced), 6)
        elif calls or unobserved:
            total = None
        else:
            total = 0.0
        partial = bool(unpriced_models or unobserved)
        try:
            version: str | None = model_prices.load_price_table().version
        except Exception:
            version = None
        return {
            "total_cost_usd": total,
            "cost_partial": partial,
            "models_used": answered,
            "model_fallback": fallbacks,
            "cost": {
                "total_cost_usd": total,
                "cost_partial": partial,
                "calls": calls,
                "by_model": _totals(calls, "model", with_tokens=True),
                "by_agent": _totals(calls, "agent"),
                "unpriced_models": unpriced_models,
                "unobserved_calls": unobserved,
                "price_table_version": version,
            },
        }


def _totals(calls: list[dict], key: str, *, with_tokens: bool = False) -> dict[str, dict]:
    """Calls and cost per ``key``; a group with an unpriced call has cost None."""
    out: dict[str, dict] = {}
    for c in calls:
        group = out.setdefault(str(c.get(key)), {"calls": 0, "cost_usd": 0.0,
                                                 **({k: 0 for k in _BILLED_KEYS} if with_tokens else {})})
        group["calls"] += 1
        if group["cost_usd"] is not None:
            group["cost_usd"] = None if c.get("cost_usd") is None else round(group["cost_usd"] + c["cost_usd"], 10)
        if with_tokens:
            for k in _BILLED_KEYS:
                group[k] += int((c.get("billed") or {}).get(k) or 0)
    return out


def current() -> TurnSpend | None:
    """The collector of the turn running in this context, if any."""
    return _CURRENT.get()


def record_call(entry: dict, *, resp: Any = None, err: BaseException | None = None) -> None:
    """Hand one ledger record to this turn's collector. A no-op outside a turn. Never raises."""
    spend = _CURRENT.get()
    if spend is None:
        return
    try:
        spend.record(entry, resp=resp, err=err)
    except Exception as exc:  # pragma: no cover - bookkeeping must never fail a turn
        print(f"[TURN_SPEND] could not record a call: {exc!r}")


@contextlib.contextmanager
def collecting() -> Iterator[TurnSpend]:
    """Collect this turn's calls; inside another turn, add to that one."""
    spend = _CURRENT.get()
    if spend is not None:
        yield spend
        return
    spend = TurnSpend()
    token = _CURRENT.set(spend)
    try:
        yield spend
    finally:
        _CURRENT.reset(token)


def collects_turn(fn: _F) -> _F:
    """Decorate an NS turn entry point so its model calls are collected.

    An exception that escapes the turn (``run_pipeline_launch`` does not guard the
    pipeline agent, so a tool loop's ``LLMFatalError`` escapes it) takes the turn's
    record with it as ``turn_record``: the collector is gone by the time the pipeline
    body reports it, and ``cost_fields`` reads it back.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with collecting() as spend:
            try:
                return fn(*args, **kwargs)
            except BaseException as exc:
                if getattr(exc, "turn_record", None) is None:
                    try:
                        exc.turn_record = spend.summary()
                    except Exception:  # pragma: no cover - bookkeeping must never mask the error
                        pass
                raise
    return wrapper  # type: ignore[return-value]


def cost_fields(exc: BaseException | None = None) -> dict[str, Any]:
    """The turn-record fields for a ``query_error`` that ends a turn: those of the turn
    that ended in ``exc`` (see ``collects_turn``), else those of the turn still running
    in this context; empty when there are none."""
    record = getattr(exc, "turn_record", None) if exc is not None else None
    if record is None:
        record = turn_record()
    if not isinstance(record, dict):
        return {}
    return {key: record[key] for key in ("total_cost_usd", "cost_partial", "models_used", "model_fallback")
            if key in record}


def turn_record() -> dict[str, Any] | None:
    """This turn's record (``TurnSpend.summary``), or None outside a turn. Never raises."""
    spend = _CURRENT.get()
    if spend is None:
        return None
    try:
        return spend.summary()
    except Exception as exc:  # pragma: no cover - bookkeeping must never fail a turn
        print(f"[TURN_SPEND] could not summarise the turn: {exc!r}")
        # No models_used or model_fallback: "nothing fell back" must not be read off a failure.
        return {"total_cost_usd": None, "cost_partial": True, "cost": {"error": repr(exc)}}

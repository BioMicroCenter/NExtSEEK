"""One turn's spend and one case's spend, summed one way everywhere.

Standard library only, on purpose. `output-skill/scripts/fetch_run.py` loads this file
by path so a grading pull sums a case exactly as the harness does, and that script runs
on an operator's host with python3 and nothing else. The runner and the manifest import
it by package.

The turn record every engine and the router write (the turn-record contract):

- `route_decided`: `router_cost_usd`, `router_cost_partial`, `router_model`,
  `router_fallback` (null, or `{from, to, reason}` where `to` may be `heuristic`).
- the turn's terminal event, `query_complete` or else `query_error`: `total_cost_usd`,
  `cost_partial`, `models_used`, `model_fallback` (a list, empty when nothing fell
  back). `cost_partial` is read off either engine: NS sets it when a call's usage was
  unseen or its model unpriced, CC when the turn ran an op whose NS agents bill outside
  Claude Code's cost (`NS_AGENT_OPS` in `NessieAI/cc/translate.py`).

A turn bills in two parts, the router's model call and the engine's turn. A turn's cost
is the sum of the parts that were observed. A part that did not run is not a part:
a `forced` route never calls the router (`NessieAI/router/policy.py`, `decide_route`),
and an `unrelated` route answers with canned text and calls no engine
(`NessieAI/cc/turn.py`, `_run`). Every other missing part is spend the harness did not
see, so the turn is partial, and a turn that saw no part at all is unmeasured. Spend
that was never observed is never a zero (`manifest.cost_summary`).
"""
from __future__ import annotations

import math

ROUTE_UNRELATED = "unrelated"
SOURCE_FORCED = "forced"


def usd(value) -> float | None:
    """A finite number as float, else None. A bool is never money."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _events(payload: dict) -> list:
    return (payload or {}).get("progress") or []


def _data(ev) -> dict:
    data = ev.get("data")
    return data if isinstance(data, dict) else {}


def _first_data(payload: dict, name: str) -> dict:
    for ev in _events(payload):
        if isinstance(ev, dict) and ev.get("event") == name:
            return _data(ev)
    return {}


def _last_data(payload: dict, name: str) -> dict | None:
    found = None
    for ev in _events(payload):
        if isinstance(ev, dict) and ev.get("event") == name:
            found = _data(ev)
    return found


def _list(value) -> list:
    return value if isinstance(value, list) else []


def read_turn(payload: dict) -> dict:
    """The money and model fields of one polled turn's payload.

    The router fields come from the FIRST `route_decided`, the event
    `route_observer.observe` reads the route from. The engine fields come from the last
    `query_complete`, or from the last `query_error` when the turn ended on one: that
    event carries `model_fallback` when a model failure ended the turn, which is exactly
    a turn a reader of fallbacks needs to see. A field of the wrong shape reads as
    absent: the record is read before the turn is scored, and one malformed field
    must not turn a paid turn into a harness error.
    """
    rd = _first_data(payload, "route_decided")
    end = _last_data(payload, "query_complete")
    if end is None:
        end = _last_data(payload, "query_error") or {}
    router_model = rd.get("router_model")
    router_fallback = rd.get("router_fallback")
    route, source = rd.get("route"), rd.get("source")
    # Per part, because a deploy where only one side writes its field must not read
    # as "no fallback". A part that did not run needs no report: a forced turn made
    # no router call and an `unrelated` turn ran no engine. A route-tier turn never
    # sees the engine's terminal event, so its engine side is unreported.
    router_reported = source == SOURCE_FORCED or "router_fallback" in rd
    engine_reported = route == ROUTE_UNRELATED or "model_fallback" in end
    return {
        "route": route,
        "source": source,
        "router_cost": usd(rd.get("router_cost_usd")),
        "router_cost_partial": rd.get("router_cost_partial") is True,
        "router_model": router_model if isinstance(router_model, str) else None,
        "router_fallback": router_fallback if isinstance(router_fallback, dict) else None,
        "engine_cost": usd(end.get("total_cost_usd")),
        "cost_partial": end.get("cost_partial") is True,
        "models_used": [m for m in _list(end.get("models_used")) if isinstance(m, str)],
        "model_fallback": [f for f in _list(end.get("model_fallback")) if isinstance(f, dict)],
        # Whether this turn said, for every part that ran, whether it fell back. A
        # server older than the contract writes neither key, and "no fallback" must
        # not be read off silence.
        "router_fallback_reported": router_reported,
        "engine_fallback_reported": engine_reported,
        "fallback_reported": router_reported and engine_reported,
    }


def turn_total(*, engine_cost, router_cost, route=None, source=None,
               cost_partial=False, router_cost_partial=False) -> tuple[float | None, bool]:
    """(cost, partial) for one turn: the observed parts summed.

    `partial` is True when a part that ran was not observed, or a part says its own
    figure is a floor. A turn that observed no part returns (None, True): unmeasured.
    """
    parts: list[float] = []
    missing = False
    if source != SOURCE_FORCED:
        if router_cost is None:
            missing = True
        else:
            parts.append(router_cost)
    if route != ROUTE_UNRELATED:
        if engine_cost is None:
            missing = True
        else:
            parts.append(engine_cost)
    if not parts:
        # Both parts ran and neither was seen; or, never produced by the product, a
        # forced `unrelated` turn with nothing to bill.
        return (None, True) if missing else (0.0, False)
    return round(sum(parts), 6), bool(missing or cost_partial or router_cost_partial)


def case_total(turns, *, missing_turns: int = 0) -> tuple[float | None, bool]:
    """(cost, partial) for one case from its turns' (cost, partial) pairs.

    The cost is the sum of every turn's observed cost, never the last turn's. It is
    None when no turn observed anything, and then `partial` is False: None already says
    nothing was seen, and `partial` qualifies a number. `missing_turns` counts turns
    that were sent and never recorded (the driver raised), which makes a number partial.
    """
    turns = list(turns)
    observed = [c for c, _ in turns if c is not None]
    if not observed:
        return None, False
    partial = missing_turns > 0 or any(p for _, p in turns)
    return round(sum(observed), 6), bool(partial)


def fell_back(turn) -> bool:
    """Did any model of this turn fall back? `turn` is a `read_turn` dict or an object
    with the same attributes."""
    get = turn.get if isinstance(turn, dict) else (lambda k, d=None: getattr(turn, k, d))
    return bool(get("model_fallback") or []) or get("router_fallback") is not None

"""The graph reviewer's Neo4j-backed pieces: Tier 2 (bounded count variants) and the live catalog Tier 1 reads.

Tier 1 (``graph_review.review_tier1``) reads a finished graph turn and names what it matched. When one of its checks
has a count that would settle the question (``stem_miss``: every spelling of tif, not only tiff;
``all_question_narrowed``: the types with no samples too; ``zero_unproven_base``: the starting set of a zero;
``unapplied_value``: how many different values the matched set holds where the question named one), Tier 2 runs
that count, at most ``max_variants`` of them per turn, inside a wall-clock budget: no variant starts with under
``MIN_START_S`` left, and each gets ``min(COUNT_TIMEOUT_S, whole seconds left)``. A statement that took over
``SKIP_AFTER_MS`` gets no variant at all.

Every count goes through ``tool_neo4j_query`` like any graph query: the write check, the caller's project scope (the
prover injects it or refuses), the READ transaction. ``count_of`` asks the tool for the total only: one statement,
the tool's total probe over the scoped text, under one timeout. It counts rows, so a variant must return one row per
counted thing: ``relaxed_variants`` rewrites a single ``count(...)`` RETURN that way, skips any other aggregate, and
makes no variant at all for a statement with a top-level UNION.

For ``unapplied_value`` on a variable bound only inside an EXISTS (an alignment under the counted patients), the
count is the matched set split by the named attribute (a rows query, ``rows_of``), with the count narrowed to the named
value as its fallback; either sets the "Only <value>" chip's ``expected_count``.

``live_values(config)`` is the ``CatalogProvider`` Tier 1 reads in production, one fresh object per turn:
``values`` runs one capped DISTINCT query per (label, attribute) through the tool (so its values are the caller's
own); ``attributes_holding`` asks once per type, through the tool, which attributes store one of the question's
spellings (``probe_statement``: index seeks on a large type's indexed attributes, ``graph_catalog.get_seekable``).
Both are cached per process per scope for ``VALUES_TTL_S`` and share one budget of statement time per provider,
with at most ``max_cold`` uncached value lists; every call is recorded for ``lookups``. ``attributes`` and
``type_name`` come from ``graph_catalog.get_snapshot``, which is the same for every caller.

Nothing here raises to its caller and nothing here blocks a reply: a failure is a ``None``, an ``ok: False`` or a
recorded ``error``. Tests patch ``tool_neo4j_query``, ``count_of``, ``_now`` (the cache clock) and ``_clock`` (the
budget clock).
"""
from __future__ import annotations

import ast
import dataclasses
import re
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Callable

from . import graph_catalog
from .graph_review import (DISCLOSURE_MAX, TERM, VALUES_CAP, CatalogProvider, GraphReview, ReviewInput, _resolve,
                           _var_labels)
from .graph_scope import scope_of
from .helpers.tools.neo4j import split_trailing_limit, tool_neo4j_query

COUNT_TIMEOUT_S = 5          # each count variant, at most
VALUES_TIMEOUT_S = 3         # each DISTINCT values query
SKIP_AFTER_MS = 5000         # an original statement slower than this gets no Tier 2
MIN_START_S = 1.0            # no variant starts with less budget than this left
VALUES_TTL_S = graph_catalog.DETAIL_TTL_S
VALUES_CACHE_MAX = 512
MAX_COLD = 8                 # uncached value lists per turn
TIER1_BUDGET_S = 2.0         # the time Tier 1's uncached statements may take in one turn, all together
MIN_COLD_S = 0.25            # no uncached statement starts with less of that budget left
MIN_TIMEOUT_S = 0.5          # the shortest timeout an uncached statement is given
PROBE_MAX_CHARS = 12_000     # a probe statement's length before scoping (the prover refuses over 20,000)
PROBE_MAX_LABELS = 4         # types probed per turn
CALLS_LISTED = 40            # calls ``lookups`` lists

#: How a statement the server timed out reads in the tool's error (the code and the GQL status).
_TIMED_OUT = re.compile(r"TransactionTimedOut|25N14")

_LABEL_RE = re.compile(r"T_[A-Z0-9_]+")
_ATTR_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# The clocks; tests patch these names.
_now = time.monotonic        # the values cache
_clock = time.perf_counter   # the Tier 2 budget


# ---------------------------------------------------------------- reading Cypher at the top level ------------------
def _mask(cy: str) -> str:
    """``cy`` with the inside of every string, backtick name, comment and bracket pair blanked out (same length), so a
    keyword found in it is a top-level one."""
    buf = list(cy)
    depth, i, n = 0, 0, len(cy)
    while i < n:
        ch = cy[i]
        if ch in "'\"`":
            j = i + 1
            while j < n and cy[j] != ch:
                j += 2 if (cy[j] == "\\" and ch != "`") else 1
            for k in range(i + 1, min(j, n)):
                buf[k] = "_"
            i = j + 1
            continue
        if cy.startswith("//", i) or cy.startswith("/*", i):
            j = cy.find("\n", i) if cy[i + 1] == "/" else cy.find("*/", i + 2) + 2
            j = n if j < i + 2 else j
            for k in range(i, j):
                buf[k] = " "
            i = j
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth:
            buf[i] = "_"
        i += 1
    return "".join(buf)


_KEYWORD = re.compile(
    r"\b(?:(?:STARTS|ENDS)\s+WITH\b|(OPTIONAL\s+MATCH|MATCH|WHERE|WITH|RETURN|UNWIND|CALL|ORDER\s+BY|SKIP|LIMIT"
    r"|UNION|YIELD)\b)", re.I)
_AGGREGATE = re.compile(r"\b(?:count|sum|avg|min|max|collect|stdevp?|percentile(?:cont|disc))\s*\(", re.I)
_COUNT_ITEM = re.compile(r"count\s*\(\s*(DISTINCT\s+)?(\*|[A-Za-z_]\w*)\s*\)(?:\s+AS\s+\w+)?", re.I)


@dataclass(frozen=True)
class _Clause:
    kw: str      # "MATCH", "OPTIONAL MATCH", "WHERE", "WITH", "RETURN", "ORDER BY", ...
    start: int   # the keyword
    body: int    # just after the keyword
    end: int     # the next top-level keyword, or the end


def _clauses(cy: str, mask: str) -> list[_Clause]:
    hits = [(re.sub(r"\s+", " ", m.group(1)).upper(), m.start(), m.end())
            for m in _KEYWORD.finditer(mask) if m.group(1)]
    return [_Clause(kw, s, b, hits[i + 1][1] if i + 1 < len(hits) else len(cy)) for i, (kw, s, b) in enumerate(hits)]


def _split_top(text: str, mask: str, sep: str) -> list[str]:
    """``text`` split at every top-level ``sep`` (a regex over the mask), each part stripped."""
    parts, last = [], 0
    for m in re.finditer(sep, mask, re.I):
        parts.append(text[last:m.start()].strip())
        last = m.end()
    parts.append(text[last:].strip())
    return parts


def _final_return(cy: str, mask: str) -> _Clause | None:
    returns = [c for c in _clauses(cy, mask) if c.kw == "RETURN"]
    return returns[-1] if returns else None


def _return_items(cy: str, mask: str, ret: _Clause) -> list[str]:
    text = re.sub(r"^\s*DISTINCT\b", "", cy[ret.body:ret.end], flags=re.I)
    tmask = re.sub(r"^\s*DISTINCT\b", "", mask[ret.body:ret.end], flags=re.I)
    return [i for i in _split_top(text, tmask, r",") if i]


def _has_union(cy: str, mask: str) -> bool:
    """A top-level UNION: the last RETURN is only the last branch, so no rewrite or comparison of it is sound."""
    return any(c.kw == "UNION" for c in _clauses(cy, mask))


def _with_aggregates(cy: str, mask: str) -> bool:
    """A top-level WITH aggregates, so the rows after it are groups, not records."""
    return any(c.kw == "WITH" and _AGGREGATE.search(mask[c.body:c.end]) for c in _clauses(cy, mask))


def _row_level(cy: str) -> str | None:
    """``cy`` rewritten so it returns one row per thing it counts, or None when that cannot be done safely.

    A RETURN with no aggregate is kept. One ``count(*)``, ``count(v)`` or ``count(DISTINCT v)`` (grouping keys
    beside it are dropped) becomes one row per counted row, value or distinct value. Anything else (sum, two
    aggregates, a count over an expression, a WITH that aggregates first) is None."""
    mask = _mask(cy)
    ret = _final_return(cy, mask)
    if ret is None or _has_union(cy, mask) or _with_aggregates(cy, mask):
        return None
    items = _return_items(cy, mask, ret)
    aggregates = [i for i in items if _AGGREGATE.search(_mask(i))]
    if not aggregates:
        return cy
    m = _COUNT_ITEM.fullmatch(aggregates[0]) if len(aggregates) == 1 else None
    if m is None:
        return None
    distinct, arg = m.group(1), m.group(2)
    if arg == "*":
        tail = "RETURN 1 AS n"
    else:
        tail = f"WITH {'DISTINCT ' if distinct else ''}{arg} AS k WHERE k IS NOT NULL\nRETURN 1 AS n"
    return cy[:ret.start].rstrip() + "\n" + tail


def _strip_trailing_order(cy: str) -> str:
    """Drop a trailing top-level ORDER BY that follows the final RETURN: a count gains nothing from a sort."""
    mask = _mask(cy)
    clauses = _clauses(cy, mask)
    if len(clauses) >= 2 and clauses[-1].kw == "ORDER BY" and clauses[-2].kw == "RETURN":
        return cy[:clauses[-1].start].rstrip()
    return cy


# ---------------------------------------------------------------- count_of -----------------------------------------
_ANY_TRAILING_LIMIT = re.compile(r"\s+(?:SKIP\s+(?:\d+|\$\w+)\s+)?LIMIT\s+(?:\d+|\$\w+)\s*;?\s*$", re.I | re.S)


def _count_statement(cypher: str, parameters: dict | None) -> str:
    """``cypher`` without its trailing ``[SKIP n] LIMIT n``, trailing ``;`` and a trailing ORDER BY after the final
    RETURN: what the tool's total probe wraps."""
    body, _limit = split_trailing_limit(cypher, parameters)
    if body is None:
        body = _ANY_TRAILING_LIMIT.sub("", cypher)   # also a LIMIT $param that is not bound to an integer
    return _strip_trailing_order(body.rstrip().rstrip(";").rstrip())


def count_of(config, cypher: str, parameters: dict, *, timeout_s: int = COUNT_TIMEOUT_S) -> dict:
    """How many rows ``cypher`` returns, as ``{ok, total, error, elapsed_ms}``; never raises, never retries.

    The statement, without its trailing LIMIT (and a trailing ORDER BY), goes to the tool with ``total_only=True``:
    the write check and the scope run as for any query, then only the total probe, one READ transaction bounded by
    ``timeout_s``. A statement that returns aggregate rows is counted by its rows (one for a bare ``count(...)``):
    callers send row-level statements (``relaxed_variants`` does). A refusal, an error or an unknown total is
    ``ok: False``."""
    t0 = time.perf_counter()

    def done(ok: bool, total=None, error=None) -> dict:
        return {"ok": ok, "total": total, "error": error, "elapsed_ms": int((time.perf_counter() - t0) * 1000)}

    try:
        result = tool_neo4j_query(config, _count_statement(cypher, parameters), parameters, timeout_s=timeout_s,
                                  total_only=True)
    except Exception as exc:
        return done(False, error=f"{type(exc).__name__}: {exc}"[:200])
    if not isinstance(result, dict) or not result.get("ok"):
        error = result.get("error") if isinstance(result, dict) else None
        return done(False, error=str(error or "the count query failed")[:300])
    total = result.get("total")
    if not isinstance(total, int) or isinstance(total, bool):
        return done(False, error="the total could not be counted")
    return done(True, total)


def rows_of(config, cypher: str, parameters: dict, *, timeout_s: float = COUNT_TIMEOUT_S) -> dict:
    """The rows ``cypher`` returns, as ``{ok, rows, error, elapsed_ms}``; never raises, never retries. For a Tier 2
    breakdown, whose cap sits in a WITH so the tool runs it once. A refusal or an error is ``ok: False``."""
    t0 = time.perf_counter()

    def done(ok: bool, rows=None, error=None) -> dict:
        return {"ok": ok, "rows": rows or [], "error": error, "elapsed_ms": int((time.perf_counter() - t0) * 1000)}

    try:
        result = tool_neo4j_query(config, cypher, parameters, timeout_s=timeout_s)
    except Exception as exc:
        return done(False, error=f"{type(exc).__name__}: {exc}"[:200])
    if not isinstance(result, dict) or not result.get("ok"):
        error = result.get("error") if isinstance(result, dict) else None
        return done(False, error=str(error or "the breakdown query failed")[:300])
    return done(True, [r for r in (result.get("data") or []) if isinstance(r, dict)])


# ---------------------------------------------------------------- live_values ---------------------------------------
_VALUES: "OrderedDict[tuple, tuple[float, tuple]]" = OrderedDict()
_VALUES_LOCK = threading.Lock()


def _cache_get(key: tuple) -> tuple | None:
    with _VALUES_LOCK:
        hit = _VALUES.get(key)
        if hit is None:
            return None
        if _now() - hit[0] >= VALUES_TTL_S:
            del _VALUES[key]
            return None
        _VALUES.move_to_end(key)
        return hit[1]


def _cache_put(key: tuple, values: tuple) -> None:
    with _VALUES_LOCK:
        _VALUES[key] = (_now(), values)
        _VALUES.move_to_end(key)
        while len(_VALUES) > VALUES_CACHE_MAX:
            _VALUES.popitem(last=False)


def reset_values_cache() -> None:
    """Forget every cached value list (tests)."""
    with _VALUES_LOCK:
        _VALUES.clear()


def cache_size() -> int:
    with _VALUES_LOCK:
        return len(_VALUES)


def values_statement(label: str, attribute: str) -> str:
    """The DISTINCT statement for one validated (label, attribute). The cap sits inside, not in a trailing LIMIT,
    so the tool never probes it for a total (that would be a second full label scan)."""
    return (f"MATCH (s:{label}) WHERE s.{attribute} IS NOT NULL "
            f"WITH toString(s.{attribute}) AS v, count(*) AS n ORDER BY n DESC LIMIT {VALUES_CAP} RETURN v, n")


def probe_statement(label: str, attributes: list[str]) -> str:
    """One statement asking which of a validated label's plain ``attributes`` store one of ``$spellings``: one EXISTS
    per attribute, ``a0``, ``a1``, ... in order.

    The prover keeps a plain comparison like ``s.<a> IN $spellings`` outside the guard it wraps a caller's WHERE in, so
    an attribute with a range index is an index seek, and EXISTS stops at the first node the caller can see. No
    trailing LIMIT, so the tool never probes it for a total."""
    return "RETURN " + ", ".join(f"EXISTS {{ MATCH (s:{label}) WHERE s.{a} IN $spellings }} AS a{i}"
                                 for i, a in enumerate(attributes))


def _probe_chunks(label: str, attributes: list[str]) -> list[list[str]]:
    """``attributes`` in order, cut so each chunk's ``probe_statement`` is at most ``PROBE_MAX_CHARS`` long: as few
    statements as fit, since each is a round trip and a plan (a 64-attribute probe took 34 ms warm on dev, 528 ms the
    first time)."""
    chunks: list[list[str]] = []
    for attr in attributes:
        if chunks and len(probe_statement(label, chunks[-1] + [attr])) <= PROBE_MAX_CHARS:
            chunks[-1].append(attr)
        else:
            chunks.append([attr])
    return chunks


class _LiveCatalog:
    """A ``graph_review.CatalogProvider`` over the live graph, for one turn (``live_values``).

    Every uncached statement (a value list, a probe) goes through ``tool_neo4j_query`` under the caller's scope and
    spends from one budget of statement time: none starts with under ``MIN_COLD_S`` of it left, and each is given what
    is left, at least ``MIN_TIMEOUT_S`` and at most ``VALUES_TIMEOUT_S``. Only time inside those statements counts, so
    the follow-up loop, whose one provider serves several queries with model calls in between, is not starved by the
    time its model thinks. ``max_cold`` caps the uncached value lists as well. Every call is recorded (``lookups``),
    so a review that read nothing says why."""

    def __init__(self, config, max_cold: int, budget_s: float):
        self._config = config
        self._max_cold = max(0, int(max_cold))
        self._cold_left = self._max_cold
        self._budget_s = max(0.0, float(budget_s))
        self._spent_s = 0.0
        self._lock = threading.Lock()
        self._counts: Counter = Counter()
        self._calls: list[dict] = []
        self._spent_ms = 0
        self._probed: set[str] = set()

    # --- bookkeeping ---------------------------------------------------------------------------------------------
    def _record(self, kind: str, key: str, outcome: str, ms: int = 0, spent_s: float = 0.0) -> None:
        with self._lock:
            self._counts[outcome] += 1
            self._spent_ms += ms
            self._spent_s += spent_s
            if len(self._calls) < CALLS_LISTED:
                self._calls.append({"kind": kind, "key": key, "outcome": outcome, "ms": ms})

    def lookups(self) -> dict:
        """What this provider read: its budget, the time its uncached statements took, a count per outcome
        (cache, fetched, budget, failed, timeout, invalid, no_scope, skipped) and the first ``CALLS_LISTED`` calls."""
        with self._lock:
            return {"budget_s": self._budget_s, "max_cold": self._max_cold, "spent_ms": self._spent_ms,
                    "counts": dict(self._counts), "calls": [dict(c) for c in self._calls]}

    def _timeout(self) -> float | None:
        with self._lock:
            left = self._budget_s - self._spent_s
        if left < MIN_COLD_S:
            return None
        return max(MIN_TIMEOUT_S, min(float(VALUES_TIMEOUT_S), left))

    def _spend_cold(self) -> bool:
        with self._lock:
            if self._cold_left <= 0:
                return False
            self._cold_left -= 1
            return True

    def _scope_key(self) -> tuple | None:
        scope = scope_of(self._config)
        if scope is None:
            return None
        return ("admin",) if scope.is_admin else tuple(scope.project_ids)

    def _graph_key(self) -> tuple[str, str]:
        return (str(getattr(self._config, "NEO4J_URI", None)),
                str(getattr(self._config, "NEO4J_DATABASE", None) or "neo4j"))

    def _run(self, kind: str, key: str, statement: str, parameters: dict) -> dict | None:
        """The tool's result for one uncached statement, or None (recorded: budget, failed or timeout)."""
        timeout = self._timeout()
        if timeout is None:
            self._record(kind, key, "budget")
            return None
        t0 = _clock()
        try:
            result = tool_neo4j_query(self._config, statement, parameters, timeout_s=timeout)
        except Exception as exc:  # noqa: BLE001 (a reviewer read never raises to its caller)
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        spent = max(0.0, _clock() - t0)
        ms = int(round(spent * 1000))
        if isinstance(result, dict) and result.get("ok"):
            self._record(kind, key, "fetched", ms, spent)
            return result
        error = str(result.get("error") or "") if isinstance(result, dict) else ""
        self._record(kind, key, "timeout" if _TIMED_OUT.search(error) else "failed", ms, spent)
        return None

    # --- CatalogProvider -----------------------------------------------------------------------------------------
    def values(self, label: str, attribute: str) -> list[tuple[str, int]] | None:
        """The stored values of ``label.attribute`` the caller can see, most frequent first, at most ``VALUES_CAP``.
        None when the names are not plain, the config carries no scope, the query fails or is refused, or the turn has
        no budget or uncached read left."""
        name = f"{label}.{attribute}"
        try:
            if not (isinstance(label, str) and isinstance(attribute, str)
                    and _LABEL_RE.fullmatch(label) and _ATTR_RE.fullmatch(attribute)):
                self._record("values", name, "invalid")
                return None
            scope_key = self._scope_key()
            if scope_key is None:
                self._record("values", name, "no_scope")
                return None
            key = (*self._graph_key(), scope_key, label, attribute)
            hit = _cache_get(key)
            if hit is not None:
                self._record("values", name, "cache")
                return list(hit)
            if self._timeout() is None or not self._spend_cold():
                self._record("values", name, "budget")
                return None
            result = self._run("values", name, values_statement(label, attribute), {})
            if result is None:
                return None
            pairs = [(str(r["v"]), int(r["n"])) for r in (result.get("data") or [])
                     if isinstance(r, dict) and r.get("v") is not None and r.get("n") is not None]
            pairs.sort(key=lambda p: -p[1])
            _cache_put(key, tuple(pairs))
            return list(pairs)
        except Exception:  # noqa: BLE001
            self._record("values", name, "failed")
            return None

    def _value_types(self, label: str) -> dict[str, str] | None:
        try:
            snapshot = graph_catalog.get_snapshot(self._config)
            title = next((row.title for row in snapshot.index if row.label == label), None)
            details = graph_catalog.get_type_details(self._config, [title]) if title else []
            return {a.title: a.value_type for a in details[0].attributes} if details else None
        except Exception:  # noqa: BLE001
            return None

    def _probe_plan(self, label: str, plain: list[str]) -> tuple[list[str], list[str]]:
        """(the attributes to probe, the attributes skipped). A large type, one with a range index on a string
        attribute (graph_sync indexes a string only at 1,000 samples or more), is probed on its indexed attributes
        only: an unindexed attribute is a full scan of the type, about 2 microseconds a node on the dev graph, so 57 of
        them on 12,000 TCGA patients cost 1.6 s. A small type, or one whose indexes cannot be read, is probed whole."""
        try:
            seekable = graph_catalog.get_seekable(self._config)
        except Exception:  # noqa: BLE001
            seekable = None
        if seekable is None:
            return plain, []
        indexed = seekable.get(label) or frozenset()
        types = self._value_types(label)
        large = any(types.get(a) == "string" for a in indexed) if types is not None else bool(indexed)
        if not large:
            return plain, []
        return [a for a in plain if a in indexed], [a for a in plain if a not in indexed]

    def attributes_holding(self, label: str, attributes, spellings) -> set[str] | None:
        """Which of ``attributes`` store one of ``spellings`` as a value the caller can see (``probe_statement``, one
        per chunk of at most ``PROBE_MAX_CHARS``), plus every attribute of a chunk that could not be asked (the budget,
        a refusal, an error), so that one is still read when the budget allows. A large type's unindexed attributes are
        skipped and not returned (``_probe_plan``). At most ``PROBE_MAX_LABELS`` types are probed per turn; past that
        every attribute is returned. None when nothing could be asked: a bad label, no scope, an error."""
        name = f"{label} ({len(attributes or ())} attributes)"
        try:
            if not (isinstance(label, str) and _LABEL_RE.fullmatch(label)):
                self._record("probe", name, "invalid")
                return None
            scope_key = self._scope_key()
            if scope_key is None:
                self._record("probe", name, "no_scope")
                return None
            plain = sorted({a for a in (attributes or ()) if isinstance(a, str) and _ATTR_RE.fullmatch(a)})
            spell = sorted({s for s in (spellings or ()) if isinstance(s, str) and s})
            if not plain or not spell:
                return set()
            probe, skipped = self._probe_plan(label, plain)
            if skipped:
                self._record("probe", f"{label}: {len(skipped)} unindexed attributes of a large type", "skipped")
            if not probe:
                return set()
            key = ("probe", *self._graph_key(), scope_key, label, tuple(probe), tuple(spell))
            hit = _cache_get(key)
            if hit is not None:
                self._record("probe", name, "cache")
                return set(hit)
            with self._lock:
                over = label not in self._probed and len(self._probed) >= PROBE_MAX_LABELS
                if not over:
                    self._probed.add(label)
            if over:
                self._record("probe", name, "budget")
                return set(probe)
            held: set[str] = set()
            unasked: list[str] = []
            start = 0
            for chunk in _probe_chunks(label, probe):
                result = self._run("probe", f"{label} [{start}:{start + len(chunk)}]",
                                   probe_statement(label, chunk), {"spellings": spell})
                start += len(chunk)
                row = ((result or {}).get("data") or [None])[0]
                if not isinstance(row, dict):
                    unasked += chunk
                    continue
                held |= {a for i, a in enumerate(chunk) if row.get(f"a{i}") is True}
            if not unasked:
                _cache_put(key, tuple(sorted(held)))
            return held | set(unasked)
        except Exception:  # noqa: BLE001
            self._record("probe", name, "failed")
            return None

    def attributes(self, label: str) -> list[str] | None:
        try:
            titles = graph_catalog.get_snapshot(self._config).guard.get(label)
            return sorted(titles) if titles is not None else None
        except Exception:
            return None

    def type_name(self, label: str) -> str | None:
        try:
            snapshot = graph_catalog.get_snapshot(self._config)
            return next((row.name for row in snapshot.index if row.label == label), None)
        except Exception:
            return None


def live_values(config, *, max_cold: int = MAX_COLD, budget_s: float = TIER1_BUDGET_S) -> CatalogProvider:
    """A fresh ``CatalogProvider`` for one turn: cached value lists and probe answers are always served; uncached
    ones are read while ``budget_s`` of statement time lasts, at most ``max_cold`` value lists (0 means cache only)."""
    return _LiveCatalog(config, max_cold, budget_s)


# ---------------------------------------------------------------- relaxed_variants ---------------------------------
@dataclass
class _Variant:
    check: str
    edit: str
    cypher: str
    parameters: dict
    fact: Callable[[int, int | None], str | None]   # (variant total, original total) -> the fact, or None
    #: A breakdown: run for its rows (``rows_of``), read as (the fact or None, the suggestion's expected count or None).
    rows: Callable[[list[dict], int | None], tuple[str | None, int | None]] | None = None
    #: Whether a total that is disclosed also becomes the suggestion's expected count.
    sets_expected: bool = True
    #: Run instead when this variant fails (a timeout, a refusal, no rows); it counts against the cap too.
    fallback: "_Variant | None" = None


def _differs(n: int, original: int | None) -> bool:
    return original is None or n != original


def _stems(detail: str) -> list[str]:
    """The stored stems Tier 1 named: ``"<attr> CONTAINS '<term>' misses ['tif', 'TIF']"``."""
    m = re.search(r"misses\s+(\[.*\])\s*$", detail, re.S)
    if m:
        try:
            got = ast.literal_eval(m.group(1))
            return [s for s in got if isinstance(s, str)]
        except Exception:
            pass
    quoted = re.findall(r"'([^']+)'", detail)
    if quoted:
        return quoted
    bare = detail.strip()
    return [bare] if bare and " " not in bare else []


def _case_insensitive(cy: str, contains_at: int) -> bool:
    """The left side of this CONTAINS is lower- or upper-cased (a heuristic over the text before it)."""
    left = re.split(r"\b(?:WHERE|AND|OR|XOR)\b|[\[,]", cy[max(0, contains_at - 80):contains_at], flags=re.I)[-1]
    return bool(re.search(r"\bto(?:Lower|Upper)\s*\(", left, re.I))


def _stem_variant(cy: str, params: dict, detail: str) -> _Variant | None:
    stems = [s.strip(".").lower() for s in _stems(detail)]
    stems = [s for s in stems if len(s) >= 3]
    if not stems:
        return None
    stem = min(stems, key=len)
    hint = re.match(r"\s*\w+ CONTAINS '(.*)' misses ", detail, re.S)
    term_hint = hint.group(1) if hint else None
    new_params, literal_edits, term, ci = dict(params), [], None, False
    for m in re.finditer(r"\bCONTAINS\s+" + TERM, cy, re.I):
        tok = m.group(1)
        value = _resolve(tok, params)
        low = value.lower() if isinstance(value, str) else None
        if low is None or (term_hint is not None and low != term_hint) or not (
                low.startswith(stem) and len(low) > len(stem)):
            continue
        if tok.startswith("$"):
            new_params[tok[1:]] = stem
        elif "'" in stem or "\\" in stem:
            continue
        else:
            literal_edits.append((m.start(1), m.end(1), f"'{stem}'"))
        term = term or low
        ci = ci or _case_insensitive(cy, m.start())
    if term is None:
        return None
    new = cy
    for start, end, text in reversed(literal_edits):
        new = new[:start] + text + new[end:]
    new = _row_level(new)
    if new is None:
        return None

    def fact(n, original):
        if not _differs(n, original):
            return None
        return (f"Every spelling of '{stem}' gives {n:,}." if ci
                else f"Searching for '{stem}' instead of '{term}' gives {n:,}.")
    return _Variant("stem_miss", f"stem_miss: '{term}' -> '{stem}'", new, new_params, fact)


def _unwrap(s: str) -> str:
    """``s`` without parentheses that wrap the whole of it: ``(a.x > 0)`` is ``a.x > 0``, ``(a) = (b)`` is kept."""
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        for i, ch in enumerate(_mask(s)):
            depth += ch in "([{"
            depth -= ch in ")]}"
            if depth == 0 and i < len(s) - 1:
                return s
        s = s[1:-1].strip()
    return s


def _narrowed_variant(cy: str, params: dict, detail: str) -> _Variant | None:
    named = re.match(r"\s*(\w+)\.(\w+) IS NOT NULL on", detail)
    if named:
        target = re.compile(rf"{re.escape(named.group(1))}\.{re.escape(named.group(2))}\s+IS\s+NOT\s+NULL", re.I)
        kind = "not_null"
    elif "sample_count" in detail:
        target, kind = re.compile(r"\w+\.sample_count\s*>\s*0", re.I), "sample_count"
    else:
        target, kind = re.compile(r"\w+\.sample_count\s*>\s*0|\w+\.\w+\s+IS\s+NOT\s+NULL", re.I), None
    mask = _mask(cy)
    for clause in _clauses(cy, mask):
        if clause.kw != "WHERE":
            continue
        body, bmask = cy[clause.body:clause.end], mask[clause.body:clause.end]
        if re.search(r"\b(?:OR|XOR)\b", bmask, re.I):
            continue                                  # AND under OR: not a conjunct we can drop by text
        parts = _split_top(body, bmask, r"\bAND\b")
        hit = next((i for i, p in enumerate(parts) if target.fullmatch(_unwrap(p))), None)
        if hit is None:
            continue
        dropped = parts[hit]
        keep = [p for i, p in enumerate(parts) if i != hit]
        new = (cy[:clause.start].rstrip() + "\n" + (f"WHERE {' AND '.join(keep)}\n" if keep else "")
               + cy[clause.end:].lstrip())
        new = _row_level(new)
        if new is None:
            return None
        sample_count = kind == "sample_count" or (kind is None and "sample_count" in dropped)

        def fact(n, original, sample_count=sample_count):
            if not _differs(n, original):
                return None
            return (f"Counting every defined type, including those with no samples, gives {n:,}." if sample_count
                    else f"Including records with no value for that property gives {n:,}.")
        return _Variant("all_question_narrowed", f"all_question_narrowed: drop {dropped}", new, dict(params), fact)
    return None


def _bound_in(var: str, text: str) -> bool:
    return bool(re.search(rf"[(\[]\s*{re.escape(var)}\b", text))


_FUZZY_OP = re.compile(r"\b(?:CONTAINS|STARTS\s+WITH|ENDS\s+WITH)\b|=~", re.I)
_STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", re.S)


def _text_match_on(conjunct: str, var: str) -> bool:
    """``conjunct`` is one text match on ``var``'s own property: ``[f(...)]var.prop CONTAINS | STARTS WITH |
    ENDS WITH | =~ <parameters, literals and function calls>``, with no AND, OR, XOR or NOT at its top level."""
    text = _unwrap(conjunct)
    mask = _mask(text)
    ops = list(_FUZZY_OP.finditer(mask))
    if len(ops) != 1 or re.search(r"\b(?:AND|OR|XOR|NOT)\b", mask, re.I):
        return False
    lhs, rhs = text[:ops[0].start()].strip(), text[ops[0].end():].strip()
    if not re.fullmatch(rf"(?:[A-Za-z_]\w*\s*\(\s*)*{re.escape(var)}\.[A-Za-z_]\w*(?:\s*\))*", lhs):
        return False
    rest = _STRING.sub(" ", rhs)
    if not rhs or not re.fullmatch(r"[\s\w$()+]*", rest):
        return False
    # every name on the right is a parameter or a function call, never a variable
    return all(tok.startswith("$") or tok.endswith("(") for tok in re.findall(r"\$?[A-Za-z_]\w*(?:\s*\()?", rest))


def _base_variant(cy: str, params: dict, detail: str) -> _Variant | None:
    """The anchor set alone: the first MATCH with only its WHERE's text match on the counted variable (R3 601: NHP by
    ``search_text CONTAINS`` a UID), every other filter and later clause dropped, one row per anchor node. Skipped
    when that text match cannot be isolated as a top-level conjunct, or when nothing would be dropped."""
    mask = _mask(cy)
    clauses = _clauses(cy, mask)
    if (len(clauses) < 2 or clauses[0].kw != "MATCH" or clauses[1].kw != "WHERE"
            or cy[:clauses[0].start].strip() or _has_union(cy, mask)):
        return None
    anchor = cy[clauses[0].start:clauses[0].end].rstrip()
    var = None
    ret = _final_return(cy, mask)
    if ret is not None:
        for item in _return_items(cy, mask, ret):
            counted = _COUNT_ITEM.fullmatch(item)
            plain = re.fullmatch(r"([A-Za-z_]\w*)(?:\.\w+)?(?:\s+AS\s+\w+)?", item, re.I)
            name = counted.group(2) if counted else (plain.group(1) if plain else None)
            if name and name != "*" and _bound_in(name, anchor):
                var = name
                break
    if var is None:
        var = next((v for v, _lab in _var_labels(anchor).items()), None)
    if var is None:
        m = re.search(r"\(\s*([A-Za-z_]\w*)\s*:\s*Sample\b", anchor)
        var = m.group(1) if m else None
    if var is None:
        return None
    where = clauses[1]
    body, bmask = cy[where.body:where.end], mask[where.body:where.end]
    if re.search(r"\b(?:OR|XOR)\b", bmask, re.I):
        return None
    parts = _split_top(body, bmask, r"\bAND\b")
    kept = [p for p in parts if _text_match_on(p, var)]
    later = any(c.kw not in ("RETURN", "ORDER BY", "SKIP", "LIMIT") for c in clauses[2:])
    if not kept or (len(kept) == len(parts) and not later):
        return None
    new = f"{anchor}\nWHERE {' AND '.join(_unwrap(p) for p in kept)}\nWITH DISTINCT {var} AS k\nRETURN 1 AS n"

    def fact(n, original):
        return f"Before its other filters, the search matches {n:,} records." if _differs(n, original) else None
    return _Variant("zero_unproven_base", f"zero_unproven_base: keep {' AND '.join(kept)}", new, dict(params), fact)


def _without_braces(text: str) -> str:
    """``text`` with the inside of every ``{...}`` blanked (same length; quotes respected), so a name bound in an
    EXISTS, COUNT or CALL subquery, or written in a property map, is not read as bound by the clause around it."""
    buf, depth, quote = list(text), 0, None
    for i, ch in enumerate(text):
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"`":
            quote = ch
        elif ch == "{":
            depth += 1
            continue
        elif ch == "}":
            depth = max(0, depth - 1)
            continue
        if depth:
            buf[i] = " "
    return "".join(buf)


def _vars_at_return(cy: str, mask: str) -> dict[str, str]:
    """variable -> T_ label for the names bound at the final RETURN: bound in a top-level MATCH or OPTIONAL MATCH
    (never only inside a subquery, whose names end with it) and carried by name through every top-level WITH after
    it (``WITH *`` carries all)."""
    bound: dict[str, str] = {}
    for c in _clauses(cy, mask):
        if c.kw in ("MATCH", "OPTIONAL MATCH"):
            bound.update(_var_labels(_without_braces(cy[c.body:c.end])))
        elif c.kw == "WITH":
            text = re.sub(r"^\s*DISTINCT\b", "", cy[c.body:c.end], flags=re.I)
            tmask = re.sub(r"^\s*DISTINCT\b", "", mask[c.body:c.end], flags=re.I)
            items = _split_top(text, tmask, r",")
            if "*" not in items:
                kept = {m.group(1) for m in (re.fullmatch(r"([A-Za-z_]\w*)(?:\s+AS\s+\1)?", i, re.I) for i in items)
                        if m}
                bound = {v: lab for v, lab in bound.items() if v in kept}
    return bound


def _breakdown_variant(cy: str, params: dict, detail: str) -> _Variant | None:
    """The matched set grouped by the attribute the question named: one row per distinct stored value.

    Only a variable bound at the final RETURN is grouped (``_vars_at_return``). r6-1225 bound its T_A_ALN variable
    only inside ``EXISTS {}``: grouping by it was refused by the prover for a member ("the name aln is not bound
    here") and is invalid Cypher on an admin's path, which skips the prover. With no such variable there is no
    variant."""
    m = re.match(r"question names (T_[A-Z0-9_]+)\.(.+?)='(.*)', Cypher never applies it\s*$", detail, re.S)
    if not m or not _ATTR_RE.fullmatch(m.group(2)):
        return None
    label, attr, value = m.groups()
    mask = _mask(cy)
    var = next((v for v, lab in _vars_at_return(cy, mask).items() if lab == label), None)
    ret = _final_return(cy, mask)
    if var is None or ret is None or _with_aggregates(cy, mask):
        return None
    new = (f"{cy[:ret.start].rstrip()}\nWITH {var} WHERE {var}.{attr} IS NOT NULL\n"
           f"RETURN DISTINCT {var}.{attr} AS value")
    shown = value if len(value) <= 40 else value[:39].rstrip() + "…"

    def fact(n, original):
        return (f"The matched records hold {n:,} different values where the question named '{shown}'."
                if n >= 2 else None)
    return _Variant("unapplied_value", f"unapplied_value: distinct {attr} values", new, dict(params), fact,
                    sets_expected=False)


#: Tier 2's facts for a value the question names that the query never applies, bound only inside an EXISTS
#: (operator ruling 2026-09-25: the answer stands, the reply names what is mixed in).
BREAKDOWN_FACT = "Counted by {attribute}: {values}; one result can fall under more than one."
NARROWED_FACT = "With {attribute} '{value}' only, the count is {n:,}."
BREAKDOWN_SHOWN = 5
BREAKDOWN_ROWS = 20
_UNAPPLIED_DETAIL = re.compile(r"question names (T_[A-Z0-9_]+)\.(.+?)='(.*)', Cypher never applies it\s*$", re.S)


def _exists_conjunct(cy: str, mask: str, label: str):
    """(the WHERE clause, its top-level AND parts, the index of the part, that EXISTS block's inside, the variable
    bound to ``label`` in it) for the first top-level ``EXISTS { ... }`` conjunct whose inside binds ``label``; None
    when there is none, or when the WHERE has a top-level OR or XOR (no conjunct can be edited by text)."""
    for clause in _clauses(cy, mask):
        if clause.kw != "WHERE":
            continue
        body, bmask = cy[clause.body:clause.end], mask[clause.body:clause.end]
        if re.search(r"\b(?:OR|XOR)\b", bmask, re.I):
            continue
        parts = _split_top(body, bmask, r"\bAND\b")
        for i, part in enumerate(parts):
            m = re.fullmatch(r"EXISTS\s*\{(.*)\}", _unwrap(part), re.S | re.I)
            bound = m and re.search(rf"\(\s*([A-Za-z_]\w*)\s*:\s*{re.escape(label)}\b", m.group(1))
            if bound:
                return clause, parts, i, m.group(1), bound.group(1)
    return None


def _inner_where(inner: str):
    imask = _mask(inner)
    clauses = _clauses(inner, imask)
    return clauses, next((c for c in clauses if c.kw == "WHERE"), None)


def _exists_variants(cy: str, params: dict, detail: str) -> _Variant | None:
    """For a named value on a variable bound only inside a top-level EXISTS (r6-1225, dev 1276: an alignment under the
    counted patients): the matched set split by that attribute, and, when the split cannot run, the count with the
    value applied inside that EXISTS.

    The split hoists the EXISTS pattern into a MATCH after the WHERE and counts each counted record once per value:
    ``WITH DISTINCT <counted>, <var>.<attr> AS value``. Its cap sits in a WITH, never a trailing LIMIT, so the tool
    runs it once. The narrowed count adds ``<var>.<attr> = $review_value`` inside the EXISTS (an existing WHERE there is
    parenthesized first) and counts rows like every count variant (``_row_level``). Only a statement whose clauses
    before the final RETURN are MATCH, OPTIONAL MATCH and WHERE, whose RETURN is one ``count(...)`` of a variable bound
    there (``count(*)`` when exactly one type variable is), and whose EXISTS variable is not bound outside it, gets
    these variants. Dev, scoped: the split 2.8 s (miRNA-Seq 10,561, RNA-Seq 10,517, WXS 3), the count 1.8 s."""
    m = _UNAPPLIED_DETAIL.match(detail)
    if not m or not _ATTR_RE.fullmatch(m.group(2)):
        return None
    label, attr, value = m.groups()
    mask = _mask(cy)
    ret = _final_return(cy, mask)
    if ret is None or _has_union(cy, mask) or _with_aggregates(cy, mask):
        return None
    if any(c.kw not in ("MATCH", "OPTIONAL MATCH", "WHERE") for c in _clauses(cy, mask) if c.start < ret.start):
        return None
    items = _return_items(cy, mask, ret)
    counted_item = _COUNT_ITEM.fullmatch(items[0]) if len(items) == 1 else None
    if counted_item is None:
        return None
    top = _without_braces(cy[:ret.start])
    counted = counted_item.group(2)
    if counted == "*":
        at_return = list(_vars_at_return(cy, mask))
        counted = at_return[0] if len(at_return) == 1 else None
    if not counted or not _bound_in(counted, top):
        return None
    found = _exists_conjunct(cy, mask, label)
    if found is None:
        return None
    clause, parts, index, inner, var = found
    if var == counted or _bound_in(var, top):
        return None
    param, k = "review_value", 1
    while param in params:
        k += 1
        param = f"review_value_{k}"

    inner_clauses, where = _inner_where(inner)
    filt = f"{var}.{attr} = ${param}"
    narrowed_inner = (inner.rstrip() + f" WHERE {filt} " if where is None else
                      inner[:where.body] + f" ({inner[where.body:where.end].strip()}) AND {filt} " + inner[where.end:])
    narrowed_parts = list(parts)
    narrowed_parts[index] = "EXISTS {" + narrowed_inner + "}"
    narrowed = _row_level(cy[:clause.body] + " " + " AND ".join(narrowed_parts) + "\n" + cy[clause.end:].lstrip())
    shown = value if len(value) <= 40 else value[:39].rstrip() + "…"

    def narrowed_fact(n, original):
        return NARROWED_FACT.format(attribute=attr, value=shown, n=n) if _differs(n, original) else None
    fallback = (_Variant("unapplied_value", f"unapplied_value: only {attr} '{shown}'", narrowed,
                         {**params, param: value}, narrowed_fact) if narrowed else None)

    breakdown = None
    if all(c.kw in ("MATCH", "WHERE") for c in inner_clauses):
        pattern = inner.strip()
        if not inner_clauses or inner_clauses[0].kw != "MATCH":
            pattern = "MATCH " + pattern                  # EXISTS { (a)-->(b) } has no MATCH of its own
        rest = [p for i, p in enumerate(parts) if i != index]
        between = cy[clause.end:ret.start].strip()
        breakdown = (cy[:clause.start].rstrip() + (f"\nWHERE {' AND '.join(rest)}" if rest else "")
                     + (f"\n{between}" if between else "") + f"\n{pattern}"
                     + f"\nWITH DISTINCT {counted}, {var}.{attr} AS value WHERE value IS NOT NULL"
                     + f"\nWITH value, count(*) AS n ORDER BY n DESC LIMIT {BREAKDOWN_ROWS}\nRETURN value, n")
    if breakdown is None:
        return fallback

    def read(rows, _original):
        pairs = [(str(r.get("value")), r.get("n")) for r in rows
                 if isinstance(r, dict) and r.get("value") is not None and _whole_int(r.get("n"))]
        expected = next((n for v, n in pairs if v == value), None)
        if len(pairs) < 2:
            return None, expected
        listed = ", ".join(f"{v if len(v) <= 40 else v[:39].rstrip() + '…'} {n:,}" for v, n in pairs[:BREAKDOWN_SHOWN])
        more = len(pairs) - BREAKDOWN_SHOWN
        listed += f" and {more} more" if more > 0 else ""
        return BREAKDOWN_FACT.format(attribute=attr, values=listed), expected
    return _Variant("unapplied_value", f"unapplied_value: split by {attr}", breakdown, dict(params),
                    lambda _n, _o: None, rows=read, fallback=fallback)


def _unapplied_variant(cy: str, params: dict, detail: str) -> _Variant | None:
    """The variable bound at the final RETURN is grouped (``_breakdown_variant``); one bound only inside an EXISTS
    gets the split and its fallback (``_exists_variants``)."""
    return _breakdown_variant(cy, params, detail) or _exists_variants(cy, params, detail)


_BUILDERS = (("stem_miss", _stem_variant), ("all_question_narrowed", _narrowed_variant),
             ("zero_unproven_base", _base_variant), ("unapplied_value", _unapplied_variant))


def _variants(inp: ReviewInput, review: GraphReview) -> list[_Variant]:
    cy = inp.cypher if isinstance(inp.cypher, str) else ""
    if not cy.strip() or _has_union(cy, _mask(cy)):
        return []
    fired = {c.name: c for c in review.checks if c.fired}
    params = dict(inp.parameters or {})
    out = []
    for name, build in _BUILDERS:
        if name not in fired:
            continue
        try:
            variant = build(cy, params, fired[name].detail or "")
        except Exception:
            variant = None
        if variant is not None:
            out.append(variant)
    return out


def relaxed_variants(inp: ReviewInput, review: GraphReview) -> list[tuple[str, str, dict]]:
    """One ``(edit, cypher, parameters)`` per fired Tier 1 check that has a count, in the order stem_miss,
    all_question_narrowed, zero_unproven_base, unapplied_value. A check whose edit cannot be made by text on the
    top-level Cypher is skipped. Never raises."""
    try:
        return [(v.edit, v.cypher, v.parameters) for v in _variants(inp, review)]
    except Exception:
        return []


# ---------------------------------------------------------------- run_tier2 ----------------------------------------
def _whole_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _original_n(inp: ReviewInput) -> int | None:
    """The number the turn answered with, in the unit a variant counts: the aggregate of a one-row count; the sum
    of a complete breakdown's ``count(*)`` or ``count(v)`` column; else the row total. None when it cannot be told
    (a per-group ``count(DISTINCT ...)`` does not add up to the whole, nor does a capped breakdown)."""
    cy = inp.cypher or ""
    mask = _mask(cy)
    if _has_union(cy, mask):
        return None
    if inp.count == 0:
        return 0
    ret = _final_return(cy, mask)
    items = _return_items(cy, mask, ret) if ret else []
    aggregates = [i for i in items if _AGGREGATE.search(_mask(i))]
    if not aggregates:
        return inp.total if inp.total is not None else inp.count
    rows = [r for r in (inp.rows or []) if isinstance(r, dict)]
    if len(items) == 1 and len(rows) == 1 and len(rows[0]) == 1:
        value = next(iter(rows[0].values()))
        return value if _whole_int(value) else None
    counted = _COUNT_ITEM.fullmatch(aggregates[0]) if len(aggregates) == 1 else None
    alias = re.search(r"\bAS\s+(\w+)\s*$", aggregates[0], re.I)
    if counted is None or counted.group(1) or alias is None or not rows:
        return None
    if inp.total is not None and len(rows) < inp.total:
        return None
    column = [r.get(alias.group(1)) for r in rows]
    return sum(column) if all(_whole_int(v) for v in column) else None


def _suggestion_owner(review: GraphReview) -> str | None:
    """Which check made the review's relaxed_variant suggestion (review_tier1 takes stem_miss's before
    all_question_narrowed's); a value_split suggestion belongs to no variant."""
    kind = review.suggestion.get("kind") if isinstance(review.suggestion, dict) else None
    if kind == "narrow_value":
        return "unapplied_value"
    if kind != "relaxed_variant":
        return None
    fired = {c.name for c in review.checks if c.fired}
    return "stem_miss" if "stem_miss" in fired else "all_question_narrowed"


def run_tier2(config, inp: ReviewInput, review: GraphReview, *, budget_s: float = 8.0,
              max_variants: int = 2) -> GraphReview:
    """Run the fired checks' count variants in order, at most ``max_variants``, inside ``budget_s`` of wall clock.

    Returns a copy of ``review``: each variant run is appended to ``variants`` as ``{edit, total, elapsed_ms, ok}``
    (a refused or failed one too; it counts against the cap and is not retried); a total that differs from the
    turn's own adds one fact to ``disclosure`` (within ``DISCLOSURE_MAX``) and, when the review's suggestion is that
    check's relaxed variant, sets its ``expected_count``. Skipped entirely when the original statement took over
    ``SKIP_AFTER_MS``. Never raises: an internal error is recorded in ``error``."""
    t0 = _clock()
    try:
        out = dataclasses.replace(
            review, variants=list(review.variants or []),
            suggestion=dict(review.suggestion) if isinstance(review.suggestion, dict) else review.suggestion)
    except Exception:
        return review
    try:
        if inp.elapsed_ms is not None and inp.elapsed_ms > SKIP_AFTER_MS:
            return out
        plans = _variants(inp, review)
        original = _original_n(inp)
        owner = _suggestion_owner(review)

        def disclose(fact: str) -> None:
            joined = f"{out.disclosure} {fact}" if out.disclosure else fact
            if len(joined) <= DISCLOSURE_MAX:
                out.disclosure = joined

        def expect(variant: _Variant, n: int | None) -> None:
            if n is not None and owner == variant.check and isinstance(out.suggestion, dict):
                out.suggestion["expected_count"] = n

        def apply(variant: _Variant) -> bool:
            """Run one variant, record it, disclose its fact; False when it gave nothing to read."""
            timeout_s = max(1, min(COUNT_TIMEOUT_S, int(budget_s - (_clock() - t0))))
            if variant.rows is not None:
                got = rows_of(config, variant.cypher, variant.parameters, timeout_s=timeout_s)
                rows = got.get("rows") or [] if got.get("ok") else []
                out.variants.append({"edit": variant.edit, "total": len(rows) if rows else None,
                                     "elapsed_ms": got.get("elapsed_ms"), "ok": bool(rows), "rows": rows})
                if not rows:
                    return False
                fact, n = variant.rows(rows, original)
                if fact:
                    disclose(fact)
                expect(variant, n)
                return True
            got = count_of(config, variant.cypher, variant.parameters, timeout_s=timeout_s)
            ok = bool(got.get("ok"))
            total = got.get("total") if ok else None
            out.variants.append({"edit": variant.edit, "total": total, "elapsed_ms": got.get("elapsed_ms"), "ok": ok})
            if total is None:
                return False
            fact = variant.fact(total, original)
            if fact:
                disclose(fact)
                if variant.sets_expected:
                    expect(variant, total)
            return True

        ran = 0
        for variant in plans:
            if ran >= max_variants or budget_s - (_clock() - t0) < MIN_START_S:
                break
            ran += 1
            if apply(variant) or variant.fallback is None:
                continue
            if ran < max_variants and budget_s - (_clock() - t0) >= MIN_START_S:
                ran += 1
                apply(variant.fallback)
    except Exception as exc:
        out.error = out.error or f"tier2: {type(exc).__name__}: {exc}"[:200]
    out.elapsed_ms = (review.elapsed_ms or 0) + int((_clock() - t0) * 1000)
    return out

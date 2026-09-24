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

``live_values(config)`` is the ``CatalogProvider`` Tier 1 reads in production, one fresh object per turn:
``values`` runs one capped DISTINCT query per (label, attribute) through the tool (so its values are the caller's
own), cached per process per scope for ``VALUES_TTL_S`` with at most ``max_cold`` uncached queries per provider;
``attributes`` and ``type_name`` come from ``graph_catalog.get_snapshot``, which is the same for every caller.

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
from collections import OrderedDict
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


class _LiveCatalog:
    """A ``graph_review.CatalogProvider`` over the live graph, for one turn (``live_values``)."""

    def __init__(self, config, max_cold: int):
        self._config = config
        self._cold_left = max(0, int(max_cold))
        self._lock = threading.Lock()

    def _spend_cold(self) -> bool:
        with self._lock:
            if self._cold_left <= 0:
                return False
            self._cold_left -= 1
            return True

    def values(self, label: str, attribute: str) -> list[tuple[str, int]] | None:
        """The stored values of ``label.attribute`` the caller can see, most frequent first, at most ``VALUES_CAP``.
        None when the names are not plain, the config carries no scope, the query fails or is refused, or this turn
        has spent its ``max_cold`` uncached queries."""
        try:
            if not (isinstance(label, str) and isinstance(attribute, str)
                    and _LABEL_RE.fullmatch(label) and _ATTR_RE.fullmatch(attribute)):
                return None
            scope = scope_of(self._config)
            if scope is None:
                return None
            scope_key = ("admin",) if scope.is_admin else tuple(scope.project_ids)
            key = (str(getattr(self._config, "NEO4J_URI", None)),
                   str(getattr(self._config, "NEO4J_DATABASE", None) or "neo4j"), scope_key, label, attribute)
            hit = _cache_get(key)
            if hit is not None:
                return list(hit)
            if not self._spend_cold():
                return None
            result = tool_neo4j_query(self._config, values_statement(label, attribute), {},
                                      timeout_s=VALUES_TIMEOUT_S)
            if not isinstance(result, dict) or not result.get("ok"):
                return None
            pairs = [(str(r["v"]), int(r["n"])) for r in (result.get("data") or [])
                     if isinstance(r, dict) and r.get("v") is not None and r.get("n") is not None]
            pairs.sort(key=lambda p: -p[1])
            _cache_put(key, tuple(pairs))
            return list(pairs)
        except Exception:
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


def live_values(config, *, max_cold: int = 2) -> CatalogProvider:
    """A fresh ``CatalogProvider`` for one turn: cached value lists are always served, and at most ``max_cold``
    uncached ones are fetched (0 means cache only)."""
    return _LiveCatalog(config, max_cold)


# ---------------------------------------------------------------- relaxed_variants ---------------------------------
@dataclass
class _Variant:
    check: str
    edit: str
    cypher: str
    parameters: dict
    fact: Callable[[int, int | None], str | None]   # (variant total, original total) -> the fact, or None


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


def _breakdown_variant(cy: str, params: dict, detail: str) -> _Variant | None:
    """The matched set grouped by the attribute the question named: one row per distinct stored value."""
    m = re.match(r"question names (T_[A-Z0-9_]+)\.(.+?)='(.*)', Cypher never applies it\s*$", detail, re.S)
    if not m or not _ATTR_RE.fullmatch(m.group(2)):
        return None
    label, attr, value = m.groups()
    var = next((v for v, lab in _var_labels(cy).items() if lab == label), None)
    mask = _mask(cy)
    ret = _final_return(cy, mask)
    if var is None or ret is None or _with_aggregates(cy, mask):
        return None
    new = (f"{cy[:ret.start].rstrip()}\nWITH {var} WHERE {var}.{attr} IS NOT NULL\n"
           f"RETURN DISTINCT {var}.{attr} AS value")
    shown = value if len(value) <= 40 else value[:39].rstrip() + "…"

    def fact(n, original):
        return (f"The matched records hold {n:,} different values where the question named '{shown}'."
                if n >= 2 else None)
    return _Variant("unapplied_value", f"unapplied_value: distinct {attr} values", new, dict(params), fact)


_BUILDERS = (("stem_miss", _stem_variant), ("all_question_narrowed", _narrowed_variant),
             ("zero_unproven_base", _base_variant), ("unapplied_value", _breakdown_variant))


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
    if not isinstance(review.suggestion, dict) or review.suggestion.get("kind") != "relaxed_variant":
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
        ran = 0
        for variant in plans:
            if ran >= max_variants:
                break
            remaining = budget_s - (_clock() - t0)
            if remaining < MIN_START_S:
                break
            ran += 1
            got = count_of(config, variant.cypher, variant.parameters,
                           timeout_s=max(1, min(COUNT_TIMEOUT_S, int(remaining))))
            ok = bool(got.get("ok"))
            total = got.get("total") if ok else None
            out.variants.append({"edit": variant.edit, "total": total, "elapsed_ms": got.get("elapsed_ms"),
                                 "ok": ok})
            if total is None:
                continue
            fact = variant.fact(total, original)
            if not fact:
                continue
            joined = f"{out.disclosure} {fact}" if out.disclosure else fact
            if len(joined) <= DISCLOSURE_MAX:
                out.disclosure = joined
            if owner == variant.check and isinstance(out.suggestion, dict):
                out.suggestion["expected_count"] = total
    except Exception as exc:
        out.error = out.error or f"tier2: {type(exc).__name__}: {exc}"[:200]
    out.elapsed_ms = (review.elapsed_ms or 0) + int((_clock() - t0) * 1000)
    return out

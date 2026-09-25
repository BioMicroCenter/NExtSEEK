"""Read a finished graph result and say what it matched: the graph reviewer, Tier 1 (deterministic, no Neo4j).

Nothing used to inspect a graph query that ran and returned rows, so confidently wrong numbers reached the reply
(production 2026-09-23): 98 "converters" of which 57 were stored as ``Non-converter``; 1,306 TIFF images that left
out every ``tif``/``TIF`` value; "how many different D. file types exist" narrowed by ``sample_count > 0``; RNA-Seq
patients counted through miRNA-Seq alignments; a zero whose starting set was never counted.

``review_tier1`` reads the question, the executed Cypher and parameters, the full in-memory result rows and the
counts, and returns a ``GraphReview``: ``ok`` (say nothing), ``note`` (the turn broke: say so plainly) or
``suggest`` (the result may not mean what the question asked: disclose the facts and, where one exists, offer one
concrete next query). It never re-asks the graph agent and never runs a query; Tier 2 (bounded count queries) fills
``variants`` later.

The catalog comes from a ``CatalogProvider``: ``values(label, attribute) -> [(value, n), ...] | None`` (a list
shorter than ``VALUES_CAP`` is the complete set of stored values), ``attributes(label)`` (the type's attributes that
hold values) and ``type_name(label)`` (its display name). One review calls each (method, args) at most once, so a
live provider that queries Neo4j is hit once per key per turn. ``DictCatalog`` is the in-memory provider the offline
fixture uses.

``reply_draft`` is optional. In the live flow the reviewer runs before the chatter writes the reply, so it is None
and nothing the reply says can suppress a check; offline (and for a later chatter-side backstop) it suppresses a
note the reply already makes.

The rules were written against 110 labelled graph turns from the 2026-09-23 runs (11 should fire, 99 should stay
quiet), kept as the offline fixture ``tests/chat_nextseek/fixtures/graph_review_replay.json``.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

VALUES_CAP = 50
DISCLOSURE_MAX = 299
LABEL_MAX = 60
QUERY_MAX = 300


class CatalogProvider(Protocol):
    """What Tier 1 reads about a sample type. ``None`` means unknown; a raise is recorded by the check that asked.

    ``values``: the stored values of one attribute with their sample counts (complete when shorter than
    ``VALUES_CAP``). ``attributes``: the type's attributes that hold values. ``type_name``: its display name."""

    def values(self, label: str, attribute: str) -> list[tuple[str, int]] | None: ...
    def attributes(self, label: str) -> list[str] | None: ...
    def type_name(self, label: str) -> str | None: ...


class DictCatalog:
    """A ``CatalogProvider`` over a plain dict, the shape of the replay fixture's ``catalog`` block:
    ``{"<label>.<attribute>": [[value, n], ...], "<label>.*": [[attribute, n_values], ...],
    "<label>.@name": [[display name, sample_count]]}``."""

    def __init__(self, block: dict | None):
        self._b = block or {}

    def values(self, label: str, attribute: str) -> list[tuple[str, int]] | None:
        got = self._b.get(f"{label}.{attribute}")
        return [tuple(v) for v in got] if got else None

    def attributes(self, label: str) -> list[str] | None:
        got = self._b.get(f"{label}.*")
        return [a[0] if isinstance(a, (list, tuple)) else a for a in got] if got else None

    def type_name(self, label: str) -> str | None:
        got = self._b.get(f"{label}.@name")
        return (got[0][0] if isinstance(got[0], (list, tuple)) else got[0]) if got else None


class _Memo:
    """Every provider call once per (method, args) within one review; a raise is remembered and re-raised."""

    def __init__(self, catalog: CatalogProvider):
        self._c = catalog
        self._seen: dict = {}

    def _call(self, method: str, *args):
        key = (method, args)
        if key not in self._seen:
            try:
                self._seen[key] = (True, getattr(self._c, method)(*args))
            except Exception as exc:
                self._seen[key] = (False, exc)
        ok, got = self._seen[key]
        if not ok:
            raise got
        return got

    def values(self, label, attribute):
        return self._call("values", label, attribute)

    def attributes(self, label):
        return self._call("attributes", label)

    def type_name(self, label):
        return self._call("type_name", label)


@dataclass
class ReviewInput:
    question: str
    cypher: str | None
    parameters: dict
    keyword_fields: dict            # GraphPlan.keyword_fields
    rows: list[dict]                # full in-memory graph_result["data"]
    count: int | None
    total: int | None
    ok: bool
    error: str | None
    reply_draft: str | None = None  # only to suppress a note the reply already makes
    elapsed_ms: int | None = None   # the final attempt's Neo4j time (Task B3 measures it)


@dataclass
class Check:
    name: str
    fired: bool
    detail: str = ""


@dataclass
class GraphReview:
    verdict: str                    # "ok" | "note" | "suggest"
    checks: list[Check]
    disclosure: str | None          # facts for the chatter note
    suggestion: dict | None         # {"kind", "label", "query", "reason", "expected_count"?}
    variants: list[dict] = field(default_factory=list)   # filled by Tier 2
    elapsed_ms: int = 0
    error: str | None = None


# The ship set, in the order their facts are disclosed. The last two are recorded, never fired.
SHIP = ("breakage", "negated_value", "value_split_rows", "value_split_catalog", "stem_miss",
        "all_question_narrowed", "zero_unproven_base", "unapplied_value", "premise_count")
INFO_ONLY = ("title_contains_multi", "count_only")

NEGATION = re.compile(r"\b(non|not|no|un|anti|never)[\s\-_]*$")
ALL_CUE = re.compile(r"\b(how many different|all|every|exist|exists|defined|in total|altogether)\b", re.I)
STOP_VALUES = {"primary", "unknown", "other", "none", "yes", "no", "n/a", "na", "true", "false", "male", "female",
               "tissue", "blood", "cell", "cells", "sample", "data"}
CLAUSE_WORDS = {"who", "that", "which", "whose", "where"}
# a reply that already tells the user to drop a filter has made the zero's point for us
DROP_FILTER_OFFER = re.compile(
    r"\b(?:without|remov\w*|drop\w*)\s+(?:the|that|this)\s+[\w\s-]{0,30}?(?:constraint|filter|restriction|condition)",
    re.I)
COUNT_WORD = r"\s+(?:\S+\s+){0,3}?(samples?|files?|records?|mice|patients|datasets?|rows|D\.[A-Z]+|sequencing|data)\b"
NUMBER = r"(?<![\w./-])(\d{1,3}(?:,\d{3})+|\d{3,})(?![\w./-])"  # not inside a UID, DOI or PMID
TERM = r"(?:toLower\(\s*)?(?:trim\(\s*)?(\$\w+|'[^']*')"

#: The premise fact, shared with the chatter's backstop (agents/chatter.py ``_premise_first``).
PREMISE_FACT = "The question says {n}; this search did not reproduce that number."
PREMISE_FACT_RE = re.compile(r"The question says [\d,]+; this search did not reproduce that number\.")

#: A number before a count word, the count word read ahead without being consumed, so a number dropped as no count
#: (a year) does not take the words up to the count word with it: "In 2023 we uploaded 4,095 D.SEQ files" reads 4,095.
#: Group 1 is the number, group 2 the count word.
STATED_COUNT = re.compile(NUMBER + "(?=" + COUNT_WORD + ")", re.I)

# What makes a number before a count word something other than the size of a set the user states. Each is read
# around the number only: right before it, right after it, between it and its count word, or right after the count
# word.
_SUPERLATIVE = (r"most|least|highest|lowest|largest|smallest|biggest|greatest|best|worst|top|latest|newest|oldest"
                r"|earliest|longest|shortest|youngest|heaviest|lightest")
#: A number right after "these" or "those" points back at a set the user has seen: "these 807 samples sorted by
#: date", "these 1,206 mice with the most complete metadata". The rank, sort and subset rules skip it; a year and a
#: threshold still apply.
_BACK_REFERENCE = re.compile(r"\b(?:these|those)\s+$", re.I)
#: A rank or pick right before the number: "the first 200 samples", "the top 500", "the latest 300", "a random 200",
#: "the smallest 500 samples by RIN", "the most recent 500 samples" ("most of 1,206 samples" is no rank).
_RANK_BEFORE = re.compile(r"\b(?:first|last|bottom|next|random(?:ly)?(?:\s+(?:chosen|selected|picked))?|"
                          + _SUPERLATIVE + r"|(?:most|least)\s+(?!(?:of|the|these|those|all|your|a|an)\b)[\w-]+)"
                          r"\s+$", re.I)
#: A sample size, "a" or "an" (one word between allowed) or "random" first: "a random subset of 200 samples", "a
#: sample of 300 mice". "the subset of 1,206 CC mice" and "this subset of 1,206 mice" name a set of known size.
_SUBSET_OF = re.compile(r"\b(?:(?:a|an)\s+(?:[\w-]+\s+)?|random\s+)(?:subset|subsample|sample|selection)\s+of\s+$",
                        re.I)
#: A threshold, or the upper bound of a range, right before the number: "more than 100 samples", "at least 500
#: samples", "> 100 samples", the 500 of "between 100 and 500 samples", "from 100 to 500 samples", "100 to 500 samples"
#: and "100 - 500 samples" (a spaced hyphen, or an en dash spaced or not). "100-500 samples" is never read at all:
#: ``NUMBER`` does not match beside a hyphen. The lower bound starts only where a number starts, never after a comma:
#: from inside a long comma-joined digit run it would re-read the rest of the run at every digit.
_THRESHOLD = re.compile(r"(?:\b(?:more|less|fewer|greater|higher|lower)\s+than|\bat\s+(?:least|most)|\bup\s+to"
                        r"|\b(?:over|under|above|below|exceeding|between)|[<>\u2264\u2265]=?"
                        r"|\bbetween\s+[\d,]+\s+and|(?<![\w./,-])\d[\d,]*(?:\s+to|\s*[-\u2013]))\s*$", re.I)
#: A threshold, or the lower bound of a range, right after the number or after its count word: "500 or more
#: samples", "1,000 samples or more", "200 or fewer samples", "500+ samples", "1,000 samples and up", the 100 of "100 to
#: 500 samples".
_BOUND_AFTER = re.compile(r"^(?:\s*\+|\s+or\s+(?:more|fewer|less|greater|higher|lower|above|below|over|under)\b"
                          r"|\s+and\s+(?:up|above)\b|\s+to\s+\d[\d,]*(?![\w./-]))", re.I)
#: The lower bound of a dashed range, right after the number only: the 100 of "100 - 500 samples" and of the same
#: with an en dash. After the count word a spaced dash is more often an aside ("the 745 samples - 300 of them female").
_DASH_RANGE_AFTER = re.compile(r"^\s*[-\u2013]\s*\d[\d,]*(?![\w./-])")
#: A rank word between the number and its count word, alone or in a hyphenated word: "the 100 most recent samples",
#: "the 200 random samples", "the 500 top-ranked D.SEQ files", "the 200 highest-RIN samples".
_RANK_BETWEEN = re.compile(r"\b(?:" + _SUPERLATIVE + r"|random|randomly|first|last)\b", re.I)
#: A ranking after the count word, within two more words: "the 500 samples with the highest RIN", "the 500 D.SEQ
#: files with the most reads", "the 500 samples with the latest collection dates", "the 500 samples sorted by RIN",
#: "the 200 samples ranked by RIN", "500 samples at random". "uploaded by the latest pipeline" is no ranking; after
#: "these" or "those" none of this applies (``_BACK_REFERENCE``).
_RANK_AFTER = re.compile(r"^(?:\s+[\w.()-]+){0,2}?\s*,?\s+(?:(?:with|having)\s+the\s+(?:" + _SUPERLATIVE
                         + r")\b|(?:ranked|sorted|ordered)\s+by\b|at\s+random\b|randomly\b)", re.I)


def _not_a_stated_count(text: str, m: re.Match) -> bool:
    """True when the number at group 1 of ``m`` (a ``STATED_COUNT`` or ``SET_COUNT`` match, count word at group 2)
    is no claim about the size of a set: a year (a 4-digit number from 1900 to 2100 written without a comma: "in
    2023", "the 2024 samples"), a threshold ("more than 100 samples", "500 or more samples"), either number of a range
    ("between 100 and 500 samples", "100 to 500 samples"), or, unless "these" or "those" comes right before it, a rank
    or sample size ("the 100 most recent samples", "the first 200 samples", "a random subset of 200 samples", "a
    sample of 300 mice", "the 500 samples with the highest RIN"). The one rule for ``premise_count``
    (``stated_counts``) and ``check_premise``."""
    raw = m.group(1)
    if "," not in raw and len(raw) == 4 and 1900 <= int(raw) <= 2100:
        return True
    before, after_count = text[:m.start(1)], text[m.end(2):]
    after_number = text[m.end(1):]
    if (_THRESHOLD.search(before) or _BOUND_AFTER.match(after_number) or _DASH_RANGE_AFTER.match(after_number)
            or _BOUND_AFTER.match(after_count)):
        return True
    if _BACK_REFERENCE.search(before):
        return False
    return bool(_RANK_BEFORE.search(before) or _SUBSET_OF.search(before)
                or _RANK_BETWEEN.search(text[m.end(1):m.start(2)]) or _RANK_AFTER.match(after_count))


def _set_sizes(pattern: re.Pattern, text: str) -> list[int]:
    """The numbers ``pattern`` reads in ``text`` that are set sizes (``_not_a_stated_count``), in order, once each."""
    out: list[int] = []
    for m in pattern.finditer(text or ""):
        if _not_a_stated_count(text, m):
            continue
        n = int(m.group(1).replace(",", ""))
        if n not in out:
            out.append(n)
    return out


def stated_counts(text: str) -> list[int]:
    """Counts the question states ("the 4,095 D.SEQ files"). A 4-digit number from 1900 to 2100 written without a
    comma is a year ("in 2023 samples"), not a count; a rank, a sample size or a threshold is not one either
    (``_not_a_stated_count``)."""
    return _set_sizes(STATED_COUNT, text)


# ---------------------------------------------------------------- Cypher reading -----------------------------------
def _var_labels(cy: str) -> dict[str, str]:
    """variable -> T_ label, from every ``(var:Label ...)`` pattern."""
    out = {}
    for var, labels in re.findall(r"\((\w+)((?::\w+)+)", cy):
        for lab in labels.split(":"):
            if lab.startswith("T_"):
                out[var] = lab
    return out


def _resolve(tok: str, params: dict) -> str | None:
    tok = tok.strip()
    if tok.startswith("$"):
        v = params.get(tok[1:])
        return v if isinstance(v, str) else None
    if tok.startswith("'"):
        return tok.strip("'")
    return None


def _contains_filters(cy: str, params: dict) -> list[tuple[str, str, str]]:
    """[(var, attr, term)] for every ``...var.attr...) CONTAINS term`` and the ``any(v IN [s.a, s.b] ...)`` form."""
    out = []
    for var, attr, tok in re.findall(r"(\w+)\.(\w+)\s*\)*\s+CONTAINS\s+" + TERM, cy):
        term = _resolve(tok, params)
        if term:
            out.append((var, attr, term.lower()))
    for body, tok in re.findall(r"any\(\s*\w+\s+IN\s+\[([^\]]+)\][^)]*?CONTAINS\s+" + TERM, cy, re.S):
        term = _resolve(tok, params)
        for var, attr in re.findall(r"(\w+)\.(\w+)", body):
            if term:
                out.append((var, attr, term.lower()))
    return out


def _equality_filters(cy: str, params: dict) -> list[tuple[str, str, str]]:
    out = []
    for var, attr, tok in re.findall(r"(\w+)\.(\w+)\s*\)*\s*=\s*" + TERM, cy):
        term = _resolve(tok, params)
        if term:
            out.append((var, attr, term.lower()))
    return out


def _return_clause(cy: str) -> str:
    parts = re.split(r"\bRETURN\b", cy)
    return parts[-1] if len(parts) > 1 else ""


def _returned_columns(cy: str) -> dict[str, tuple[str, str]]:
    """alias -> (var, attr) for ``var.attr AS alias`` in the last RETURN."""
    return {alias: (var, attr) for var, attr, alias in re.findall(r"(\w+)\.(\w+)\s+AS\s+(\w+)", _return_clause(cy))}


def _is_grouped(cy: str) -> bool:
    return bool(re.search(r"\bcount\s*\(", _return_clause(cy), re.I))


def _return_items(cy: str) -> list[str]:
    """Top-level comma split of the last RETURN clause (ORDER BY / LIMIT dropped)."""
    rc = re.split(r"\bORDER\s+BY\b|\bLIMIT\b", _return_clause(cy))[0]
    items, depth, cur = [], 0, ""
    for ch in rc:
        depth += ch in "([{"
        depth -= ch in ")]}"
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    return [i.strip() for i in items + [cur] if i.strip()]


def _is_count_only(cy: str) -> bool:
    items = _return_items(cy)
    return bool(items) and all(re.search(r"\b(count|sum)\s*\(", i, re.I) for i in items)


def _tokens(s: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", s.lower()) if w}


def _distinct_meanings(values, term: str) -> bool:
    """True when the matched values are not all spelling variants of one another (residual token sets not nested)."""
    res = sorted({frozenset(_tokens(v) - _tokens(term)) for v in {str(x).lower() for x in values}}, key=len)
    return any(not (res[i] <= res[j]) for i in range(len(res)) for j in range(i + 1, len(res)))


def _states_number(text: str, n) -> bool:
    return bool(re.search(rf"(?<![\d.]){re.escape(str(n))}(?![\d])", text))


def _reply_states_split(reply: str | None, counter: Counter) -> bool:
    """The reply already gives every matched value's COUNT (value names alone are not enough)."""
    if not reply:
        return False
    txt = reply.replace(",", "")
    return all(_states_number(txt, n) for _v, n in counter.items())


def _negated(value: str, term: str) -> bool:
    low = value.lower()
    i = low.find(term)
    return i > 0 and bool(NEGATION.search(low[:i]))


# ---------------------------------------------------------------- the turn, read once --------------------------------
@dataclass
class _Turn:
    inp: ReviewInput
    catalog: _Memo
    cy: str
    params: dict
    q: str
    rows: list[dict]
    vl: dict
    cf: list
    eq: list
    cols: dict
    grouped: bool
    count_only: bool

    def vals(self, label: str | None, attr: str) -> list[tuple[str, int]]:
        if not label:
            return []
        got = self.catalog.values(label, attr) or []
        return [(v[0], v[1] if len(v) > 1 else 0) for v in got]

    def result_n(self):
        inp = self.inp
        if inp.count == 0:
            return 0
        if self.count_only and len(self.rows) == 1 and len(self.rows[0]) == 1:
            return next(iter(self.rows[0].values()))
        return inp.total if inp.total is not None else inp.count


def _prepare(inp: ReviewInput, catalog: _Memo) -> _Turn:
    cy = inp.cypher or ""
    params = inp.parameters or {}
    rows = [r for r in (inp.rows or []) if isinstance(r, dict)]
    return _Turn(inp=inp, catalog=catalog, cy=cy, params=params, q=inp.question or "", rows=rows,
                 vl=_var_labels(cy), cf=_contains_filters(cy, params), eq=_equality_filters(cy, params),
                 cols=_returned_columns(cy), grouped=_is_grouped(cy), count_only=_is_count_only(cy))


# A finding: what fired, the facts to disclose, and (for three checks) one concrete next query.
@dataclass
class _Finding:
    detail: str
    fact: str
    suggestion: dict | None = None


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _list_values(counter_items) -> str:
    items = list(counter_items)
    shown = ", ".join(f"{v} {n:,}" if isinstance(n, int) else f"{v}" for v, n in items[:5])
    return shown + (f" and {len(items) - 5} more" if len(items) > 5 else "")


def _quoted(values) -> str:
    q = [f"'{v}'" for v in values]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " and " + q[-1]


def _rewrite_with_value(question: str, term: str, value: str) -> str:
    """The question with the matched term replaced by the exact stored value.

    "show samples for human subjects who convert to Mtb infection positive" + Converter ->
    "show samples for human subjects classified as Converter" (a relative clause holding the term is replaced
    whole); "what macaca monkeys have ..." + Macaca fascicularis -> "what Macaca fascicularis monkeys have ...".
    """
    words = list(re.finditer(r"[A-Za-z0-9][\w\-]*", question))
    hit = None
    for i, m in enumerate(words):
        w = m.group(0).lower()
        if len(w) >= 3 and (term in w or w in term or len(os.path.commonprefix([w, term])) >= 5):
            hit = i
            break
    if hit is None:
        return _clip(f"{question.rstrip(' ?.')}. Only {value}.", QUERY_MAX)
    for j in range(hit - 1, max(-1, hit - 3), -1):
        if words[j].group(0).lower() in CLAUSE_WORDS:
            return _clip(question[: words[j].start()].rstrip() + f" classified as {value}", QUERY_MAX)
    m = words[hit]
    return _clip(question[: m.start()] + value + question[m.end():], QUERY_MAX)


def _split_suggestion(t: _Turn, term: str, pairs: list[tuple[str, int]], reason: str, from_rows: bool) -> dict | None:
    keep = [(v, n) for v, n in pairs if not _negated(str(v), term)]
    if not keep:
        return None
    value, n = max(keep, key=lambda p: p[1])
    sug = {"kind": "value_split", "label": _clip(f"Only {value}", LABEL_MAX),
           "query": _rewrite_with_value(t.q, term, str(value)), "reason": reason}
    total = t.inp.total if t.inp.total is not None else t.inp.count
    if from_rows and total is not None and len(t.rows) >= total:
        sug["expected_count"] = n
    return sug


# ---------------------------------------------------------------- the checks ---------------------------------------
def _value_checks(t: _Turn) -> dict[str, _Finding]:
    """negated_value, value_split_rows, value_split_catalog and stem_miss, one pass over the CONTAINS filters."""
    out: dict[str, _Finding] = {}
    for var, attr, term in t.cf:
        if attr in ("search_text", "title"):  # free text; a partial title is the Tier 2 title gate's business
            continue
        lab = t.vl.get(var)
        stored = t.vals(lab, attr)
        names = [str(v) for v, _n in stored]
        matched = [(str(v), n) for v, n in stored if term in str(v).lower()]
        alias = next((a for a, va in t.cols.items() if va == (var, attr)), None)
        counter = None
        if alias and t.rows and any(alias in r for r in t.rows):
            counter = Counter(r.get(alias) for r in t.rows if r.get(alias) is not None)
        told = t.grouped or (counter is not None and _reply_states_split(t.inp.reply_draft, counter))

        # a matched stored value carries a negation right before the term (Non-converter for 'convert')
        if not told and "negated_value" not in out:
            neg = [v for v, _n in matched if _negated(v, term)]
            if neg:
                pairs = counter.most_common() if counter else matched
                fact = (f"The matched values were: {_list_values(counter.most_common())}." if counter
                        else f"The search term also matches {_quoted(neg[:3])}.")
                out["negated_value"] = _Finding(f"{attr} CONTAINS '{term}' also matches '{neg[0]}'", fact,
                                                _split_suggestion(t, term, pairs, fact, counter is not None))
        # the matched column came back with values that are not spellings of one another
        if counter is not None and not t.grouped:
            # a column where no value repeats is a list of distinct records, not a split into categories
            if (len(counter) >= 2 and max(counter.values()) >= 2 and _distinct_meanings(list(counter), term)
                    and not told
                    and "value_split_rows" not in out):
                fact = f"The matched values were: {_list_values(counter.most_common())}."
                out["value_split_rows"] = _Finding(
                    f"{alias}: " + ", ".join(f"{v} {n}" for v, n in counter.most_common()), fact,
                    _split_suggestion(t, term, counter.most_common(), fact, True))
        elif (len({v.lower() for v, _n in matched}) >= 2 and _distinct_meanings([v for v, _ in matched], term)
              and not t.grouped and "value_split_catalog" not in out):
            fact = f"The search term matches several stored values: {_quoted([v for v, _ in matched[:4]])}."
            out["value_split_catalog"] = _Finding(f"{attr} CONTAINS '{term}' matches {[v for v, _ in matched]}",
                                                  fact, _split_suggestion(t, term, matched, fact, False))
        # a stored value is a shorter stem of the term (tif for tiff), so CONTAINS misses it
        stems = [v for v in names if 3 <= len(v.strip(".").lower()) < len(term)
                 and term.startswith(v.strip(".").lower()) and term not in v.lower()]
        if stems and "stem_miss" not in out:
            fact = f"The search matched '{term}' only; stored values also include {_quoted(stems[:4])}."
            stem = min((s.strip(".").lower() for s in stems), key=len)
            out["stem_miss"] = _Finding(
                f"{attr} CONTAINS '{term}' misses {stems}", fact,
                {"kind": "relaxed_variant", "label": "Include all spellings",
                 "query": _clip(f"{t.q.rstrip()} Include every spelling of {stem}.", QUERY_MAX), "reason": fact})
    return out


def _all_question_narrowed(t: _Turn) -> _Finding | None:
    if not ALL_CUE.search(t.q):
        return None
    where = re.split(r"\bRETURN\b", t.cy)[0]
    if re.search(r"sample_count\s*>\s*0", where):
        fact = "The query counted only types that hold samples."
        return _Finding("sample_count > 0 on an all/different question", fact,
                        {"kind": "relaxed_variant", "label": "Count every defined type",
                         "query": _clip(f"{t.q.rstrip()} Include types with no samples.", QUERY_MAX),
                         "reason": fact})
    for var, prop in re.findall(r"(\w+)\.(\w+)\s+IS NOT NULL", where):
        rest = t.cy.replace(f"{var}.{prop} IS NOT NULL", "")
        if not re.search(rf"{var}\.{prop}\b", rest):
            return _Finding(f"{var}.{prop} IS NOT NULL on an all/different question",
                            "The query left out records with no value for a property the question did not ask about.")
    return None


def _zero_unproven_base(t: _Turn) -> _Finding | None:
    """A zero behind a fuzzy anchor and another filter, whose starting set was never counted.

    Explained zeros stay quiet: an exact UID that is absent ("not found"), a count over the catalog nodes, an exact
    value missing from a complete stored list, and a reply that already offers dropping the filter.
    """
    if t.result_n() != 0:
        return None
    if re.search(r"uuid\s*[:=]\s*\$\w+", t.cy):
        return None
    if re.search(r"\(\w+:(Attribute|SampleType)\b", t.cy) and not t.vl:
        return None
    for var, attr, term in t.eq:
        stored = t.vals(t.vl.get(var), attr)
        if stored and len(stored) < VALUES_CAP and term not in {str(v).lower() for v, _n in stored}:
            return None
    n_filters = len(t.cf) + len(t.eq) + len(re.findall(r"\bEXISTS\s*\{", t.cy))
    if not t.cf or n_filters < 2:
        return None
    if t.inp.reply_draft and DROP_FILTER_OFFER.search(t.inp.reply_draft):
        return None
    return _Finding("zero behind a fuzzy anchor and another filter; base never counted",
                    "The starting set for this search was never counted.")


def _named_alias_applied(question: str, value_words: list[str], blob: set[str]) -> bool:
    """The question glosses the value with its own abbreviation, and that abbreviation is applied: 'glioblastoma
    (GBM)' is applied when the query filters on 'gbm'."""
    pat = r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(map(re.escape, value_words)) + r"(?![a-z0-9])\s*\(([^)]{1,20})\)"
    for m in re.finditer(pat, question.lower()):
        gloss = _tokens(m.group(1))
        if gloss and gloss <= blob:
            return True
    return False


def _unapplied_value(t: _Turn) -> _Finding | None:
    """A value the question names, stored on a queried type, that the query never applies (neither the value nor
    its attribute)."""
    qn = " " + re.sub(r"[^a-z0-9]+", " ", t.q.lower()) + " "
    blob = _tokens(re.sub(r"\bT_\w+", " ", t.cy) + " " + json.dumps(t.params, default=str))
    for _var, lab in t.vl.items():
        type_words = _tokens(str(t.catalog.type_name(lab) or ""))
        for attr in t.catalog.attributes(lab) or []:
            if attr.lower() in blob:
                continue
            for v, _c in t.vals(lab, attr):
                vn = re.sub(r"[^a-z0-9]+", " ", str(v).lower()).strip()
                if len(vn) < 3 or vn.isdigit() or vn in STOP_VALUES or _tokens(vn) <= type_words:
                    continue
                if f" {vn} " in qn and not _tokens(vn) <= blob and not _named_alias_applied(t.q, vn.split(), blob):
                    return _Finding(f"question names {lab}.{attr}='{v}', Cypher never applies it",
                                    f"The question names '{v}', but the search did not filter on it.")
    return None


def _premise_count(t: _Turn) -> _Finding | None:
    got = {t.result_n(), t.inp.total, t.inp.count}
    for x in stated_counts(t.q):
        if x >= 50 and x not in got:
            return _Finding(f"question states {x}, result is {t.result_n()}", PREMISE_FACT.format(n=f"{x:,}"))
    return None


def _breakage(inp: ReviewInput) -> _Finding | None:
    if inp.cypher is None:
        return _Finding("no Cypher ran", "No database query ran for this question.")
    if inp.ok is False:
        # The fact says only that it failed: "on its final attempt" told the reply there were others, which it may
        # never narrate. The detail, for the debug panel, keeps it.
        return _Finding("Neo4j error on the final attempt", "The database query failed.")
    return None


# ---------------------------------------------------------------- review -------------------------------------------
def review_tier1(inp: ReviewInput, catalog: CatalogProvider, *, skip: dict[str, str] | None = None) -> GraphReview:
    """Tier 1: deterministic, no Neo4j. Never raises; a check that fails is recorded as not fired.

    ``skip`` maps a check that runs on its own (``all_question_narrowed``, ``zero_unproven_base``,
    ``unapplied_value``, ``premise_count``) to why this caller leaves it out; it is recorded as not fired with
    that reason and never runs. The follow-up loop leaves out ``premise_count`` (``FOLLOWUP_TIER1_SKIP``)."""
    t0 = time.monotonic()
    findings: dict[str, _Finding] = {}
    checks: list[Check] = []
    error = None
    skip = dict(skip or {})

    def run(name, fn):
        if name in skip:
            checks.append(Check(name, False, skip[name]))
            return
        try:
            f = fn()
        except Exception as exc:  # a reviewer bug must never cost the user their answer
            checks.append(Check(name, False, f"error: {type(exc).__name__}: {exc}"[:200]))
            return
        if f is not None:
            findings[name] = f
        checks.append(Check(name, f is not None, f.detail if f else ""))

    run("breakage", lambda: _breakage(inp))
    turn = None
    if "breakage" not in findings:
        try:
            turn = _prepare(inp, _Memo(catalog))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:200]

    value_names = ("negated_value", "value_split_rows", "value_split_catalog", "stem_miss")
    if turn is None:
        why = "skipped: the query did not run" if "breakage" in findings else f"error: {error}"
        checks += [Check(n, False, why) for n in SHIP[1:]]
    else:
        try:
            vf = _value_checks(turn)
        except Exception as exc:
            vf = None
            checks += [Check(n, False, f"error: {type(exc).__name__}: {exc}"[:200]) for n in value_names]
        if vf is not None:
            for n in value_names:
                findings.update({n: vf[n]} if n in vf else {})
                checks.append(Check(n, n in vf, vf[n].detail if n in vf else ""))
        run("all_question_narrowed", lambda: _all_question_narrowed(turn))
        run("zero_unproven_base", lambda: _zero_unproven_base(turn))
        run("unapplied_value", lambda: _unapplied_value(turn))
        run("premise_count", lambda: _premise_count(turn))

    # recorded, never fired: the title gate belongs to Tier 2; count_only is information only
    try:
        titles = [term for _v, a, term in (turn.cf if turn else []) if a == "title"]
        checks.append(Check("title_contains_multi", False,
                            f"tier 2 gate: a title matched by the partial term '{titles[0]}'" if titles else ""))
        checks.append(Check("count_only", False,
                            "information only: the RETURN is an aggregate" if turn and turn.count_only else ""))
    except Exception as exc:
        checks += [Check(n, False, f"error: {type(exc).__name__}: {exc}"[:200]) for n in INFO_ONLY]

    if "breakage" in findings:
        verdict = "note"
    elif findings:
        verdict = "suggest"
    else:
        verdict = "ok"

    disclosure = None
    suggestion = None
    if verdict != "ok":
        facts: list[str] = []
        for name in SHIP:
            f = findings.get(name)
            if f and f.fact not in facts and len(" ".join(facts + [f.fact])) <= DISCLOSURE_MAX:
                facts.append(f.fact)
        disclosure = " ".join(facts) or None
        for name in ("negated_value", "value_split_rows", "value_split_catalog", "stem_miss",
                     "all_question_narrowed"):
            f = findings.get(name)
            if f and f.suggestion:
                suggestion = f.suggestion
                break
    return GraphReview(verdict=verdict, checks=checks, disclosure=disclosure, suggestion=suggestion, variants=[],
                       elapsed_ms=int((time.monotonic() - t0) * 1000), error=error)


def as_debug(review: GraphReview) -> dict:
    """The whole review, for ``debug.graph_review``."""
    return dataclasses.asdict(review)


# ---------------------------------------------------------------- the follow-up loop's checks ----------------------
# A follow-up loop query runs Tier 1 like a graph turn, minus premise_count, plus two checks that read the user's
# words against the stored result the loop is about: premise (the user's count of the earlier set) and binding (the
# user refers back, and the loop is about an older result than the newest). No Tier 2: the loop's model can run a
# relaxed query itself, and a count per loop query would stack the reviewer's time budget.

PREMISE = "premise"
BINDING = "binding"

#: Tier 1's premise_count compares a number in the question with this query's result. A loop query's result is part
#: of the earlier set by design ("Of the 745 CC mice, how many are female?" answers 300), so that comparison would
#: fire on every question that names the set's size. The loop checks the user's number against the stored total.
FOLLOWUP_TIER1_SKIP = {"premise_count": "skipped: a follow-up's result is part of the earlier set; "
                                        "the user's number is checked against the stored total (premise)"}

#: A loop query bound to the earlier result's UIDs ($uids) holds every filter that result had: "How many of the
#: 1,641 NDMA-treated mice are female?" rightly filters on sex alone, so unapplied_value would call NDMA dropped.
FOLLOWUP_SEEDED_SKIP = {"unapplied_value": "skipped: the query is scoped to the earlier result's UIDs, "
                                            "which carry that result's filters"}

#: A number the user states as the size of the earlier set: "these 1,206 mouse sample records", "all the 4,095
#: Sequencing Data (D.SEQ) files". It must follow a word that points at a set, so a threshold ("more than 100
#: samples") is not read as one; NUMBER and COUNT_WORD are premise_count's own. A year, a rank or a sample size
#: after such a word ("Of the 2024 samples", "the 100 most recent samples", "a random subset of 200 samples") is
#: dropped by premise_count's own rule (``_not_a_stated_count``).
SET_COUNT = re.compile(r"\b(?:these|those|the|all|of|your)\s+" + NUMBER + COUNT_WORD, re.I)


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def check_premise(user_text: str, *, stored_total) -> Check:
    """``premise``: the user states the earlier set's size, and the stored result says otherwise.

    "how many of these 1,206 mouse sample records ..." about a result that held 745 fires with the detail "the
    earlier result had 745, not 1,206". Quiet when any stated size matches ``stored_total``, when the user states no
    size, and when no total is known (``stored_total`` None or not a count). A year, a rank, a sample size or a
    threshold is no stated size (``_not_a_stated_count``, shared with ``premise_count``)."""
    if not _is_count(stored_total):
        return Check(PREMISE, False, "skipped: no stored total")
    text = user_text if isinstance(user_text, str) else ""
    stated = _set_sizes(SET_COUNT, text)
    if not stated or stored_total in stated:
        return Check(PREMISE, False, "")
    return Check(PREMISE, True, f"the earlier result had {stored_total:,}, not {stated[0]:,}")


def _router_followup_cue():
    """The router's follow-up cue check, or None when it cannot be loaded (``helpers/suggestions.py`` loads it the
    same way)."""
    try:
        from NessieAI.router import followup
        return followup.followup_cue
    except Exception:
        return None


def check_binding(*, target_bundle_id, newest_bundle_id, user_text: str) -> Check:
    """``binding``: the user refers back ("of those", "these samples"), and the follow-up is about a stored result
    that is not the newest one, so "those" may not be what the loop is answering about.

    The back-reference is the router's own ``followup_cue``. When that check cannot be loaded or raises, the text
    counts as referring back: fail closed, as the suggestion chips do. Quiet when the result is the newest, and when
    either id is unknown."""
    if not (_is_count(target_bundle_id) and _is_count(newest_bundle_id)):
        return Check(BINDING, False, "skipped: which stored result is the newest is not known")
    if target_bundle_id == newest_bundle_id:
        return Check(BINDING, False, "")
    cue = _router_followup_cue()
    try:
        refers_back = True if cue is None else bool(cue(user_text))
    except Exception:
        refers_back = True
    if not refers_back:
        return Check(BINDING, False, "")
    return Check(BINDING, True, f"this follow-up is about an earlier result (result {target_bundle_id}), "
                                f"not the newest one (result {newest_bundle_id})")


def _sentence(detail: str) -> str:
    s = " ".join(str(detail).split())
    s = s[:1].upper() + s[1:]
    return s if s.endswith(".") else s + "."


def with_checks(review: GraphReview, extra: list[Check]) -> GraphReview:
    """``review`` with ``extra`` recorded after its own checks.

    A fired check's detail, as a sentence, is disclosed BEFORE the review's own facts (the reply must say it
    first), and the facts that follow are kept whole while they fit in ``DISCLOSURE_MAX``. A fired check turns an
    ``ok`` verdict into ``suggest``; a ``note`` stays a note. The suggestion is the review's own."""
    fired = [c for c in extra if c.fired]
    checks = list(review.checks) + list(extra)
    if not fired:
        return dataclasses.replace(review, checks=checks)
    facts: list[str] = []
    for fact in [_sentence(c.detail) for c in fired] + re.split(r"(?<=\.)\s+", review.disclosure or ""):
        if fact and fact not in facts and len(" ".join(facts + [fact])) <= DISCLOSURE_MAX:
            facts.append(fact)
    verdict = "note" if review.verdict == "note" else "suggest"
    return dataclasses.replace(review, verdict=verdict, checks=checks, disclosure=" ".join(facts) or None)


# ---------------------------------------------------------------- a computation over rows in hand -------------------
# The follow-up loop's compute_over_rows (agents/followup_compute.py) filters and counts rows it already has, so there
# is no Cypher and no catalog to read: its payload says what each filter matched. The checks that read those are
# Tier 1's own ideas applied to the payload, and premise and binding read the user's words as they do for a loop query.

#: How many matched values a disclosure names before "and N more".
COMPUTE_VALUES_SHOWN = 5


def _matched_pairs(entry: dict) -> list[tuple[str, int]]:
    pairs = []
    for item in entry.get("matched_values") or []:
        if isinstance(item, (list, tuple)) and len(item) == 2 and _is_count(item[1]):
            pairs.append((str(item[0]), item[1]))
    return pairs


def _matched_text(entry: dict, pairs: list[tuple[str, int]]) -> str:
    """The matched values with their counts, the most common first, and how many more there were."""
    shown = pairs[:COMPUTE_VALUES_SHOWN]
    distinct = entry.get("distinct_matched")
    more = (distinct if _is_count(distinct) else len(pairs)) - len(shown)
    return ", ".join(f"{v} {n:,}" for v, n in shown) + (f" and {more:,} more" if more > 0 else "")


def _columns_text(columns: list, room: int) -> str:
    """The column names, as many as fit in ``room`` characters, then how many more."""
    names = [str(c) for c in columns]
    for k in range(len(names), 0, -1):
        text = ", ".join(names[:k]) + (f" and {len(names) - k} more" if k < len(names) else "")
        if len(text) <= room:
            return text
    return f"{len(names)} columns"


def review_compute(*, question: str, source_kind: str, source_total, target_bundle_id, newest_bundle_id,
                   payload: dict) -> GraphReview:
    """The reviewer over one ``compute_over_rows`` payload, for the payload's ``review``.

    Six checks, always recorded in this order: ``breakage`` (the computation failed; a refusal that sends the model
    to a new query is not a failure), ``premise`` (``check_premise``: the user's number against ``source_total``, the
    stored result's size as a set), ``binding`` (``check_binding``, stored rows only: the rows of this turn's own query
    were checked when that query ran), ``negated_value`` (a ``contains`` matched a value that negates its term:
    "Non-converter" for "convert"), ``value_split`` (a ``contains`` matched two or more different values that repeat;
    every count is disclosed) and ``snapshot_zero`` (a zero from these rows only means they do not show it: the
    disclosure names the columns they hold).

    The verdict is ``note`` on breakage or snapshot_zero, else ``suggest`` when anything fired, else ``ok``; the
    disclosure is the fired checks' facts in that order, whole facts only, within ``DISCLOSURE_MAX``. No suggestion
    and no variants: the loop's model runs any next query itself. Never raises: a malformed payload or an error gives
    an ``ok`` review with ``error`` set and the checks recorded so far."""
    t0 = time.monotonic()
    checks: list[Check] = []
    facts: list[str] = []

    def record(name: str, fired: bool, detail: str = "", fact: str | None = None) -> None:
        checks.append(Check(name, fired, detail))
        if fired and fact and fact not in facts and len(" ".join(facts + [fact])) <= DISCLOSURE_MAX:
            facts.append(fact)

    def from_check(check: Check | None, name: str) -> None:
        fired = bool(check is not None and check.fired)
        detail = check.detail if check is not None else ""
        record(name, fired, detail, _sentence(detail) if fired and detail else None)

    try:
        if not isinstance(payload, dict):
            raise ValueError("the computation's payload is not a dict")
        where = payload.get("where")
        where = [] if where is None else where
        if not isinstance(where, list):
            raise ValueError("the computation's `where` is not a list")
        ok = payload.get("ok") is True
        breakage = not ok and not payload.get("needs_query")
        record("breakage", breakage, str(payload.get("error") or "")[:200] if breakage else "",
               "The computation over the earlier result failed.")

        from_check(check_premise(question, stored_total=source_total), PREMISE)
        if source_kind == "stored":
            from_check(check_binding(target_bundle_id=target_bundle_id, newest_bundle_id=newest_bundle_id,
                                     user_text=question), BINDING)
        else:
            record(BINDING, False, "skipped: these are the rows of this turn's own query, checked when it ran")

        contains = [e for e in where if isinstance(e, dict) and e.get("op") == "contains"] if ok else []
        negated_at = None
        for i, entry in enumerate(contains):
            term = str(entry.get("value") or "").lower()
            pairs = _matched_pairs(entry)
            neg = [v for v, _n in pairs if term and _negated(v, term)]
            if neg:
                negated_at = i
                column = entry.get("column")
                record("negated_value", True, f"{column} contains '{term}' also matches '{neg[0]}'",
                       f"The filter on {column} also matched '{neg[0]}': {_matched_text(entry, pairs)}.")
                break
        else:
            record("negated_value", False)

        for i, entry in enumerate(contains):
            if i == negated_at:  # its values are disclosed already
                continue
            pairs = _matched_pairs(entry)
            if len({v.lower() for v, _n in pairs}) >= 2 and max(n for _v, n in pairs) >= 2:
                column = entry.get("column")
                record("value_split", True, f"{column}: " + ", ".join(f"{v} {n}" for v, n in pairs),
                       f"The filter on {column} matched several values: {_matched_text(entry, pairs)}.")
                break
        else:
            record("value_split", False)

        result = payload.get("result")
        zero = ok and ((bool(where) and payload.get("count") == 0)
                       or (isinstance(result, dict) and result.get("count") == 0))
        columns = payload.get("columns") if isinstance(payload.get("columns"), list) else []
        lead = "Zero here means none of these rows show it; they hold only: "
        record("snapshot_zero", zero, "a zero over the rows in hand" if zero else "",
               lead + _columns_text(columns, DISCLOSURE_MAX - len(lead) - 1) + ".")
    except Exception as exc:  # a reviewer bug must never cost the user their answer
        return GraphReview("ok", checks, None, None, [], int((time.monotonic() - t0) * 1000),
                           error=f"{type(exc).__name__}: {exc}"[:200])

    fired = {c.name for c in checks if c.fired}
    if fired & {"breakage", "snapshot_zero"}:
        verdict = "note"
    elif fired:
        verdict = "suggest"
    else:
        verdict = "ok"
    disclosure = (" ".join(facts) or None) if verdict != "ok" else None
    return GraphReview(verdict, checks, disclosure, None, [], int((time.monotonic() - t0) * 1000))

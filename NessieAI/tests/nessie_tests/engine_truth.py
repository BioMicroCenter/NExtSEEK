"""Ground truth for the graph_search Nessie POC (spec E5): the truth-file models and the reply check.

Host-safe on purpose: pydantic and the standard library only, no Django, no Neo4j. The
same models are read by three programs that run in different places:

- `scripts/derive_truth.py` fills them inside the operator's venue (read-only oracles);
- `scripts/build_engine_cases.py` makes skeletons and `--cases` files on the host;
- `engine_compare.py` scores the arms on the host with only `uv run --with pydantic`.

Truth files live under `$GS_WORK/nessie/truth/` and never in the repository: several
questions and one ladder value name real people (spec E4).

A question has one intended reading (`TruthTurn.reading`) and an accepted answer
(`Expected`), plus any defensible alternate reading, marked as such. Alternates count as
correct and are tallied separately, so every verdict can be read with and without them.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# Flags a truth author may set on a question (spec E4, E5; plan task G1 to G7). Free
# strings, so a later flag needs no schema change; these are the ones the tools read.
FLAG_CHANGED_BY_MERGE = "changed_by_merge"
FLAG_INTERPRETIVE = "interpretive"
FLAG_ENTITY_VOCABULARY_GAP = "entity_vocabulary_gap"
FLAG_BROAD_MATCH = "broad_match"
FLAG_REST_ROUTED_TODAY = "rest_routed_today"
# `rest_endpoint:<name>` records which REST endpoint today's corpus criteria expect for a
# REST-routed lineage question (sample-tree or parents_by_child_types); the pilot uses it.
REST_ENDPOINT_FLAG_PREFIX = "rest_endpoint:"


class Oracle(BaseModel):
    """How one answer is computed, read-only, as the superuser.

    graph_search: `body` is POSTed to the graph_search endpoint, `params` are its query
      parameters; the answer is the response's `total`.
    cypher: `statement` with `params`, in a READ transaction.
    sql: one SELECT or WITH `statement` on the SEEK database, inside START TRANSACTION
      READ ONLY; `params` are the driver's pyformat parameters.
    measured: a number copied from a results file named by `source` (relative to the
      truth file's directory); `params` pick the cell (`query`, `arm`, `account`), or
      name a dotted `path` into any other JSON file.
    """
    engine: Literal["graph_search", "cypher", "sql", "measured"]
    body: dict | None = None
    statement: str | None = None
    params: dict = Field(default_factory=dict)
    source: str | None = None


class Alternate(BaseModel):
    """A defensible second reading. Correct when met, and counted apart from the primary."""
    reading: str
    required_numbers: list[float] = Field(default_factory=list)
    required_items: list[str] = Field(default_factory=list)


class Expected(BaseModel):
    """The accepted answer.

    `required_numbers` and `required_items` are what a reply must state. When both are
    empty, a numeric `value` stands in as the one required number; a `none` answer is
    met by a zero or a plain "no"/"none". The types, attributes and relationships the
    question names are what the scorer's entity and request stages check.
    """
    kind: Literal["count", "value", "list", "set", "none"]
    value: int | float | str | list | None = None
    required_numbers: list[float] = Field(default_factory=list)
    required_items: list[str] = Field(default_factory=list)
    alternates: list[Alternate] = Field(default_factory=list)
    sampletypes: list[str] = Field(default_factory=list)
    attributes: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)


class TruthTurn(BaseModel):
    label: str
    query: str
    reading: str
    oracle: Oracle | None
    expected: Expected
    second_oracle: Oracle | None = None
    single_source: bool = False   # a graph-only fact, one oracle
    derived_at: str | None = None
    # Written by derive_truth when a second oracle ran: its answer, and the disagreement
    # with the first one (None when they agree). A disagreement is resolved by the truth
    # author or the question is marked interpretive (spec E5); nothing clears it silently.
    second_value: int | float | str | list | None = None
    disagreement: str | None = None


class TruthQuestion(BaseModel):
    id: str
    family: str
    group: Literal["A", "B"]
    source: Literal["corpus", "ladder", "b2"]
    turns: list[TruthTurn]                     # one turn in this POC
    scorable: bool = True
    exclusion: str | None = None
    flags: list[str] = Field(default_factory=list)
    corpus_numbers: list[float] = Field(default_factory=list)   # the corpus's old reply numbers
    merged_into: str | None = None     # a ladder or B2 question that duplicates another question


class Fingerprint(BaseModel):
    sample_count: int
    catalog_hash: str
    synced_at: str | None
    derived_at: str


class TruthFile(BaseModel):
    name: str
    group: Literal["A", "B"]
    fingerprint: Fingerprint | None = None
    questions: list[TruthQuestion]


# ── numbers in a reply ───────────────────────────────────────────────────────

# Separators a model writes between thousands groups besides a comma: a space, a
# no-break space, a narrow no-break space and a thin space.
_SPACES = "    "
# Not preceded by a word character, a decimal point, a comma, a hyphen or a slash: the
# number is not the tail of a longer number, a decimal, a UID ("TIS-...-26") or a date.
_LEFT = r"(?<![\w.,/-])"
# Not followed by a word character, a decimal or thousands continuation, or a date part.
_RIGHT = r"(?![\w]|[.,]\d|[-/]\d)"


def _grouped(digits: str, sep: str) -> str:
    head = len(digits) % 3 or 3
    groups = [digits[:head]] + [digits[i:i + 3] for i in range(head, len(digits), 3)]
    return sep.join(groups)


def number_patterns(n: float) -> re.Pattern:
    """A pattern that finds the number `n` as a model would write it in a reply.

    107412 matches "107,412", "107412" and "107 412" (any of the usual space
    separators), and not "1,107,412", "107,412.5", or the 2019 in "2019-05-01". A
    non-integer matches its shortest decimal spelling with the same grouping rules.
    The pattern is plain `re` syntax, so the case builder can hand `.pattern` to the e2e
    DSL's `matches_re`, which runs it with re.IGNORECASE.
    """
    value = float(n)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value.is_integer():
        whole, frac = str(int(value)), ""
    else:
        text = repr(value)
        if "e" in text or "E" in text:
            text = f"{value:.12f}".rstrip("0")
        whole, frac = text.split(".")
    tail = re.escape("." + frac) if frac else ""
    forms = [re.escape(whole) + tail]
    if len(whole) > 3:
        forms.append(re.escape(_grouped(whole, ",")) + tail)
        spaced = _grouped(whole, "\x00").replace("\x00", f"[{_SPACES}]")
        # A space-grouped number must not continue a space-grouped number on the left
        # ("1 107 412") or on the right ("107 412 000").
        forms.append(rf"(?<!\d[{_SPACES}]){spaced}{tail}(?![{_SPACES}]\d{{3}}(?!\d))")
    body = "|".join(f"(?:{f})" for f in forms)
    return re.compile(f"{_LEFT}{re.escape(sign)}(?:{body}){_RIGHT}")


_NONE_WORDS = re.compile(r"\b(?:no|none|zero|nothing)\b", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def reply_text(reply) -> str:
    """The reply as plain text: tags dropped, entities decoded. Non-strings read as empty."""
    if not isinstance(reply, str):
        return ""
    return html.unescape(_TAG.sub(" ", reply))


def item_pattern(item: str) -> re.Pattern:
    """A required item (a UID, a title) as a whole token, ignoring case."""
    return re.compile(rf"(?<![\w]){re.escape(item)}(?![\w])", re.IGNORECASE)


def fmt_number(n) -> str:
    """107412.0 -> "107412"; 12.5 -> "12.5". For messages and reports."""
    value = float(n)
    return str(int(value)) if value.is_integer() else repr(value)


def _meets(text: str, numbers, items) -> tuple[bool, str]:
    missing = [fmt_number(n) for n in numbers if not number_patterns(n).search(text)]
    missing += [repr(i) for i in items if not item_pattern(str(i)).search(text)]
    return (not missing, "missing " + ", ".join(missing) if missing else "")


def primary_requirements(expected: Expected) -> tuple[list[float], list[str]]:
    """The numbers and items the primary reading requires (a numeric `value` stands in
    when neither list is given). The case builder writes these as reply criteria."""
    numbers = list(expected.required_numbers)
    items = list(expected.required_items)
    if not numbers and not items and expected.kind in ("count", "value"):
        value = expected.value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numbers = [float(value)]
        elif isinstance(value, str) and value:
            items = [value]
    return numbers, items


def reply_satisfies(reply, expected: Expected) -> tuple[bool, str]:
    """Does the reply state the accepted answer? `(True, "primary")`,
    `(True, "alternate: <reading>")`, or `(False, <why>)`.

    The primary reading is tried first, then each alternate in order. A `none` answer
    with nothing required is met by a zero or a plain "no", "none", "zero" or "nothing".
    An expectation that requires nothing is never met: an unfilled truth must not score
    a reply correct.
    """
    text = reply_text(reply)
    if not text.strip():
        return False, "no reply"
    numbers, items = primary_requirements(expected)
    if numbers or items:
        ok, why = _meets(text, numbers, items)
        if ok:
            return True, "primary"
    elif expected.kind == "none":
        if number_patterns(0).search(text) or _NONE_WORDS.search(text):
            return True, "primary"
        why = "no zero or 'none' in the reply"
    else:
        why = "the truth requires nothing (unfilled)"
    for alt in expected.alternates:
        if (alt.required_numbers or alt.required_items) and _meets(
                text, alt.required_numbers, alt.required_items)[0]:
            return True, f"alternate: {alt.reading}"
    return False, why


# ── truth files on disk ──────────────────────────────────────────────────────

def _looks_like_truth(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and "questions" in payload and "group" in payload


def truth_paths(path) -> list[Path]:
    """A truth file, or every truth file in a directory (sorted by name).

    A directory may hold other JSON (a copied benchmark results file that a `measured`
    oracle reads); only files shaped like a TruthFile are returned.
    """
    path = Path(path)
    if path.is_dir():
        return [p for p in sorted(path.glob("*.json")) if _looks_like_truth(p)]
    return [path]


def load_truth(path) -> TruthFile:
    return TruthFile.model_validate_json(Path(path).read_text(encoding="utf-8"))

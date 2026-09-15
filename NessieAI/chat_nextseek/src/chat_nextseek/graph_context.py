"""Render the graph agent's schema context, variant (b), from the v1.1 catalog.

Pure functions: no Neo4j, no config, no Django. The catalog reader (``graph_catalog``) supplies the rows; this
module only turns them into compact text. The rows are read by field name, so any object with the fields of
``graph_catalog``'s dataclasses (or a dict with the same keys) renders.

The text has three parts (spec section 4.2):

1. the structure, hand-owned text in ``prompts/graph_schema_structure.txt``, kept consistent with
   ``docs/neo4j-schema.md`` v1.1 by a test;
2. the type index, one line per non-deprecated sample type;
3. at most ``MAX_TYPES`` resolved types, each with its K most-filled attributes in full and the rest by name.

``render_graph_context`` holds the whole text within ``BUDGET_BYTES``: when it is over, K steps down through
``K_STEPS`` (0 means names only) before a resolved section is dropped, the last one first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

STRUCTURE_PATH: Path = Path(__file__).resolve().parent / "prompts" / "graph_schema_structure.txt"
BUDGET_BYTES = 32_768
K_STEPS = (25, 15, 10, 0)  # 0 means names only
MAX_TYPES, MEANING_MAX, VALUE_MAX = 3, 120, 60
TOP_VALUES = 10  # values rendered per attribute at most (the catalog stores up to 10)
SUMMARY_MAX = 240  # a summary's first sentence is cut here, so one long summary cannot outgrow the budget

# The keyword gates the graph agent used before the catalog (agents/graph.py), kept word for word.
PROTOCOL_WORDS = ("protocol", "method", "procedure", "technique")
ASSAY_WORDS = ("assay", "sequencing", "cytometry", "spectrometry", "imaging", "data", "processed", "associated",
               "underwent", "via", "collection", "extraction")
# Studies and published studies: study, paper, publication (and publish), DOI, PMID, as whole words.
STUDY_WORDS_RE = re.compile(r"\b(?:stud(?:y|ies)|papers?|publications?|publish\w*|doi|pmid)\b", re.IGNORECASE)

# v1.1 rule 1: the metadata key UID is never written as a property (it equals uuid).
_SKIPPED_ATTRIBUTES = frozenset({"UID"})
_ABBREVIATIONS = frozenset({"e.g", "i.e", "etc", "vs", "approx", "cf", "ca", "no", "fig", "resp", "incl", "esp"})
_STOP_RE = re.compile(r"[.;!?](?=\s|$)")
_KEY_PREFIX_RE = re.compile(r"^\d+:")


# ---------------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------------

def _get(obj: Any, name: str, default: Any = None) -> Any:
    """A field of a dataclass, a SimpleNamespace or a dict, or ``default``."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump() or {}
    return dict(vars(obj)) if hasattr(obj, "__dict__") else {}


def _count(n: int) -> str:
    return f"{int(n):,}"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{_count(n)} {singular if int(n) == 1 else plural}"


def _samples(n: int | None) -> str | None:
    if n is None:
        return None
    if int(n) == 0:
        return "no samples"
    return _plural(n, "sample", "samples")


def _quote(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _number(x: Any) -> str:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return str(x)
    if float(x).is_integer() and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.6g}"


def _property_name(title: str, needs_backticks: bool) -> str:
    return "`" + title.replace("`", "``") + "`" if needs_backticks else title


def _one_line(text: Any) -> str:
    return " ".join(str(text).split())


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit - 3]
    space = head.rfind(" ")
    if space > limit // 2:
        head = head[:space]
    return head.rstrip(" ,;:") + "..."


def _first_stop(text: str) -> re.Match | None:
    """The first sentence or clause end, skipping abbreviations (``e.g.``) and initials."""
    for match in _STOP_RE.finditer(text):
        if match.group() == ".":
            word = text[:match.start()].rsplit(" ", 1)[-1].lower()
            if word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                continue
        return match
    return None


def first_clause(text: Any, limit: int = MEANING_MAX) -> str | None:
    """An attribute meaning trimmed to its first clause (up to ``.`` or ``;``), at most ``limit`` characters."""
    if text is None:
        return None
    s = _one_line(text)
    if not s:
        return None
    stop = _first_stop(s)
    if stop is not None:
        s = s[:stop.start()].rstrip()
    return _cut(s, limit) if s else None


def first_sentence(text: Any, limit: int = SUMMARY_MAX) -> str | None:
    """A summary's first sentence, its full stop kept, at most ``limit`` characters."""
    if text is None:
        return None
    s = _one_line(text)
    if not s:
        return None
    stop = _first_stop(s)
    if stop is not None:
        s = s[:stop.end()] if stop.group() != ";" else s[:stop.start()]
    return _cut(s, limit)


# ---------------------------------------------------------------------------------------------------------------
# Resolved codes
# ---------------------------------------------------------------------------------------------------------------

def _item_code(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    code = _get(item, "code")
    return code if isinstance(code, str) else None


def resolved_type_codes(plan: dict | None, entity: dict | None, known: set[str]) -> list[str]:
    """The sample type codes whose sections the graph agent gets, at most ``MAX_TYPES``, in order.

    The parser plan's ``resolved.sampletypes`` and ``filters.sampletype_code`` come first, then the entity
    output's ``sampletypes``. Codes the catalog does not know are dropped, and each code is kept once. A code that
    differs from a known title only in case maps onto it when that title is the only such match.
    """
    known = set(known or ())
    by_fold: dict[str, list[str]] = {}
    for title in known:
        by_fold.setdefault(title.casefold(), []).append(title)

    def resolve(raw: str | None) -> str | None:
        if not raw:
            return None
        raw = raw.strip()
        if raw in known:
            return raw
        folded = by_fold.get(raw.casefold(), [])
        return folded[0] if len(folded) == 1 else None

    plan_d, entity_d = _as_dict(plan), _as_dict(entity)
    candidates: list[str | None] = []
    candidates += [_item_code(item) for item in (_as_dict(plan_d.get("resolved")).get("sampletypes") or [])]
    filter_code = _as_dict(plan_d.get("filters")).get("sampletype_code")
    if isinstance(filter_code, str) and filter_code.strip():
        if resolve(filter_code) is not None:
            candidates.append(filter_code)
        else:
            candidates += re.split(r"[,;|\s]+", filter_code)
    candidates += [_item_code(item) for item in (entity_d.get("sampletypes") or [])]

    codes: list[str] = []
    for raw in candidates:
        code = resolve(raw)
        if code is not None and code not in codes:
            codes.append(code)
        if len(codes) == MAX_TYPES:
            break
    return codes


# ---------------------------------------------------------------------------------------------------------------
# The type index and the per-type sections
# ---------------------------------------------------------------------------------------------------------------

def _index_line(row: Any) -> str:
    head = f"{_get(row, 'title')} :{_get(row, 'label')}"
    if _get(row, "name"):
        head += " " + _quote(_get(row, "name"))
    if _get(row, "clade"):
        head += f" clade {_get(row, 'clade')}"
    samples = _samples(_get(row, "sample_count"))
    parts = [head, samples if samples is not None else "sample count unknown",
             _plural(_get(row, "attributes_with_values") or 0, "attribute with values", "attributes with values")]
    return ", ".join(parts)


def render_type_index(rows) -> str:
    """One line per non-deprecated sample type, in the order given, under a heading."""
    lines = ["## Sample types (code :label \"name\" clade, samples, attributes with values)"]
    lines += [_index_line(row) for row in rows or () if not _get(row, "deprecated", False)]
    return "\n".join(lines)


def _filled(attributes) -> list:
    """Attributes that hold a value, most-filled first (an unknown count after the known ones), then by title."""
    rows = [a for a in attributes or ()
            if _get(a, "title") not in _SKIPPED_ATTRIBUTES and _get(a, "sample_count") != 0]
    return sorted(rows, key=lambda a: (_get(a, "sample_count") is None, -(_get(a, "sample_count") or 0),
                                       str(_get(a, "title"))))


def _values(attribute: Any) -> str | None:
    values = list(_get(attribute, "top_values") or ())
    counts = list(_get(attribute, "top_counts") or ())
    rendered = []
    for i, value in enumerate(values):
        if value is None or len(str(value)) > VALUE_MAX:
            continue
        count = counts[i] if i < len(counts) else None
        rendered.append(_quote(value) + (f" {_count(count)}" if isinstance(count, int) else ""))
        if len(rendered) == TOP_VALUES:
            break
    return "values: " + ", ".join(rendered) if rendered else None


def _range(low: Any, high: Any, fmt) -> str | None:
    if low is None and high is None:
        return None
    return f"range {fmt(low) if low is not None else '?'}..{fmt(high) if high is not None else '?'}"


def _attribute_line(attribute: Any) -> str:
    title = str(_get(attribute, "title"))
    head = f"- {_property_name(title, bool(_get(attribute, 'needs_backticks')))} [{_get(attribute, 'value_type')}]"
    if _get(attribute, "sample_count") is not None:
        head += f" n={_count(_get(attribute, 'sample_count'))}"
    if _get(attribute, "declared") is False:
        head += " (undeclared)"
    parts = [head]
    unit_key = _get(attribute, "unit_key")
    if unit_key:
        parts.append(f"unit of {_KEY_PREFIX_RE.sub('', str(unit_key))}")
    for piece in (_range(_get(attribute, "num_min"), _get(attribute, "num_max"), _number),
                  _range(_get(attribute, "date_min"), _get(attribute, "date_max"), str),
                  _values(attribute),
                  first_clause(_get(attribute, "meaning"))):
        if piece:
            parts.append(piece)
    return " | ".join(parts)


def render_type_section(detail, k: int) -> str:
    """One resolved type: header, summary sentence, curated lines, K attributes in full, the rest by name."""
    head = f"### {_get(detail, 'title')} :{_get(detail, 'label')}"
    if _get(detail, "name"):
        head += " " + _quote(_get(detail, "name"))
    parts = [head]
    if _get(detail, "clade"):
        parts.append(f"clade {_get(detail, 'clade')}")
    samples = _samples(_get(detail, "sample_count"))
    if samples is not None:
        parts.append(samples)
    lines = [", ".join(parts)]

    summary = first_sentence(_get(detail, "summary"))
    if summary:
        lines.append(summary)
    for label, field in (("Curated parents", "curated_parents"), ("Curated children", "curated_children")):
        value = _get(detail, field)
        if value:
            lines.append(f"{label}: {_one_line(value)}")

    filled = _filled(_get(detail, "attributes"))
    k = max(int(k), 0)
    lines += [_attribute_line(a) for a in filled[:k]]
    rest = filled[k:]
    if rest:
        names = ", ".join(_property_name(str(_get(a, "title")), bool(_get(a, "needs_backticks"))) for a in rest)
        lines.append(f"{'also filled' if k else 'filled'}: {names}")
    if not filled:
        lines.append("(no attribute holds a value)")
    never = int(_get(detail, "never_filled") or 0)
    if never:
        lines.append(f"{_plural(never, 'declared attribute holds', 'declared attributes hold')} no value")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# The whole context
# ---------------------------------------------------------------------------------------------------------------

def load_structure() -> str:
    """The hand-owned structure section, without trailing blank lines."""
    return STRUCTURE_PATH.read_text(encoding="utf-8").strip()


def _assemble(structure: str, index: str, titles: list[str], sections: list[str], k: int,
              omitted: list[str]) -> str:
    parts = [structure, index]
    if sections:
        if k:
            how = f"the {k} most-filled attributes in full, then the rest by name"
        else:
            how = "attribute names only"
        parts.append(
            f"## Resolved sample types: {', '.join(titles)} ({how}; per attribute: [value type] n=samples "
            "holding a value | range | most frequent values with their sample counts | meaning)")
        parts += sections
    if omitted:
        parts.append(f"Left out to fit the context budget: {', '.join(omitted)} (see the type index).")
    return "\n\n".join(parts) + "\n"


def _fits(text: str, budget: int) -> bool:
    return len(text.encode("utf-8")) <= budget


def render_graph_context(snapshot, details, *, k: int = 25, budget: int = BUDGET_BYTES) -> str:
    """Structure, type index and at most ``MAX_TYPES`` resolved sections, within ``budget`` bytes.

    When the text is over the budget, K steps down (``k``, then each smaller step of ``K_STEPS``, 0 meaning
    names only) for every section at once; only at names only are sections dropped, the last one first. The
    structure and the index are always sent, so the text exceeds the budget only when they alone do.
    """
    structure = load_structure()
    index = render_type_index(_get(snapshot, "index") or ())
    details = list(details or ())[:MAX_TYPES]
    titles = [str(_get(d, "title")) for d in details]

    steps = [max(int(k), 0)] + [step for step in K_STEPS if step < k]
    text = ""
    for step in steps:
        text = _assemble(structure, index, titles, [render_type_section(d, step) for d in details], step, [])
        if _fits(text, budget):
            return text

    last_step = steps[-1]
    kept = list(details)
    omitted: list[str] = []
    while kept:
        omitted.insert(0, str(_get(kept.pop(), "title")))
        text = _assemble(structure, index, [str(_get(d, "title")) for d in kept],
                         [render_type_section(d, last_step) for d in kept], last_step, omitted)
        if _fits(text, budget):
            return text
    return text


# ---------------------------------------------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------------------------------------------

def _title_block(heading: str, titles) -> str | None:
    titles = [t for t in titles or () if t not in (None, "")]
    if not titles:
        return None
    return f"{heading}:\n" + ", ".join(_quote(t) for t in titles)


def _field_ci(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        for key, value in record.items():
            if str(key).lower() == name.lower():
                return value
        return None
    return _get(record, name) or _get(record, name.lower())


def _published_block(studies) -> str | None:
    lines = []
    for study in studies or ():
        title = _field_ci(study, "title")
        bits = [_quote(title) if title else "(untitled)"]
        for field in ("DOI", "PMID"):
            value = _field_ci(study, field)
            if value not in (None, ""):
                bits.append(f"{field} {value}")
        lines.append("- " + ", ".join(bits))
    if not lines:
        return None
    return "PUBLISHED STUDIES (Study nodes with a DOI or PMID):\n" + "\n".join(lines)


def _connections_block(connections) -> str | None:
    grouped: dict[str, list[str]] = {}
    for conn in connections or ():
        assay = _get(conn, "assay")
        parent, child = _get(conn, "parent_type"), _get(conn, "child_type")
        if assay is None or parent is None or child is None:
            grouped.setdefault("", []).append(json.dumps(conn if isinstance(conn, dict) else _as_dict(conn),
                                                         ensure_ascii=False, sort_keys=True))
            continue
        pair = f"{parent} -> {child}"
        grouped.setdefault(str(assay), [])
        if pair not in grouped[str(assay)]:
            grouped[str(assay)].append(pair)
    if not grouped:
        return None
    lines = [f"- {_quote(assay)}: {', '.join(pairs)}" if assay else f"- {', '.join(pairs)}"
             for assay, pairs in grouped.items()]
    return ("ASSAY-SAMPLE CONNECTIONS (assay: parent type -> child type; shows which side of an assay a sample "
            "type sits on):\n" + "\n".join(lines))


def render_vocabulary(vocab, question: str) -> str:
    """The keyword-gated vocabulary blocks, blank-line separated ("" when there is nothing to send).

    Investigation and project titles always; study titles and published studies when the question names a
    study, paper, publication, DOI or PMID; assay titles and assay connections on the assay words; protocol
    titles on the protocol words (the last two gates are the graph agent's existing word lists).
    """
    q = (question or "").lower()
    blocks = [
        _title_block("INVESTIGATION TITLES (Investigation.title)", _get(vocab, "investigation_titles")),
        _title_block("PROJECT TITLES (Project.title)", _get(vocab, "project_titles")),
    ]
    if STUDY_WORDS_RE.search(q):
        blocks.append(_title_block("STUDY TITLES (Study.title)", _get(vocab, "study_titles")))
        blocks.append(_published_block(_get(vocab, "published_studies")))
    if any(word in q for word in ASSAY_WORDS):
        blocks.append(_title_block("ASSAY TITLES (DERIVED_FROM.internal_assay_title values)",
                                   _get(vocab, "assay_titles")))
        blocks.append(_connections_block(_get(vocab, "assay_connections")))
    if any(word in q for word in PROTOCOL_WORDS):
        blocks.append(_title_block("PROTOCOL TITLES (DERIVED_FROM.protocol_title values)",
                                   _get(vocab, "protocol_titles")))
    return "\n\n".join(block for block in blocks if block)

"""FIELDS NAMED IN THIS QUESTION: the sample fields whose title holds a word the question counts.

"How many <word> does <project> have?" was answered as a count of Studies (who-asked-2, R3): the word was a sample
attribute on several types, and the graph step sees attribute lists only for the types the entity step resolved,
while a member cannot read the attribute catalog by query. So before the graph step writes anything, each word of
the resolved keywords is matched against the attribute titles of every type in the catalog snapshot's guard (which a
member's snapshot keeps; only counts are redacted for them), and the matches go to the graph step as one block, beside
the PROJECTS NAMED IN THIS QUESTION block. A signal, never a gate: the graph step still decides.

A word matches a title when it equals one of the title's word parts (``Stripe_Band`` and ``BaselineBand`` hold
``band``; ``Bandage`` does not), singular or plural. Only the words of the counted phrase are looked up, the one
right after "how many", "number of" or "count of" up to its verb or preposition ("study arms" in "how many study
arms are there"), so a field word used as a filter ("how many quolls have a band") sends no block. A container
word, a type's code or name word, or a word of a resolved project is not looked up.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: The block's heading; the graph agent prompt does not name it, the heading says what to do.
FIELDS_HEADING = (
    "FIELDS NAMED IN THIS QUESTION (sample attributes whose title holds a word the question counts, by type; such a "
    "word is a field of the samples, not a Study or another container: count its distinct values on each type's "
    "samples, and say which type each count comes from)"
)
#: At most this many fields are listed.
MAX_FIELDS = 12
#: Words that name a container or the graph itself, never a field to look up.
_CONTAINER_WORDS = frozenset({"sample", "study", "project", "investigation", "assay", "type", "record", "person",
                              "people", "lab", "attribute", "field", "value", "unique", "distinct", "total"})
_PART_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_COUNTED_RE = re.compile(r"\b(?:how\s+many|number\s+of|count\s+of)\s+([^?.,;:!]*)", re.I)
#: Where the counted phrase ends.
_PHRASE_END = frozenset({"are", "is", "was", "were", "do", "does", "did", "have", "has", "had", "in", "for", "of",
                         "with", "without", "there", "from", "per", "by", "on", "at", "that", "which", "who", "across",
                         "under", "to", "and", "or", "can", "could", "would", "will", "exist"})
#: The most words a counted phrase holds.
_PHRASE_WORDS = 4


def _counted_words(question: str) -> set[str]:
    """The words of each counted phrase in ``question``, singular, lower case."""
    out: set[str] = set()
    for m in _COUNTED_RE.finditer(question or ""):
        for token in m.group(1).split()[:_PHRASE_WORDS]:
            if token.lower() in _PHRASE_END:
                break
            out |= _parts(token)
    return out


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _parts(text: str) -> set[str]:
    return {_singular(p.lower()) for p in _PART_RE.findall(text or "")}


def _strings(*sources) -> list[str]:
    return [v for source in sources for v in (source or ()) if isinstance(v, str)]


def fields_named(snapshot: Any, question: str, plan: Mapping, entity: Mapping) -> list[tuple[str, str]]:
    """``[(type code, attribute title)]``, sorted, for each attribute whose title holds a word of the question's
    counted phrase; [] when the question counts nothing or no counted word is a field."""
    resolved = plan.get("resolved") if isinstance(plan.get("resolved"), Mapping) else {}
    projects = _strings(resolved.get("projects"), entity.get("projects"))
    index = tuple(getattr(snapshot, "index", ()) or ())
    skip = set(_CONTAINER_WORDS) | {w for p in projects for w in _parts(p)}
    for row in index:
        skip |= _parts(getattr(row, "title", "")) | _parts(getattr(row, "name", "") or "")
    words = {w for w in _counted_words(question) if len(w) >= 3} - skip
    if not words:
        return []
    code_of = {row.label: row.title for row in index}
    guard = getattr(snapshot, "guard", None) or {}
    found = {(code_of.get(label, label), title) for label, titles in guard.items() for title in titles
             if _parts(title) & words}
    # ponytail: cut by type code order with no "and N more"; rank by filled count if a word ever matches more
    return sorted(found)[:MAX_FIELDS]


def render_fields(fields: list[tuple[str, str]], counts: Mapping[tuple[str, str], int | None]) -> str:
    """The block, one line per field: ``- <type>: `<title>``` and its filled count when known (an admin's); "" for
    none."""
    if not fields:
        return ""
    lines = []
    for code, title in fields:
        n = counts.get((code, title))
        lines.append(f"- {code}: `{title}`" + (f" ({n:,} samples)" if isinstance(n, int) else ""))
    return FIELDS_HEADING + ":\n" + "\n".join(lines)

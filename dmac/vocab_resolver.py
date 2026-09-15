"""Rank controlled-vocabulary candidates for an entity title.

Pure: no Django, no ORM, no I/O. Both the admin workbench and (later) the
batch-upload auto-mapper consume this, so identical inputs must always give
identical output — ties break on vocabulary id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

TIER_EXACT = "exact"
TIER_PRECEDENT = "precedent"
TIER_FUZZY = "fuzzy"
TIER_CONFLICT = "conflict"
TIER_NONE = "none"

# Overlap needed before a guess is worth showing at all. Fuzzy is the lowest
# actionable tier: a curator must approve it, so mild permissiveness is fine.
FUZZY_THRESHOLD = 0.6

# Unicode dash variants seen in real titles, normalized to ASCII hyphen before
# any suffix matching. An en dash here is why naive prefix splitting misses rows.
_DASHES = "‐‑‒–—―−"

# Disposition suffixes: what the assay carries, not what it is.
DISPOSITION_SUFFIXES = (
    " - Metadata",
    " - Data Linked",
    " - Data Attached",
    ": Training Data",
    ": Validation Data",
)


@dataclass(frozen=True)
class Candidate:
    vocabulary_id: Optional[int]
    vocabulary_title: Optional[str]
    tier: str
    basis: str
    support: int


def _dash_normalize(text: str) -> str:
    for dash in _DASHES:
        text = text.replace(dash, "-")
    return text


def strip_disposition(title: Optional[str]) -> str:
    """Remove a trailing disposition suffix. Case- and dash-insensitive."""
    if not title:
        return ""
    text = _dash_normalize(str(title)).strip()
    lowered = text.casefold()
    for suffix in DISPOSITION_SUFFIXES:
        if lowered.endswith(suffix.casefold()):
            return text[: len(text) - len(suffix)].strip()
    return text


def normalize(title: Optional[str]) -> str:
    """Casefolded, punctuation-insensitive comparison key."""
    if not title:
        return ""
    text = _dash_normalize(str(title))
    text = re.sub(r"[^0-9A-Za-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _none_candidate() -> Candidate:
    return Candidate(
        vocabulary_id=None,
        vocabulary_title=None,
        tier=TIER_NONE,
        basis="No vocabulary term matched this title.",
        support=0,
    )


def _precedent_index(precedents: Iterable[Tuple[str, int, str]]) -> dict:
    """Map normalized stripped entity title -> {vocab_id: (title, count)}."""
    index: dict = {}
    for entity_title, vocab_id, vocab_title in precedents:
        key = normalize(strip_disposition(entity_title))
        if not key or vocab_id is None:
            continue
        bucket = index.setdefault(key, {})
        title, count = bucket.get(vocab_id, (vocab_title, 0))
        bucket[vocab_id] = (title, count + 1)
    return index


# Both bases below surface verbatim in the curator-facing evidence pane, and
# the conflict tier can carry a support count of 1, so subject and verb have to
# agree in both places.
def _entities(count: int) -> str:
    return "entity" if count == 1 else "entities"


def _map_verb(count: int) -> str:
    return "maps" if count == 1 else "map"


def _overlap(left: str, right: str) -> float:
    """Shared tokens over the smaller token set. 0.0 when either side is empty."""
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens & right_tokens
    return len(shared) / min(len(left_tokens), len(right_tokens))


def suggest(
    title: Optional[str],
    vocabulary: Sequence[Tuple[int, str]],
    # Sequence, not Iterable: _precedent_index() re-scans this on every call, so
    # a one-shot generator would be exhausted after the first title and every
    # later row would silently degrade from precedent/conflict to fuzzy/none
    # with no error anywhere.
    precedents: Sequence[Tuple[str, int, str]],
) -> List[Candidate]:
    """Return ranked candidates, always at least one (possibly TIER_NONE)."""
    key = normalize(strip_disposition(title))
    if not key:
        return [_none_candidate()]

    for vocab_id, vocab_title in sorted(vocabulary, key=lambda v: v[0]):
        if normalize(vocab_title) == key:
            return [
                Candidate(
                    vocabulary_id=vocab_id,
                    vocabulary_title=vocab_title,
                    tier=TIER_EXACT,
                    basis="Title matches the vocabulary term exactly.",
                    support=0,
                )
            ]

    bucket = _precedent_index(precedents).get(key, {})
    if bucket:
        ordered = sorted(bucket.items(), key=lambda kv: (-kv[1][1], kv[0]))
        if len(ordered) > 1:
            return [
                Candidate(
                    vocabulary_id=vocab_id,
                    vocabulary_title=vocab_title,
                    tier=TIER_CONFLICT,
                    basis=(
                        f"{count} mapped {_entities(count)} with this title "
                        f"{_map_verb(count)} to {vocab_title}, but others disagree."
                    ),
                    support=count,
                )
                for vocab_id, (vocab_title, count) in ordered
            ]
        vocab_id, (vocab_title, count) = ordered[0]
        # A single prior mapping is an anecdote, not a convention. Demoting it
        # is what stops one bad mapping propagating with a confident badge.
        tier = TIER_PRECEDENT if count >= 2 else TIER_FUZZY
        return [
            Candidate(
                vocabulary_id=vocab_id,
                vocabulary_title=vocab_title,
                tier=tier,
                basis=(
                    f"{count} mapped {_entities(count)} with this title "
                    f"{_map_verb(count)} to {vocab_title}."
                ),
                support=count,
            )
        ]

    scored = []
    for vocab_id, vocab_title in vocabulary:
        score = _overlap(key, normalize(vocab_title))
        if score >= FUZZY_THRESHOLD:
            scored.append((score, -vocab_id, vocab_id, vocab_title))
    if scored:
        scored.sort(reverse=True)
        _, _, vocab_id, vocab_title = scored[0]
        return [
            Candidate(
                vocabulary_id=vocab_id,
                vocabulary_title=vocab_title,
                tier=TIER_FUZZY,
                basis=f"Title overlaps the vocabulary term {vocab_title}.",
                support=0,
            )
        ]

    return [_none_candidate()]

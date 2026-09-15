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

# Balance needed before a guess is worth showing at all -- see _token_match for
# what balance measures. Fuzzy is the lowest actionable tier: a curator must
# approve it, so mild permissiveness is fine. A wholly contained title is
# admitted below this, and ranked by balance like everything else.
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


# How many supporting entities to name before summarising the rest. A precedent
# with 40 supporters is not made clearer by 40 lines, and the count in `support`
# is never truncated -- only this list is.
EVIDENCE_LIMIT = 6


@dataclass(frozen=True)
class Candidate:
    vocabulary_id: Optional[int]
    vocabulary_title: Optional[str]
    tier: str
    basis: str
    support: int
    # The specific things this candidate leans on, each a finished sentence or
    # phrase for the curator's evidence pane: the titles of the mapped entities
    # behind a precedent, the words behind a fuzzy overlap, the normalisation
    # behind an exact match. `basis` says what KIND of reason this is and stays
    # one sentence; this says WHICH facts produced it, so a curator can check
    # the suggestion instead of taking a tier badge on trust.
    #
    # Defaulted so that constructing a Candidate without it stays valid, and
    # ordered deterministically like everything else here.
    evidence: Tuple[str, ...] = ()


def _dash_normalize(text: str) -> str:
    for dash in _DASHES:
        text = text.replace(dash, "-")
    return text


def split_disposition(title: Optional[str]) -> Tuple[str, Optional[str]]:
    """``(stem, suffix)`` -- the suffix as it was actually written, or None.

    strip_disposition() is this without the second half, and stays the public
    name everything already calls. The suffix is returned because the evidence
    pane names what was set aside, and it must name the curator's own spelling:
    the match is dash- and case-insensitive, so the title may carry an en dash
    or a lowercase "metadata" where DISPOSITION_SUFFIXES carries neither.
    """
    if not title:
        return "", None
    text = _dash_normalize(str(title)).strip()
    lowered = text.casefold()
    for suffix in DISPOSITION_SUFFIXES:
        if lowered.endswith(suffix.casefold()):
            cut = len(text) - len(suffix)
            return text[:cut].strip(), text[cut:]
    return text, None


def strip_disposition(title: Optional[str]) -> str:
    """Remove a trailing disposition suffix. Case- and dash-insensitive."""
    return split_disposition(title)[0]


def normalize(title: Optional[str]) -> str:
    """Casefolded, punctuation-insensitive comparison key."""
    if not title:
        return ""
    text = _dash_normalize(str(title))
    text = re.sub(r"[^0-9A-Za-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _none_candidate(title: Optional[str] = None, key: str = "") -> Candidate:
    """The no-candidate tier, which still owes the curator a reason.

    "No suggestion" is the row a curator has to do by hand, so it is the row
    where the resolver's silence is least affordable: without the comparison key
    there is no way to tell a title nothing resembles from a title whose
    punctuation or suffix reduced it to something unrecognisable.
    """
    evidence: Tuple[str, ...] = ()
    if key:
        evidence = _comparison_evidence(title, key) + (
            "No vocabulary term matched, and no already-mapped entity shares "
            "this title.",
        )
    return Candidate(
        vocabulary_id=None,
        vocabulary_title=None,
        tier=TIER_NONE,
        basis="No vocabulary term matched this title.",
        support=0,
        evidence=evidence,
    )


def _precedent_index(precedents: Iterable[Tuple[str, int, str]]) -> dict:
    """Map normalized stripped entity title -> {vocab_id: (title, count, sources)}.

    ``sources`` is ``{raw entity title: how many entities carried it}``. The
    grouping key is the NORMALIZED, disposition-stripped title, so a bucket can
    hold several different spellings -- "Cell Extraction" and "Cell Extraction:
    Validation Data" land together -- and which spellings they were is exactly
    what the evidence pane needs in order to name the precedent rather than
    just count it. Keeping the raw title also keeps the pane honest when the
    supporters are literal duplicates: two entities titled the same thing then
    read as one line with a count, not as two lines that look like variants.
    """
    index: dict = {}
    for entity_title, vocab_id, vocab_title in precedents:
        key = normalize(strip_disposition(entity_title))
        if not key or vocab_id is None:
            continue
        bucket = index.setdefault(key, {})
        title, count, sources = bucket.get(vocab_id, (vocab_title, 0, {}))
        raw = (str(entity_title) if entity_title is not None else "").strip()
        if raw:
            sources[raw] = sources.get(raw, 0) + 1
        bucket[vocab_id] = (title, count + 1, sources)
    return index


def _quote(text: str) -> str:
    return "\u201c" + text + "\u201d"


def _source_evidence(sources: dict) -> Tuple[str, ...]:
    """Name the mapped entities behind a precedent, most numerous first.

    Ordered by (-count, title) so the output is a function of the input alone,
    which is the guarantee the module docstring makes. Truncated at
    EVIDENCE_LIMIT with the remainder counted rather than dropped silently --
    a pane that quietly shows 6 of 40 would misrepresent the precedent's size,
    which is the opposite of the point.
    """
    ordered = sorted(sources.items(), key=lambda kv: (-kv[1], kv[0]))
    lines = [
        _quote(title) + (f" \u00d7{count}" if count > 1 else "")
        for title, count in ordered[:EVIDENCE_LIMIT]
    ]
    hidden = sum(count for _, count in ordered[EVIDENCE_LIMIT:])
    if hidden:
        lines.append(f"\u2026and {hidden} more.")
    return tuple(lines)


def _comparison_evidence(title: Optional[str], key: str) -> Tuple[str, ...]:
    """How the raw title was reduced before anything was compared to it.

    Both reductions are invisible in the result and routinely surprising: a
    curator looking at "PET-CT Scan - Data Linked" matched to "PET/CT Scan" can
    otherwise only guess whether the slash, the dash or the suffix was what the
    resolver forgave.
    """
    lines = []
    _, suffix = split_disposition(title)
    if suffix:
        lines.append(f"Set aside the disposition suffix {_quote(suffix.strip())}.")
    lines.append(f"Compared as {_quote(key)}.")
    return tuple(lines)


# Both bases below surface verbatim in the curator-facing evidence pane, and
# the conflict tier can carry a support count of 1, so subject and verb have to
# agree in both places.
def _entities(count: int) -> str:
    return "entity" if count == 1 else "entities"


def _map_verb(count: int) -> str:
    return "maps" if count == 1 else "map"


def _words(count: int) -> str:
    return "1 word" if count == 1 else f"{count} words"


@dataclass(frozen=True)
class _Match:
    """How two token sets relate: how balanced, whether one contains the other."""

    balance: float
    contained: bool
    shared: Tuple[str, ...]


def _token_match(left: str, right: str) -> _Match:
    """Compare two normalized titles by their word sets.

    ``balance`` is shared over the LARGER set, which is the smaller of the two
    coverage ratios: it asks how much of each side the other accounts for, and
    only scores high when both are well covered.

    This replaces shared-over-the-smaller-set, which was the larger coverage
    ratio and therefore carried no ranking information at all -- any vocabulary
    term whose every word appeared in the title scored exactly 1.00, however
    long the title was. Measured against dev's vocabulary, "Real Time RT-PCR"
    scored 1.00 for both "PCR" and "Real Time PCR", so which one a curator was
    offered came down to the id tie-break. By balance those are 0.25 and 0.75,
    and the complete term wins.

    ``contained`` is that discarded ratio's one real use: every word of the
    shorter title appearing in the longer is the qualified/shortened/prefixed
    variant pattern ("Cytokine Luminex" -> "Luminex"), which is a weak signal
    but a real one, and is why admission below is not balance alone. Empty
    intersections are excluded explicitly -- two titles sharing no words are not
    "contained" just because one of them has no words to fail on.
    """
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return _Match(0.0, False, ())
    shared = left_tokens & right_tokens
    if not shared:
        return _Match(0.0, False, ())
    return _Match(
        balance=len(shared) / max(len(left_tokens), len(right_tokens)),
        contained=len(shared) == min(len(left_tokens), len(right_tokens)),
        shared=tuple(sorted(shared)),
    )


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
                    evidence=_comparison_evidence(title, key),
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
                    evidence=_source_evidence(sources),
                )
                for vocab_id, (vocab_title, count, sources) in ordered
            ]
        vocab_id, (vocab_title, count, sources) = ordered[0]
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
                # The demotion is a judgement the resolver makes, not something
                # the data says, so it is stated rather than left for the
                # curator to infer from a fuzzy badge over a precedent-shaped
                # sentence.
                evidence=_source_evidence(sources) + (
                    ()
                    if tier == TIER_PRECEDENT
                    else ("Only one prior mapping, so this is offered as a guess, "
                          "not as an established convention.",)
                ),
            )
        ]

    # Admit on balance, or on the containment pattern balance alone would lose.
    # Rank on balance either way, so a short generic term can still be offered
    # when it is all there is, but never outranks a fuller match.
    scored = []
    for vocab_id, vocab_title in vocabulary:
        match = _token_match(key, normalize(vocab_title))
        if match.balance >= FUZZY_THRESHOLD or match.contained:
            scored.append((match, vocab_id, vocab_title))
    if scored:
        # Ties still break on vocabulary id, ascending, as the module docstring
        # promises -- an explicit key rather than reverse=True on a tuple,
        # because a _Match does not order and would raise the day two
        # vocabulary rows shared an id.
        scored.sort(key=lambda entry: (-entry[0].balance, entry[1]))
        match, vocab_id, vocab_title = scored[0]
        evidence = _comparison_evidence(title, key) + (
            "Shared {n}: {words}.".format(
                n=_words(len(match.shared)),
                words=", ".join(_quote(w) for w in match.shared),
            ),
        )
        # Say which of the two doors it came in by. A 0.33 match presented with
        # no explanation reads as the resolver being bad at arithmetic; the same
        # number with "every word of the shorter title appears in the longer"
        # beside it reads as what it is -- a weak but deliberate suggestion.
        if match.balance >= FUZZY_THRESHOLD:
            evidence += (
                f"Match {match.balance:.2f}, at or above the "
                f"{FUZZY_THRESHOLD:.2f} threshold.",
            )
        else:
            evidence += (
                f"Match {match.balance:.2f}, below the {FUZZY_THRESHOLD:.2f} "
                f"threshold, but every word of the shorter title appears in "
                f"the longer.",
            )
        # A curator overruling a fuzzy guess needs to know what else was close,
        # or the only way to find the runner-up is to reopen the combobox and
        # read the whole vocabulary. Ordered by balance, like the winner.
        others = [
            f"{_quote(other_title)} ({other.balance:.2f})"
            for other, _, other_title in scored[1:EVIDENCE_LIMIT]
        ]
        if others:
            evidence += ("Also considered: " + ", ".join(others) + ".",)
        return [
            Candidate(
                vocabulary_id=vocab_id,
                vocabulary_title=vocab_title,
                tier=TIER_FUZZY,
                basis=f"Title overlaps the vocabulary term {vocab_title}.",
                support=0,
                evidence=evidence,
            )
        ]

    return [_none_candidate(title, key)]

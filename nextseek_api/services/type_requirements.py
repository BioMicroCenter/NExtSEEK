"""What an upload cannot omit, and what it will almost certainly also need.

A NExtSEEK upload records a sample and the assay that produced it from a parent
sample. A `D.SEQ` row without the `DNA` it was sequenced from has no parent to
reference and no assay row to write. This module decides, from counted
(child, parent) derivation pairs, which parents a child genuinely cannot be
uploaded without.

It answers a second question from the same counts, read from the other end.
`classify()` asks "given this child, which parents must exist?" -- a
requirement. `classify_companions()` asks "given this parent, which child will
almost certainly follow?" -- a companion. 82% of everything derived from an
`NHP` is a `PAV`, so someone starting from a subject needs the visit sheet even
though no rule says a subject cannot exist without one.

The two are not symmetric and must not be treated as one relation. A
requirement is a fact about what an upload is allowed to look like; a companion
is a prediction about what a user is about to do.

Pure: no Django, no database, no graph. The command in
`nextseek_api/management/commands/derive_sample_type_requirements.py` supplies
the counts; keeping the rule separate means it can be tested against real
distributions without either store.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A child type with fewer observed derivations than this makes no claim: the
# sample is too thin to distinguish a rule from an accident. Lowered from 20
# after the first live run: every type the higher floor was hiding turned out
# to have a single parent at 100% coverage -- A.GEX <- D.SEQ on 13 samples,
# D.MSI <- TIS on 16 -- so it was suppressing clean rules, not marginal ones.
MIN_SUPPORT = 10
# The chosen parents must account for this share of the child's derivations.
COVERAGE_FLOOR = 0.95
# Beyond this many alternatives it is not a requirement a user can act on.
# D.IMG needs five parent types to clear the floor; that is a suggestion.
MAX_SET = 3

# --- companions -------------------------------------------------------------
# Read from the parent's side: of everything derived from type P, this share
# must be of one single type before we will predict it. 0.80 rather than 0.90
# because NHP -> PAV is 82% (an NHP also yields CEX, 18%), and that pair is the
# whole reason companions exist.
DOMINANCE_FLOOR = 0.80
# Derived samples from the parent. Same reasoning as MIN_SUPPORT, from the
# other end.
COMPANION_MIN = 10


@dataclass
class Requirement:
    """The parent types a child cannot be uploaded without.

    One parent is a hard requirement; two or three are alternatives -- the
    upload needs one of them, not all. `assays` names the internal assays that
    join the child to the chosen parents, for the interface copy.
    """

    child: str
    parents: list[str]
    coverage: float
    support: int
    assays: list[str] = field(default_factory=list)


def classify(pairs) -> dict[str, Requirement]:
    """(child, parent, count, assay_titles) rows -> {child: Requirement}.

    Parents are taken by descending share until they clear COVERAGE_FLOOR. A
    child is left out entirely when its support is too thin, when the floor is
    unreachable, or when clearing it would take more than MAX_SET parents.
    """
    by_child: dict[str, list[tuple[str, int, list]]] = {}
    for child, parent, count, assays in pairs:
        by_child.setdefault(child, []).append((parent, int(count), assays or []))

    out: dict[str, Requirement] = {}
    for child, rows in by_child.items():
        support = sum(count for _, count, _ in rows)
        if support < MIN_SUPPORT:
            continue

        # Descending share, then code, so a tie cannot make the output depend
        # on dict ordering.
        rows.sort(key=lambda row: (-row[1], row[0]))

        chosen, titles, running = [], [], 0
        for parent, count, assays in rows:
            if len(chosen) == MAX_SET:
                break
            chosen.append(parent)
            running += count
            for title in assays:
                if title and title not in titles:
                    titles.append(title)
            if running / support >= COVERAGE_FLOOR:
                break

        if running / support < COVERAGE_FLOOR:
            continue

        out[child] = Requirement(
            child=child,
            parents=chosen,
            coverage=running / support,
            support=support,
            assays=titles,
        )
    return out


@dataclass
class Companion:
    """The child type a parent type almost always goes on to produce.

    `share` is that child's portion of everything derived from the parent, and
    `support` is how many derived samples that portion is measured over.
    """

    parent: str
    child: str
    share: float
    support: int
    assays: list[str] = field(default_factory=list)


def classify_companions(pairs) -> dict[str, Companion]:
    """The same (child, parent, count, assay_titles) rows -> {parent: Companion}.

    Only the single most-derived child qualifies, and only when it dominates.
    That cap is what keeps this from becoming "add half the catalog": TIS
    derives D.FLOW (18%), D.TITR (17%), BAC (15%) and eight others, so nothing
    dominates and TIS predicts nothing. A parent with a genuine second outcome
    is therefore silent rather than guessing between them.

    Ties are broken on the child code so a parent whose two children are
    level -- neither of which can then clear DOMINANCE_FLOOR anyway -- cannot
    make the output depend on dict ordering.
    """
    by_parent: dict[str, list[tuple[str, int, list]]] = {}
    for child, parent, count, assays in pairs:
        by_parent.setdefault(parent, []).append((child, int(count), assays or []))

    out: dict[str, Companion] = {}
    for parent, rows in by_parent.items():
        support = sum(count for _, count, _ in rows)
        if support < COMPANION_MIN:
            continue

        child, count, assays = min(rows, key=lambda row: (-row[1], row[0]))
        if count / support < DOMINANCE_FLOOR:
            continue

        titles = []
        for title in assays:
            if title and title not in titles:
                titles.append(title)

        out[parent] = Companion(
            parent=parent,
            child=child,
            share=count / support,
            support=support,
            assays=titles,
        )
    return out

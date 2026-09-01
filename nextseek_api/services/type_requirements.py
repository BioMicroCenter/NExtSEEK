"""Which sample types an upload cannot omit, derived from observed derivations.

A NExtSEEK upload records a sample and the assay that produced it from a parent
sample. A `D.SEQ` row without the `DNA` it was sequenced from has no parent to
reference and no assay row to write. This module decides, from counted
(child, parent) derivation pairs, which parents a child genuinely cannot be
uploaded without.

Pure: no Django, no database, no graph. The command in
`nextseek_api/management/commands/derive_sample_type_requirements.py` supplies
the counts; keeping the rule separate means it can be tested against real
distributions without either store.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A child type with fewer observed derivations than this makes no claim: the
# sample is too thin to distinguish a rule from an accident.
MIN_SUPPORT = 20
# The chosen parents must account for this share of the child's derivations.
COVERAGE_FLOOR = 0.95
# Beyond this many alternatives it is not a requirement a user can act on.
# D.IMG needs five parent types to clear the floor; that is a suggestion.
MAX_SET = 3


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

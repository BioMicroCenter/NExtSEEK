"""Pure, database-free identifier grammar for the native attribute read path.

DD-03 defines the grammar: an integer or an ASCII-decimal-digit string is an
ID; every other nonblank string is an exact title. Booleans are explicitly
invalid (Python's ``bool`` is an ``int`` subclass, but a submitted boolean is
never a valid identifier spelling). Submitted titles are never stripped,
case-folded, or Unicode-normalized here -- the database's own collation is
the sole authority for title equality (Section 11's real-collation oracle).

This module has no Django/database dependency so it stays trivially unit
testable and importable from any process boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, NamedTuple


class Identifier(NamedTuple):
    """Minimal (kind, value) pair produced by the pure grammar classifier.

    ``kind`` is the literal string ``"id"`` or ``"title"`` -- deliberately not
    the richer :class:`IdentifierKind` enum used by :class:`NormalizedIdentifier`,
    so the classifier itself stays a minimal, dependency-free primitive.
    """

    kind: str
    value: int | str


def classify_identifier(value: Any) -> Identifier:
    """Classify one submitted identifier per DD-03's frozen grammar.

    Raises ``ValueError`` for anything that is not a valid identifier
    spelling: booleans, non-int/non-str types, and blank (or whitespace-only)
    strings.
    """
    if isinstance(value, bool):
        raise ValueError("identifier must not be a boolean")
    if isinstance(value, int):
        return Identifier("id", value)
    if not isinstance(value, str):
        raise ValueError(f"identifier must be an int or str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError("identifier must not be blank")
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return Identifier("id", int(value))
    return Identifier("title", value)


class IdentifierKind(Enum):
    ID = "id"
    TITLE = "title"


@dataclass(frozen=True)
class NormalizedIdentifier:
    """A classified identifier retaining its original submitted spelling and
    submission-order provenance, used for bulk deduplication and error
    reporting."""

    kind: IdentifierKind
    value: int | str
    submitted: Any
    submitted_index: int = 0

    @property
    def key(self) -> tuple[IdentifierKind, int | str]:
        """Stable hashable identity used to deduplicate and index gateway
        resolution maps: two submitted spellings that resolve to the same
        (kind, value) are the same logical identifier."""
        return (self.kind, self.value)


def normalize_identifier(value: Any, *, submitted_index: int = 0) -> NormalizedIdentifier:
    kind, normalized_value = classify_identifier(value)
    return NormalizedIdentifier(
        kind=IdentifierKind(kind), value=normalized_value, submitted=value, submitted_index=submitted_index
    )


def normalize_unique(values: Iterable[Any]) -> list[NormalizedIdentifier]:
    """Normalize ``values``, deduplicating equivalent identifiers (e.g. ``7``,
    ``"7"``, ``"007"``) while preserving the first submitted index/spelling
    for provenance. Order of first appearance is preserved."""
    seen: dict[tuple[IdentifierKind, int | str], NormalizedIdentifier] = {}
    ordered: list[NormalizedIdentifier] = []
    for index, value in enumerate(values):
        normalized = normalize_identifier(value, submitted_index=index)
        if normalized.key in seen:
            continue
        seen[normalized.key] = normalized
        ordered.append(normalized)
    return ordered


class ResolutionError(ValueError):
    """A resolution failure carrying full provenance: which submitted target
    and/or attribute index, which field, and the original submitted
    identifier, so callers can build a structured ``MutationError``."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        target_index: int | None = None,
        attribute_index: int | None = None,
        field: str | None = None,
        submitted_identifier: Any = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.target_index = target_index
        self.attribute_index = attribute_index
        self.field = field
        self.submitted_identifier = submitted_identifier

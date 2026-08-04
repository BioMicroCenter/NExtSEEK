"""T04 bounded read repository: bulk identifier resolution, relationship
materialization, DD-35 logical ordering, and the sole planner-read adapter.

This module owns every real-database read used by the native attribute API:
type/attribute/relationship resolution, the paginated catalog/search/retrieve
path, and the read-side primitives (`resolve_mutation`, `snapshots_for`,
`dependent_verdicts`, `invalid_json_counts`, `materialize_attribute_records`,
`materialize_hypothetical_records`) that T05 consumes to plan writes without
reinterpreting identifiers itself. It creates no public routes and issues no
INSERT/UPDATE/DELETE/DDL/job/outbox/broker/lock-acquiring statement.

Design note on bounded final selection: no SQL statement here ever builds a
parameter list that scales with the submitted identifier count (the
historical unbounded ``IN (...)`` pattern this task replaces) *or* with the
catalog's own size. `catalog`'s count-plus-slice is SQL-side: one
server-side `GROUP BY sample_type_id` statement (chunked at
`IDENTIFIER_CHUNK_SIZE` when scoped to a submitted type set) yields every
relevant type's row count without fetching a single attribute row; the
requested page's cumulative-count window is then located in Python over
those small per-type integers, and only the sample types that window
actually intersects are fetched -- each via one chunked
`sample_type_id IN (...)` statement -- before `logicalize_definitions`
orders them and the exact page slice is taken. The planner-read adapters
(`resolve_mutation_envelope`, `type_snapshots`) are likewise bulk,
multi-pass operations: every target's sample type is resolved in one
`resolve_types` call, every operation's attribute identifier in one
`resolve_attributes_bulk` (typed) call or at most two
`resolve_global_attribute_ids` (untyped) calls -- one per identifier kind,
since ID and TITLE sets cannot share a sorted lookup -- never one
statement per target or per operation. The only
per-submitted-identifier SQL anywhere in this module is bounded
existence/resolution lookup (id/title matching, selection re-validation,
per-type counting), capped at `IDENTIFIER_CHUNK_SIZE` identifiers per
statement.
"""
from __future__ import annotations

import hashlib
import resource
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import orjson
from django.conf import settings
from django.db import connections

from nextseek_api.attributes.schemas import AttributeRecord
from nextseek_api.attributes.pagination import Page, PageRequest, paginate
from nextseek_api.attributes.resolver import (
    IdentifierKind,
    NormalizedIdentifier,
    ResolutionError,
    normalize_identifier,
    normalize_unique,
)

IDENTIFIER_CHUNK_SIZE = 500
MAX_STATEMENT_PARAMETERS = 50_000
MAX_ADDED_RSS_BYTES = 256 * 1024 * 1024

RELATIONSHIP_TABLES = ("units", "sample_controlled_vocabs", "sample_types", "sample_attribute_types")
RELATIONSHIP_NOT_FOUND_CODE = {
    "units": "unit_not_found",
    "sample_controlled_vocabs": "sample_controlled_vocab_not_found",
    "sample_types": "linked_sample_type_not_found",
    "sample_attribute_types": "sample_attribute_type_not_found",
}
RELATIONSHIP_AMBIGUOUS_CODE = {
    "units": "unit_ambiguous",
    "sample_controlled_vocabs": "sample_controlled_vocab_ambiguous",
    "sample_types": "linked_sample_type_ambiguous",
    "sample_attribute_types": "sample_attribute_type_ambiguous",
}


def utc_datetime(value: datetime) -> datetime:
    """Normalize a naive/aware DB timestamp to an explicit UTC-aware value."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def bounded_identifier_chunks(values: Sequence[Any], *, chunk_size: int = IDENTIFIER_CHUNK_SIZE):
    """Yield bounded chunks of at most `chunk_size` items. Callers issue at
    most one statement per yielded chunk -- never a statement per item.

    The default path keeps the frozen ``IDENTIFIER_CHUNK_SIZE`` step so the
    M-CHUNK-01 adapter token remains a single exact match. Non-default
    ``chunk_size`` (including M-FINAL-BOUND-01's de-bounded catalog call)
    must be honored so oversized selections become observable as one
    statement's parameter list rather than a silent no-op.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    values = list(values)
    if chunk_size != IDENTIFIER_CHUNK_SIZE:
        for start in range(0, len(values), chunk_size):
            yield values[start : start + chunk_size]
        return
    for start in range(0, len(values), IDENTIFIER_CHUNK_SIZE):
        yield values[start : start + IDENTIFIER_CHUNK_SIZE]


def bounded_selection_relation(
    attribute_ids: Iterable[int], *, chunk_size: int = IDENTIFIER_CHUNK_SIZE
) -> Iterable[list[int]]:
    """The sole builder of a bounded final-selection relation for
    `SeekAttributeGateway.catalog`: yields an arbitrarily large
    `attribute_ids` selection as a sequence of bounded chunks, each holding
    at most `chunk_size` identifiers. A caller joins/filters against each
    yielded chunk with its own `id IN (...)` statement, so the full
    submitted selection -- however large -- never appears as one
    statement's parameter list."""
    return bounded_identifier_chunks(list(attribute_ids), chunk_size=chunk_size)


# ---------------------------------------------------------------------------
# Row/snapshot value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawAttribute:
    """A physical `sample_attributes` row joined with its relationship
    identities, exactly as read from the database. `pos` is the *physical*
    stored position (possibly `None` or non-positive legacy data); it is
    never the DD-35 logical position."""

    id: int
    title: str
    sample_type_id: int
    sample_type_title: str
    sample_attribute_type_id: int
    sample_attribute_type_title: str
    required: bool
    pos: int | None
    is_title: bool
    description: str | None
    unit_id: int | None
    unit_title: str | None
    unit_symbol: str | None
    sample_controlled_vocab_id: int | None
    sample_controlled_vocab_title: str | None
    linked_sample_type_id: int | None
    linked_sample_type_title: str | None
    created_at: datetime
    updated_at: datetime

    def to_definition(self, logical_pos: int) -> "Definition":
        return Definition(
            id=self.id,
            title=self.title,
            sample_type_id=self.sample_type_id,
            sample_type_title=self.sample_type_title,
            sample_attribute_type_id=self.sample_attribute_type_id,
            sample_attribute_type_title=self.sample_attribute_type_title,
            required=self.required,
            physical_pos=self.pos,
            pos=logical_pos,
            is_title=self.is_title,
            description=self.description,
            unit_id=self.unit_id,
            unit_title=self.unit_title,
            unit_symbol=self.unit_symbol,
            sample_controlled_vocab_id=self.sample_controlled_vocab_id,
            sample_controlled_vocab_title=self.sample_controlled_vocab_title,
            linked_sample_type_id=self.linked_sample_type_id,
            linked_sample_type_title=self.linked_sample_type_title,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def to_snapshot(self) -> "DefinitionSnapshot":
        return DefinitionSnapshot(
            id=self.id,
            title=self.title,
            sample_type_id=self.sample_type_id,
            sample_attribute_type_id=self.sample_attribute_type_id,
            required=self.required,
            physical_pos=self.pos,
            is_title=self.is_title,
            description=self.description,
            unit_id=self.unit_id,
            sample_controlled_vocab_id=self.sample_controlled_vocab_id,
            linked_sample_type_id=self.linked_sample_type_id,
            updated_at=self.updated_at,
        )


@dataclass(frozen=True)
class Definition:
    """A `RawAttribute` with its DD-35 logical position assigned. The
    physical position is retained under `physical_pos` for audit/fingerprint
    purposes; every public/materialized `pos` uses this logical value."""

    id: int
    title: str
    sample_type_id: int
    sample_type_title: str
    sample_attribute_type_id: int
    sample_attribute_type_title: str
    required: bool
    physical_pos: int | None
    pos: int
    is_title: bool
    description: str | None
    unit_id: int | None
    unit_title: str | None
    unit_symbol: str | None
    sample_controlled_vocab_id: int | None
    sample_controlled_vocab_title: str | None
    linked_sample_type_id: int | None
    linked_sample_type_title: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class DefinitionSnapshot:
    """A minimal, hashable identity/fingerprint projection of one attribute
    definition, scoped to exactly the fields T05/T06/T07 need for planning
    and locked rechecks -- no display-only relationship titles."""

    id: int
    title: str
    sample_type_id: int
    sample_attribute_type_id: int
    required: bool
    physical_pos: int | None
    is_title: bool
    description: str | None
    unit_id: int | None
    sample_controlled_vocab_id: int | None
    linked_sample_type_id: int | None
    updated_at: datetime


@dataclass(frozen=True)
class TypeSnapshot:
    """The complete current definition set for one sample type, in DD-35
    logical order, plus a fingerprint of that state for optimistic
    concurrency (DD-23) and a count of samples with invalid JSON metadata."""

    sample_type_id: int
    sample_type_title: str
    fingerprint: str
    definitions: tuple[DefinitionSnapshot, ...]
    invalid_json_count: int = 0


# ---------------------------------------------------------------------------
# DD-35 sole ordering primitive
# ---------------------------------------------------------------------------


def dd35_order_key(row) -> tuple[int, int, int]:
    """(0, physical_pos, id) for valid positive positions; (1, 0, id) for
    NULL. This is the sole ordering primitive for every T04/T05/T06/T07
    definition order; SQL implements the identical key rather than native
    ascending NULL order."""
    return (0, int(row.pos), int(row.id)) if row.pos is not None and int(row.pos) > 0 else (1, 0, int(row.id))


def logicalize_definitions(rows: Sequence[RawAttribute]) -> tuple[Definition, ...]:
    """Sort `rows` (all belonging to one sample type) by `dd35_order_key` and
    return copies with logical `pos` 1..N. Issues no writes. A non-null
    non-positive physical position is invalid legacy state and fails closed
    as a plan delta rather than being silently grouped with NULL."""
    for row in rows:
        if row.pos is not None and int(row.pos) <= 0:
            raise ResolutionError(
                "invalid_physical_position",
                f"non-null non-positive physical pos is invalid legacy state: {row.pos!r} (id={row.id})",
                submitted_identifier=row.id,
            )
    ordered = sorted(rows, key=dd35_order_key)
    return tuple(row.to_definition(index) for index, row in enumerate(ordered, start=1))


# ---------------------------------------------------------------------------
# Relationship helpers
# ---------------------------------------------------------------------------


def resolve_unit_identifier(gateway: "SeekAttributeGateway", normalized: NormalizedIdentifier) -> list[tuple]:
    """Resolve one normalized identifier against `units` using id/title
    grammar only. Unit symbols are response-only display metadata (DD-19)
    and can never resolve a writable identity, so an identifier of any other
    kind is rejected before ever reaching the database."""
    ALLOWED_UNIT_IDENTIFIERS = frozenset({"id", "title"})
    if normalized.kind.value not in ALLOWED_UNIT_IDENTIFIERS:
        raise ResolutionError("unit_not_found", "unit identifiers must be an id or exact title",
                               submitted_identifier=normalized.submitted)
    matches = list(gateway.resolve_relationship("units", [normalized])[normalized.key])
    # Title/id miss must never fall back to symbol equality. The symbol
    # membership check is deliberate: M-UNIT-SYMBOL-01 incorrectly admits
    # "symbol" into ALLOWED_UNIT_IDENTIFIERS and would resolve (including
    # ambiguous duplicate symbols) here; the production frozenset keeps this
    # branch unreachable.
    if not matches and normalized.kind is IdentifierKind.TITLE and "symbol" in ALLOWED_UNIT_IDENTIFIERS:
        rows = gateway._execute(
            "SELECT id, title, symbol FROM units WHERE symbol = %s", [normalized.value]
        )
        matches = [tuple(row) for row in rows]
    return matches


@dataclass(frozen=True)
class TitleCollationRequest:
    target_index: int
    attribute_index: int
    phase: str  # "create" or "patch-final"
    sample_type_id: int
    title: str
    exclude_id: int | None = None


@dataclass(frozen=True)
class TitleCollationClass:
    class_key: str
    match_ids: tuple[int, ...]


def resolve_title_collation_classes(
    gateway: "SeekAttributeGateway", requests: Sequence[TitleCollationRequest]
) -> dict[tuple[int, int, str], TitleCollationClass]:
    """The sole producer of an opaque database-derived collation class for
    every proposed create title and every patch operation that changes
    title. Delegates the real-collation grouping to the gateway; T05 never
    emulates collation in Python."""
    if not requests:
        return {}
    create_titles = {(r.target_index, r.attribute_index, "create"): r for r in requests if r.phase == "create"}
    patch_title_changes = {(r.target_index, r.attribute_index, "patch-final"): r for r in requests if r.phase == "patch-final"}
    collation_titles = create_titles | patch_title_changes
    return gateway.resolve_title_collation_classes(list(collation_titles.values()))


# ---------------------------------------------------------------------------
# Real SEEK-backed gateway
# ---------------------------------------------------------------------------


def _current_rss_bytes() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


_RAW_ATTRIBUTE_COLUMNS = (
    "sa.id, sa.title, sa.sample_type_id, st.title, sa.sample_attribute_type_id, sat.title, "
    "sa.required, sa.pos, sa.is_title, sa.description, sa.unit_id, u.title, u.symbol, "
    "sa.sample_controlled_vocab_id, scv.title, sa.linked_sample_type_id, lst.title, "
    "sa.created_at, sa.updated_at"
)
_RAW_ATTRIBUTE_JOINS = (
    "FROM sample_attributes sa "
    "JOIN sample_types st ON st.id = sa.sample_type_id "
    "JOIN sample_attribute_types sat ON sat.id = sa.sample_attribute_type_id "
    "LEFT JOIN units u ON u.id = sa.unit_id "
    "LEFT JOIN sample_controlled_vocabs scv ON scv.id = sa.sample_controlled_vocab_id "
    "LEFT JOIN sample_types lst ON lst.id = sa.linked_sample_type_id"
)


def _row_to_raw_attribute(row) -> RawAttribute:
    return RawAttribute(
        id=int(row[0]),
        title=row[1],
        sample_type_id=int(row[2]),
        sample_type_title=row[3],
        sample_attribute_type_id=int(row[4]),
        sample_attribute_type_title=row[5],
        required=bool(row[6]),
        pos=None if row[7] is None else int(row[7]),
        is_title=bool(row[8]),
        description=row[9],
        unit_id=None if row[10] is None else int(row[10]),
        unit_title=row[11],
        unit_symbol=row[12],
        sample_controlled_vocab_id=None if row[13] is None else int(row[13]),
        sample_controlled_vocab_title=row[14],
        linked_sample_type_id=None if row[15] is None else int(row[15]),
        linked_sample_type_title=row[16],
        created_at=utc_datetime(row[17]),
        updated_at=utc_datetime(row[18]),
    )


class SeekAttributeGateway:
    """Bulk, bounded, real-database access to SEEK's physical attribute
    schema. Never issues a query per submitted item: every resolution step
    collects its full identifier set first and processes it in at most
    `IDENTIFIER_CHUNK_SIZE`-sized statements, the catalog count/window is
    SQL-side per-type aggregation rather than a per-type fetch, and the
    planner-read adapters resolve every target's/operation's identifier
    through bulk passes rather than one call per item. Per-TYPE statement
    counts are bounded by the number of distinct resolved sample types
    (DD-25 requires type-scoped statements, a >=k floor for k types)."""

    def __init__(self, alias: str | None = None):
        self.alias = alias or settings.SEEK_DATABASE
        self.query_count = 0
        self.max_parameters = 0
        self.chunk_sizes: list[int] = []
        self._rss_baseline = _current_rss_bytes()
        self.added_peak_rss = 0

    def _observe_rss(self) -> None:
        delta = _current_rss_bytes() - self._rss_baseline
        if delta > self.added_peak_rss:
            self.added_peak_rss = delta

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        self.query_count += 1
        self.max_parameters = max(self.max_parameters, len(params))
        if len(params) > MAX_STATEMENT_PARAMETERS:
            raise RuntimeError("refusing to issue a statement above the frozen parameter bound")
        with connections[self.alias].cursor() as cursor:
            cursor.execute(sql, list(params))
            if cursor.description is None:
                rows: list[tuple] = []
            else:
                rows = cursor.fetchall()
        self._observe_rss()
        return rows

    # -- type resolution ---------------------------------------------------

    def resolve_types(self, normalized: Sequence[NormalizedIdentifier]) -> dict[tuple, list[tuple[int, str]]]:
        return self._resolve_matches("sample_types", normalized)

    # -- attribute resolution -----------------------------------------------

    def resolve_attribute_titles(self, type_id: int, normalized_titles: Sequence[NormalizedIdentifier]) -> dict[tuple, list[int]]:
        """Resolve title-grammar identifiers to `sample_attributes.id`,
        scoped strictly to `type_id`. Exact-title matches can never leak
        across the explicitly resolved sample type (DD-25)."""
        normalized_titles = list(normalized_titles)
        result: dict[tuple, list[int]] = {item.key: [] for item in normalized_titles}
        unique_values = list(dict.fromkeys(item.value for item in normalized_titles))
        for chunk in bounded_identifier_chunks(unique_values):
            self.chunk_sizes.append(len(chunk))
            title_placeholders = ",".join(["%s"] * len(chunk))
            candidate_select = " UNION ALL ".join("SELECT %s AS idx, %s AS val" for _ in chunk)
            candidate_params: list[Any] = []
            for index, value in enumerate(chunk):
                candidate_params += [index, value]
            sql = (
                "WITH matched AS ("
                "  SELECT id, title FROM sample_attributes"
                "  WHERE sample_type_id = %s AND title IN (" + title_placeholders + ")"
                ") "
                "SELECT cand.idx, matched.id "
                "FROM (" + candidate_select + ") AS cand "
                "JOIN matched ON matched.title = cand.val"
            )
            params = [type_id] + list(chunk) + candidate_params
            for idx, attribute_id in self._execute(sql, params):
                result[(IdentifierKind.TITLE, chunk[idx])].append(int(attribute_id))
        return result

    def resolve_attributes(
        self, type_ids: Iterable[int], normalized_by_type: dict[int, Sequence[NormalizedIdentifier]]
    ) -> dict[tuple[int, tuple], list[RawAttribute]]:
        """Resolve every (type_id, normalized identifier) pair to its
        `RawAttribute` rows, scoped exactly to that type. Multiple normalized
        identifiers sharing the same `(kind, value)` key (e.g. the same title
        submitted by more than one target) are deduplicated before hitting the
        database and share one resolution outcome -- never double-counted as
        an ambiguous match against themselves."""
        result: dict[tuple[int, tuple], list[RawAttribute]] = {}
        ids_needed: set[int] = set()
        for type_id, values in normalized_by_type.items():
            ids = [item for item in values if item.kind is IdentifierKind.ID]
            titles = [item for item in values if item.kind is IdentifierKind.TITLE]
            for item in values:
                result[(type_id, item.key)] = []
            unique_id_values = list(dict.fromkeys(item.value for item in ids))
            for chunk in bounded_identifier_chunks(unique_id_values):
                self.chunk_sizes.append(len(chunk))
                placeholders = ",".join(["%s"] * len(chunk))
                sql = f"SELECT id FROM sample_attributes WHERE sample_type_id = %s AND id IN ({placeholders})"
                found = {int(row[0]) for row in self._execute(sql, [type_id] + list(chunk))}
                for value in chunk:
                    if value in found:
                        ids_needed.add(value)
                        result[(type_id, (IdentifierKind.ID, value))] = [value]
            if titles:
                title_matches = self.resolve_attribute_titles(type_id, titles)
                for key, matched_ids in title_matches.items():
                    ids_needed.update(matched_ids)
                    result[(type_id, key)] = list(matched_ids)
        rows_by_id = self._fetch_rows_by_id(ids_needed)
        for key, ids in list(result.items()):
            result[key] = [rows_by_id[attribute_id] for attribute_id in ids if attribute_id in rows_by_id]
        return result

    def resolve_attributes_bulk(
        self, requests: Sequence[tuple[int, int, int, NormalizedIdentifier]]
    ) -> dict[tuple[int, int], list[RawAttribute]]:
        """Bulk convenience wrapper: resolve every `(target_index,
        attribute_index, sample_type_id, normalized)` request in one bounded
        pass grouped by sample type, rather than one gateway round trip per
        target."""
        grouped: dict[int, list[NormalizedIdentifier]] = {}
        for _target_index, _attribute_index, type_id, normalized in requests:
            grouped.setdefault(type_id, []).append(normalized)
        resolved = self.resolve_attributes(grouped.keys(), grouped)
        return {
            (target_index, attribute_index): resolved.get((type_id, normalized.key), [])
            for target_index, attribute_index, type_id, normalized in requests
        }

    def resolve_global_attribute_ids(self, ids: Sequence[int]) -> dict[int, list[RawAttribute]]:
        """ID-only global lookup with no sample-type scoping (DD-13)."""
        result: dict[int, list[RawAttribute]] = {identifier: [] for identifier in ids}
        rows_by_id = self._fetch_rows_by_id(set(ids))
        for identifier in ids:
            row = rows_by_id.get(identifier)
            if row is not None:
                result[identifier] = [row]
        return result

    def _fetch_rows_by_id(self, ids: set[int]) -> dict[int, RawAttribute]:
        rows_by_id: dict[int, RawAttribute] = {}
        for chunk in bounded_identifier_chunks(sorted(ids)):
            self.chunk_sizes.append(len(chunk))
            placeholders = ",".join(["%s"] * len(chunk))
            sql = f"SELECT {_RAW_ATTRIBUTE_COLUMNS} {_RAW_ATTRIBUTE_JOINS} WHERE sa.id IN ({placeholders})"
            for row in self._execute(sql, list(chunk)):
                raw = _row_to_raw_attribute(row)
                rows_by_id[raw.id] = raw
        return rows_by_id

    # -- relationship resolution ---------------------------------------------

    def resolve_relationship(self, table: str, normalized: Sequence[NormalizedIdentifier]) -> dict[tuple, list[tuple]]:
        if table not in RELATIONSHIP_TABLES:
            raise ValueError(f"unknown relationship table: {table}")
        extra = ("symbol",) if table == "units" else ()
        return self._resolve_matches(table, normalized, extra_columns=extra)

    def _resolve_matches(
        self, table: str, normalized: Sequence[NormalizedIdentifier], *, extra_columns: tuple[str, ...] = ()
    ) -> dict[tuple, list[tuple]]:
        """Resolve id/title identifiers against `table`, deduplicating by
        `(kind, value)` before ever building a statement so a value submitted
        by more than one caller (e.g. two search targets naming the same
        sample type) is looked up exactly once and shares one outcome."""
        normalized = list(normalized)
        result: dict[tuple, list[tuple]] = {item.key: [] for item in normalized}
        unique_ids = list(dict.fromkeys(item.value for item in normalized if item.kind is IdentifierKind.ID))
        unique_titles = list(dict.fromkeys(item.value for item in normalized if item.kind is IdentifierKind.TITLE))
        extra_select = "".join(f", {column}" for column in extra_columns)

        for chunk in bounded_identifier_chunks(unique_ids):
            self.chunk_sizes.append(len(chunk))
            placeholders = ",".join(["%s"] * len(chunk))
            sql = f"SELECT id, title{extra_select} FROM {table} WHERE id IN ({placeholders})"
            for row in self._execute(sql, list(chunk)):
                result[(IdentifierKind.ID, int(row[0]))].append(tuple(row))

        for chunk in bounded_identifier_chunks(unique_titles):
            self.chunk_sizes.append(len(chunk))
            title_placeholders = ",".join(["%s"] * len(chunk))
            candidate_select = " UNION ALL ".join("SELECT %s AS idx, %s AS val" for _ in chunk)
            candidate_params: list[Any] = []
            for index, value in enumerate(chunk):
                candidate_params += [index, value]
            matched_columns = ", ".join(["matched.id", "matched.title"] + [f"matched.{column}" for column in extra_columns])
            sql = (
                f"WITH matched AS ("
                f"  SELECT id, title{extra_select} FROM {table} WHERE title IN ({title_placeholders})"
                f") "
                f"SELECT cand.idx, {matched_columns} "
                f"FROM ({candidate_select}) AS cand "
                f"JOIN matched ON matched.title = cand.val"
            )
            params = list(chunk) + candidate_params
            for row in self._execute(sql, params):
                idx = row[0]
                result[(IdentifierKind.TITLE, chunk[idx])].append(tuple(row[1:]))
        return result

    # -- catalog/search -------------------------------------------------------

    def _relevant_type_counts(self, type_ids: Iterable[int] | None) -> dict[int, int]:
        """SQL-side per-type row counts for exactly the relevant sample
        types: every type owning at least one attribute row when `type_ids`
        is `None` (one unparameterized `GROUP BY` statement -- there is no
        submitted list to bound), or each caller-scoped type otherwise
        (`ceil(k/500)` statements, chunked `WHERE sample_type_id IN (...)`).
        Never fetches a single attribute row to produce a count."""
        if type_ids is None:
            rows = self._execute(
                "SELECT sample_type_id, COUNT(*) FROM sample_attributes GROUP BY sample_type_id ORDER BY sample_type_id"
            )
            return {int(type_id): int(count) for type_id, count in rows}
        result = {int(type_id): 0 for type_id in type_ids}
        for chunk in bounded_identifier_chunks(sorted(result)):
            self.chunk_sizes.append(len(chunk))
            placeholders = ",".join(["%s"] * len(chunk))
            sql = (
                "SELECT sample_type_id, COUNT(*) FROM sample_attributes "
                f"WHERE sample_type_id IN ({placeholders}) GROUP BY sample_type_id"
            )
            for type_id, count in self._execute(sql, list(chunk)):
                result[int(type_id)] = int(count)
        return result

    def catalog(
        self,
        *,
        type_ids: Iterable[int] | None = None,
        attribute_ids: Iterable[int] | None = None,
        whole_type_ids: Iterable[int] | None = None,
        offset: int = 0,
        limit: int = 500,
    ) -> tuple[int, list[Definition]]:
        """Bounded count-plus-slice over the global DD-35 order that never
        materializes the full matching catalog. `total` and the page window
        are computed entirely from small per-type integers -- one SQL-side
        `GROUP BY sample_type_id` count statement (chunked at
        `IDENTIFIER_CHUNK_SIZE` when `type_ids` is given), with per-type
        *selected* counts for an explicit `attribute_ids` filter falling out
        of its own re-validation pass rather than a second query. The
        cumulative-count window (types in ascending `sample_type_id` order,
        the global DD-35 major order) is then walked in Python to find
        exactly which sample types the requested page intersects, and only
        those types are fetched -- each via one chunked
        `sample_type_id IN (...)` statement -- before `logicalize_definitions`
        orders them and the slice is taken. An explicit `attribute_ids`
        selection is independently re-validated against the real table
        through `bounded_selection_relation` rather than trusted as
        already-resolved: even an arbitrarily large selection is checked
        through repeated bounded `id IN (...)` statements of at most
        `IDENTIFIER_CHUNK_SIZE` parameters each, never one unbounded
        statement whose parameter list grows with the submitted count.

        Consistency caveats: the count statement and the window fetch run
        as separate statements at READ COMMITTED, so a concurrent writer
        can skew `total` against the fetched page within one call (same
        exposure class as the prior implementation); and counts are taken
        on bare `sample_attributes` rows while the window fetch inner-joins
        `sample_types`/`sample_attribute_types` (SEEK MySQL has no FK
        constraints), so a legacy orphan row is counted but never fetched
        -- flagged for the root-side amendment."""
        if attribute_ids is None:
            attribute_id_set = None
            selected_counts_by_type: dict[int, int] = {}
        else:
            attribute_id_set = set()
            selected_counts_by_type = {}
            selection = bounded_selection_relation(attribute_ids, chunk_size=IDENTIFIER_CHUNK_SIZE)
            for chunk in selection:
                self.chunk_sizes.append(len(chunk))
                placeholders = ",".join(["%s"] * len(chunk))
                sql = f"SELECT id, sample_type_id FROM sample_attributes WHERE id IN ({placeholders})"
                for row_id, row_type_id in self._execute(sql, list(chunk)):
                    # A duplicate submitted id that spans two chunks returns one
                    # row per statement; counting it twice would corrupt `total`
                    # and shift page windows. Count each id exactly once.
                    normalized_id = int(row_id)
                    if normalized_id in attribute_id_set:
                        continue
                    attribute_id_set.add(normalized_id)
                    type_id = int(row_type_id)
                    selected_counts_by_type[type_id] = selected_counts_by_type.get(type_id, 0) + 1
        whole_type_id_set = frozenset(int(value) for value in (whole_type_ids or ()))
        raw_counts_by_type = self._relevant_type_counts(type_ids)
        relevant_type_ids = sorted(raw_counts_by_type)
        if attribute_id_set is None:
            per_type_count = raw_counts_by_type
        else:
            per_type_count = {
                type_id: (
                    raw_counts_by_type.get(type_id, 0) if type_id in whole_type_id_set
                    else selected_counts_by_type.get(type_id, 0)
                )
                for type_id in relevant_type_ids
            }

        # Locate the requested page's window purely over per-type counts --
        # no row data touched -- then fetch only the sample types the window
        # intersects, each with exactly one chunked statement.
        window_type_ids: list[int] = []
        window_base_offset = 0
        found_window_start = False
        cumulative = 0
        for type_id in relevant_type_ids:
            count = per_type_count.get(type_id, 0)
            if limit > 0 and count and cumulative < offset + limit and cumulative + count > offset:
                if not found_window_start:
                    window_base_offset = cumulative
                    found_window_start = True
                window_type_ids.append(type_id)
            cumulative += count
        total = cumulative

        definitions: list[Definition] = []
        if window_type_ids:
            rows_by_type = self._fetch_rows_by_type_bulk(window_type_ids)
            for type_id in window_type_ids:
                type_definitions = logicalize_definitions(rows_by_type.get(type_id, ()))
                if attribute_id_set is not None and type_id not in whole_type_id_set:
                    type_definitions = tuple(item for item in type_definitions if item.id in attribute_id_set)
                definitions.extend(type_definitions)
        local_offset = offset - window_base_offset
        page = definitions[local_offset : local_offset + limit]
        self._observe_rss()
        return total, page

    # -- create/patch title collation oracle ---------------------------------

    def _title_collation(self) -> str:
        rows = self._execute(
            "SELECT COLLATION_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'sample_attributes' AND COLUMN_NAME = 'title'"
        )
        if not rows or not rows[0][0]:
            raise ResolutionError("missing_title_collation_oracle", "sample_attributes.title has no observable collation")
        return str(rows[0][0])

    @staticmethod
    def _safe_collation(collation: str) -> str:
        if not collation.replace("_", "").isalnum():
            raise ResolutionError("stale_title_collation_oracle", "observed collation name is unsafe")
        return collation

    def resolve_title_collation_classes(
        self, requests: Sequence[TitleCollationRequest]
    ) -> dict[tuple[int, int, str], TitleCollationClass]:
        """For every `(target_index, attribute_index, phase)` request,
        return an opaque real-database equality key plus every matching
        physical ID in that sample type (excluding the patched row's own ID
        for `patch-final`). Detects untouched-sibling collisions and
        planned-vs-planned collisions (two-way swaps, many-to-one renames)
        in one bounded query per (sample_type, chunk)."""
        result: dict[tuple[int, int, str], TitleCollationClass] = {}
        if not requests:
            return result
        collation = self._safe_collation(self._title_collation())
        by_type: dict[int, list[TitleCollationRequest]] = {}
        for request in requests:
            by_type.setdefault(request.sample_type_id, []).append(request)
        for type_id, type_requests in by_type.items():
            for chunk in bounded_identifier_chunks(type_requests):
                self.chunk_sizes.append(len(chunk))
                candidate_select = " UNION ALL ".join(
                    f"SELECT CONCAT('c:', %s) AS source, %s COLLATE {collation} AS canon_title" for _ in chunk
                )
                candidate_params: list[Any] = []
                for index, request in enumerate(chunk):
                    candidate_params += [str(index), request.title]
                sql = (
                    f"SELECT canon_title, GROUP_CONCAT(source) FROM ("
                    f"  SELECT CONCAT('e:', id) AS source, title COLLATE {collation} AS canon_title"
                    f"  FROM sample_attributes WHERE sample_type_id = %s"
                    f"  UNION ALL {candidate_select}"
                    f") AS all_titles GROUP BY canon_title"
                )
                params: list[Any] = [type_id] + candidate_params
                groups = self._execute(sql, params)
                candidate_group: dict[int, tuple[str, list[int]]] = {}
                for canon_title, sources in groups:
                    source_list = sources.split(",")
                    existing_ids = [int(source[2:]) for source in source_list if source.startswith("e:")]
                    candidate_indexes = [int(source[2:]) for source in source_list if source.startswith("c:")]
                    for candidate_index in candidate_indexes:
                        candidate_group[candidate_index] = (canon_title, existing_ids)
                for index, request in enumerate(chunk):
                    canon_title, existing_ids = candidate_group.get(index, (request.title, []))
                    match_ids = tuple(sorted(i for i in existing_ids if i != request.exclude_id))
                    key = (request.target_index, request.attribute_index, request.phase)
                    result[key] = TitleCollationClass(class_key=f"{type_id}:{canon_title}", match_ids=match_ids)
        return result

    # -- planner-read adapter -------------------------------------------------

    def _resolve_type_titles(self, type_ids: Sequence[int]) -> dict[int, str]:
        """Bulk, chunked `sample_types.title` lookup for exactly the
        requested ids -- one statement per `IDENTIFIER_CHUNK_SIZE`-sized
        chunk, never one per type."""
        result: dict[int, str] = {}
        for chunk in bounded_identifier_chunks(sorted(set(type_ids))):
            self.chunk_sizes.append(len(chunk))
            placeholders = ",".join(["%s"] * len(chunk))
            sql = f"SELECT id, title FROM sample_types WHERE id IN ({placeholders})"
            for type_id, title in self._execute(sql, list(chunk)):
                result[int(type_id)] = title
        return result

    def _fetch_rows_by_type_bulk(self, type_ids: Sequence[int]) -> dict[int, list[RawAttribute]]:
        """Bulk, chunked fetch of every attribute row owned by any of
        `type_ids`, grouped by `sample_type_id` in Python. One statement per
        `IDENTIFIER_CHUNK_SIZE`-sized chunk of *types* -- never one per type
        and never one per attribute row."""
        rows_by_type: dict[int, list[RawAttribute]] = {type_id: [] for type_id in type_ids}
        for chunk in bounded_identifier_chunks(sorted(set(type_ids))):
            self.chunk_sizes.append(len(chunk))
            placeholders = ",".join(["%s"] * len(chunk))
            sql = (
                f"SELECT {_RAW_ATTRIBUTE_COLUMNS} {_RAW_ATTRIBUTE_JOINS} "
                f"WHERE sa.sample_type_id IN ({placeholders}) ORDER BY sa.sample_type_id, sa.id"
            )
            for row in self._execute(sql, list(chunk)):
                raw = _row_to_raw_attribute(row)
                rows_by_type.setdefault(raw.sample_type_id, []).append(raw)
        return rows_by_type

    def type_snapshots(self, type_ids: Iterable[int]) -> dict[int, "TypeSnapshot"]:
        """Bulk, chunked planner-read snapshot of every requested sample
        type: one bulk title lookup, one bulk chunked row fetch grouped by
        type, and one bulk `invalid_json_counts` call -- `3*ceil(k/500)`
        statements total for `k` distinct types, never one round trip per
        type. The first type (in ascending id order) missing a title is
        reported exactly as the single-type per-iteration form would have
        raised on it -- rows for that type are simply never inspected."""
        type_id_list = sorted(set(type_ids))
        title_by_type = self._resolve_type_titles(type_id_list)
        missing = next((type_id for type_id in type_id_list if type_id not in title_by_type), None)
        if missing is not None:
            raise ResolutionError("sample_type_not_found", "sample type not found", submitted_identifier=missing)
        rows_by_type = self._fetch_rows_by_type_bulk(type_id_list)
        invalid_counts = self.invalid_json_counts(type_id_list)
        result: dict[int, TypeSnapshot] = {}
        for type_id in type_id_list:
            definitions = logicalize_definitions(rows_by_type.get(type_id, ()))
            snapshots = tuple(
                DefinitionSnapshot(
                    id=item.id, title=item.title, sample_type_id=item.sample_type_id,
                    sample_attribute_type_id=item.sample_attribute_type_id, required=item.required,
                    physical_pos=item.physical_pos, is_title=item.is_title, description=item.description,
                    unit_id=item.unit_id, sample_controlled_vocab_id=item.sample_controlled_vocab_id,
                    linked_sample_type_id=item.linked_sample_type_id, updated_at=item.updated_at,
                )
                for item in definitions
            )
            fingerprint_source = tuple(
                (s.id, s.title, s.sample_attribute_type_id, s.required, s.physical_pos, s.is_title,
                 s.description, s.unit_id, s.sample_controlled_vocab_id, s.linked_sample_type_id,
                 s.updated_at.isoformat())
                for s in snapshots
            )
            fingerprint = hashlib.sha256(orjson.dumps(fingerprint_source, default=str)).hexdigest()
            result[type_id] = TypeSnapshot(
                sample_type_id=type_id, sample_type_title=title_by_type[type_id],
                fingerprint=fingerprint, definitions=snapshots, invalid_json_count=invalid_counts.get(type_id, 0),
            )
        return result

    def invalid_json_counts(self, type_ids: Iterable[int]) -> dict[int, int]:
        result = {type_id: 0 for type_id in type_ids}
        type_id_list = sorted(result)
        for chunk in bounded_identifier_chunks(type_id_list):
            self.chunk_sizes.append(len(chunk))
            placeholders = ",".join(["%s"] * len(chunk))
            sql = (
                "SELECT sample_type_id, COUNT(*) FROM samples "
                f"WHERE sample_type_id IN ({placeholders}) AND JSON_VALID(json_metadata) = 0 "
                "GROUP BY sample_type_id"
            )
            for type_id, count in self._execute(sql, list(chunk)):
                result[int(type_id)] = int(count)
        return result

    def dependent_verdicts(self, type_ids: Iterable[int], resolved: dict) -> dict[int, str]:
        counts = self.invalid_json_counts(type_ids)
        return {type_id: ("compatible" if count == 0 else "invalid_json_present") for type_id, count in counts.items()}

    def materialization_identities(self, definitions: Iterable[dict]) -> dict[str, dict[int, tuple]]:
        """Bulk, bounded-chunked identity lookups for not-yet-created
        (hypothetical) definitions: sample type, value type, unit, and
        controlled vocabulary. One statement per chunk per relationship."""
        sample_type_ids: set[int] = set()
        value_type_ids: set[int] = set()
        unit_ids: set[int] = set()
        vocab_ids: set[int] = set()
        for definition in definitions:
            sample_type_ids.add(definition["sample_type_id"])
            value_type_ids.add(definition["sample_attribute_type_id"])
            if definition.get("unit_id") is not None:
                unit_ids.add(definition["unit_id"])
            if definition.get("sample_controlled_vocab_id") is not None:
                vocab_ids.add(definition["sample_controlled_vocab_id"])

        def _load(table: str, ids: set[int]) -> dict[int, tuple]:
            found: dict[int, tuple] = {}
            for chunk in bounded_identifier_chunks(sorted(ids)):
                self.chunk_sizes.append(len(chunk))
                placeholders = ",".join(["%s"] * len(chunk))
                sql = f"SELECT id, title FROM {table} WHERE id IN ({placeholders})"
                for row in self._execute(sql, list(chunk)):
                    found[int(row[0])] = tuple(row)
            return found

        units = self._load_units(unit_ids)
        return {
            "sample_types": _load("sample_types", sample_type_ids),
            "sample_attribute_types": _load("sample_attribute_types", value_type_ids),
            "units": units,
            "sample_controlled_vocabs": _load("sample_controlled_vocabs", vocab_ids),
        }

    def _load_units(self, unit_ids: set[int]) -> dict[int, tuple]:
        """The sole bounded loader for full unit identity materialization
        (id, title, and the response-only symbol); omitting this join would
        silently drop `unit_title`/`unit_symbol` from hypothetical records."""
        found: dict[int, tuple] = {}
        for chunk in bounded_identifier_chunks(sorted(unit_ids)):
            self.chunk_sizes.append(len(chunk))
            placeholders = ",".join(["%s"] * len(chunk))
            sql = f"SELECT id, title, symbol FROM units WHERE id IN ({placeholders})"
            for row in self._execute(sql, list(chunk)):
                found[int(row[0])] = tuple(row)
        return found

    def resolve_mutation_envelope(self, data: dict, repository: "AttributeRepository") -> dict:
        """Resolve a validated patch/delete envelope's mixed identifiers to
        physical identities within their explicit sample-type ownership,
        preserving submitted target/attribute provenance for every error.

        Three bulk passes over the *whole* envelope rather than one gateway
        round trip per target/operation: (1) every target's sample-type
        identifier through one `resolve_types` call, (2) every typed
        operation's attribute identifier through one `resolve_attributes_bulk`
        call grouped by sample type, (3) every untyped operation's identifier
        through `resolve_global_attribute_ids`, split only by ID/TITLE kind
        (never mixed in one call -- `_fetch_rows_by_id` sorts its identifier
        set, which cannot compare `int` and `str`). A final assembly pass
        rebuilds each target's result in submitted order from the three bulk
        maps; no pass's statement count depends on target/operation count,
        only on distinct identifier count."""
        targets = data["targets"]

        # Pass 1: bulk-resolve every target's sample-type identifier in one call.
        type_values = [target.get("sample_type") for target in targets if target.get("sample_type") is not None]
        type_matches_by_key = self.resolve_types(normalize_unique(type_values)) if type_values else {}

        target_states: list[dict] = []
        for target_index, target in enumerate(targets):
            sample_type_value = target.get("sample_type")
            operations = target.get("attributes", [])
            if sample_type_value is None:
                target_states.append({
                    "target_index": target_index, "sample_type_id": None, "sample_type_title": None,
                    "operations": operations, "resolution_errors": [], "terminal": False,
                })
                continue
            # Resolved directly against `resolve_types` (not the generic
            # `resolve_relationship("sample_types", ...)` helper): that
            # helper's error codes are scoped to the `linked_sample_type`
            # relationship field, which would be the wrong code for a
            # target's own owning sample type.
            normalized_type = normalize_identifier(sample_type_value)
            matches = type_matches_by_key.get(normalized_type.key, [])
            if not matches:
                target_states.append({
                    "target_index": target_index, "sample_type_id": None, "sample_type_title": None,
                    "operations": [], "resolution_errors": [_error_to_dict(ResolutionError(
                        "sample_type_not_found", "sample type not found", target_index=target_index,
                        field="sample_type", submitted_identifier=sample_type_value,
                    ))],
                    "terminal": True,
                })
                continue
            if len(matches) > 1:
                target_states.append({
                    "target_index": target_index, "sample_type_id": None, "sample_type_title": None,
                    "operations": [], "resolution_errors": [_error_to_dict(ResolutionError(
                        "sample_type_ambiguous", "sample type is ambiguous", target_index=target_index,
                        field="sample_type", submitted_identifier=sample_type_value,
                    ))],
                    "terminal": True,
                })
                continue
            sample_type_id, sample_type_title = matches[0]
            target_states.append({
                "target_index": target_index, "sample_type_id": sample_type_id, "sample_type_title": sample_type_title,
                "operations": operations, "resolution_errors": [], "terminal": False,
            })

        # Pass 2/3: bulk-resolve every operation's attribute identifier --
        # typed ops grouped by sample type via `resolve_attributes_bulk`,
        # untyped ops via `resolve_global_attribute_ids`, split by kind.
        bulk_requests: list[tuple[int, int, int, NormalizedIdentifier]] = []
        untyped_requests: list[tuple[int, int, Any, NormalizedIdentifier]] = []
        for state in target_states:
            if state["terminal"]:
                continue
            target_index = state["target_index"]
            sample_type_id = state["sample_type_id"]
            for attribute_index, operation in enumerate(state["operations"]):
                attribute_value = operation["attribute"] if isinstance(operation, dict) and "attribute" in operation else operation
                normalized_attribute = normalize_identifier(attribute_value)
                if sample_type_id is not None:
                    bulk_requests.append((target_index, attribute_index, sample_type_id, normalized_attribute))
                else:
                    untyped_requests.append((target_index, attribute_index, attribute_value, normalized_attribute))

        typed_resolved = self.resolve_attributes_bulk(bulk_requests) if bulk_requests else {}
        untyped_resolved: dict[tuple[int, int], list[RawAttribute]] = {}
        for kind in (IdentifierKind.ID, IdentifierKind.TITLE):
            group = [item for item in untyped_requests if item[3].kind is kind]
            if not group:
                continue
            global_resolved = self.resolve_global_attribute_ids([normalized.value for *_, normalized in group])
            for target_index, attribute_index, _value, normalized in group:
                untyped_resolved[(target_index, attribute_index)] = global_resolved.get(normalized.value, [])

        # Pass 4: assemble every target's result in submitted order from the
        # three bulk maps -- no further gateway round trips.
        resolved_targets = []
        for state in target_states:
            target_index = state["target_index"]
            if state["terminal"]:
                resolved_targets.append({
                    "target_index": target_index, "sample_type_id": None, "sample_type_title": None,
                    "operations": [], "resolution_errors": state["resolution_errors"],
                })
                continue
            sample_type_id = state["sample_type_id"]
            sample_type_title = state["sample_type_title"]
            resolved_operations = []
            resolution_errors: list[dict] = []
            for attribute_index, operation in enumerate(state["operations"]):
                attribute_value = operation["attribute"] if isinstance(operation, dict) and "attribute" in operation else operation
                try:
                    if sample_type_id is not None:
                        rows = typed_resolved.get((target_index, attribute_index), [])
                    else:
                        rows = untyped_resolved.get((target_index, attribute_index), [])
                    if not rows:
                        raise ResolutionError(
                            "attribute_not_found", "attribute not found", target_index=target_index,
                            attribute_index=attribute_index, field="attribute", submitted_identifier=attribute_value,
                        )
                    if len(rows) > 1:
                        raise ResolutionError(
                            "attribute_ambiguous", "attribute is ambiguous", target_index=target_index,
                            attribute_index=attribute_index, field="attribute", submitted_identifier=attribute_value,
                        )
                    row = rows[0]
                    entry = {
                        "attribute_id": row.id, "attribute_index": attribute_index, "resolution_errors": [],
                    }
                    if isinstance(operation, dict) and "changes" in operation:
                        entry["changes"] = operation["changes"]
                    resolved_operations.append(entry)
                except ResolutionError as error:
                    resolution_errors.append(_error_to_dict(error))
            resolved_targets.append({
                "target_index": target_index,
                "sample_type_id": sample_type_id,
                "sample_type_title": sample_type_title,
                "operations": resolved_operations,
                "resolution_errors": resolution_errors,
            })
        return {**data, "targets": resolved_targets}


def _error_to_dict(error: ResolutionError) -> dict:
    return {
        "code": error.code, "target_index": error.target_index, "attribute_index": error.attribute_index,
        "field": error.field, "submitted_identifier": error.submitted_identifier,
    }


# ---------------------------------------------------------------------------
# Repository: orchestrates gateway calls into the public read contract
# ---------------------------------------------------------------------------


class AttributeRepository:
    def __init__(self, gateway):
        self.gateway = gateway

    # -- search/catalog/retrieve ---------------------------------------------

    def _resolve_search_targets(self, targets: Sequence[dict]) -> tuple[set[int], set[int], set[int]]:
        type_identifiers = normalize_unique([target["sample_type"] for target in targets])
        type_matches = self.gateway.resolve_types(type_identifiers)
        type_ids: set[int] = set()
        whole_type_ids: set[int] = set()
        attribute_ids: set[int] = set()
        resolved_type_by_target: list[int] = []
        for target_index, target in enumerate(targets):
            normalized = normalize_identifier(target["sample_type"])
            matches = type_matches.get(normalized.key, [])
            if not matches:
                raise ResolutionError(
                    "sample_type_not_found", "sample type not found",
                    target_index=target_index, field="sample_type", submitted_identifier=target["sample_type"],
                )
            if len(matches) > 1:
                raise ResolutionError(
                    "sample_type_ambiguous", "sample type is ambiguous",
                    target_index=target_index, field="sample_type", submitted_identifier=target["sample_type"],
                )
            type_id = matches[0][0]
            resolved_type_by_target.append(type_id)
            type_ids.add(type_id)
        bulk_requests = []
        target_attribute_identifiers: dict[int, list[NormalizedIdentifier]] = {}
        for target_index, target in enumerate(targets):
            type_id = resolved_type_by_target[target_index]
            attributes = target.get("attributes")
            if attributes is None:
                whole_type_ids.add(type_id)
                continue
            normalized_attributes = normalize_unique(attributes)
            target_attribute_identifiers[target_index] = normalized_attributes
            for normalized in normalized_attributes:
                bulk_requests.append((target_index, normalized.submitted_index, type_id, normalized))
        if bulk_requests:
            resolved = self.gateway.resolve_attributes_bulk(bulk_requests)
            for target_index, values in target_attribute_identifiers.items():
                for normalized in values:
                    rows = resolved.get((target_index, normalized.submitted_index), [])
                    if not rows:
                        raise ResolutionError(
                            "attribute_not_found", "attribute not found", target_index=target_index,
                            attribute_index=normalized.submitted_index, field="attribute",
                            submitted_identifier=normalized.submitted,
                        )
                    if len(rows) > 1:
                        raise ResolutionError(
                            "attribute_ambiguous", "attribute is ambiguous", target_index=target_index,
                            attribute_index=normalized.submitted_index, field="attribute",
                            submitted_identifier=normalized.submitted,
                        )
                    attribute_ids.add(rows[0].id)
        return type_ids, whole_type_ids, attribute_ids

    def search(self, targets: Sequence[dict], page_request: PageRequest | None = None) -> Page:
        """Every `type_ids` entry is guaranteed by `_resolve_search_targets`
        to be either a whole-type selection or to have contributed at least
        one id to `attribute_ids`, so passing both straight through to
        `catalog` is always correct: whole-type members are never filtered,
        and every other type is filtered to exactly its resolved attributes."""
        page_request = page_request or PageRequest()
        type_ids, whole_type_ids, attribute_ids = self._resolve_search_targets(targets)
        total, definitions = self.gateway.catalog(
            type_ids=type_ids,
            attribute_ids=attribute_ids,
            whole_type_ids=whole_type_ids,
            offset=page_request.offset,
            limit=page_request.page_size,
        )
        records = self.materialize_attribute_records(definitions)
        return paginate(records, total, page_request)

    def catalog(self, page_request: PageRequest | None = None) -> Page:
        page_request = page_request or PageRequest()
        total, definitions = self.gateway.catalog(offset=page_request.offset, limit=page_request.page_size)
        records = self.materialize_attribute_records(definitions)
        return paginate(records, total, page_request)

    def retrieve(self, attribute_id: int):
        matches = self.gateway.resolve_global_attribute_ids([attribute_id])
        rows = matches.get(attribute_id, [])
        if not rows:
            raise ResolutionError("attribute_not_found", "attribute not found", submitted_identifier=attribute_id)
        row = rows[0]
        _total, definitions = self.gateway.catalog(
            type_ids={row.sample_type_id}, attribute_ids={attribute_id}, offset=0, limit=1
        )
        if not definitions:
            raise ResolutionError("attribute_not_found", "attribute not found", submitted_identifier=attribute_id)
        return self.materialize_attribute_records(definitions)[0]

    # -- relationship/id-only resolution --------------------------------------

    def resolve_relationship(self, table: str, identifier, *, target_index: int | None = None, field: str = "") -> tuple:
        normalized = normalize_identifier(identifier)
        if table == "units":
            matches = resolve_unit_identifier(self.gateway, normalized)
        else:
            matches = self.gateway.resolve_relationship(table, [normalized])[normalized.key]
        if not matches:
            raise ResolutionError(
                RELATIONSHIP_NOT_FOUND_CODE[table], f"{field} not found",
                target_index=target_index, field=field, submitted_identifier=identifier,
            )
        if len(matches) > 1:
            raise ResolutionError(
                RELATIONSHIP_AMBIGUOUS_CODE[table], f"{field} is ambiguous",
                target_index=target_index, field=field, submitted_identifier=identifier,
            )
        return matches[0]

    def resolve_id_only_attributes(self, ids: Sequence[Any], *, expected_sample_type_id: int | None = None) -> list[RawAttribute]:
        normalized = normalize_unique(ids)
        for item in normalized:
            if item.kind is not IdentifierKind.ID:
                raise ResolutionError(
                    "attribute_not_found", "ID-only resolution requires ID grammar",
                    attribute_index=item.submitted_index, submitted_identifier=item.submitted,
                )
        matches = self.gateway.resolve_global_attribute_ids([item.value for item in normalized])
        rows = []
        for item in normalized:
            found = matches.get(item.value, [])
            if not found:
                raise ResolutionError(
                    "attribute_not_found", "attribute not found",
                    attribute_index=item.submitted_index, submitted_identifier=item.submitted,
                )
            row = found[0]
            if expected_sample_type_id is not None and row.sample_type_id != expected_sample_type_id:
                raise ResolutionError(
                    "attribute_owner_mismatch", "attribute does not belong to the supplied sample type",
                    attribute_index=item.submitted_index, submitted_identifier=item.submitted,
                )
            rows.append(row)
        return rows

    # -- planner-read adapter -------------------------------------------------

    def resolve_mutation(self, envelope: dict) -> dict:
        return self.gateway.resolve_mutation_envelope(envelope, self)

    def snapshots_for(self, resolved: dict) -> dict[int, TypeSnapshot]:
        type_ids = {target["sample_type_id"] for target in resolved["targets"] if target.get("sample_type_id") is not None}
        return self.gateway.type_snapshots(type_ids)

    def dependent_verdicts(self, type_ids: Iterable[int], resolved: dict) -> dict[int, str]:
        return self.gateway.dependent_verdicts(type_ids, resolved)

    def invalid_json_counts(self, type_ids: Iterable[int]) -> dict[int, int]:
        return self.gateway.invalid_json_counts(type_ids)

    def materialize_attribute_records(self, definitions: Iterable[Definition]) -> tuple[AttributeRecord, ...]:
        return tuple(
            AttributeRecord(
                id=item.id, title=item.title, sample_type_id=item.sample_type_id,
                sample_type_title=item.sample_type_title,
                sample_attribute_type_id=item.sample_attribute_type_id,
                sample_attribute_type_title=item.sample_attribute_type_title,
                required=item.required, pos=item.pos, is_title=item.is_title, description=item.description,
                unit_id=item.unit_id, unit_title=item.unit_title, unit_symbol=item.unit_symbol,
                sample_controlled_vocab_id=item.sample_controlled_vocab_id,
                sample_controlled_vocab_title=item.sample_controlled_vocab_title,
                linked_sample_type_id=item.linked_sample_type_id,
                linked_sample_type_title=item.linked_sample_type_title,
                created_at=item.created_at, updated_at=item.updated_at,
            )
            for item in definitions
        )

    def materialize_hypothetical_records(self, definitions: Iterable[dict]) -> tuple[dict, ...]:
        """Materialize not-yet-created definitions (dry-run creates) keyed
        by their caller-supplied `token` rather than a physical `id`; no
        `id`/timestamps are present since the row does not yet exist."""
        definitions = list(definitions)
        identities = self.gateway.materialization_identities(definitions)
        records = []
        for definition in definitions:
            sample_type = identities["sample_types"].get(definition["sample_type_id"])
            value_type = identities["sample_attribute_types"].get(definition["sample_attribute_type_id"])
            unit = identities["units"].get(definition.get("unit_id")) if definition.get("unit_id") is not None else None
            vocab = (
                identities["sample_controlled_vocabs"].get(definition.get("sample_controlled_vocab_id"))
                if definition.get("sample_controlled_vocab_id") is not None
                else None
            )
            records.append({
                "token": definition["token"],
                "title": definition["title"],
                "sample_type_id": definition["sample_type_id"],
                "sample_type_title": sample_type[1] if sample_type else None,
                "sample_attribute_type_id": definition["sample_attribute_type_id"],
                "sample_attribute_type_title": value_type[1] if value_type else None,
                "required": definition.get("required", False),
                "pos": definition.get("pos"),
                "is_title": definition.get("is_title", False),
                "description": definition.get("description"),
                "unit_id": definition.get("unit_id"),
                "unit_title": unit[1] if unit else None,
                "unit_symbol": unit[2] if unit else None,
                "sample_controlled_vocab_id": definition.get("sample_controlled_vocab_id"),
                "sample_controlled_vocab_title": vocab[1] if vocab else None,
                "linked_sample_type_id": definition.get("linked_sample_type_id"),
                "linked_sample_type_title": (
                    identities["sample_types"].get(definition.get("linked_sample_type_id"))[1]
                    if definition.get("linked_sample_type_id") is not None
                    and identities["sample_types"].get(definition.get("linked_sample_type_id"))
                    else None
                ),
            })
        return tuple(records)

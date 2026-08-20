"""Bounded bulk rewrite kernel for `samples.json_metadata`.

This module owns exactly one boundary: given a `RewriteSpec` describing how an
attribute-definition mutation (create/rename/delete) changes the title set for
one sample type, rewrite every existing sample's `json_metadata` document to
match. It preserves the legacy per-sample behavior (DD-01) -- create adds an
empty-string value, rename moves the existing value, delete drops the key,
and any key that is not part of the resulting title set is dropped as stale --
while replacing the historical per-row ORM save loop with deterministic,
primary-key-ordered, bulk SQL.

The caller owns the transaction (DD-05): this module never opens, commits, or
rolls back one. It validates every document in a read-only first pass and
raises before any row is written if a single document is not valid JSON or a
rename would collide with an existing destination key (fail-closed). Only
after every row in the sample type has been proven valid does the second pass
bulk-update rows, chunked by row count and byte size so no single statement is
unbounded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

import orjson


class InvalidMetadata(ValueError):
    """A `json_metadata` document failed validation before any row was written."""


class RewriteCountMismatch(RuntimeError):
    """A bulk UPDATE affected a different number of rows than it was given."""


@dataclass(frozen=True)
class RewriteSpec:
    """Describes the exact title set every rewritten document must end with.

    `resulting_titles` is the complete, final key set (in stable projection
    order); any existing key absent from it is dropped as stale. `renames`
    moves an existing value from its old title to its new title. `additions`
    guarantees a title is present, defaulting to `""` if it was absent.
    `deletions` removes a title outright before the final projection (mostly
    redundant with omitting it from `resulting_titles`, but kept explicit so
    callers can express delete intent even when the deleted title collides
    with an unrelated resulting title elsewhere in the same document).
    """

    resulting_titles: tuple[str, ...]
    renames: tuple[tuple[str, str], ...] = ()
    additions: tuple[str, ...] = ()
    deletions: tuple[str, ...] = ()


@dataclass(frozen=True)
class RewriteResult:
    """Counts observed while rewriting one sample type's metadata."""

    scanned: int
    updated: int
    statements: int


def rewrite_document(raw: bytes | str, spec: RewriteSpec) -> bytes:
    """Return the rewritten canonical JSON bytes for one document.

    Raises `InvalidMetadata` if `raw` is not valid JSON, is not a JSON object,
    or if a requested rename would silently overwrite an existing destination
    key. Never partially applies a rewrite: either the full spec is applied
    and canonical bytes are returned, or nothing is returned at all.
    """
    try:
        value = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise InvalidMetadata("metadata is not valid JSON") from exc
    if not isinstance(value, dict):
        raise InvalidMetadata("metadata document must be a JSON object")
    current = dict(value)
    for old_title, new_title in spec.renames:
        if old_title in current and new_title in current and old_title != new_title:
            raise InvalidMetadata(f"rename destination already exists: {new_title!r}")
        if old_title in current:
            current[new_title] = current.pop(old_title)
    for title in spec.deletions:
        current.pop(title, None)
    for title in spec.additions:
        current.setdefault(title, "")
    normalized = {title: current.get(title, "") for title in spec.resulting_titles}
    return orjson.dumps(normalized, option=orjson.OPT_SORT_KEYS)


def iter_pk_chunks(
    rows: Iterable[tuple[int, bytes]], max_rows: int, max_bytes: int,
) -> Iterator[list[tuple[int, bytes]]]:
    """Group `(pk, raw_bytes)` rows into bounded, primary-key-ordered chunks.

    Each yielded chunk has at most `max_rows` rows and at most `max_bytes`
    total raw bytes. `rows` must already arrive in strictly increasing
    primary-key order (as a `SELECT ... ORDER BY id` cursor guarantees); this
    is verified defensively and raises `ValueError` if violated. A single row
    whose raw byte length exceeds `max_bytes` on its own can never fit in any
    chunk and raises `ValueError` rather than silently being skipped.
    """
    if max_rows < 1 or max_bytes < 1:
        raise ValueError("chunk limits must be positive")
    chunk: list[tuple[int, bytes]] = []
    used_bytes = 0
    previous_pk: int | None = None
    for row in rows:
        primary_key, raw = row
        if previous_pk is not None and primary_key <= previous_pk:
            raise ValueError("rows must arrive in strict primary-key order")
        previous_pk = primary_key
        size = len(raw)
        if size > max_bytes:
            raise ValueError("single row exceeds max_bytes")
        if chunk and (len(chunk) == max_rows or used_bytes + size > max_bytes):
            yield chunk
            chunk, used_bytes = [], 0
        chunk.append(row)
        used_bytes += size
    if chunk:
        yield chunk


def _fetch_locked_rows(cursor, sample_type_id: int, fetch_rows: int) -> Iterator[tuple[int, bytes]]:
    """Yield every `(id, json_metadata)` row for `sample_type_id` in PK order.

    Uses strict keyset pagination (`id > last_pk ... LIMIT fetch_rows FOR
    UPDATE`) rather than `OFFSET`, so pagination cost stays bounded as rows
    are scanned, and `FOR UPDATE` locks each page under the caller's
    transaction. Never calls `fetchall()` or materializes the whole result.
    """
    last_pk = 0
    while True:
        cursor.execute(
            "SELECT id,json_metadata FROM samples WHERE sample_type_id=%s AND id>%s "
            "ORDER BY id LIMIT %s FOR UPDATE",
            [sample_type_id, last_pk, fetch_rows],
        )
        rows = cursor.fetchmany(fetch_rows)
        if not rows:
            return
        for row in rows:
            yield row
        last_pk = rows[-1][0]


def rewrite_type_metadata(
    connection,
    sample_type_id: int,
    spec: RewriteSpec,
    max_rows: int,
    max_bytes: int,
    fault_hook: Callable[[str, int, int], None] | None = None,
) -> RewriteResult:
    """Bulk-rewrite `json_metadata` for every sample of `sample_type_id`.

    Two passes share one cursor over the caller's connection:

    1. A read-only validate pass scans every row once, locking it with
       `FOR UPDATE` and calling `rewrite_document` to prove every document is
       valid and every rename is collision-free, without writing anything.
       If any row fails, this raises before the write pass ever starts.
    2. A write pass replays the identical keyset-paginated chunking and bulk
       ``UPDATE ... CASE id`` per chunk (one statement per chunk), reconciling
       the server-reported affected-row count against the chunk size after
       every statement.

    This function never opens, commits, or rolls back a transaction -- the
    caller owns the one-sample-type transaction (DD-05) -- and it never calls
    an ORM `save()`. `fault_hook`, when given, is invoked as
    `fault_hook("before_bulk_update", ordinal, total_chunks)` immediately
    before each bulk UPDATE statement in the write pass, letting tests inject
    faults at the first (`ordinal == 1`) or penultimate
    (`ordinal == total_chunks - 1`) bulk statement.
    """
    cursor = connection.cursor()
    try:
        scanned = 0
        total_chunks = 0
        validate_rows = _fetch_locked_rows(cursor, sample_type_id, max_rows)
        for chunk in iter_pk_chunks(validate_rows, max_rows, max_bytes):
            for _, raw in chunk:
                rewrite_document(raw, spec)
                scanned += 1
            total_chunks += 1
        updated = 0
        statements = 0
        write_rows = _fetch_locked_rows(cursor, sample_type_id, max_rows)
        for statements, chunk in enumerate(iter_pk_chunks(write_rows, max_rows, max_bytes), start=1):
            if fault_hook is not None:
                fault_hook("before_bulk_update", statements, total_chunks)
            params = [(rewrite_document(raw, spec), primary_key) for primary_key, raw in chunk]
            # One CASE UPDATE per chunk (not executemany): MariaDB expands
            # executemany into per-row performance_schema events, which blows
            # history_long and the manifest SQL-count ceiling under bulk load.
            # batch_upload.update uses the same CASE-id shape for bulk metadata.
            when_clauses = " ".join(["WHEN %s THEN %s"] * len(params))
            id_placeholders = ",".join(["%s"] * len(params))
            sql = (
                f"UPDATE samples SET json_metadata = CASE id {when_clauses} END "
                f"WHERE id IN ({id_placeholders})"
            )
            flat: list = []
            primary_keys: list[int] = []
            for rewritten, primary_key in params:
                flat.extend([primary_key, rewritten])
                primary_keys.append(primary_key)
            cursor.execute(sql, flat + primary_keys)
            # Without CLIENT_FOUND_ROWS, MySQL/MariaDB reports 0 for matched rows
            # whose values did not change (idempotent rewrite). Treat that as
            # success only when every target primary key still exists.
            if cursor.rowcount == len(params):
                updated += cursor.rowcount
            elif cursor.rowcount == 0 and params:
                placeholders = ",".join(["%s"] * len(params))
                cursor.execute(
                    f"SELECT COUNT(*) FROM samples WHERE id IN ({placeholders})",
                    primary_keys,
                )
                found = int(cursor.fetchone()[0])
                if found != len(params):
                    raise RewriteCountMismatch(
                        f"expected to update {len(params)} rows, server reported "
                        f"{cursor.rowcount} and only {found} target ids exist"
                    )
                updated += len(params)
            else:
                raise RewriteCountMismatch(
                    f"expected to update {len(params)} rows, server reported {cursor.rowcount}"
                )
        return RewriteResult(scanned=scanned, updated=updated, statements=statements)
    finally:
        cursor.close()

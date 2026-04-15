"""Stage 1.5: UID_GEN — generate UIDs for rows with empty UIDs and resolve parent references."""
from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .errors import ErrorCollector, ErrorType, Severity
from .helpers import UID_RE, collect_parent_tokens, split_parent_field
from .identity import extract_identity
from .models import InputRowModel

try:
    import orjson

    def _json_loads(s):
        return orjson.loads(s)

    def _json_dumps_min(obj):
        return orjson.dumps(obj).decode("utf-8")

except ImportError:
    _json_loads = json.loads

    def _json_dumps_min(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


log = logging.getLogger(__name__)


# ── helpers ─────────────────────────────────────────────────────────────────


def _parse_meta(row: InputRowModel) -> dict:
    """Parse json_metadata into a dict, returning {} on failure."""
    try:
        meta = _json_loads(row.json_metadata) if row.json_metadata else {}
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


@dataclass(frozen=True)
class AmbiguousIdentityError(RuntimeError):
    row_index: int
    identity: str
    conflicting_sample_ids: Tuple[int, ...]

    def __init__(
        self,
        *,
        row_index: int,
        identity: str,
        conflicting_sample_ids: List[int] | Tuple[int, ...],
    ) -> None:
        ids = tuple(conflicting_sample_ids)
        object.__setattr__(self, "row_index", row_index)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "conflicting_sample_ids", ids)
        super().__init__(
            f"ambiguous identity match: {len(ids)} existing samples with "
            f"name_identity={identity!r} - duplicates must be resolved before batch "
            f"can proceed (conflicting sample ids: {list(ids)})"
        )


def _extract_identity(row: InputRowModel) -> str | None:
    """Extract the stable batch identity for uniqueness and parent resolution."""
    return extract_identity(_parse_meta(row), uid=row.UID, sample_type=row.SampleType)


# ── 4a. Deduplication ───────────────────────────────────────────────────────


def _deduplicate_rows(
    rows: List[InputRowModel],
) -> Tuple[List[InputRowModel], List[str]]:
    """Remove duplicate rows by Name/File_PrimaryData. Keep first, warn on dupes.

    Returns (deduplicated_rows, warning_messages).
    """
    seen: Dict[str, int] = {}  # identity -> first row index
    keep: List[InputRowModel] = []
    warnings: List[str] = []

    for idx, row in enumerate(rows):
        # Only deduplicate rows that need UIDs generated
        if row.UID is not None:
            keep.append(row)
            continue

        identity = _extract_identity(row)
        if identity is None:
            keep.append(row)
            continue

        if identity in seen:
            warnings.append(
                f"Duplicate identity '{identity}' at row {idx} "
                f"(first seen at row {seen[identity]}); skipping duplicate"
            )
        else:
            seen[identity] = idx
            keep.append(row)

    return keep, warnings


# ── 4b. UID generation ──────────────────────────────────────────────────────


def _compute_uid_prefix(sample_type: str) -> str:
    """Extract UID prefix from sample type title.

    Replicates legacy logic: split on '_', take the first term.
    E.g., 'NHP_blood' -> 'NHP', 'D.IMG_files' -> 'D.IMG', 'A.GEX' -> 'A.GEX'
    """
    if "_" in sample_type:
        return sample_type.split("_")[0]
    return sample_type


def _query_max_index(conn: Connection, prefix: str) -> int:
    """Query max UID index for a given prefix from the samples table.

    Uses a single SQL query with SUBSTRING_INDEX to extract and MAX the
    numeric index after the prefix. Returns 0 if none found.

    Handles UIDs like '{prefix}-5' and '{prefix}-5-PUB2' by splitting on
    '-' and taking the first segment after the prefix.
    """
    prefix_dash = f"{prefix}-"
    result = conn.execute(
        text(
            "SELECT COALESCE(MAX("
            "  CAST(SUBSTRING_INDEX(SUBSTRING(uuid, :plen + 1), '-', 1) AS UNSIGNED)"
            "), 0) FROM samples WHERE uuid LIKE :pattern"
        ),
        {"plen": len(prefix_dash), "pattern": f"{prefix}-%"},
    ).scalar()
    return int(result) if result else 0


def _generate_uids_for_prefix(
    rows_needing_uids: List[InputRowModel],
    prefix: str,
    conn: Connection,
) -> int:
    """Generate sequential UIDs for all rows sharing a prefix, under advisory lock.

    Mutates rows in place. Returns count of UIDs generated.
    """
    lock_name = f"uid_gen:{prefix}"
    try:
        # Acquire per-prefix advisory lock (10s timeout)
        lock_result = conn.execute(
            text("SELECT GET_LOCK(:name, 10)"), {"name": lock_name}
        ).scalar()
        if lock_result != 1:
            raise RuntimeError(f"Could not acquire advisory lock '{lock_name}'")

        max_index = _query_max_index(conn, prefix)

        for i, row in enumerate(rows_needing_uids, start=1):
            row.UID = f"{prefix}-{max_index + i}"

        return len(rows_needing_uids)
    finally:
        conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})


def generate_uids(
    rows: List[InputRowModel],
    lababbv: str,
    conn: Connection,
) -> Tuple[List[InputRowModel], int]:
    """Generate UIDs for all rows with UID=None. Returns (rows, count_generated).

    Computes date string YYMMDD from now, groups null-UID rows by prefix,
    and generates sequential UIDs per prefix under advisory locks.
    """
    date_str = datetime.datetime.now().strftime("%y%m%d")
    total_generated = 0

    # Group null-UID rows by their full prefix
    by_prefix: Dict[str, List[InputRowModel]] = {}
    for row in rows:
        if row.UID is not None:
            continue
        type_prefix = _compute_uid_prefix(row.SampleType)
        full_prefix = f"{type_prefix}-{date_str}{lababbv}"
        by_prefix.setdefault(full_prefix, []).append(row)

    # Generate UIDs per prefix
    for prefix, prefix_rows in sorted(by_prefix.items()):
        count = _generate_uids_for_prefix(prefix_rows, prefix, conn)
        total_generated += count

    return rows, total_generated


# ── 4c. Parent resolution ──────────────────────────────────────────────────


def _build_identity_to_uid_map(rows: List[InputRowModel]) -> Dict[str, str]:
    """Build unified Name/File_PrimaryData -> UID lookup for all rows.

    All rows must have UIDs at this point (after UID generation).
    """
    mapping: Dict[str, str] = {}
    for row in rows:
        if row.UID is None:
            continue
        identity = _extract_identity(row)
        if identity and identity not in mapping:
            mapping[identity] = row.UID
    return mapping


def _resolve_parents(
    rows: List[InputRowModel],
    identity_to_uid: Dict[str, str],
    conn: Connection,
) -> Tuple[List[InputRowModel], List[str], int, List[dict]]:
    """Resolve Parent field references from Name/File_PrimaryData to UIDs.

    Per token in the Parent field:
    - If it matches UID regex -> keep as-is
    - If it matches identity_to_uid -> replace with UID
    - Else -> collect for bulk DB lookup

    Returns (modified_rows, warning_messages, parents_resolved_count).
    """
    warnings: List[str] = []
    parents_resolved = 0
    failed_rows: List[dict] = []

    # First pass: collect all unresolved tokens across all rows
    unresolved_tokens: Set[str] = set()
    rows_with_parents: List[Tuple[int, dict, List[str]]] = []

    for idx, row in enumerate(rows):
        meta = _parse_meta(row)
        tokens = collect_parent_tokens(meta)
        if not tokens:
            continue

        rows_with_parents.append((idx, meta, tokens))

        for token in tokens:
            if UID_RE.match(token):
                continue
            if token in identity_to_uid:
                continue
            unresolved_tokens.add(token)

    # Bulk DB fallback for unresolved tokens
    db_identity_to_uid: Dict[str, str] = {}
    ambiguous_identities: Dict[str, List[int]] = {}
    if unresolved_tokens:
        db_identity_to_uid, ambiguous_identities = _bulk_resolve_from_db(unresolved_tokens, conn)

    # Second pass: resolve tokens and update rows
    surviving_by_index: Dict[int, InputRowModel] = {}
    rows_with_parents_idx = {row_idx for row_idx, _meta, _tokens in rows_with_parents}
    for idx, meta, tokens in rows_with_parents:
        row = rows[idx]
        resolved_tokens = []
        row_failed = False
        for token in tokens:
            if UID_RE.match(token):
                resolved_tokens.append(token)
            elif token in identity_to_uid:
                resolved_tokens.append(identity_to_uid[token])
                parents_resolved += 1
            elif token in ambiguous_identities:
                failed_rows.append({
                    "row": row,
                    "uid": row.UID,
                    "row_index": idx,
                    "error": AmbiguousIdentityError(
                        row_index=idx,
                        identity=token,
                        conflicting_sample_ids=ambiguous_identities[token],
                    ),
                })
                row_failed = True
                break
            elif token in db_identity_to_uid:
                resolved_tokens.append(db_identity_to_uid[token])
                parents_resolved += 1
            else:
                # Identity-based token not resolvable now — preserve for orphan resolution
                resolved_tokens.append(token)
                warnings.append(
                    f"Row {idx}: unresolved parent reference '{token}'; "
                    f"preserving for future orphan resolution"
                )

        if row_failed:
            continue

        if resolved_tokens:
            # Deduplicate while preserving order
            seen_wb: set = set()
            deduped: list = []
            for t in resolved_tokens:
                if t not in seen_wb:
                    seen_wb.add(t)
                    deduped.append(t)
            meta["Parent"] = ";".join(deduped)
        else:
            meta.pop("Parent", None)
            meta.pop("parent", None)

        row.json_metadata = _json_dumps_min(meta)
        surviving_by_index[idx] = row

    surviving_rows: List[InputRowModel] = []
    failed_indexes = {item["row_index"] for item in failed_rows}
    for idx, row in enumerate(rows):
        if idx in failed_indexes:
            continue
        if idx in rows_with_parents_idx:
            if idx in surviving_by_index:
                surviving_rows.append(surviving_by_index[idx])
            continue
        surviving_rows.append(row)

    return surviving_rows, warnings, parents_resolved, failed_rows


def _bulk_resolve_from_db(
    identities: Set[str], conn: Connection
) -> Tuple[Dict[str, str], Dict[str, List[int]]]:
    """Query samples.name_identity for unresolved parent references."""
    if not identities:
        return {}, {}

    identity_to_uid: Dict[str, str] = {}
    ambiguous_identities: Dict[str, List[int]] = {}

    identity_list = sorted(identities)
    placeholders = ", ".join(f":t{i}" for i in range(len(identity_list)))
    params = {f"t{i}": t for i, t in enumerate(identity_list)}

    result = conn.execute(
        text(
            f"SELECT uuid, id, name_identity FROM samples "
            f"WHERE name_identity IN ({placeholders})"
        ),
        params,
    )

    by_identity: Dict[str, List[Tuple[str, int]]] = {}
    for uuid_val, sample_id, identity_val in result:
        by_identity.setdefault(identity_val, []).append((uuid_val, int(sample_id)))

    for identity, matches in by_identity.items():
        if len(matches) == 1:
            identity_to_uid[identity] = matches[0][0]
        else:
            ambiguous_identities[identity] = sorted(sample_id for _uuid, sample_id in matches)

    return identity_to_uid, ambiguous_identities


# ── 4d. json_metadata injection ─────────────────────────────────────────────


def _inject_uid_into_metadata(rows: List[InputRowModel], generated_uids: Set[str]) -> None:
    """Inject generated UID into json_metadata for rows that had UIDs generated."""
    for row in rows:
        if row.UID in generated_uids:
            meta = _parse_meta(row)
            meta["UID"] = row.UID
            row.json_metadata = _json_dumps_min(meta)


# ── 4e. Pre-UID-gen idempotence check ─────────────────────────────────────


def check_name_exists_in_db(
    rows: List[InputRowModel],
    conn: Connection,
) -> Tuple[List[InputRowModel], Dict[str, dict], List[Tuple[InputRowModel, str]], List[Tuple[InputRowModel, AmbiguousIdentityError]]]:
    """Check if Name/File_PrimaryData of null-UID rows already exist in samples.name_identity.

    Case-insensitive global check (uses DB collation utf8mb4_unicode_ci).
    Only checks rows where UID is None.
    Rows with existing UIDs are passed through unchanged.

    Returns:
        (remaining_rows, name_matches, matched_rows, ambiguous_rows)
        - remaining_rows: rows that did NOT match (+ rows with UIDs)
        - name_matches: dict of {identity: {"uid": str, "sample_id": int}} for matched rows
        - matched_rows: list of (InputRowModel, identity) tuples for matched rows
        - ambiguous_rows: list of (InputRowModel, AmbiguousIdentityError) tuples
    """
    remaining: List[InputRowModel] = []
    to_check: List[Tuple[int, InputRowModel, str]] = []

    for idx, row in enumerate(rows):
        if row.UID is not None:
            remaining.append(row)
            continue
        identity = _extract_identity(row)
        if identity is None:
            remaining.append(row)
            continue
        to_check.append((idx, row, identity))

    if not to_check:
        return remaining, {}, [], []

    # Bulk query: case-insensitive match against samples.name_identity
    # The DB column uses utf8mb4_unicode_ci collation, so comparison is
    # already case-insensitive — no need for LOWER() wrappers.
    identities = list({item[2] for item in to_check})
    db_matches: Dict[str, List[dict]] = {}  # lowercase_identity -> [{uid, sample_id}]

    for chunk_start in range(0, len(identities), 1000):
        chunk = identities[chunk_start : chunk_start + 1000]
        params = {f"t{i}": t for i, t in enumerate(chunk)}
        placeholders = ", ".join(f":t{i}" for i in range(len(chunk)))
        result = conn.execute(
            text(
                f"SELECT uuid, id, name_identity FROM samples "
                f"WHERE name_identity IN ({placeholders})"
            ),
            params,
        )
        for uuid_val, sample_id, identity_val in result.fetchall():
            key = identity_val.lower() if identity_val else ""
            db_matches.setdefault(key, []).append(
                {"uid": uuid_val, "sample_id": int(sample_id)}
            )

    # Partition: matched vs remaining
    matched_rows: List[Tuple[InputRowModel, str]] = []
    ambiguous_rows: List[Tuple[InputRowModel, AmbiguousIdentityError]] = []
    name_matches: Dict[str, dict] = {}
    for idx, row, identity in to_check:
        key = identity.lower()
        if key in db_matches:
            matches = db_matches[key]
            if len(matches) == 1:
                name_matches[identity] = matches[0]
                matched_rows.append((row, identity))
            else:
                ambiguous_rows.append((
                    row,
                    AmbiguousIdentityError(
                        row_index=idx,
                        identity=identity,
                        conflicting_sample_ids=sorted(match["sample_id"] for match in matches),
                    ),
                ))
        else:
            remaining.append(row)

    return remaining, name_matches, matched_rows, ambiguous_rows


# ── 4f. Main entry point ───────────────────────────────────────────────────


def run_uid_gen(
    rows: List[InputRowModel],
    lababbv: str,
    conn: Connection,
    error_collector: ErrorCollector,
) -> Tuple[List[InputRowModel], Dict[str, Any]]:
    """Stage 1.5: UID_GEN — generate UIDs and resolve parent references.

    Returns (processed_rows, stage_report).
    stage_report contains: uids_generated, duplicates_removed, parents_resolved,
                           parents_unresolved, warnings.
    """
    report: Dict[str, Any] = {
        "uids_generated": 0,
        "duplicates_removed": 0,
        "parents_resolved": 0,
        "parents_unresolved": 0,
        "failed_rows": [],
        "warnings": [],
    }

    # Step 1: Deduplicate rows with empty UIDs by identity
    rows, dedup_warnings = _deduplicate_rows(rows)
    report["duplicates_removed"] = len(dedup_warnings)
    report["warnings"].extend(dedup_warnings)
    for w in dedup_warnings:
        error_collector.add(
            row_index=-1, uid=None, error_type=ErrorType.DUPLICATE,
            message=w, severity=Severity.WARNING,
        )

    rows_needing_uids = [r for r in rows if r.UID is None]
    generated_uid_set_before = {r.UID for r in rows if r.UID is not None}
    if rows_needing_uids:
        rows, count = generate_uids(rows, lababbv, conn)
        report["uids_generated"] = count
    else:
        log.info("UID_GEN: all rows have UIDs, skipping generation")
    generated_uid_set_after = {r.UID for r in rows if r.UID is not None}
    newly_generated = generated_uid_set_after - generated_uid_set_before

    # Step 3: Inject generated UIDs into json_metadata
    _inject_uid_into_metadata(rows, newly_generated)

    # Step 4: Build identity lookup and resolve parents
    identity_to_uid = _build_identity_to_uid_map(rows)
    rows, parent_warnings, parents_resolved_count, failed_rows = _resolve_parents(rows, identity_to_uid, conn)
    report["parents_resolved"] = parents_resolved_count
    report["failed_rows"] = [
        {
            "uid": item["uid"],
            "row_index": item["row_index"],
            "reason": str(item["error"]),
            "error_type": ErrorType.AMBIGUOUS_IDENTITY.value,
        }
        for item in failed_rows
    ]
    report["warnings"].extend(parent_warnings)
    for item in failed_rows:
        error_collector.add(
            row_index=item["row_index"],
            uid=item["uid"],
            error_type=ErrorType.AMBIGUOUS_IDENTITY,
            message=str(item["error"]),
        )

    # Count resolved/unresolved
    for w in parent_warnings:
        if "unresolvable" in w.lower():
            report["parents_unresolved"] += 1
            error_collector.add(
                row_index=-1, uid=None, error_type=ErrorType.VALIDATION_JSON,
                message=w, severity=Severity.WARNING,
            )
    log.info(
        "UID_GEN: generated=%d, deduped=%d, parent_warnings=%d, parent_failures=%d",
        report["uids_generated"],
        report["duplicates_removed"],
        len(parent_warnings),
        len(failed_rows),
    )

    return rows, report

"""Stage 1.5: UID_GEN — generate UIDs for rows with empty UIDs and resolve parent references."""
from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .errors import ErrorCollector, ErrorType, Severity
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

# UID regex matching the full UID format (same as dag.py but compiled once)
_UID_RE = re.compile(r"^([AD]\.)?[A-Z]{3,}-\d{6}[A-Z]{2,5}-\d+(-PUB\d*)?$")

# Parent field token splitter (same as dag.py)
_PARENT_SPLIT_RE = re.compile(r"[;\,\s]+")

# File_PrimaryData field names (including legacy typo variants)
_FILE_PRIMARY_FIELDS = (
    "File_PrimaryData",
    "File_PrimartyData",
    "File_PrimaryData_Forward",
    "File_PrimartyData_Forward",
    "File_PrimaryData_Reverse",
    "File_PrimartyData_Reverse",
)

# Sample type prefixes that use File_PrimaryData instead of Name
_FILE_BASED_PREFIXES = ("D.", "A.")


# ── helpers ─────────────────────────────────────────────────────────────────


def _parse_meta(row: InputRowModel) -> dict:
    """Parse json_metadata into a dict, returning {} on failure."""
    try:
        meta = _json_loads(row.json_metadata) if row.json_metadata else {}
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _is_file_based_type(sample_type: str) -> bool:
    """Check if sample type uses File_PrimaryData for identity (D.* or A.* prefixes)."""
    return any(sample_type.startswith(p) for p in _FILE_BASED_PREFIXES)


def _extract_identity(row: InputRowModel) -> str | None:
    """Extract Name or File_PrimaryData from json_metadata for uniqueness check.

    For D./A. sample types: uses File_PrimaryData (or typo variants).
    For other types: uses Name.
    Returns the identity value or None.
    """
    meta = _parse_meta(row)
    if not meta:
        return None

    if _is_file_based_type(row.SampleType):
        for field in _FILE_PRIMARY_FIELDS:
            val = meta.get(field)
            if val and str(val).strip():
                return str(val).strip()
        return None

    name = meta.get("Name") or meta.get("name")
    if name is not None:
        s = str(name).strip()
        return s if s else None
    return None


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
) -> Tuple[List[InputRowModel], List[str]]:
    """Resolve Parent field references from Name/File_PrimaryData to UIDs.

    Per token in the Parent field:
    - If it matches UID regex -> keep as-is
    - If it matches identity_to_uid -> replace with UID
    - Else -> collect for bulk DB lookup

    Returns (modified_rows, warning_messages, parents_resolved_count).
    """
    warnings: List[str] = []
    parents_resolved = 0

    # First pass: collect all unresolved tokens across all rows
    unresolved_tokens: Set[str] = set()
    rows_with_parents: List[Tuple[int, dict, List[str]]] = []

    for idx, row in enumerate(rows):
        meta = _parse_meta(row)
        parent_raw = meta.get("Parent") or meta.get("parent") or ""
        if not parent_raw or not isinstance(parent_raw, str):
            continue

        tokens = [t.strip() for t in _PARENT_SPLIT_RE.split(parent_raw.strip()) if t.strip()]
        if not tokens:
            continue

        rows_with_parents.append((idx, meta, tokens))

        for token in tokens:
            if _UID_RE.match(token):
                continue
            if token in identity_to_uid:
                continue
            unresolved_tokens.add(token)

    # Bulk DB fallback for unresolved tokens
    db_title_to_uid: Dict[str, str] = {}
    if unresolved_tokens:
        db_title_to_uid, db_warnings = _bulk_resolve_from_db(unresolved_tokens, conn)
        warnings.extend(db_warnings)

    # Second pass: resolve tokens and update rows
    for idx, meta, tokens in rows_with_parents:
        resolved_tokens = []
        for token in tokens:
            if _UID_RE.match(token):
                resolved_tokens.append(token)
            elif token in identity_to_uid:
                resolved_tokens.append(identity_to_uid[token])
                parents_resolved += 1
            elif token in db_title_to_uid:
                resolved_tokens.append(db_title_to_uid[token])
                parents_resolved += 1
            else:
                warnings.append(
                    f"Row {idx}: unresolvable parent reference '{token}'; "
                    f"inserting sample without this parent link"
                )
                # Don't include unresolvable tokens in the resolved parent field

        row = rows[idx]
        if resolved_tokens:
            meta["Parent"] = ";".join(resolved_tokens)
        else:
            meta.pop("Parent", None)
            meta.pop("parent", None)

        row.json_metadata = _json_dumps_min(meta)

    return rows, warnings, parents_resolved


def _bulk_resolve_from_db(
    titles: Set[str], conn: Connection
) -> Tuple[Dict[str, str], List[str]]:
    """Query samples.title for unresolved parent references.

    Returns (title_to_uid_map, warnings).
    Errors on ambiguous matches (multiple UUIDs per title).
    """
    if not titles:
        return {}, []

    title_to_uid: Dict[str, str] = {}
    warnings: List[str] = []

    # Build parameterized query for titles
    title_list = sorted(titles)
    placeholders = ", ".join(f":t{i}" for i in range(len(title_list)))
    params = {f"t{i}": t for i, t in enumerate(title_list)}

    result = conn.execute(
        text(f"SELECT uuid, title FROM samples WHERE title IN ({placeholders})"),
        params,
    )

    # Group by title
    by_title: Dict[str, List[str]] = {}
    for uuid_val, title_val in result:
        by_title.setdefault(title_val, []).append(uuid_val)

    for title, uuids in by_title.items():
        if len(uuids) == 1:
            title_to_uid[title] = uuids[0]
        else:
            warnings.append(
                f"Ambiguous parent reference '{title}': "
                f"found {len(uuids)} samples with this title; skipping"
            )

    return title_to_uid, warnings


# ── 4d. json_metadata injection ─────────────────────────────────────────────


def _inject_uid_into_metadata(rows: List[InputRowModel], generated_uids: Set[str]) -> None:
    """Inject generated UID into json_metadata for rows that had UIDs generated."""
    for row in rows:
        if row.UID in generated_uids:
            meta = _parse_meta(row)
            meta["UID"] = row.UID
            row.json_metadata = _json_dumps_min(meta)


# ── 4e. Main entry point ───────────────────────────────────────────────────


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

    # Check if any rows need UIDs
    rows_needing_uids = [r for r in rows if r.UID is None]
    if not rows_needing_uids:
        log.info("UID_GEN: all rows have UIDs, skipping generation")
        return rows, report

    # Step 2: Generate UIDs
    generated_uid_set_before = {r.UID for r in rows if r.UID is not None}
    rows, count = generate_uids(rows, lababbv, conn)
    report["uids_generated"] = count
    generated_uid_set_after = {r.UID for r in rows if r.UID is not None}
    newly_generated = generated_uid_set_after - generated_uid_set_before

    # Step 3: Inject generated UIDs into json_metadata
    _inject_uid_into_metadata(rows, newly_generated)

    # Step 4: Build identity lookup and resolve parents
    identity_to_uid = _build_identity_to_uid_map(rows)
    rows, parent_warnings, parents_resolved_count = _resolve_parents(rows, identity_to_uid, conn)
    report["parents_resolved"] = parents_resolved_count
    report["warnings"].extend(parent_warnings)

    # Count resolved/unresolved
    for w in parent_warnings:
        if "unresolvable" in w.lower():
            report["parents_unresolved"] += 1
            error_collector.add(
                row_index=-1, uid=None, error_type=ErrorType.VALIDATION_JSON,
                message=w, severity=Severity.WARNING,
            )
        elif "ambiguous" in w.lower():
            report["parents_unresolved"] += 1
            error_collector.add(
                row_index=-1, uid=None, error_type=ErrorType.VALIDATION_JSON,
                message=w, severity=Severity.WARNING,
            )

    log.info(
        "UID_GEN: generated=%d, deduped=%d, parent_warnings=%d",
        report["uids_generated"],
        report["duplicates_removed"],
        len(parent_warnings),
    )

    return rows, report

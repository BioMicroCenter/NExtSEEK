"""Sample UPDATE logic for batch upload upsert mode."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .associations import batch_insert_assay_assets, batch_insert_projects_samples
from .identity import canonicalize_file_primary_data
from .models import InsertableSample, RowOutcome

try:
    import orjson
    def _json_loads(s): return orjson.loads(s)
    def _json_dumps_min(obj): return orjson.dumps(obj).decode("utf-8")
except ImportError:
    _json_loads = json.loads
    def _json_dumps_min(obj): return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

log = logging.getLogger(__name__)


def _canonicalize_json_metadata(raw_json: str) -> str:
    try:
        parsed = _json_loads(raw_json) if raw_json else {}
    except Exception:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return _json_dumps_min(canonicalize_file_primary_data(parsed))


def deep_merge_metadata(
    old_json: str, new_json: str
) -> Tuple[str, Set[str]]:
    """Deep merge new metadata into old. New keys overwrite, old preserved.

    Returns (merged_json_str, set_of_changed_keys).
    """
    try:
        old = _json_loads(old_json) if old_json else {}
    except Exception:
        old = {}
    try:
        new = _json_loads(new_json) if new_json else {}
    except Exception:
        new = {}

    if not isinstance(old, dict):
        old = {}
    if not isinstance(new, dict):
        new = {}

    changed_keys: Set[str] = set()
    merged = dict(old)

    for key, new_val in new.items():
        old_val = old.get(key)
        if old_val != new_val:
            changed_keys.add(key)
        merged[key] = new_val

    return _json_dumps_min(merged), changed_keys


def load_existing_sample_details(
    uuids: List[str], conn: Connection
) -> Dict[str, dict]:
    """Load existing sample details needed for update.

    Returns {uuid: {"sample_id": int, "policy_id": int, "json_metadata": str, "title": str}}.
    """
    details: Dict[str, dict] = {}
    for chunk_start in range(0, len(uuids), 1000):
        chunk = uuids[chunk_start : chunk_start + 1000]
        params = {f"u{i}": u for i, u in enumerate(chunk)}
        placeholders = ", ".join(f":u{i}" for i in range(len(chunk)))
        result = conn.execute(
            text(
                f"SELECT uuid, id, policy_id, json_metadata, title "
                f"FROM samples WHERE uuid IN ({placeholders})"
            ),
            params,
        )
        for uuid_val, sample_id, policy_id, jmeta, title in result.fetchall():
            details[uuid_val] = {
                "sample_id": sample_id,
                "policy_id": policy_id,
                "json_metadata": jmeta or "{}",
                "title": title or "",
            }
    return details


def update_sample_metadata(
    uuid: str,
    sample_id: int,
    new_title: str,
    merged_metadata: str,
    conn: Connection,
) -> None:
    """UPDATE samples SET title, json_metadata, updated_at WHERE id = sample_id."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        text(
            "UPDATE samples SET title = :title, json_metadata = :meta, "
            "updated_at = :now WHERE id = :sid"
        ),
        {"title": new_title, "meta": merged_metadata, "now": now, "sid": sample_id},
    )


def smart_merge_assay_assets(
    sample_id: int,
    new_assay_ids: List[int],
    direction_by_pair: Dict,
    uid: str,
    conn: Connection,
) -> Tuple[Set[int], Set[int]]:
    """Smart merge assay links: add new, remove unlisted, keep unchanged.

    Returns (added_assay_ids, removed_assay_ids).
    """
    result = conn.execute(
        text("SELECT assay_id FROM assay_assets WHERE asset_id = :sid AND asset_type = 'Sample'"),
        {"sid": sample_id},
    )
    existing_assays = {row[0] for row in result.fetchall()}
    new_set = set(new_assay_ids)

    to_add = new_set - existing_assays
    to_remove = existing_assays - new_set

    if to_remove:
        for aid in to_remove:
            conn.execute(
                text("DELETE FROM assay_assets WHERE assay_id = :aid AND asset_id = :sid AND asset_type = 'Sample'"),
                {"aid": aid, "sid": sample_id},
            )

    if to_add:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for aid in to_add:
            direction = direction_by_pair.get((uid, aid), 1)
            conn.execute(
                text(
                    "INSERT INTO assay_assets (assay_id, asset_id, version, created_at, updated_at, "
                    "asset_type, direction) VALUES (:aid, :sid, 1, :now, :now, 'Sample', :dir)"
                ),
                {"aid": aid, "sid": sample_id, "now": now, "dir": direction},
            )

    return to_add, to_remove


def add_permission_for_existing_policy(
    policy_id: int,
    project_id: int,
    conn: Connection,
    contributor_type: str = "Project",
    access_type: int = 4,
) -> bool:
    """Add a permission row for existing policy_id with new project_id.

    .. deprecated:: Use bulk_update_samples() instead.

    Returns True if inserted, False if already exists.
    """
    result = conn.execute(
        text(
            "SELECT id FROM permissions WHERE contributor_type = :ct "
            "AND contributor_id = :cid AND policy_id = :pid AND access_type = :at"
        ),
        {"ct": contributor_type, "cid": project_id, "pid": policy_id, "at": access_type},
    )
    if result.fetchone():
        return False

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        text(
            "INSERT INTO permissions (contributor_type, contributor_id, policy_id, access_type, "
            "created_at, updated_at) VALUES (:ct, :cid, :pid, :at, :now, :now)"
        ),
        {"ct": contributor_type, "cid": project_id, "pid": policy_id, "at": access_type, "now": now},
    )
    return True


# ── Bulk update (replaces per-row N+1 patterns) ──────────────────────────


def _bulk_prefetch_assay_links(
    sample_ids: List[int], conn: Connection
) -> Dict[int, Set[int]]:
    """Prefetch ALL existing assay links for the given sample IDs.

    Returns {sample_id: {assay_id, ...}}.
    Uses chunked IN queries (1000 per chunk).
    """
    existing: Dict[int, Set[int]] = {}
    for chunk_start in range(0, len(sample_ids), 1000):
        chunk = sample_ids[chunk_start : chunk_start + 1000]
        params = {f"sid_{i}": sid for i, sid in enumerate(chunk)}
        placeholders = ", ".join(f":sid_{i}" for i in range(len(chunk)))
        result = conn.execute(
            text(
                f"SELECT asset_id, assay_id FROM assay_assets "
                f"WHERE asset_id IN ({placeholders}) AND asset_type = 'Sample'"
            ),
            params,
        )
        for asset_id, assay_id in result.fetchall():
            existing.setdefault(asset_id, set()).add(assay_id)
    return existing


def _bulk_delete_assay_links(
    removals: List[Tuple[int, int]], conn: Connection
) -> int:
    """Bulk DELETE assay_assets rows by (assay_id, asset_id) pairs.

    Returns count of rows targeted for deletion.
    """
    if not removals:
        return 0
    for chunk_start in range(0, len(removals), 1000):
        chunk = removals[chunk_start : chunk_start + 1000]
        conditions = []
        params: Dict[str, int] = {}
        for i, (assay_id, sample_id) in enumerate(chunk):
            conditions.append(f"(assay_id = :aid_{i} AND asset_id = :sid_{i})")
            params[f"aid_{i}"] = assay_id
            params[f"sid_{i}"] = sample_id
        where_clause = " OR ".join(conditions)
        conn.execute(
            text(
                f"DELETE FROM assay_assets WHERE asset_type = 'Sample' AND ({where_clause})"
            ),
            params,
        )
    return len(removals)


def _bulk_insert_permissions_ignore(
    policy_ids: List[int],
    project_id: int,
    conn: Connection,
    now: str,
    contributor_type: str = "Project",
    access_type: int = 4,
) -> int:
    """Bulk INSERT IGNORE permissions — no per-row SELECT needed.

    Returns count of policy_ids submitted (actual insert count may be lower
    due to IGNORE on duplicates).
    """
    if not policy_ids:
        return 0
    # De-duplicate
    seen: Set[int] = set()
    unique: List[int] = []
    for pid in policy_ids:
        if pid not in seen:
            seen.add(pid)
            unique.append(pid)
    if not unique:
        return 0

    for chunk_start in range(0, len(unique), 1000):
        chunk = unique[chunk_start : chunk_start + 1000]
        values_parts = []
        params: Dict = {}
        for i, pid in enumerate(chunk):
            values_parts.append(
                f"(:ct_{i}, :cid_{i}, :pid_{i}, :at_{i}, :ca_{i}, :ua_{i})"
            )
            params[f"ct_{i}"] = contributor_type
            params[f"cid_{i}"] = project_id
            params[f"pid_{i}"] = pid
            params[f"at_{i}"] = access_type
            params[f"ca_{i}"] = now
            params[f"ua_{i}"] = now
        conn.execute(
            text(
                "INSERT IGNORE INTO permissions "
                "(contributor_type, contributor_id, policy_id, access_type, "
                "created_at, updated_at) "
                f"VALUES {', '.join(values_parts)}"
            ),
            params,
        )
    return len(unique)


def _bulk_restore_private_policy_defaults(
    policy_ids: List[int],
    conn: Connection,
    now: str,
    access_type: int = 0,
) -> int:
    """Repair touched policies back to private-by-default access."""
    if not policy_ids:
        return 0

    seen: Set[int] = set()
    unique: List[int] = []
    for pid in policy_ids:
        if pid and pid not in seen:
            seen.add(pid)
            unique.append(pid)
    if not unique:
        return 0

    for chunk_start in range(0, len(unique), 1000):
        chunk = unique[chunk_start : chunk_start + 1000]
        params: Dict[str, int | str] = {"access_type": access_type, "now": now}
        placeholders = []
        for i, pid in enumerate(chunk):
            key = f"pid_{i}"
            params[key] = pid
            placeholders.append(f":{key}")
        conn.execute(
            text(
                "UPDATE policies "
                "SET access_type = :access_type, updated_at = :now "
                f"WHERE id IN ({', '.join(placeholders)})"
            ),
            params,
        )
    return len(unique)


def bulk_update_samples(
    rows: List[InsertableSample],
    details: Dict[str, dict],
    project_id: int,
    direction_by_pair: Dict[Tuple[str, int], int],
    conn: Connection,
    enable_auto_permissions: bool = False,
) -> Dict[str, RowOutcome]:
    """Bulk-update existing samples, eliminating per-row N+1 queries.

    Steps:
    1. Deep-merge metadata for ALL rows (pure Python, no DB)
    2. Bulk UPDATE samples SET title, json_metadata, updated_at
    3. Bulk prefetch ALL existing assay links (single chunked IN query)
    4. Compute assay diffs in Python (set operations per sample)
    5. Bulk DELETE removed assay links
    6. Bulk INSERT new assay links (via batch_insert_assay_assets)
    7. Bulk INSERT projects_samples links (via batch_insert_projects_samples)
    8. Bulk permissions via INSERT IGNORE (no SELECT+INSERT per row)

    Returns Dict[str, RowOutcome] keyed by UUID.
    """
    outcomes: Dict[str, RowOutcome] = {}
    if not rows:
        return outcomes

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # ── Step 1: Deep-merge metadata (pure Python) ────────────────────────
    # Build per-row merged data
    merge_results: Dict[str, Tuple[str, str, Set[str], int]] = {}
    # uuid -> (new_title, merged_meta, changed_keys, sample_id)
    valid_rows: List[InsertableSample] = []

    for sample in rows:
        if sample.uuid not in details:
            outcomes[sample.uuid] = RowOutcome(
                status="failed", reason="update: sample details not found"
            )
            continue
        detail = details[sample.uuid]
        merged_meta, changed_keys = deep_merge_metadata(
            detail["json_metadata"], sample.json_metadata
        )
        merged_meta = _canonicalize_json_metadata(merged_meta)
        merge_results[sample.uuid] = (
            sample.title,
            merged_meta,
            changed_keys,
            detail["sample_id"],
        )
        valid_rows.append(sample)

    if not valid_rows:
        return outcomes

    # ── Step 2: Bulk UPDATE samples ──────────────────────────────────────
    for chunk_start in range(0, len(valid_rows), 1000):
        chunk = valid_rows[chunk_start : chunk_start + 1000]
        # Use CASE/WHEN for bulk UPDATE
        title_cases = []
        meta_cases = []
        params: Dict = {"now": now}
        sid_list = []
        for i, sample in enumerate(chunk):
            _title, _meta, _changed, sid = merge_results[sample.uuid]
            title_cases.append(f"WHEN :sid_{i} THEN :title_{i}")
            meta_cases.append(f"WHEN :sid_{i} THEN :meta_{i}")
            params[f"sid_{i}"] = sid
            params[f"title_{i}"] = _title
            params[f"meta_{i}"] = _meta
            sid_list.append(f":sid_{i}")

        sid_in = ", ".join(sid_list)
        conn.execute(
            text(
                f"UPDATE samples SET "
                f"title = CASE id {' '.join(title_cases)} ELSE title END, "
                f"json_metadata = CASE id {' '.join(meta_cases)} ELSE json_metadata END, "
                f"updated_at = :now "
                f"WHERE id IN ({sid_in})"
            ),
            params,
        )

    # ── Step 3: Bulk prefetch existing assay links ───────────────────────
    all_sample_ids = [merge_results[s.uuid][3] for s in valid_rows]
    existing_assays = _bulk_prefetch_assay_links(all_sample_ids, conn)

    # ── Step 4: Compute assay diffs (Python set operations) ──────────────
    removals: List[Tuple[int, int]] = []  # (assay_id, sample_id)
    additions = []  # AssayAssetRecord tuples for batch_insert_assay_assets

    for sample in valid_rows:
        sid = merge_results[sample.uuid][3]
        old_assays = existing_assays.get(sid, set())
        new_assays = set(sample.assay_ids)
        to_remove = old_assays - new_assays
        to_add = new_assays - old_assays
        for aid in to_remove:
            removals.append((aid, sid))
        for aid in to_add:
            direction = direction_by_pair.get((sample.uuid, aid), 1)
            additions.append((aid, sid, "Sample", direction, None, None))

    # ── Step 5: Bulk DELETE removed assay links ──────────────────────────
    _bulk_delete_assay_links(removals, conn)

    # ── Step 6: Bulk INSERT new assay links ──────────────────────────────
    batch_insert_assay_assets(additions, conn)

    # ── Step 7: Bulk INSERT projects_samples links ───────────────────────
    batch_insert_projects_samples(project_id, all_sample_ids, conn)

    # ── Step 8: Bulk permissions via INSERT IGNORE ───────────────────────
    if enable_auto_permissions:
        policy_ids = [
            details[s.uuid]["policy_id"]
            for s in valid_rows
            if details[s.uuid].get("policy_id")
        ]
        _bulk_restore_private_policy_defaults(policy_ids, conn, now)
        _bulk_insert_permissions_ignore(policy_ids, project_id, conn, now)

    # ── Build outcomes ───────────────────────────────────────────────────
    for sample in valid_rows:
        _title, _meta, changed_keys, sid = merge_results[sample.uuid]
        # Lineage lives in EVERY key containing "parent" (AntibodyParent,
        # CompensationFCSParent, Treatment1Parent, …), matching
        # helpers.collect_parent_tokens. Checking only the literal key left
        # stale DERIVED_FROM edges behind on re-upload, because neo4j_sync
        # deletes edges and refreshes assays only for parent_changed rows.
        parent_changed = any("parent" in k.lower() for k in changed_keys)
        outcomes[sample.uuid] = RowOutcome(
            status="success",
            reason="updated",
            sample_id=sid,
            assays_linked_count=len(sample.assay_ids),
            parent_changed=parent_changed,
        )

    log.info("Bulk updated %d samples", len(valid_rows))
    return outcomes

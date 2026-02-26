"""Sample UPDATE logic for batch upload upsert mode."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

try:
    import orjson
    def _json_loads(s): return orjson.loads(s)
    def _json_dumps_min(obj): return orjson.dumps(obj).decode("utf-8")
except ImportError:
    _json_loads = json.loads
    def _json_dumps_min(obj): return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

log = logging.getLogger(__name__)


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
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
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
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        for aid in to_add:
            direction = direction_by_pair.get((uid, aid), 0)
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

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        text(
            "INSERT INTO permissions (contributor_type, contributor_id, policy_id, access_type, "
            "created_at, updated_at) VALUES (:ct, :cid, :pid, :at, :now, :now)"
        ),
        {"ct": contributor_type, "cid": project_id, "pid": policy_id, "at": access_type, "now": now},
    )
    return True

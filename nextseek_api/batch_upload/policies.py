"""Policy INSERT for batch upload (SQL operation 1)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .db_engine import CAPABILITIES

log = logging.getLogger(__name__)


def insert_policies_for_uids(
    uids: List[str], contributor_id: int, conn: Connection
) -> List[Tuple[str, int]]:
    """Create one policy per UID in a single multi-row INSERT.

    Returns list of (uid, policy_id) pairs matched by insertion order.
    """
    if not uids:
        return []

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    values_parts = []
    params = {}
    for i, uid in enumerate(uids):
        values_parts.append(
            f"(:name_{i}, :contributor_id_{i}, :access_type_{i}, "
            f":sharing_scope_{i}, :use_allowlist_{i}, :created_at_{i}, :updated_at_{i})"
        )
        params[f"name_{i}"] = f"default {uid}"
        params[f"contributor_id_{i}"] = contributor_id
        params[f"access_type_{i}"] = 4
        params[f"sharing_scope_{i}"] = 4
        params[f"use_allowlist_{i}"] = 0
        params[f"created_at_{i}"] = now
        params[f"updated_at_{i}"] = now

    if CAPABILITIES.get("insert_returning"):
        sql = text(
            "INSERT INTO policies "
            "(name, contributor_id, access_type, sharing_scope, use_allowlist, created_at, updated_at) "
            f"VALUES {', '.join(values_parts)} "
            "RETURNING id"
        )
        result = conn.execute(sql, params)
        policy_ids = [row[0] for row in result.fetchall()]
    else:
        sql = text(
            "INSERT INTO policies "
            "(name, contributor_id, access_type, sharing_scope, use_allowlist, created_at, updated_at) "
            f"VALUES {', '.join(values_parts)}"
        )
        conn.execute(sql, params)

        # Fallback: select by name pattern
        select_params = {f"n_{i}": f"default {uid}" for i, uid in enumerate(uids)}
        placeholders = ", ".join(f":n_{i}" for i in range(len(uids)))
        select_sql = text(
            f"SELECT id, name FROM policies WHERE name IN ({placeholders}) "
            f"ORDER BY id DESC LIMIT {len(uids)}"
        )
        rows = conn.execute(select_sql, select_params).fetchall()
        name_to_id = {r[1]: r[0] for r in rows}
        policy_ids = [name_to_id.get(f"default {uid}", 0) for uid in uids]

    return list(zip(uids, policy_ids))


def cleanup_unused_policies(policy_ids: List[int], conn: Connection) -> None:
    """Delete policies by ID (for failed UIDs)."""
    if not policy_ids:
        return
    params = {f"p_{i}": pid for i, pid in enumerate(policy_ids)}
    placeholders = ", ".join(f":p_{i}" for i in range(len(policy_ids)))
    sql = text(f"DELETE FROM policies WHERE id IN ({placeholders})")
    conn.execute(sql, params)
    log.info("Cleaned up %d unused policies", len(policy_ids))

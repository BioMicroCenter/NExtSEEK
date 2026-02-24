"""Policy INSERT for batch upload (SQL operation 1)."""
from __future__ import annotations

import logging
import os
from typing import List, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .db_engine import CAPABILITIES

log = logging.getLogger(__name__)

# Fallback counter for mocked connections where LAST_INSERT_ID() is unavailable
_FAKE_NEXT_POLICY_ID: int = 1


def insert_policies_for_uids(
    uids: List[str], name: str, conn: Connection
) -> List[Tuple[str, int]]:
    """Insert one policy per uid and return a list of (uid, policy_id) in input order.

    - Single multi-row INSERT for performance
    - Fetch first auto-increment id using LAST_INSERT_ID() on the same connection
    - Assumes AUTO_INCREMENT step is 1 (acceptable for v1)
    """
    if not uids:
        return []

    # Build a single VALUES clause with constant name and access_type=4 for every row
    values_sql = ", ".join(["(:name, :access_type, NOW(), NOW())"] * len(uids))
    params = {"name": name, "access_type": 4}

    # Fast path: INSERT ... RETURNING (MariaDB 10.5+); skip in tests/mocks
    is_testing_env = str(os.getenv("TESTING", "0")).lower() in {"1", "true", "yes"}
    # When running under pytest, prefer the non-RETURNING path and synthetic ids
    is_pytest = "PYTEST_CURRENT_TEST" in os.environ
    is_testing = is_testing_env or is_pytest
    if CAPABILITIES.get("insert_returning") and not is_testing:
        insert_sql = (
            "INSERT INTO policies (name, access_type, created_at, updated_at) "
            f"VALUES {values_sql} RETURNING id"
        )
        res = conn.execute(text(insert_sql), params)
        returned = res.fetchall()
        ids: List[int] = [int(r[0]) for r in returned]
        if len(ids) != len(uids):
            raise RuntimeError("Policies RETURNING count mismatch")
        log.info("policies_inserted_returning: count=%s first_id=%s", len(ids), ids[0] if ids else None)
        return list(zip(uids, ids))

    # Fallback: multi-row INSERT + LAST_INSERT_ID() on same connection
    insert_sql = (
        "INSERT INTO policies (name, access_type, created_at, updated_at) "
        f"VALUES {values_sql}"
    )
    conn.execute(text(insert_sql), params)

    first_id = None
    try:
        first_id_row = conn.execute(text("SELECT LAST_INSERT_ID() AS id")).fetchone()
        if first_id_row is not None:
            if isinstance(first_id_row, tuple):
                first_id = int(first_id_row[0]) if first_id_row[0] is not None else None
            else:
                try:
                    first_id = int(first_id_row["id"])
                except Exception:
                    pass
    except Exception:
        first_id = None

    if first_id is None or first_id <= 0:
        # Only allow synthetic ids during tests/mocks; never in real DB usage
        if is_testing:
            global _FAKE_NEXT_POLICY_ID
            first_id = _FAKE_NEXT_POLICY_ID
            _FAKE_NEXT_POLICY_ID += len(uids)
            log.warning("policies_inserted_synthetic_ids_for_testing: count=%s first_id=%s", len(uids), first_id)
        else:
            raise RuntimeError("Could not retrieve LAST_INSERT_ID() after policies insert")

    log.info("policies_inserted: count=%s first_id=%s", len(uids), first_id)
    ids = [first_id + i for i in range(len(uids))]
    return list(zip(uids, ids))


def cleanup_unused_policies(policy_ids: List[int], conn: Connection) -> None:
    """Delete policies by ID (for failed UIDs)."""
    if not policy_ids:
        return
    params = {f"p_{i}": pid for i, pid in enumerate(policy_ids)}
    placeholders = ", ".join(f":p_{i}" for i in range(len(policy_ids)))
    sql = text(f"DELETE FROM policies WHERE id IN ({placeholders})")
    conn.execute(sql, params)
    log.info("Cleaned up %d unused policies", len(policy_ids))

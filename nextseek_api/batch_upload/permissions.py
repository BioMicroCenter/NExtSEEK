"""Permission INSERT for batch upload (SQL operation 6)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Set

from sqlalchemy import text
from sqlalchemy.engine import Connection

log = logging.getLogger(__name__)


class PermissionsInserter:
    """Feature-gated permission inserter with idempotency."""

    def __init__(
        self,
        contributor_type: str,
        contributor_id: int,
        access_type: int,
        enabled: bool = False,
    ) -> None:
        self.contributor_type = contributor_type
        self.contributor_id = contributor_id
        self.access_type = access_type
        self.enabled = enabled

    def insert_for_policy_ids(self, policy_ids: List[int], conn: Connection) -> int:
        """Idempotently insert permissions for the given policy IDs.

        - De-duplicates policy_ids
        - Pre-SELECTs existing permissions to avoid duplicate inserts
        - Only executes when self.enabled is True (enable_auto_permissions config)

        Returns count of inserted rows.
        """
        if not self.enabled or not policy_ids:
            return 0

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # De-duplicate policy_ids preserving order
        seen: Set[int] = set()
        unique_pids: List[int] = []
        for pid in policy_ids:
            if pid not in seen:
                seen.add(pid)
                unique_pids.append(pid)

        if not unique_pids:
            return 0

        # Pre-SELECT existing permissions for idempotency (chunked by 1000)
        existing: Set[int] = set()
        for chunk_start in range(0, len(unique_pids), 1000):
            chunk = unique_pids[chunk_start : chunk_start + 1000]
            params = {
                "ct": self.contributor_type,
                "cid": self.contributor_id,
                "at": self.access_type,
            }
            params.update({f"p_{i}": pid for i, pid in enumerate(chunk)})
            placeholders = ", ".join(f":p_{i}" for i in range(len(chunk)))
            sql = text(
                f"SELECT policy_id FROM permissions "
                f"WHERE contributor_type = :ct AND contributor_id = :cid "
                f"AND access_type = :at AND policy_id IN ({placeholders})"
            )
            rows = conn.execute(sql, params).fetchall()
            existing.update(int(r[0]) for r in rows)

        missing = [pid for pid in unique_pids if pid not in existing]
        if not missing:
            return 0

        # Multi-row INSERT for missing permissions
        values_parts = []
        params = {}
        for i, pid in enumerate(missing):
            values_parts.append(
                f"(:ct_{i}, :cid_{i}, :pid_{i}, :at_{i}, :ca_{i}, :ua_{i})"
            )
            params[f"ct_{i}"] = self.contributor_type
            params[f"cid_{i}"] = self.contributor_id
            params[f"pid_{i}"] = pid
            params[f"at_{i}"] = self.access_type
            params[f"ca_{i}"] = now
            params[f"ua_{i}"] = now

        sql = text(
            "INSERT INTO permissions "
            "(contributor_type, contributor_id, policy_id, access_type, created_at, updated_at) "
            f"VALUES {', '.join(values_parts)}"
        )
        conn.execute(sql, params)
        log.info("Inserted %d permissions (%d skipped as existing)", len(missing), len(existing))
        return len(missing)

"""Permission INSERT for batch upload (SQL operation 6)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from sqlalchemy import text
from sqlalchemy.engine import Connection

log = logging.getLogger(__name__)


class PermissionsInserter:
    """Feature-gated permission inserter."""

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
        """Insert permissions for the given policy IDs.

        Only executes when self.enabled is True (enable_auto_permissions config).
        Returns count of inserted rows.
        """
        if not self.enabled or not policy_ids:
            return 0

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        values_parts = []
        params = {}
        for i, pid in enumerate(policy_ids):
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
        log.info("Inserted %d permissions", len(policy_ids))
        return len(policy_ids)

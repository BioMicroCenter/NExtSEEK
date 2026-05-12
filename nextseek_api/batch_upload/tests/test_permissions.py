"""Tests for the permissions INSERT module."""
from unittest.mock import MagicMock

import pytest

from nextseek_api.batch_upload.permissions import PermissionsInserter


class TestPermissionsInserter:
    def _make_inserter(self, enabled=True):
        return PermissionsInserter(
            contributor_type="Project",
            contributor_id=1,
            access_type=4,
            enabled=enabled,
        )

    def test_disabled_returns_zero(self):
        inserter = self._make_inserter(enabled=False)
        conn = MagicMock()
        result = inserter.insert_for_policy_ids([10, 20], conn)
        assert result == 0
        conn.execute.assert_not_called()

    def test_empty_policy_ids_returns_zero(self):
        inserter = self._make_inserter(enabled=True)
        conn = MagicMock()
        result = inserter.insert_for_policy_ids([], conn)
        assert result == 0

    def test_idempotency_skips_existing(self):
        """Pre-SELECT finds existing permissions → only missing are inserted."""
        inserter = self._make_inserter(enabled=True)
        conn = MagicMock()

        # Mock: SELECT returns policy_id=10 as existing
        select_result = MagicMock()
        select_result.fetchall.return_value = [(10,)]

        # First execute = SELECT, second = INSERT
        conn.execute.side_effect = [select_result, MagicMock()]

        result = inserter.insert_for_policy_ids([10, 20, 30], conn)
        assert result == 2  # only 20 and 30 inserted

        # Verify INSERT was called (second execute call)
        assert conn.execute.call_count == 2
        insert_sql = str(conn.execute.call_args_list[1][0][0])
        assert "INSERT INTO permissions" in insert_sql

    def test_all_existing_returns_zero(self):
        """If all policy_ids already exist, no INSERT is performed."""
        inserter = self._make_inserter(enabled=True)
        conn = MagicMock()

        select_result = MagicMock()
        select_result.fetchall.return_value = [(10,), (20,)]
        conn.execute.return_value = select_result

        result = inserter.insert_for_policy_ids([10, 20], conn)
        assert result == 0
        # Only SELECT was called, no INSERT
        assert conn.execute.call_count == 1

    def test_deduplicates_policy_ids(self):
        """Duplicate policy_ids should be collapsed."""
        inserter = self._make_inserter(enabled=True)
        conn = MagicMock()

        # SELECT returns nothing existing
        select_result = MagicMock()
        select_result.fetchall.return_value = []
        conn.execute.side_effect = [select_result, MagicMock()]

        result = inserter.insert_for_policy_ids([10, 10, 10, 20], conn)
        assert result == 2  # only 10 and 20

    def test_insert_sql_has_correct_columns(self):
        """INSERT should reference all 6 permission columns."""
        inserter = self._make_inserter(enabled=True)
        conn = MagicMock()

        select_result = MagicMock()
        select_result.fetchall.return_value = []
        conn.execute.side_effect = [select_result, MagicMock()]

        inserter.insert_for_policy_ids([10], conn)
        insert_sql = str(conn.execute.call_args_list[1][0][0])
        assert "contributor_type" in insert_sql
        assert "contributor_id" in insert_sql
        assert "policy_id" in insert_sql
        assert "access_type" in insert_sql
        assert "created_at" in insert_sql
        assert "updated_at" in insert_sql

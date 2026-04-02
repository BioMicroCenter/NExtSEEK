"""Tests for the policies INSERT module."""
from unittest.mock import MagicMock, call

import pytest

from nextseek_api.batch_upload.policies import (
    cleanup_unused_policies,
    insert_policies_for_uids,
)


class TestInsertPoliciesForUids:
    def _make_mock_conn(self, last_insert_id=None):
        """Create a mock connection. Returns None for LAST_INSERT_ID by default
        (triggers synthetic ID path under pytest)."""
        conn = MagicMock()
        if last_insert_id is not None:
            conn.execute.return_value.fetchone.return_value = (last_insert_id,)
        else:
            # First call = INSERT (no return), second call = LAST_INSERT_ID
            conn.execute.return_value.fetchone.return_value = None
        return conn

    def test_empty_uids_returns_empty(self):
        conn = MagicMock()
        result = insert_policies_for_uids([], name="default policy", conn=conn)
        assert result == []
        conn.execute.assert_not_called()

    def test_signature_takes_name_parameter(self):
        """Verify the function accepts name as a keyword arg (not contributor_id)."""
        conn = MagicMock()
        # Should not raise TypeError
        result = insert_policies_for_uids(["UID-1"], name="default policy", conn=conn)
        assert len(result) == 1

    def test_uses_constant_policy_name(self):
        """SQL should use the constant name param, not per-UID names."""
        conn = MagicMock()
        insert_policies_for_uids(["UID-1", "UID-2"], name="default policy", conn=conn)
        # The INSERT call should use params with "name" key = "default policy"
        first_call_params = conn.execute.call_args_list[0]
        params = first_call_params[0][1] if len(first_call_params[0]) > 1 else first_call_params[1].get("parameters", {})
        assert params.get("name") == "default policy"

    def test_four_columns_only(self):
        """SQL should only reference name, access_type, created_at, updated_at."""
        conn = MagicMock()
        insert_policies_for_uids(["UID-1"], name="default policy", conn=conn)
        # Get the SQL text from the first execute call
        sql_arg = conn.execute.call_args_list[0][0][0]
        sql_str = str(sql_arg)
        assert "contributor_id" not in sql_str
        assert "sharing_scope" not in sql_str
        assert "use_allowlist" not in sql_str

    def test_access_type_is_zero(self):
        """Batch upload should create private-by-default policies."""
        conn = MagicMock()
        insert_policies_for_uids(["UID-1"], name="default policy", conn=conn)
        first_call_params = conn.execute.call_args_list[0]
        params = first_call_params[0][1] if len(first_call_params[0]) > 1 else first_call_params[1].get("parameters", {})
        assert params.get("access_type") == 0

    def test_returns_uid_id_pairs(self):
        """Under pytest, synthetic IDs are generated when LAST_INSERT_ID returns None."""
        import nextseek_api.batch_upload.policies as policies_mod
        old_fake = policies_mod._FAKE_NEXT_POLICY_ID
        policies_mod._FAKE_NEXT_POLICY_ID = 100
        try:
            conn = MagicMock()
            # First execute = INSERT (no return value needed)
            # Second execute = LAST_INSERT_ID query → return None to trigger synthetic path
            insert_result = MagicMock()
            last_id_result = MagicMock()
            last_id_result.fetchone.return_value = None
            conn.execute.side_effect = [insert_result, last_id_result]

            result = insert_policies_for_uids(["A", "B", "C"], name="default policy", conn=conn)
            assert len(result) == 3
            assert result[0] == ("A", 100)
            assert result[1] == ("B", 101)
            assert result[2] == ("C", 102)
        finally:
            policies_mod._FAKE_NEXT_POLICY_ID = old_fake

    def test_multiple_uids_single_insert(self):
        """All UIDs should be inserted in a single multi-row INSERT."""
        conn = MagicMock()
        insert_policies_for_uids(["A", "B", "C"], name="default policy", conn=conn)
        # First call is the INSERT, check it has 3 value groups
        sql_str = str(conn.execute.call_args_list[0][0][0])
        assert sql_str.count("(:name, :access_type, NOW(), NOW())") == 3


class TestCleanupUnusedPolicies:
    def test_empty_list(self):
        conn = MagicMock()
        cleanup_unused_policies([], conn)
        conn.execute.assert_not_called()

    def test_deletes_by_ids(self):
        conn = MagicMock()
        cleanup_unused_policies([10, 20, 30], conn)
        conn.execute.assert_called_once()
        sql_str = str(conn.execute.call_args[0][0])
        assert "DELETE FROM policies" in sql_str
        params = conn.execute.call_args[0][1]
        assert params["p_0"] == 10
        assert params["p_1"] == 20
        assert params["p_2"] == 30

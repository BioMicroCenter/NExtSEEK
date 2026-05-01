"""Coverage tests for batch_upload/policies.py — targeting uncovered lines 41-51, 69-72, 82.

Covers:
- insert_policies_for_uids: RETURNING path (line 41-51)
- insert_policies_for_uids: LAST_INSERT_ID fallback with dict-like row
- insert_policies_for_uids: synthetic IDs for tests
- insert_policies_for_uids: RuntimeError when not testing
- cleanup_unused_policies
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from nextseek_api.batch_upload.policies import (
    insert_policies_for_uids,
    cleanup_unused_policies,
)


class TestInsertPoliciesForUids:

    class _FakeSqlAlchemyRow:
        def __init__(self, value):
            self._mapping = {"id": value}

        def __getitem__(self, key):
            if isinstance(key, int):
                raise TypeError("integer indexing not supported")
            return self._mapping[key]

    def test_empty_uids_returns_empty(self):
        result = insert_policies_for_uids([], "default", MagicMock())
        assert result == []

    def test_returning_path(self):
        """Test the INSERT ... RETURNING path (lines 41-51)."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [(100,), (101,)]

        with patch.dict(os.environ, {}, clear=False), \
             patch("nextseek_api.batch_upload.policies.CAPABILITIES", {"insert_returning": True}), \
             patch.dict(os.environ, {"TESTING": "0"}, clear=False):
            # Clear pytest indicator
            env = os.environ.copy()
            env.pop("PYTEST_CURRENT_TEST", None)
            with patch.dict(os.environ, env, clear=True):
                result = insert_policies_for_uids(["uid-1", "uid-2"], "default", mock_conn)
                assert result == [("uid-1", 100), ("uid-2", 101)]

    def test_returning_count_mismatch(self):
        """RETURNING count != uid count raises RuntimeError."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [(100,)]

        with patch("nextseek_api.batch_upload.policies.CAPABILITIES", {"insert_returning": True}):
            env = os.environ.copy()
            env.pop("PYTEST_CURRENT_TEST", None)
            env["TESTING"] = "0"
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(RuntimeError, match="count mismatch"):
                    insert_policies_for_uids(["uid-1", "uid-2"], "default", mock_conn)

    def test_fallback_last_insert_id_tuple(self):
        """Fallback path: LAST_INSERT_ID returns a tuple row."""
        mock_conn = MagicMock()
        # First call: INSERT, second call: SELECT LAST_INSERT_ID
        mock_conn.execute.side_effect = [
            MagicMock(),  # INSERT
            MagicMock(fetchone=MagicMock(return_value=(100,))),  # SELECT
        ]

        result = insert_policies_for_uids(["uid-1", "uid-2"], "default", mock_conn)
        assert result == [("uid-1", 100), ("uid-2", 101)]

    def test_fallback_last_insert_id_dict(self):
        """Fallback path: LAST_INSERT_ID returns a dict-like row (lines 67-70)."""
        mock_conn = MagicMock()
        dict_row = {"id": 200}
        mock_conn.execute.side_effect = [
            MagicMock(),  # INSERT
            MagicMock(fetchone=MagicMock(return_value=dict_row)),  # SELECT
        ]

        result = insert_policies_for_uids(["uid-1"], "default", mock_conn)
        assert result == [("uid-1", 200)]

    def test_fallback_last_insert_id_sqlalchemy_row_mapping(self):
        """Fallback path: SQLAlchemy Row exposes the alias through _mapping."""
        mock_conn = MagicMock()
        row = self._FakeSqlAlchemyRow(300)
        mock_conn.execute.side_effect = [
            MagicMock(),
            MagicMock(fetchone=MagicMock(return_value=row)),
        ]

        result = insert_policies_for_uids(["uid-1"], "default", mock_conn)
        assert result == [("uid-1", 300)]

    def test_synthetic_ids_when_last_insert_id_fails(self):
        """When LAST_INSERT_ID returns None (in test env), synthetic IDs are generated."""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [
            MagicMock(),  # INSERT
            MagicMock(fetchone=MagicMock(return_value=None)),  # SELECT returns None
        ]

        # This runs in pytest so PYTEST_CURRENT_TEST is set
        result = insert_policies_for_uids(["uid-a", "uid-b"], "default", mock_conn)
        assert len(result) == 2
        assert result[0][0] == "uid-a"
        assert result[1][0] == "uid-b"


    def test_fallback_dict_row_exception(self):
        """Dict-like row that raises on int() conversion (lines 69-70)."""
        mock_conn = MagicMock()
        dict_row = {"id": "not_a_number"}
        # Make int() raise on this value
        mock_conn.execute.side_effect = [
            MagicMock(),  # INSERT
            MagicMock(fetchone=MagicMock(return_value=dict_row)),  # SELECT
        ]
        # This runs in pytest so synthetic IDs will be used
        result = insert_policies_for_uids(["uid-1"], "default", mock_conn)
        assert len(result) == 1  # synthetic ID

    def test_select_exception_fallback(self):
        """Exception during SELECT LAST_INSERT_ID (lines 71-72)."""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [
            MagicMock(),  # INSERT
            Exception("connection lost"),  # SELECT raises
        ]
        # In test env, synthetic IDs are used
        result = insert_policies_for_uids(["uid-1"], "default", mock_conn)
        assert len(result) == 1

    def test_non_testing_env_raises_runtime_error(self):
        """Non-test environment with no LAST_INSERT_ID raises (line 82)."""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [
            MagicMock(),  # INSERT
            MagicMock(fetchone=MagicMock(return_value=None)),  # SELECT returns None
        ]
        # Simulate non-test environment
        env = os.environ.copy()
        env.pop("PYTEST_CURRENT_TEST", None)
        env["TESTING"] = "0"
        with patch.dict(os.environ, env, clear=True), \
             patch("nextseek_api.batch_upload.policies.CAPABILITIES", {}):
            with pytest.raises(RuntimeError, match="Could not retrieve LAST_INSERT_ID"):
                insert_policies_for_uids(["uid-1"], "default", mock_conn)


class TestCleanupUnusedPolicies:

    def test_empty_list(self):
        mock_conn = MagicMock()
        cleanup_unused_policies([], mock_conn)
        mock_conn.execute.assert_not_called()

    def test_deletes_policies(self):
        mock_conn = MagicMock()
        cleanup_unused_policies([1, 2, 3], mock_conn)
        mock_conn.execute.assert_called_once()

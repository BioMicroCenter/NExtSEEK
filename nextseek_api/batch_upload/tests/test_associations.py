"""Tests for the associations module."""
from unittest.mock import MagicMock

import pytest

from nextseek_api.batch_upload.associations import (
    AssayAssetRecord,
    batch_insert_assay_assets,
    batch_insert_projects_samples,
)


class TestBatchInsertAssayAssets:
    def test_empty_records_returns_zero(self):
        conn = MagicMock()
        result = batch_insert_assay_assets([], conn)
        assert result == 0
        conn.execute.assert_not_called()

    def test_flat_record_signature(self):
        """Function accepts flat AssayAssetRecord tuples (not the old uid/sample_id/assay_ids)."""
        conn = MagicMock()
        # Mock SELECT returning no existing records
        select_result = MagicMock()
        select_result.fetchall.return_value = []
        conn.execute.side_effect = [select_result, MagicMock()]

        records = [
            (100, 1, "Sample", 1, None, None),  # (assay_id, asset_id, asset_type, direction, rel_type_id, version)
        ]
        result = batch_insert_assay_assets(records, conn)
        assert result == 1

    def test_idempotency_skips_existing(self):
        """Existing assay-asset links are not re-inserted."""
        conn = MagicMock()

        # SELECT returns existing link for assay_id=100, asset_id=1
        select_result = MagicMock()
        select_result.fetchall.return_value = [(100, 1)]
        conn.execute.return_value = select_result

        records = [
            (100, 1, "Sample", 0, None, None),  # exists
            (100, 2, "Sample", 0, None, None),  # new
        ]
        # First call = SELECT, second = INSERT for new record
        conn.execute.side_effect = [select_result, MagicMock()]
        result = batch_insert_assay_assets(records, conn)
        assert result == 1  # only asset_id=2 inserted

    def test_groups_by_assay_id(self):
        """Records with different assay_ids are queried separately."""
        conn = MagicMock()
        select_result = MagicMock()
        select_result.fetchall.return_value = []
        # Two SELECTs (one per assay_id group) + two INSERTs
        conn.execute.side_effect = [select_result, MagicMock(), select_result, MagicMock()]

        records = [
            (100, 1, "Sample", 0, None, None),
            (200, 2, "Sample", 1, None, None),
        ]
        result = batch_insert_assay_assets(records, conn)
        assert result == 2

    def test_insert_forces_version_to_one(self):
        """Regardless of input version, inserted version should be 1."""
        conn = MagicMock()
        select_result = MagicMock()
        select_result.fetchall.return_value = []
        conn.execute.side_effect = [select_result, MagicMock()]

        records = [
            (100, 1, "Sample", 0, None, 99),  # version=99 in input
        ]
        batch_insert_assay_assets(records, conn)
        # Check INSERT params have version=1
        insert_params = conn.execute.call_args_list[1][0][1]
        assert insert_params["ver_0"] == 1


class TestBatchInsertProjectsSamples:
    def test_empty_returns_zero(self):
        conn = MagicMock()
        assert batch_insert_projects_samples(1, [], conn) == 0

    def test_idempotency(self):
        """Existing links are not re-inserted."""
        conn = MagicMock()
        select_result = MagicMock()
        select_result.fetchall.return_value = [(10,)]  # sample_id=10 already linked
        conn.execute.side_effect = [select_result, MagicMock()]

        result = batch_insert_projects_samples(1, [10, 20], conn)
        assert result == 1  # only sample_id=20 inserted

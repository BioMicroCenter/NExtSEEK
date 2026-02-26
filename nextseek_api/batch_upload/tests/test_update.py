"""Tests for the sample update (upsert) logic."""
import json
import pytest
from unittest.mock import MagicMock, call


class TestDeepMergeMetadata:
    def test_new_keys_overwrite_old(self):
        from nextseek_api.batch_upload.update import deep_merge_metadata
        old = '{"Name":"Old","Protocol":"/sops/1"}'
        new = '{"Name":"New","Extra":"value"}'
        merged, changed_keys = deep_merge_metadata(old, new)
        result = json.loads(merged)
        assert result["Name"] == "New"
        assert result["Protocol"] == "/sops/1"
        assert result["Extra"] == "value"
        assert "Name" in changed_keys
        assert "Extra" in changed_keys
        assert "Protocol" not in changed_keys

    def test_empty_new_preserves_old(self):
        from nextseek_api.batch_upload.update import deep_merge_metadata
        old = '{"Name":"Old","Protocol":"/sops/1"}'
        new = '{}'
        merged, changed_keys = deep_merge_metadata(old, new)
        result = json.loads(merged)
        assert result["Name"] == "Old"
        assert len(changed_keys) == 0

    def test_parent_change_detected(self):
        from nextseek_api.batch_upload.update import deep_merge_metadata
        old = '{"Parent":"NHP-250101MIT-1"}'
        new = '{"Parent":"NHP-250101MIT-2"}'
        merged, changed_keys = deep_merge_metadata(old, new)
        assert "Parent" in changed_keys

    def test_same_values_no_change(self):
        from nextseek_api.batch_upload.update import deep_merge_metadata
        old = '{"Name":"Same","Protocol":"/sops/1"}'
        new = '{"Name":"Same","Protocol":"/sops/1"}'
        merged, changed_keys = deep_merge_metadata(old, new)
        assert len(changed_keys) == 0

    def test_invalid_old_json(self):
        from nextseek_api.batch_upload.update import deep_merge_metadata
        old = 'not json'
        new = '{"Name":"New"}'
        merged, changed_keys = deep_merge_metadata(old, new)
        result = json.loads(merged)
        assert result["Name"] == "New"
        assert "Name" in changed_keys

    def test_empty_strings(self):
        from nextseek_api.batch_upload.update import deep_merge_metadata
        merged, changed_keys = deep_merge_metadata("", "")
        assert json.loads(merged) == {}
        assert len(changed_keys) == 0


class TestLoadExistingSampleDetails:
    def test_loads_details(self):
        from nextseek_api.batch_upload.update import load_existing_sample_details
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            ("NHP-250101MIT-1", 42, 5, '{"Name":"Old"}', "Old"),
        ]
        details = load_existing_sample_details(["NHP-250101MIT-1"], conn)
        assert "NHP-250101MIT-1" in details
        assert details["NHP-250101MIT-1"]["policy_id"] == 5
        assert details["NHP-250101MIT-1"]["sample_id"] == 42
        assert details["NHP-250101MIT-1"]["json_metadata"] == '{"Name":"Old"}'
        assert details["NHP-250101MIT-1"]["title"] == "Old"

    def test_empty_uuids(self):
        from nextseek_api.batch_upload.update import load_existing_sample_details
        conn = MagicMock()
        details = load_existing_sample_details([], conn)
        assert len(details) == 0

    def test_null_metadata_defaults(self):
        from nextseek_api.batch_upload.update import load_existing_sample_details
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            ("U1", 1, 2, None, None),
        ]
        details = load_existing_sample_details(["U1"], conn)
        assert details["U1"]["json_metadata"] == "{}"
        assert details["U1"]["title"] == ""


class TestSmartMergeAssayAssets:
    def test_adds_new_removes_unlisted(self):
        from nextseek_api.batch_upload.update import smart_merge_assay_assets
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(1,), (2,)]
        added, removed = smart_merge_assay_assets(
            sample_id=42, new_assay_ids=[2, 3], direction_by_pair={}, uid="U1", conn=conn
        )
        assert added == {3}
        assert removed == {1}

    def test_no_changes_when_identical(self):
        from nextseek_api.batch_upload.update import smart_merge_assay_assets
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(1,), (2,)]
        added, removed = smart_merge_assay_assets(
            sample_id=42, new_assay_ids=[1, 2], direction_by_pair={}, uid="U1", conn=conn
        )
        assert len(added) == 0
        assert len(removed) == 0

    def test_all_new(self):
        from nextseek_api.batch_upload.update import smart_merge_assay_assets
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        added, removed = smart_merge_assay_assets(
            sample_id=42, new_assay_ids=[1, 2], direction_by_pair={}, uid="U1", conn=conn
        )
        assert added == {1, 2}
        assert len(removed) == 0

    def test_all_removed(self):
        from nextseek_api.batch_upload.update import smart_merge_assay_assets
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(1,), (2,)]
        added, removed = smart_merge_assay_assets(
            sample_id=42, new_assay_ids=[], direction_by_pair={}, uid="U1", conn=conn
        )
        assert len(added) == 0
        assert removed == {1, 2}


class TestAddPermissionForExistingPolicy:
    def test_inserts_when_not_exists(self):
        from nextseek_api.batch_upload.update import add_permission_for_existing_policy
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None  # no existing
        result = add_permission_for_existing_policy(policy_id=5, project_id=1, conn=conn)
        assert result is True
        assert conn.execute.call_count == 2  # SELECT + INSERT

    def test_skips_when_exists(self):
        from nextseek_api.batch_upload.update import add_permission_for_existing_policy
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (1,)  # exists
        result = add_permission_for_existing_policy(policy_id=5, project_id=1, conn=conn)
        assert result is False
        assert conn.execute.call_count == 1  # only SELECT

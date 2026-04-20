"""Integration tests for name idempotence + study node creation + sample update."""
import json
import pytest
from unittest.mock import MagicMock

from nextseek_api.batch_upload.identity import hash_identity


class TestNameIdempotenceIntegration:
    """Test the full name check -> skip/update flow."""

    def test_name_duplicate_skipped_in_default_mode(self):
        """When update_existing=False, name-matched rows are skipped."""
        from nextseek_api.batch_upload.uid_gen import check_name_exists_in_db
        from nextseek_api.batch_upload.models import InputRowModel

        conn = MagicMock()
        rows = [
            InputRowModel(SampleType="NHP_blood", json_metadata='{"Name":"Existing Sample"}'),
            InputRowModel(SampleType="NHP_blood", json_metadata='{"Name":"New Sample"}'),
        ]
        conn.execute.return_value.fetchall.return_value = [
            ("NHP-250101MIT-1", 42, hash_identity("Existing Sample")),
        ]
        remaining, matches, matched_rows, ambiguous_rows = check_name_exists_in_db(rows, conn)
        assert len(remaining) == 1
        assert remaining[0].json_metadata  # the "New Sample" row
        assert len(matches) == 1
        assert "Existing Sample" in matches
        assert matches["Existing Sample"]["uid"] == "NHP-250101MIT-1"
        assert matches["Existing Sample"]["sample_id"] == 42
        assert ambiguous_rows == []

    def test_name_duplicate_updated_in_upsert_mode(self):
        """When update_existing=True, the update module can deep-merge metadata."""
        from nextseek_api.batch_upload.update import deep_merge_metadata

        old_meta = '{"Name":"Old Name","Protocol":"/sops/1","Legacy":"preserved"}'
        new_meta = '{"Name":"Updated Name","Extra":"new_field"}'
        merged, changed = deep_merge_metadata(old_meta, new_meta)
        result = json.loads(merged)
        assert result["Name"] == "Updated Name"
        assert result["Protocol"] == "/sops/1"
        assert result["Legacy"] == "preserved"
        assert result["Extra"] == "new_field"
        assert "Name" in changed
        assert "Extra" in changed
        assert "Protocol" not in changed
        assert "Legacy" not in changed


class TestStudyNodeCreationIntegration:
    """Test study/investigation node payload building."""

    def test_builds_study_and_investigation_payloads(self):
        from nextseek_api.batch_upload.neo4j_sync import build_study_node_payloads

        conn = MagicMock()
        studies_result = MagicMock()
        studies_result.fetchall.return_value = [
            (1, "Study A", "Desc A", 10),
            (2, "Study B", None, 10),  # null description
        ]
        inv_result = MagicMock()
        inv_result.fetchall.return_value = [
            (10, "Investigation X", "Inv Desc"),
        ]
        conn.execute.side_effect = [studies_result, inv_result]
        study_rows, inv_rows, inv_rels = build_study_node_payloads({1, 2}, conn)
        assert len(study_rows) == 2
        assert study_rows[0].title == "Study A"
        assert study_rows[0].description == "Desc A"
        assert study_rows[1].description == ""  # null -> empty string
        assert len(inv_rows) == 1
        assert inv_rows[0].title == "Investigation X"
        assert len(inv_rels) == 2  # both studies link to investigation 10

    def test_study_without_investigation(self):
        from nextseek_api.batch_upload.neo4j_sync import build_study_node_payloads

        conn = MagicMock()
        studies_result = MagicMock()
        studies_result.fetchall.return_value = [
            (1, "Study A", "Desc", None),
        ]
        # Only one execute call expected (studies query); no investigation query
        conn.execute.return_value = studies_result
        study_rows, inv_rows, inv_rels = build_study_node_payloads({1}, conn)
        assert len(study_rows) == 1
        assert len(inv_rows) == 0
        assert len(inv_rels) == 0


class TestSampleUpdateIntegration:
    """Test deep merge + smart assay merge + permission together."""

    def test_full_update_flow(self):
        from nextseek_api.batch_upload.update import (
            deep_merge_metadata,
            smart_merge_assay_assets,
            add_permission_for_existing_policy,
        )

        # 1. Deep merge
        old = '{"Name":"Old","Parent":"NHP-1","Protocol":"/sops/1"}'
        new = '{"Name":"New","Parent":"NHP-2","NewField":"value"}'
        merged, changed = deep_merge_metadata(old, new)
        result = json.loads(merged)
        assert result["Name"] == "New"
        assert result["Parent"] == "NHP-2"
        assert result["Protocol"] == "/sops/1"
        assert result["NewField"] == "value"
        assert "Parent" in changed  # triggers DERIVED_FROM re-resolve
        assert "Name" in changed
        assert "Protocol" not in changed

        # 2. Smart assay merge
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(1,), (2,), (3,)]
        added, removed = smart_merge_assay_assets(
            sample_id=42, new_assay_ids=[2, 4, 5], direction_by_pair={}, uid="U1", conn=conn
        )
        assert added == {4, 5}
        assert removed == {1, 3}

    def test_permission_for_existing_policy(self):
        from nextseek_api.batch_upload.update import add_permission_for_existing_policy

        conn = MagicMock()
        # Permission doesn't exist yet
        conn.execute.return_value.fetchone.return_value = None
        result = add_permission_for_existing_policy(policy_id=5, project_id=2, conn=conn)
        assert result is True

        # Reset mock and test already exists
        conn.reset_mock()
        conn.execute.return_value.fetchone.return_value = (99,)
        result = add_permission_for_existing_policy(policy_id=5, project_id=2, conn=conn)
        assert result is False


class TestUpdateExistingConfigIntegration:
    """Test that config flag flows correctly."""

    def test_config_flows_to_batch_upload(self):
        from nextseek_api.batch_upload.config import BatchUploadConfig

        config = BatchUploadConfig(update_existing=True)
        assert config.update_existing is True
        # Verify it serializes
        d = config.to_dict()
        assert d["update_existing"] is True

    def test_config_default_insert_mode(self):
        from nextseek_api.batch_upload.config import BatchUploadConfig

        config = BatchUploadConfig()
        assert config.update_existing is False

    def test_batch_result_tracks_updated_count(self):
        from nextseek_api.batch_upload.models import BatchResult

        br = BatchResult(inserted_count=5, updated_count=3)
        assert br.inserted_count == 5
        assert br.updated_count == 3

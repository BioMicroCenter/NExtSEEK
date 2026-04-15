"""Tests for the sample update (upsert) logic."""
import json
import pytest
from unittest.mock import MagicMock, call, patch

from nextseek_api.batch_upload.models import InsertableSample, RowOutcome


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


class TestCanonicalizeJsonMetadata:
    def test_invalid_json_returns_empty_object(self):
        from nextseek_api.batch_upload.update import _canonicalize_json_metadata
        assert _canonicalize_json_metadata('{"bad"') == "{}"

    def test_non_object_json_returns_empty_object(self):
        from nextseek_api.batch_upload.update import _canonicalize_json_metadata
        assert _canonicalize_json_metadata('["x"]') == "{}"

    def test_typo_key_rewritten(self):
        from nextseek_api.batch_upload.update import _canonicalize_json_metadata
        parsed = json.loads(_canonicalize_json_metadata('{"File_PrimartyData":"x.csv"}'))
        assert parsed == {"File_PrimaryData": "x.csv"}


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

    def test_update_sample_metadata_emits_update(self):
        from nextseek_api.batch_upload.update import update_sample_metadata
        conn = MagicMock()
        update_sample_metadata("U1", 5, "Title", '{"Name":"x"}', conn)
        assert "UPDATE samples SET title = :title" in conn.execute.call_args[0][0].text
        assert conn.execute.call_args[0][1]["sid"] == 5


class TestSmartMergeAssayAssets:
    """Tests for deprecated per-row function (kept for backward compat)."""

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
    """Tests for deprecated per-row function (kept for backward compat)."""

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


# ── New tests for bulk_update_samples ────────────────────────────────────


def _make_sample(uuid, title="Title", assay_ids=None, meta=None):
    """Helper to create an InsertableSample for testing."""
    return InsertableSample(
        uuid=uuid,
        title=title,
        sample_type_id=1,
        json_metadata=meta or '{"Name":"New"}',
        assay_ids=assay_ids or [],
    )


def _make_details(*entries):
    """Helper: entries are (uuid, sample_id, policy_id, json_metadata, title)."""
    return {
        uuid: {
            "sample_id": sid,
            "policy_id": pid,
            "json_metadata": jmeta,
            "title": title,
        }
        for uuid, sid, pid, jmeta, title in entries
    }


class TestBulkUpdateSamples:
    def test_uses_one_connection_for_all_operations(self):
        """All DB operations go through the single conn passed in."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        # Prefetch returns empty assay links
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample("U1", assay_ids=[10]), _make_sample("U2", assay_ids=[20])]
        details = _make_details(
            ("U1", 1, 5, '{"Name":"Old1"}', "Old1"),
            ("U2", 2, 6, '{"Name":"Old2"}', "Old2"),
        )

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets") as mock_assay, \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples") as mock_proj:
            mock_assay.return_value = 0
            mock_proj.return_value = 0

            outcomes = bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
            )

        # All operations used the same conn object
        assert conn.execute.call_count >= 1  # UPDATE + prefetch at minimum
        # Both samples succeeded
        assert outcomes["U1"].status == "success"
        assert outcomes["U2"].status == "success"
        # batch_insert_assay_assets received the same conn
        mock_assay.assert_called_once()
        assert mock_assay.call_args[0][1] is conn
        # batch_insert_projects_samples received the same conn
        mock_proj.assert_called_once()
        assert mock_proj.call_args[0][2] is conn

    def test_parent_changed_flag_set_when_parent_changes(self):
        """parent_changed=True when 'Parent' key is in changed_keys."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample("U1", meta='{"Parent":"NHP-250101MIT-2","Name":"New"}')]
        details = _make_details(
            ("U1", 1, 5, '{"Parent":"NHP-250101MIT-1","Name":"Old"}', "Old"),
        )

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            outcomes = bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
            )

        assert outcomes["U1"].parent_changed is True

    def test_parent_changed_flag_false_when_parent_unchanged(self):
        """parent_changed=False when Parent key is not in changed_keys."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample("U1", meta='{"Name":"New"}')]
        details = _make_details(
            ("U1", 1, 5, '{"Name":"Old"}', "Old"),
        )

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            outcomes = bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
            )

        assert outcomes["U1"].parent_changed is False

    def test_parent_changed_lowercase_parent(self):
        """parent_changed=True when lowercase 'parent' key changes."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample("U1", meta='{"parent":"NHP-250101MIT-2"}')]
        details = _make_details(
            ("U1", 1, 5, '{"parent":"NHP-250101MIT-1"}', "Old"),
        )

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            outcomes = bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
            )

        assert outcomes["U1"].parent_changed is True

    def test_assay_prefetch_uses_chunked_in_query(self):
        """Assay prefetch uses a single bulk query, not per-sample queries."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        # Return empty for all queries (UPDATE, prefetch, etc.)
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample(f"U{i}", assay_ids=[100 + i]) for i in range(5)]
        details = _make_details(
            *[(f"U{i}", i + 1, 10 + i, '{}', f"Title{i}") for i in range(5)]
        )

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            outcomes = bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
            )

        # conn.execute is called for:
        # 1. Bulk UPDATE (1 call for 5 rows)
        # 2. Prefetch assay links (1 call for 5 sample_ids)
        # Total: 2 calls, NOT 5+5 per-row calls
        assert conn.execute.call_count == 2
        assert all(o.status == "success" for o in outcomes.values())

    def test_permissions_use_insert_ignore(self):
        """Permissions use INSERT IGNORE, not SELECT+INSERT per row."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample("U1"), _make_sample("U2")]
        details = _make_details(
            ("U1", 1, 5, '{}', "T1"),
            ("U2", 2, 6, '{}', "T2"),
        )

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            outcomes = bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
                enable_auto_permissions=True,
            )

        # Find the INSERT IGNORE call by inspecting the TextClause.text attribute
        insert_ignore_calls = [
            c for c in conn.execute.call_args_list
            if hasattr(c[0][0], "text") and "INSERT IGNORE" in c[0][0].text
        ]
        assert len(insert_ignore_calls) == 1, "Expected exactly one INSERT IGNORE call for permissions"
        # No SELECT for permissions — only sample UPDATE + prefetch + policy repair + INSERT IGNORE = 4 calls
        assert conn.execute.call_count == 4

    def test_missing_details_produces_failed_outcome(self):
        """Rows without details get failed outcome."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample("U1"), _make_sample("U_MISSING")]
        details = _make_details(("U1", 1, 5, '{}', "T1"))

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            outcomes = bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
            )

        assert outcomes["U1"].status == "success"
        assert outcomes["U_MISSING"].status == "failed"
        assert "not found" in outcomes["U_MISSING"].reason

    def test_all_rows_missing_details_returns_failed_outcomes_only(self):
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        rows = [_make_sample("U_MISSING")]
        outcomes = bulk_update_samples(
            rows=rows,
            details={},
            project_id=99,
            direction_by_pair={},
            conn=conn,
        )

        assert outcomes["U_MISSING"].status == "failed"
        conn.execute.assert_not_called()

    def test_empty_rows_returns_empty(self):
        """Empty rows list returns empty outcomes."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        outcomes = bulk_update_samples(
            rows=[], details={}, project_id=99,
            direction_by_pair={}, conn=conn,
        )
        assert outcomes == {}
        conn.execute.assert_not_called()

    def test_assay_diff_adds_and_removes(self):
        """Assay diffs correctly compute adds and removes."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        # The UPDATE call doesn't use fetchall, so the first fetchall call
        # is from the prefetch step. Return assay links for sample_id=1.
        conn.execute.return_value.fetchall.side_effect = [
            [(1, 10), (1, 20)],  # prefetch: asset_id=1 has assays 10, 20
        ]

        # New assays: keep 20, add 30, remove 10
        rows = [_make_sample("U1", assay_ids=[20, 30])]
        details = _make_details(("U1", 1, 5, '{}', "T1"))

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets") as mock_assay, \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            mock_assay.return_value = 1

            outcomes = bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={("U1", 30): 2},
                conn=conn,
            )

        # Verify DELETE was called for assay 10
        delete_calls = [
            c for c in conn.execute.call_args_list
            if hasattr(c[0][0], "text") and "DELETE" in c[0][0].text
        ]
        assert len(delete_calls) == 1

        # Verify batch_insert_assay_assets was called with assay 30
        assert mock_assay.call_count == 1
        records = mock_assay.call_args[0][0]
        assert len(records) == 1
        assert records[0][0] == 30  # assay_id
        assert records[0][1] == 1   # sample_id
        assert records[0][3] == 2   # direction from direction_by_pair

    def test_no_permissions_when_disabled(self):
        """No permission INSERT when enable_auto_permissions=False."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample("U1")]
        details = _make_details(("U1", 1, 5, '{}', "T1"))

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            outcomes = bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
                enable_auto_permissions=False,
            )

        insert_ignore_calls = [
            c for c in conn.execute.call_args_list
            if hasattr(c[0][0], "text") and "INSERT IGNORE" in c[0][0].text
        ]
        assert len(insert_ignore_calls) == 0

    def test_existing_manage_policy_is_repaired_to_private_when_permissions_enabled(self):
        """update_existing should repair bad public policy defaults on touched samples."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample("U1")]
        details = _make_details(("U1", 1, 5, '{}', "T1"))

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
                enable_auto_permissions=True,
            )

        repair_calls = [
            c for c in conn.execute.call_args_list
            if hasattr(c[0][0], "text")
            and "UPDATE policies" in c[0][0].text
            and c[0][1].get("access_type") == 0
        ]
        assert len(repair_calls) == 1

    def test_bulk_upsert_permissions_empty_returns_zero(self):
        from nextseek_api.batch_upload.update import _bulk_insert_permissions_ignore

        conn = MagicMock()
        assert _bulk_insert_permissions_ignore([], 99, conn, "2026-01-01 00:00:00") == 0
        conn.execute.assert_not_called()

    def test_bulk_restore_private_policy_defaults_empty_returns_zero(self):
        from nextseek_api.batch_upload.update import _bulk_restore_private_policy_defaults

        conn = MagicMock()
        assert _bulk_restore_private_policy_defaults([], conn, "2026-01-01 00:00:00") == 0
        conn.execute.assert_not_called()

    def test_bulk_restore_private_policy_defaults_ignores_falsey_ids(self):
        from nextseek_api.batch_upload.update import _bulk_restore_private_policy_defaults

        conn = MagicMock()
        assert _bulk_restore_private_policy_defaults([0, None], conn, "2026-01-01 00:00:00") == 0
        conn.execute.assert_not_called()

    def test_update_path_canonicalizes_merged_metadata(self):
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        rows = [_make_sample("U1", title="NewTitle", meta='{"File_PrimaryData":"new.csv"}')]
        details = _make_details(("U1", 1, 5, '{"File_PrimartyData":"old.csv"}', "OldTitle"))

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
            )

        update_call = next(
            c for c in conn.execute.call_args_list
            if hasattr(c[0][0], "text") and c[0][0].text.startswith("UPDATE samples SET")
        )
        params = update_call[0][1]
        parsed = json.loads(params["meta_0"])
        assert parsed["File_PrimaryData"] == "new.csv"
        assert "File_PrimartyData" not in parsed
        assert params["title_0"] == "NewTitle"

    def test_update_path_canonicalization_is_noop_for_canonical_metadata(self):
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        rows = [_make_sample("U1", title="NewTitle", meta='{"File_PrimaryData":"canon.csv"}')]
        details = _make_details(("U1", 1, 5, '{"File_PrimaryData":"canon.csv"}', "OldTitle"))

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
            )

        update_call = next(
            c for c in conn.execute.call_args_list
            if hasattr(c[0][0], "text") and c[0][0].text.startswith("UPDATE samples SET")
        )
        params = update_call[0][1]
        assert params["meta_0"] == '{"File_PrimaryData":"canon.csv"}'

    def test_update_path_still_overwrites_title(self):
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        rows = [_make_sample("U1", title="NewTitle", meta='{"Name":"Updated"}')]
        details = _make_details(("U1", 1, 5, '{"Name":"Old"}', "OldTitle"))

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
            )

        update_call = next(
            c for c in conn.execute.call_args_list
            if hasattr(c[0][0], "text") and c[0][0].text.startswith("UPDATE samples SET")
        )
        assert update_call[0][1]["title_0"] == "NewTitle"


class TestRowOutcomeParentChanged:
    """Test the new parent_changed field on RowOutcome."""

    def test_default_false(self):
        outcome = RowOutcome(status="success")
        assert outcome.parent_changed is False

    def test_explicit_true(self):
        outcome = RowOutcome(status="success", parent_changed=True)
        assert outcome.parent_changed is True

    def test_serialization_roundtrip(self):
        outcome = RowOutcome(status="success", parent_changed=True, sample_id=42)
        data = outcome.model_dump()
        restored = RowOutcome(**data)
        assert restored.parent_changed is True
        assert restored.sample_id == 42

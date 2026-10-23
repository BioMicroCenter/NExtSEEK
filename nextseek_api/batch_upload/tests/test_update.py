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
    def _update_with_existing_links(self, **kwargs):
        """U1 (sample 1) holds assays 198 and 199; the update row lists none."""
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        details = _make_details(("U1", 1, 5, '{"Name":"Old"}', "Old"))
        with patch("nextseek_api.batch_upload.update._bulk_prefetch_assay_links",
                   return_value={1: {198, 199}}), \
             patch("nextseek_api.batch_upload.update.delete_assay_links") as mock_delete, \
             patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            outcomes = bulk_update_samples(
                rows=[_make_sample("U1", assay_ids=[])], details=details, project_id=99,
                direction_by_pair={}, conn=conn, **kwargs)
        assert outcomes["U1"].status == "success"
        return sorted(mock_delete.call_args[0][0])

    def test_an_unlisted_assay_link_is_removed_by_default(self):
        assert self._update_with_existing_links() == [(198, 1), (199, 1)]

    def test_keep_assay_links_never_removes_a_link(self):
        # A reingest backfill carries QC values only; its empty ASSAY sheet must
        # not strip the sequencing samples out of their studies' assays.
        assert self._update_with_existing_links(keep_assay_links=True) == []

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

    @pytest.mark.parametrize("parent_key", [
        "AntibodyParent",
        "CompensationFCSParent",
        "Treatment1Parent",
        "Treatment2Parent",
        "BacterialParent",
        "AntibodyPanelParent",
        "antibodyparent",
        "ANTIBODYPARENT",
    ])
    def test_parent_changed_flag_set_for_variant_parent_keys(self, parent_key):
        """parent_changed=True for any changed key CONTAINING 'parent'.

        The literal-key check missed AntibodyParent (12,367 live references) and
        CompensationFCSParent (66,529), leaving stale DERIVED_FROM edges behind
        on re-upload because the graph writer of the time deleted edges only for parent_changed rows.
        """
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample("U1", meta=json.dumps({parent_key: "NHP-250101MIT-2"}))]
        details = _make_details(
            ("U1", 1, 5, json.dumps({parent_key: "NHP-250101MIT-1"}), "Old"),
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

    def test_parent_changed_false_when_only_non_parent_key_changes(self):
        """A non-parent key changing must NOT set parent_changed.

        Guards the substring check against degenerating into `bool(changed_keys)`,
        which would make every re-upload delete and rebuild lineage edges.
        """
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample("U1", meta='{"ReferenceGenome":"GRCh39","Scientist":"B"}')]
        details = _make_details(
            ("U1", 1, 5, '{"ReferenceGenome":"GRCh38","Scientist":"A"}', "Old"),
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

    def test_parent_changed_false_when_unchanged_variant_parent_is_present(self):
        """An UNCHANGED AntibodyParent must not set the flag just by existing.

        Guards against reading the merged metadata's keys instead of changed_keys.
        """
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        rows = [_make_sample("U1", meta='{"AntibodyParent":"AB-230327BOO-3","Name":"New"}')]
        details = _make_details(
            ("U1", 1, 5, '{"AntibodyParent":"AB-230327BOO-3","Name":"Old"}', "Old"),
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

    def test_reupload_grant_follows_the_config(self):
        """The grant goes through the PermissionsInserter: SELECT first, config access_type."""
        from nextseek_api.batch_upload.permissions import PermissionsInserter
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        inserter = PermissionsInserter("Project", 99, 2, enabled=True)

        rows = [_make_sample("U1"), _make_sample("U2")]
        details = _make_details(
            ("U1", 1, 5, '{}', "T1"),
            ("U2", 2, 5, '{}', "T2"),  # same policy as U1: one grant only
        )

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            bulk_update_samples(
                rows=rows,
                details=details,
                project_id=99,
                direction_by_pair={},
                conn=conn,
                permissions_inserter=inserter,
            )

        texts = [c[0][0].text for c in conn.execute.call_args_list if hasattr(c[0][0], "text")]
        sel = [i for i, t in enumerate(texts) if "SELECT policy_id FROM permissions" in t]
        ins = [i for i, t in enumerate(texts) if t.startswith("INSERT INTO permissions")]
        assert len(sel) == 1 and len(ins) == 1 and sel[0] < ins[0]
        assert not any("INSERT IGNORE" in t for t in texts)
        ins_call = [c for c in conn.execute.call_args_list
                    if hasattr(c[0][0], "text") and c[0][0].text.startswith("INSERT INTO permissions")][0]
        params = ins_call[0][1]
        assert params["at_0"] == 2 and params["pid_0"] == 5 and "pid_1" not in params

    def test_reupload_never_changes_policy_access(self):
        """A re-upload leaves every policy's access_type alone."""
        from nextseek_api.batch_upload.permissions import PermissionsInserter
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
                permissions_inserter=PermissionsInserter("Project", 99, 4, enabled=True),
            )

        assert not any(
            hasattr(c[0][0], "text") and "UPDATE policies" in c[0][0].text
            for c in conn.execute.call_args_list
        )

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

    @pytest.mark.parametrize("disabled", ["none", "inserter_off"])
    def test_no_permissions_when_disabled(self, disabled):
        """No permissions statement when the inserter is absent or disabled."""
        from nextseek_api.batch_upload.permissions import PermissionsInserter
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        inserter = None if disabled == "none" else PermissionsInserter("Project", 99, 4, enabled=False)

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
                permissions_inserter=inserter,
            )

        assert not any(
            hasattr(c[0][0], "text") and "permissions" in c[0][0].text
            for c in conn.execute.call_args_list
        )

    def test_grant_insert_is_chunked(self):
        """One update call over many policies never builds one giant INSERT."""
        from nextseek_api.batch_upload.permissions import PermissionsInserter
        from nextseek_api.batch_upload.update import bulk_update_samples

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        n = 2500
        rows = [_make_sample(f"U{i}") for i in range(n)]
        details = _make_details(*[(f"U{i}", i + 1, i + 1, '{}', "T1") for i in range(n)])

        with patch("nextseek_api.batch_upload.update.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.update.batch_insert_projects_samples", return_value=0):
            bulk_update_samples(
                rows=rows, details=details, project_id=99, direction_by_pair={}, conn=conn,
                permissions_inserter=PermissionsInserter("Project", 99, 4, enabled=True),
            )

        inserts = [c[0][1] for c in conn.execute.call_args_list
                   if hasattr(c[0][0], "text") and c[0][0].text.startswith("INSERT INTO permissions")]
        assert len(inserts) == 3 and max(len(p) // 6 for p in inserts) <= 1000
        granted = [v for p in inserts for k, v in p.items() if k.startswith("pid_")]
        assert sorted(granted) == list(range(1, n + 1))  # every policy exactly once

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


class TestDeleteAssayLinks:
    """The link delete batch upload's update path and the studies tool share. It lives in update.py, never in
    associations.py: the registration executor's only write module must hold no DELETE (test_executor.py's
    TestNoDeletePath)."""

    def test_no_pairs_sends_nothing(self):
        from nextseek_api.batch_upload.update import delete_assay_links

        conn = MagicMock()
        assert delete_assay_links([], conn) == 0
        conn.execute.assert_not_called()

    def test_one_statement_per_thousand_pairs_sample_rows_only(self):
        from nextseek_api.batch_upload.update import delete_assay_links

        conn = MagicMock()
        pairs = [(100, i) for i in range(1, 1502)]
        assert delete_assay_links(pairs, conn) == 1501
        assert conn.execute.call_count == 2
        first_sql = str(conn.execute.call_args_list[0].args[0])
        assert "asset_type = 'Sample'" in first_sql
        assert "(assay_id = :aid_0 AND asset_id = :sid_0)" in first_sql
        params = conn.execute.call_args_list[1].args[1]
        assert params == {**{f"aid_{i}": 100 for i in range(501)}, **{f"sid_{i}": 1001 + i for i in range(501)}}

    def test_the_delete_is_public_in_update_and_absent_from_associations(self):
        from nextseek_api.batch_upload import associations, update

        assert callable(update.delete_assay_links)
        assert not hasattr(update, "_bulk_delete_assay_links")
        assert not hasattr(associations, "delete_assay_links")

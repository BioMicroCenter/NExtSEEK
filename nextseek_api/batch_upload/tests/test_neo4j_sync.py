"""Unit tests for neo4j_sync payload builders and merge functions."""
import json
import pytest
from unittest.mock import MagicMock, call, patch

from nextseek_api.batch_upload.models import (
    InStudyRelRow,
    InputRowModel,
    InsertableSample,
    OfTypeRelRow,
    RowOutcome,
)
from nextseek_api.batch_upload.neo4j_sync import (
    _json_loads,
    build_derived_from_payloads_from_db,
    build_in_study_payloads,
    build_of_type_payloads,
    build_payloads,
    bulk_merge_in_study_relationships,
    delete_derived_from_for_uuids,
    refresh_assays_for_uuids,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _input(uid, study_id=None):
    return InputRowModel(UID=uid, SampleType="Blood", json_metadata="{}", study_id=study_id)


def _insertable(uuid, sample_type_id=10):
    return InsertableSample(uuid=uuid, title="s", sample_type_id=sample_type_id, json_metadata="{}")


def _outcome(status="success", sample_id=None):
    return RowOutcome(status=status, sample_id=sample_id)


def _mock_driver(processed_per_chunk=None):
    """Create a mock Neo4j driver that returns processed counts."""
    driver = MagicMock()
    if processed_per_chunk is None:
        processed_per_chunk = [0]

    call_idx = [0]

    def _execute_query(cypher, params, database_=None):
        result = MagicMock()
        idx = min(call_idx[0], len(processed_per_chunk) - 1)
        record = {"processed": processed_per_chunk[idx]}
        result.records = [record]
        call_idx[0] += 1
        return result

    driver.execute_query = MagicMock(side_effect=_execute_query)
    return driver


# ── TestBuildPayloadsFilter ──────────────────────────────────────────────────


class TestBuildPayloadsFilter:
    """Verify build_payloads includes skipped-duplicate outcomes with sample_id."""

    def test_includes_skipped_duplicate_with_sample_id(self):
        outcomes = {
            "UID-1": _outcome("success", sample_id=100),
            "UID-2": _outcome("skipped", sample_id=200),
        }
        models = [_input("UID-1"), _input("UID-2")]
        node_rows, _ = build_payloads(outcomes, models)
        uuids = {r.sample_uuid for r in node_rows}
        assert uuids == {"UID-1", "UID-2"}

    def test_excludes_failed_no_sample_id(self):
        outcomes = {
            "UID-1": _outcome("success", sample_id=100),
            "UID-2": _outcome("failed", sample_id=None),
        }
        models = [_input("UID-1"), _input("UID-2")]
        node_rows, _ = build_payloads(outcomes, models)
        assert len(node_rows) == 1
        assert node_rows[0].sample_uuid == "UID-1"


# ── TestBuildInStudyPayloads ────────────────────────────────────────────────


class TestBuildInStudyPayloads:
    def test_basic(self):
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [_input("UID-1", study_id=5)]
        rows, warns = build_in_study_payloads(outcomes, models)
        assert len(rows) == 1
        assert rows[0].sample_uuid == "UID-1"
        assert rows[0].study_id == 5
        assert warns == 0

    def test_skips_failed(self):
        outcomes = {"UID-1": _outcome("failed", sample_id=None)}
        models = [_input("UID-1", study_id=5)]
        rows, warns = build_in_study_payloads(outcomes, models)
        assert len(rows) == 0
        assert warns == 0

    def test_skips_none_study_id_with_warning(self):
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [_input("UID-1", study_id=None)]
        rows, warns = build_in_study_payloads(outcomes, models)
        assert len(rows) == 0
        assert warns == 1

    def test_includes_skipped_duplicate(self):
        outcomes = {"UID-1": _outcome("skipped", sample_id=200)}
        models = [_input("UID-1", study_id=3)]
        rows, warns = build_in_study_payloads(outcomes, models)
        assert len(rows) == 1
        assert rows[0].sample_uuid == "UID-1"
        assert rows[0].study_id == 3

    def test_mixed_batch(self):
        outcomes = {
            "UID-1": _outcome("success", sample_id=100),
            "UID-2": _outcome("skipped", sample_id=200),
            "UID-3": _outcome("failed", sample_id=None),
            "UID-4": _outcome("success", sample_id=400),
        }
        models = [
            _input("UID-1", study_id=5),
            _input("UID-2", study_id=6),
            _input("UID-3", study_id=7),
            _input("UID-4", study_id=None),
        ]
        rows, warns = build_in_study_payloads(outcomes, models)
        assert len(rows) == 2  # UID-1 and UID-2
        assert warns == 1  # UID-4 has no study_id


# ── TestBulkMergeInStudyRelationships ────────────────────────────────────────


class TestBulkMergeInStudyRelationships:
    def test_empty_list(self):
        driver = _mock_driver()
        total = bulk_merge_in_study_relationships(driver, "testdb", [])
        assert total == 0
        driver.execute_query.assert_not_called()

    def test_single_chunk(self):
        driver = _mock_driver([5])
        rows = [InStudyRelRow(sample_uuid=f"UID-{i}", study_id=1) for i in range(5)]
        total = bulk_merge_in_study_relationships(driver, "testdb", rows)
        assert total == 5
        assert driver.execute_query.call_count == 1

    def test_chunking(self):
        driver = _mock_driver([2, 2, 1])
        rows = [InStudyRelRow(sample_uuid=f"UID-{i}", study_id=1) for i in range(5)]
        total = bulk_merge_in_study_relationships(driver, "testdb", rows, chunk_size=2)
        assert total == 5
        assert driver.execute_query.call_count == 3


# ── TestBuildOfTypePayloads ──────────────────────────────────────────────────


class TestBuildOfTypePayloads:
    def test_basic(self):
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        insertables = [_insertable("UID-1", sample_type_id=10)]
        rows = build_of_type_payloads(outcomes, insertables)
        assert len(rows) == 1
        assert rows[0].sample_id == 100
        assert rows[0].sample_uuid == "UID-1"
        assert rows[0].sample_type_id == 10

    def test_skips_failed(self):
        outcomes = {"UID-1": _outcome("failed", sample_id=None)}
        insertables = [_insertable("UID-1")]
        rows = build_of_type_payloads(outcomes, insertables)
        assert len(rows) == 0

    def test_skips_missing_insertable(self):
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        insertables = []  # no matching insertable
        rows = build_of_type_payloads(outcomes, insertables)
        assert len(rows) == 0

    def test_includes_skipped_duplicate(self):
        outcomes = {
            "UID-1": _outcome("success", sample_id=100),
            "UID-2": _outcome("skipped", sample_id=200),
        }
        insertables = [_insertable("UID-1", 10), _insertable("UID-2", 20)]
        rows = build_of_type_payloads(outcomes, insertables)
        assert len(rows) == 2


# ── TestBuildStudyNodePayloads ────────────────────────────────────────────


class TestBuildStudyNodePayloads:
    def test_builds_study_and_investigation_payloads(self):
        from nextseek_api.batch_upload.neo4j_sync import build_study_node_payloads

        conn = MagicMock()
        # Two execute calls: studies then investigations
        studies_result = MagicMock()
        studies_result.fetchall.return_value = [
            (1, "Study A", "Desc A", 10),
            (2, "Study B", "Desc B", 10),
        ]
        inv_result = MagicMock()
        inv_result.fetchall.return_value = [
            (10, "Investigation X", "Inv Desc"),
        ]
        conn.execute.side_effect = [studies_result, inv_result]
        study_rows, inv_rows, inv_rels = build_study_node_payloads({1, 2}, conn)
        assert len(study_rows) == 2
        assert study_rows[0].title == "Study A"
        assert len(inv_rows) == 1
        assert inv_rows[0].title == "Investigation X"
        assert len(inv_rels) == 2  # both studies -> investigation 10

    def test_empty_study_ids(self):
        from nextseek_api.batch_upload.neo4j_sync import build_study_node_payloads

        conn = MagicMock()
        study_rows, inv_rows, inv_rels = build_study_node_payloads(set(), conn)
        assert len(study_rows) == 0
        assert len(inv_rows) == 0
        assert len(inv_rels) == 0
        conn.execute.assert_not_called()

    def test_study_without_investigation(self):
        from nextseek_api.batch_upload.neo4j_sync import build_study_node_payloads

        conn = MagicMock()
        studies_result = MagicMock()
        studies_result.fetchall.return_value = [
            (1, "Study A", "Desc", None),  # no investigation_id
        ]
        conn.execute.return_value = studies_result
        study_rows, inv_rows, inv_rels = build_study_node_payloads({1}, conn)
        assert len(study_rows) == 1
        assert len(inv_rows) == 0
        assert len(inv_rels) == 0

    def test_study_with_null_title_skipped(self):
        from nextseek_api.batch_upload.neo4j_sync import build_study_node_payloads

        conn = MagicMock()
        studies_result = MagicMock()
        studies_result.fetchall.return_value = [
            (1, None, "Desc", 10),  # null title
        ]
        conn.execute.return_value = studies_result
        study_rows, inv_rows, inv_rels = build_study_node_payloads({1}, conn)
        assert len(study_rows) == 0


# ── TestBulkMergeStudyNodes ──────────────────────────────────────────────


class TestBulkMergeStudyNodes:
    def test_merge_cypher_uses_merge(self):
        from nextseek_api.batch_upload.neo4j_sync import bulk_merge_study_nodes
        from nextseek_api.batch_upload.models import StudyNodeRow

        driver = MagicMock()
        mock_result = MagicMock()
        mock_result.summary.counters.nodes_created = 1
        driver.execute_query.return_value = mock_result
        rows = [StudyNodeRow(id=1, title="Study A", description="desc")]
        created = bulk_merge_study_nodes(driver, "testdb", rows)
        call_args = driver.execute_query.call_args
        cypher = call_args[0][0]
        assert "MERGE" in cypher
        assert "Study" in cypher
        assert created == 1

    def test_empty_rows(self):
        from nextseek_api.batch_upload.neo4j_sync import bulk_merge_study_nodes

        driver = MagicMock()
        created = bulk_merge_study_nodes(driver, "testdb", [])
        assert created == 0
        driver.execute_query.assert_not_called()


# ── TestBulkMergeInvestigationNodes ──────────────────────────────────────


class TestBulkMergeInvestigationNodes:
    def test_merge_cypher_uses_merge(self):
        from nextseek_api.batch_upload.neo4j_sync import bulk_merge_investigation_nodes
        from nextseek_api.batch_upload.models import InvestigationNodeRow

        driver = MagicMock()
        mock_result = MagicMock()
        mock_result.summary.counters.nodes_created = 2
        driver.execute_query.return_value = mock_result
        rows = [
            InvestigationNodeRow(id=10, title="Inv X", description="desc"),
            InvestigationNodeRow(id=20, title="Inv Y", description=""),
        ]
        created = bulk_merge_investigation_nodes(driver, "testdb", rows)
        call_args = driver.execute_query.call_args
        cypher = call_args[0][0]
        assert "MERGE" in cypher
        assert "Investigation" in cypher
        assert created == 2

    def test_empty_rows(self):
        from nextseek_api.batch_upload.neo4j_sync import bulk_merge_investigation_nodes

        driver = MagicMock()
        created = bulk_merge_investigation_nodes(driver, "testdb", [])
        assert created == 0
        driver.execute_query.assert_not_called()


# ── TestBulkMergeInInvestigationRelationships ────────────────────────────


class TestBulkMergeInInvestigationRelationships:
    def test_merge_cypher(self):
        from nextseek_api.batch_upload.neo4j_sync import bulk_merge_in_investigation_relationships
        from nextseek_api.batch_upload.models import InInvestigationRelRow

        driver = _mock_driver([2])
        rows = [
            InInvestigationRelRow(study_id=1, investigation_id=10),
            InInvestigationRelRow(study_id=2, investigation_id=10),
        ]
        total = bulk_merge_in_investigation_relationships(driver, "testdb", rows)
        call_args = driver.execute_query.call_args
        cypher = call_args[0][0]
        assert "MERGE" in cypher
        assert "IN_INVESTIGATION" in cypher
        assert total == 2

    def test_empty_rows(self):
        from nextseek_api.batch_upload.neo4j_sync import bulk_merge_in_investigation_relationships

        driver = MagicMock()
        total = bulk_merge_in_investigation_relationships(driver, "testdb", [])
        assert total == 0
        driver.execute_query.assert_not_called()


# ── TestDeleteDerivedFromForUuids ──────────────────────────────────────────


def _mock_driver_deleted(deleted_per_chunk=None):
    """Create a mock Neo4j driver that returns deleted counts."""
    driver = MagicMock()
    if deleted_per_chunk is None:
        deleted_per_chunk = [0]

    call_idx = [0]

    def _execute_query(cypher, params, database_=None):
        result = MagicMock()
        idx = min(call_idx[0], len(deleted_per_chunk) - 1)
        record = {"deleted": deleted_per_chunk[idx]}
        result.records = [record]
        call_idx[0] += 1
        return result

    driver.execute_query = MagicMock(side_effect=_execute_query)
    return driver


class TestDeleteDerivedFromForUuids:
    def test_deletes_derived_from_for_given_uuids(self):
        """Should issue UNWIND+DELETE Cypher for given UUIDs."""
        driver = _mock_driver_deleted([3])
        result = delete_derived_from_for_uuids(driver, "testdb", ["UID-1", "UID-2", "UID-3"])
        assert result == 3
        assert driver.execute_query.call_count == 1
        # Verify the Cypher contains DELETE and DERIVED_FROM
        cypher = driver.execute_query.call_args[0][0]
        assert "DELETE" in cypher
        assert "DERIVED_FROM" in cypher
        # Verify UUIDs were passed
        params = driver.execute_query.call_args[0][1]
        assert params["uuids"] == ["UID-1", "UID-2", "UID-3"]

    def test_empty_uuids_no_op(self):
        """Empty list should return 0 without calling driver."""
        driver = MagicMock()
        result = delete_derived_from_for_uuids(driver, "testdb", [])
        assert result == 0
        driver.execute_query.assert_not_called()

    def test_chunking(self):
        """Should chunk large UUID lists."""
        driver = _mock_driver_deleted([2, 1])
        uuids = [f"UID-{i}" for i in range(5)]
        result = delete_derived_from_for_uuids(driver, "testdb", uuids, chunk_size=3)
        assert result == 3  # 2 + 1
        assert driver.execute_query.call_count == 2


# ── TestRefreshAssaysForUuids ──────────────────────────────────────────────


class TestRefreshAssaysForUuids:
    def test_queries_actual_assay_assets(self):
        """Should query assay_assets from MySQL with chunked IN."""
        outcomes = {
            "UID-1": RowOutcome(status="success", sample_id=100, parent_changed=True),
            "UID-2": RowOutcome(status="success", sample_id=200, parent_changed=True),
        }
        conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (100, 10),  # asset_id=100, assay_id=10
            (100, 20),  # asset_id=100, assay_id=20
            (200, 10),  # asset_id=200, assay_id=10
        ]
        conn.execute.return_value = mock_result

        result = refresh_assays_for_uuids(["UID-1", "UID-2"], outcomes, conn)
        assert result["UID-1"] == {10, 20}
        assert result["UID-2"] == {10}
        # Verify SQL was called
        assert conn.execute.call_count == 1

    def test_empty_uuids_returns_empty(self):
        """Empty list should return empty dict."""
        conn = MagicMock()
        result = refresh_assays_for_uuids([], {}, conn)
        assert result == {}
        conn.execute.assert_not_called()

    def test_uuids_without_sample_ids_returns_empty(self):
        """UUIDs with no sample_id in outcomes should return empty."""
        outcomes = {
            "UID-1": RowOutcome(status="failed", sample_id=None),
        }
        conn = MagicMock()
        result = refresh_assays_for_uuids(["UID-1"], outcomes, conn)
        assert result == {}
        conn.execute.assert_not_called()

    def test_returns_empty_sets_for_uuids_with_no_assays(self):
        """UUIDs that have no assay_assets should get empty sets."""
        outcomes = {
            "UID-1": RowOutcome(status="success", sample_id=100, parent_changed=True),
        }
        conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []  # no assays found
        conn.execute.return_value = mock_result

        result = refresh_assays_for_uuids(["UID-1"], outcomes, conn)
        assert result["UID-1"] == set()


# ── TestJsonLoads ────────────────────────────────────────────────────────────


class TestJsonLoads:
    """Verify _json_loads is used instead of stdlib json.loads."""

    def test_json_loads_callable(self):
        """_json_loads should be a callable that parses JSON."""
        assert callable(_json_loads)
        assert _json_loads('{"a": 1}') == {"a": 1}
        assert _json_loads('[1, 2, 3]') == [1, 2, 3]

    def test_json_loads_used_in_build_payloads(self):
        """build_payloads should use _json_loads, not stdlib json.loads."""
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [InputRowModel(UID="UID-1", SampleType="Blood", json_metadata='{"key": "val"}')]
        with patch("nextseek_api.batch_upload.neo4j_sync._json_loads", wraps=_json_loads) as mock_loads:
            node_rows, _ = build_payloads(outcomes, models)
            mock_loads.assert_called()
        assert len(node_rows) == 1
        assert node_rows[0].properties == {"key": "val"}

    def test_json_loads_used_in_derived_from(self):
        """build_derived_from_payloads_from_db should use _json_loads for json_metadata parsing."""
        conn = MagicMock()
        # Step 0: parent UUID lookup
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 200)]
        # Step 1: child metadata
        child_result = MagicMock()
        child_result.fetchall.return_value = [(100, '{"Protocol": "/sops/5"}')]
        # Step 2: sop titles
        sop_result = MagicMock()
        sop_result.fetchall.return_value = [(5, "SOP Title")]
        conn.execute.side_effect = [parent_result, child_result, sop_result]

        outcomes = {"C-1": _outcome("success", sample_id=100)}
        models = [_input("C-1")]
        parent_child_rels = {"C-1": {"P-1"}}
        assays_by_uid = {}

        with patch("nextseek_api.batch_upload.neo4j_sync._json_loads", wraps=_json_loads) as mock_loads:
            rows = build_derived_from_payloads_from_db(
                parent_child_rels, conn, assays_by_uid, outcomes, models,
            )
            # _json_loads called for the json_metadata parsing
            mock_loads.assert_called()


# ── TestBulkAssayTitleFetch ──────────────────────────────────────────────────


class TestBulkAssayTitleFetch:
    """Verify assay titles are bulk-fetched, not lazy-loaded one-by-one."""

    def test_bulk_fetches_assay_titles_single_query(self):
        """Multiple shared assay IDs should be fetched in one bulk query, not N queries."""
        conn = MagicMock()
        # Step 0: parent UUID lookup — two parents
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201), ("P-2", 202)]
        # Step 1: child metadata — two children, no protocol
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, "{}"), (102, "{}")]
        # Step 2: no sop_ids -> no sop query needed
        # Step 3 (bulk): assay titles for IDs 10, 20
        assay_result = MagicMock()
        assay_result.fetchall.return_value = [(10, "Assay A"), (20, "Assay B")]

        conn.execute.side_effect = [parent_result, child_result, assay_result]

        outcomes = {
            "C-1": _outcome("success", sample_id=101),
            "C-2": _outcome("success", sample_id=102),
        }
        models = [_input("C-1"), _input("C-2")]
        parent_child_rels = {"C-1": {"P-1"}, "C-2": {"P-2"}}
        # Each child-parent pair shares a different assay
        assays_by_uid = {
            "C-1": {10, 30},
            "P-1": {10, 40},
            "C-2": {20, 50},
            "P-2": {20, 60},
        }

        rows = build_derived_from_payloads_from_db(
            parent_child_rels, conn, assays_by_uid, outcomes, models,
        )
        assert len(rows) == 2

        # Verify: exactly 3 SQL calls (parent lookup, child metadata, bulk assay titles)
        # NOT 4+ calls (which would indicate lazy per-assay queries)
        assert conn.execute.call_count == 3

        # Verify the assay titles were resolved
        titles = {r.internal_assay_title for r in rows}
        assert titles == {"Assay A", "Assay B"}

    def test_provided_assay_titles_skip_db_fetch(self):
        """Assay IDs with user-provided titles should not trigger DB fetch."""
        conn = MagicMock()
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201)]
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, "{}")]
        conn.execute.side_effect = [parent_result, child_result]

        outcomes = {"C-1": _outcome("success", sample_id=101)}
        # Provide assay_titles via input model
        model = InputRowModel(
            UID="C-1", SampleType="Blood", json_metadata="{}",
            assay_ids=[10], assay_titles=["User Assay"],
        )
        parent_child_rels = {"C-1": {"P-1"}}
        assays_by_uid = {"C-1": {10}, "P-1": {10}}

        rows = build_derived_from_payloads_from_db(
            parent_child_rels, conn, assays_by_uid, outcomes, [model],
        )
        assert len(rows) == 1
        assert rows[0].internal_assay_title == "User Assay"
        # Only 2 SQL calls (parent lookup + child metadata), no assay title fetch
        assert conn.execute.call_count == 2

    def test_no_shared_assays_skips_bulk_fetch(self):
        """When no edges share assays, no assay title query should be issued."""
        conn = MagicMock()
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201)]
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, "{}")]
        conn.execute.side_effect = [parent_result, child_result]

        outcomes = {"C-1": _outcome("success", sample_id=101)}
        models = [_input("C-1")]
        parent_child_rels = {"C-1": {"P-1"}}
        # No overlap in assays
        assays_by_uid = {"C-1": {10}, "P-1": {20}}

        rows = build_derived_from_payloads_from_db(
            parent_child_rels, conn, assays_by_uid, outcomes, models,
        )
        assert len(rows) == 1
        assert rows[0].internal_assay_id is None
        assert rows[0].internal_assay_title is None
        # Only 2 SQL calls (parent lookup + child metadata)
        assert conn.execute.call_count == 2

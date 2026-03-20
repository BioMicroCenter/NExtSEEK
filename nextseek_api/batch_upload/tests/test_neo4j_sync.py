"""Unit tests for neo4j_sync payload builders and merge functions."""
import json
import pytest
from unittest.mock import MagicMock, call, patch

from nextseek_api.batch_upload.models import (
    InStudyRelRow,
    InputRowModel,
    InsertableSample,
    NodeRow,
    OfTypeRelRow,
    RowOutcome,
)
from nextseek_api.batch_upload.neo4j_sync import (
    _json_loads,
    _resolve_internal_assays,
    build_derived_from_payloads_from_db,
    build_in_study_payloads,
    build_in_study_payloads_enriched,
    build_of_type_payloads,
    build_payloads,
    build_sample_type_node_payloads,
    build_study_node_payloads,
    bulk_merge_in_study_relationships,
    delete_derived_from_for_uuids,
    enrich_parent_titles,
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


# ── TestBuildSampleTypeNodePayloads ─────────────────────────────────────────


class TestBuildSampleTypeNodePayloads:
    """Tests for build_sample_type_node_payloads."""

    def test_basic_single_type(self):
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [_input("UID-1")]
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(10, "Blood")]
        rows = build_sample_type_node_payloads(outcomes, models, conn)
        assert len(rows) == 1
        assert rows[0].title == "Blood"
        assert rows[0].id == 10

    def test_multiple_types(self):
        outcomes = {
            "UID-1": _outcome("success", sample_id=100),
            "UID-2": _outcome("success", sample_id=200),
        }
        models = [
            InputRowModel(UID="UID-1", SampleType="Blood", json_metadata="{}"),
            InputRowModel(UID="UID-2", SampleType="Tissue", json_metadata="{}"),
        ]
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(10, "Blood"), (20, "Tissue")]
        rows = build_sample_type_node_payloads(outcomes, models, conn)
        assert len(rows) == 2
        id_by_title = {r.title: r.id for r in rows}
        assert id_by_title["Blood"] == 10
        assert id_by_title["Tissue"] == 20

    def test_deduplication(self):
        outcomes = {
            "UID-1": _outcome("success", sample_id=100),
            "UID-2": _outcome("success", sample_id=200),
        }
        models = [_input("UID-1"), _input("UID-2")]
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(10, "Blood")]
        rows = build_sample_type_node_payloads(outcomes, models, conn)
        assert len(rows) == 1
        assert rows[0].title == "Blood"
        assert rows[0].id == 10

    def test_missing_sample_type_in_db(self):
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [_input("UID-1")]
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        with patch("nextseek_api.batch_upload.neo4j_sync.log") as mock_log:
            rows = build_sample_type_node_payloads(outcomes, models, conn)
        assert len(rows) == 1
        assert rows[0].title == "Blood"
        assert rows[0].id is None
        mock_log.warning.assert_called_once()

    def test_empty_outcomes(self):
        outcomes = {}
        models = []
        conn = MagicMock()
        rows = build_sample_type_node_payloads(outcomes, models, conn)
        assert rows == []
        conn.execute.assert_not_called()

    def test_skips_failed_outcomes(self):
        outcomes = {
            "UID-1": _outcome("failed", sample_id=None),
            "UID-2": _outcome("success", sample_id=200),
        }
        models = [
            InputRowModel(UID="UID-1", SampleType="Blood", json_metadata="{}"),
            InputRowModel(UID="UID-2", SampleType="Tissue", json_metadata="{}"),
        ]
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(20, "Tissue")]
        rows = build_sample_type_node_payloads(outcomes, models, conn)
        assert len(rows) == 1
        assert rows[0].title == "Tissue"
        assert rows[0].id == 20

    def test_chunking(self):
        titles = [f"Type_{i}" for i in range(1500)]
        outcomes = {f"UID-{i}": _outcome("success", sample_id=i+1) for i in range(1500)}
        models = [
            InputRowModel(UID=f"UID-{i}", SampleType=titles[i], json_metadata="{}")
            for i in range(1500)
        ]
        conn = MagicMock()
        chunk1_results = [(i+1, titles[i]) for i in range(1000)]
        chunk2_results = [(i+1, titles[i]) for i in range(1000, 1500)]
        result_mock_1 = MagicMock()
        result_mock_1.fetchall.return_value = chunk1_results
        result_mock_2 = MagicMock()
        result_mock_2.fetchall.return_value = chunk2_results
        conn.execute.side_effect = [result_mock_1, result_mock_2]
        rows = build_sample_type_node_payloads(outcomes, models, conn)
        assert len(rows) == 1500
        assert conn.execute.call_count == 2
        assert all(r.id is not None for r in rows)


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
    """Verify assay titles are bulk-fetched, not lazy-loaded one-by-one.

    These tests focus on bulk fetch behaviour and fallback title resolution,
    so we patch _resolve_internal_assays (junction-table lookup) to isolate
    the fallback path.
    """

    @patch("nextseek_api.batch_upload.neo4j_sync._resolve_internal_assays", return_value={})
    def test_bulk_fetches_assay_titles_single_query(self, _mock_resolve):
        """Multiple shared assay IDs should be fetched in one bulk query, not N queries."""
        conn = MagicMock()
        # Step 0: parent UUID lookup — two parents
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201), ("P-2", 202)]
        # Step 1: child metadata — two children, no protocol
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, "{}"), (102, "{}")]
        # Step 2: no sop_ids -> no sop query needed
        # Step 3: _resolve_internal_assays patched out -> empty
        # Step 4 (fallback bulk): assay titles for IDs 10, 20
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

        # Verify: exactly 3 SQL calls (parent lookup, child metadata, fallback assay titles)
        # Junction table lookup is patched out, so no extra SQL call for it.
        assert conn.execute.call_count == 3

        # Verify the assay titles were resolved via fallback
        titles = {r.internal_assay_title for r in rows}
        assert titles == {"Assay A", "Assay B"}

    @patch(
        "nextseek_api.batch_upload.neo4j_sync._resolve_internal_assays",
        return_value={10: (10, "DB Resolved Title")},
    )
    def test_provided_assay_titles_skip_db_fetch(self, _mock_resolve):
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
        # User-provided title overrides the DB-resolved title
        assert rows[0].internal_assay_title == "User Assay"
        # internal_assay_id comes from junction table resolution
        assert rows[0].internal_assay_id == 10
        # Only 2 SQL calls (parent lookup + child metadata) — junction table is patched,
        # and all assay_ids resolved via primary mapping so no fallback query needed.
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


# ── TestInternalAssayResolution ──────────────────────────────────────────────


class TestInternalAssayResolution:
    """Verify _resolve_internal_assays() junction table lookup and its integration
    with build_derived_from_payloads_from_db().
    """

    # ── _resolve_internal_assays() unit tests (mocked SQL) ──────────────

    @patch("django.conf.settings")
    def test_resolve_normal_case(self, mock_settings):
        """Normal case: returns correct mapping assay_id -> (internal_assay_id, title)."""
        mock_settings.NEXTSEEK_DATABASE = "default"
        mock_settings.DATABASES = {"default": {"NAME": "testdb"}}

        conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (50, 10, "Internal Assay Alpha"),
            (60, 20, "Internal Assay Beta"),
        ]
        conn.execute.return_value = mock_result

        result = _resolve_internal_assays({10, 20}, conn)
        assert result == {
            10: (50, "Internal Assay Alpha"),
            20: (60, "Internal Assay Beta"),
        }
        assert conn.execute.call_count == 1

    def test_resolve_empty_input(self):
        """Empty input returns empty dict without executing SQL."""
        conn = MagicMock()
        result = _resolve_internal_assays(set(), conn)
        assert result == {}
        conn.execute.assert_not_called()

    @patch("django.conf.settings")
    def test_resolve_1_to_n_keeps_smallest(self, mock_settings):
        """1:N junction mapping: multiple internal_assay_ids per assay_id -> keeps smallest."""
        mock_settings.NEXTSEEK_DATABASE = "default"
        mock_settings.DATABASES = {"default": {"NAME": "testdb"}}

        conn = MagicMock()
        mock_result = MagicMock()
        # assay_id=10 maps to internal_assay_id 200 and 50 — should keep 50
        mock_result.fetchall.return_value = [
            (200, 10, "IA 200"),
            (50, 10, "IA 50"),
        ]
        conn.execute.return_value = mock_result

        result = _resolve_internal_assays({10}, conn)
        assert result == {10: (50, "IA 50")}

    # ── End-to-end through build_derived_from_payloads_from_db() ────────

    @patch("django.conf.settings")
    def test_e2e_all_resolved_via_junction(self, mock_settings):
        """All shared assay_ids have junction mapping -> uses real internal_assay_id."""
        mock_settings.NEXTSEEK_DATABASE = "default"
        mock_settings.DATABASES = {"default": {"NAME": "testdb"}}

        conn = MagicMock()
        # Step 0: parent UUID lookup
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201)]
        # Step 1: child metadata
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, "{}")]
        # Step 3b: junction table query -> assay_id=10 maps to internal_assay_id=50
        junction_result = MagicMock()
        junction_result.fetchall.return_value = [(50, 10, "Internal Assay X")]
        # No fallback needed (all resolved)
        conn.execute.side_effect = [parent_result, child_result, junction_result]

        outcomes = {"C-1": _outcome("success", sample_id=101)}
        models = [_input("C-1")]
        parent_child_rels = {"C-1": {"P-1"}}
        assays_by_uid = {"C-1": {10}, "P-1": {10}}

        rows = build_derived_from_payloads_from_db(
            parent_child_rels, conn, assays_by_uid, outcomes, models,
        )
        assert len(rows) == 1
        assert rows[0].internal_assay_id == 50
        assert rows[0].internal_assay_title == "Internal Assay X"
        # 3 SQL calls: parent lookup, child metadata, junction table
        assert conn.execute.call_count == 3

    @patch("django.conf.settings")
    def test_e2e_no_junction_mapping_falls_back(self, mock_settings):
        """No junction mapping -> falls back to assay_id as internal_assay_id, assays.title as title."""
        mock_settings.NEXTSEEK_DATABASE = "default"
        mock_settings.DATABASES = {"default": {"NAME": "testdb"}}

        conn = MagicMock()
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201)]
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, "{}")]
        # Junction table returns nothing for assay_id=10
        junction_result = MagicMock()
        junction_result.fetchall.return_value = []
        # Fallback: fetch from assays table
        fallback_result = MagicMock()
        fallback_result.fetchall.return_value = [(10, "Fallback Assay Title")]
        conn.execute.side_effect = [parent_result, child_result, junction_result, fallback_result]

        outcomes = {"C-1": _outcome("success", sample_id=101)}
        models = [_input("C-1")]
        parent_child_rels = {"C-1": {"P-1"}}
        assays_by_uid = {"C-1": {10}, "P-1": {10}}

        rows = build_derived_from_payloads_from_db(
            parent_child_rels, conn, assays_by_uid, outcomes, models,
        )
        assert len(rows) == 1
        # Fallback: internal_assay_id == assay_id, title from assays table
        assert rows[0].internal_assay_id == 10
        assert rows[0].internal_assay_title == "Fallback Assay Title"
        # 4 SQL calls: parent, child, junction (empty), fallback
        assert conn.execute.call_count == 4

    @patch("django.conf.settings")
    def test_e2e_mixed_resolution(self, mock_settings):
        """Mixed: some assay_ids have junction mapping, some don't -> correct resolution for each."""
        mock_settings.NEXTSEEK_DATABASE = "default"
        mock_settings.DATABASES = {"default": {"NAME": "testdb"}}

        conn = MagicMock()
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201), ("P-2", 202)]
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, "{}"), (102, "{}")]
        # Junction: assay_id=10 resolved, assay_id=20 not
        junction_result = MagicMock()
        junction_result.fetchall.return_value = [(50, 10, "IA via Junction")]
        # Fallback: assay_id=20 only (10 is already resolved)
        fallback_result = MagicMock()
        fallback_result.fetchall.return_value = [(20, "Fallback Title")]
        conn.execute.side_effect = [parent_result, child_result, junction_result, fallback_result]

        outcomes = {
            "C-1": _outcome("success", sample_id=101),
            "C-2": _outcome("success", sample_id=102),
        }
        models = [_input("C-1"), _input("C-2")]
        parent_child_rels = {"C-1": {"P-1"}, "C-2": {"P-2"}}
        assays_by_uid = {
            "C-1": {10},
            "P-1": {10},
            "C-2": {20},
            "P-2": {20},
        }

        rows = build_derived_from_payloads_from_db(
            parent_child_rels, conn, assays_by_uid, outcomes, models,
        )
        assert len(rows) == 2

        row_map = {r.child_uuid: r for r in rows}
        # C-1 -> junction-resolved
        assert row_map["C-1"].internal_assay_id == 50
        assert row_map["C-1"].internal_assay_title == "IA via Junction"
        # C-2 -> fallback (assay_id as internal_assay_id)
        assert row_map["C-2"].internal_assay_id == 20
        assert row_map["C-2"].internal_assay_title == "Fallback Title"
        # 4 SQL calls: parent, child, junction, fallback
        assert conn.execute.call_count == 4

    def test_e2e_empty_shared_assays(self):
        """Empty shared assays -> internal_assay_id = None."""
        conn = MagicMock()
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201)]
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, "{}")]
        conn.execute.side_effect = [parent_result, child_result]

        outcomes = {"C-1": _outcome("success", sample_id=101)}
        models = [_input("C-1")]
        parent_child_rels = {"C-1": {"P-1"}}
        # No shared assays at all
        assays_by_uid = {"C-1": {10}, "P-1": {20}}

        rows = build_derived_from_payloads_from_db(
            parent_child_rels, conn, assays_by_uid, outcomes, models,
        )
        assert len(rows) == 1
        assert rows[0].internal_assay_id is None
        assert rows[0].internal_assay_title is None
        # Only 2 SQL calls (parent + child), no junction or fallback
        assert conn.execute.call_count == 2

    @patch("django.conf.settings")
    def test_e2e_min_internal_assay_id_not_min_assay_id(self, mock_settings):
        """min(internal_assay_id) is selected, not min(assay_id).

        assay_id=100 maps to internal_assay_id=5,
        assay_id=50 maps to internal_assay_id=200
        -> picks internal_assay_id=5 (from assay_id=100), not assay_id=50.
        """
        mock_settings.NEXTSEEK_DATABASE = "default"
        mock_settings.DATABASES = {"default": {"NAME": "testdb"}}

        conn = MagicMock()
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201)]
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, "{}")]
        # Junction: both assay_ids resolve via junction table
        junction_result = MagicMock()
        junction_result.fetchall.return_value = [
            (5, 100, "IA Five"),       # assay_id=100 -> internal_assay_id=5
            (200, 50, "IA Two Hundred"),  # assay_id=50 -> internal_assay_id=200
        ]
        conn.execute.side_effect = [parent_result, child_result, junction_result]

        outcomes = {"C-1": _outcome("success", sample_id=101)}
        models = [_input("C-1")]
        parent_child_rels = {"C-1": {"P-1"}}
        # Both assay_ids 50 and 100 are shared
        assays_by_uid = {"C-1": {50, 100}, "P-1": {50, 100}}

        rows = build_derived_from_payloads_from_db(
            parent_child_rels, conn, assays_by_uid, outcomes, models,
        )
        assert len(rows) == 1
        # Should pick internal_assay_id=5 (smallest), NOT 200 (from smaller assay_id=50)
        assert rows[0].internal_assay_id == 5
        assert rows[0].internal_assay_title == "IA Five"


# ── TestEnrichParentTitles ─────────────────────────────────────────────────


def _node_row(sample_id, uuid, sample_type, props):
    """Helper to create a NodeRow with given properties."""
    return NodeRow(sample_id=sample_id, sample_uuid=uuid, sample_type=sample_type, properties=props)


def _input_model(uid, sample_type="Blood", meta=None):
    """Helper to create an InputRowModel."""
    if meta is None:
        meta = "{}"
    return InputRowModel(UID=uid, SampleType=sample_type, json_metadata=meta)


class TestEnrichParentTitles:
    """Tests for enrich_parent_titles: resolves parent identities for Neo4j."""

    def test_resolved_uid_parent_gets_title_from_in_batch(self):
        """Parent has a UID that exists in current batch -> use that sample's Name as identity."""
        child_props = {"Name": "Child_Sample", "Parent": "NHP-260225MIT-1"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-2", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-2", "Blood", json.dumps({"Name": "Child_Sample", "Parent": "NHP-260225MIT-1"})),
            _input_model("NHP-260225MIT-1", "NHP", json.dumps({"Name": "Parent_Sample"})),
        ]
        enrich_parent_titles(node_rows, input_models, sql_conn=None)
        assert node_rows[0].parent_titles == ["Parent_Sample"]
        assert node_rows[0].properties["parent_titles"] == ["Parent_Sample"]

    def test_unresolved_name_parent_is_identity_itself(self):
        """Parent has a non-UID token (unresolved name) -> use token as-is."""
        child_props = {"Name": "Child_Sample", "Parent": "My_Parent_Name"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-1", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-1", "Blood", json.dumps({"Name": "Child_Sample", "Parent": "My_Parent_Name"})),
        ]
        enrich_parent_titles(node_rows, input_models, sql_conn=None)
        assert node_rows[0].parent_titles == ["My_Parent_Name"]
        assert node_rows[0].properties["parent_titles"] == ["My_Parent_Name"]

    def test_external_uid_parent_gets_title_from_sql(self):
        """Parent has a UID NOT in batch -> SQL lookup returns its Name."""
        child_props = {"Name": "Child_Sample", "Parent": "NHP-260225MIT-99"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-1", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-1", "Blood", json.dumps({"Name": "Child_Sample", "Parent": "NHP-260225MIT-99"})),
        ]
        # Mock SQL connection: returns json_metadata for the external parent
        conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("NHP-260225MIT-99", json.dumps({"Name": "External_Parent"})),
        ]
        conn.execute.return_value = mock_result

        enrich_parent_titles(node_rows, input_models, sql_conn=conn)
        assert node_rows[0].parent_titles == ["External_Parent"]
        assert node_rows[0].properties["parent_titles"] == ["External_Parent"]
        # Verify SQL was called
        assert conn.execute.call_count == 1

    def test_file_based_parent_uses_file_primary_data(self):
        """D./A. prefix parents use File_PrimaryData for identity."""
        child_props = {"Name": "Child_Sample", "Parent": "D.IMG-260225MIT-5"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-1", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-1", "Blood", json.dumps({"Name": "Child_Sample", "Parent": "D.IMG-260225MIT-5"})),
            _input_model("D.IMG-260225MIT-5", "D.IMG_files", json.dumps({"File_PrimaryData": "image_data.tiff"})),
        ]
        enrich_parent_titles(node_rows, input_models, sql_conn=None)
        assert node_rows[0].parent_titles == ["image_data.tiff"]
        assert node_rows[0].properties["parent_titles"] == ["image_data.tiff"]

    def test_mixed_parents_resolved_and_unresolved(self):
        """Parent field with UID + unresolved Name -> both identities."""
        child_props = {"Name": "Child", "Parent": "NHP-260225MIT-3;Unresolved_Name"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-1", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-1", "Blood", json.dumps({"Name": "Child", "Parent": "NHP-260225MIT-3;Unresolved_Name"})),
            _input_model("NHP-260225MIT-3", "NHP", json.dumps({"Name": "Resolved_Parent"})),
        ]
        enrich_parent_titles(node_rows, input_models, sql_conn=None)
        assert node_rows[0].parent_titles == ["Resolved_Parent", "Unresolved_Name"]
        assert node_rows[0].properties["parent_titles"] == ["Resolved_Parent", "Unresolved_Name"]

    def test_no_parents_no_parent_titles(self):
        """No Parent field -> empty parent_titles, NOT in properties."""
        child_props = {"Name": "Lonely_Sample"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-1", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-1", "Blood", json.dumps({"Name": "Lonely_Sample"})),
        ]
        enrich_parent_titles(node_rows, input_models, sql_conn=None)
        assert node_rows[0].parent_titles == []
        assert "parent_titles" not in node_rows[0].properties


class TestParentTitlesIndex:
    """Tests for auto-creation of parent_titles index in upload_all."""

    @patch("neo4j.GraphDatabase")
    def test_upload_all_creates_parent_titles_index(self, mock_gdb):
        """upload_all should CREATE INDEX IF NOT EXISTS on Sample.parent_titles."""
        from nextseek_api.batch_upload.neo4j_sync import upload_all
        from nextseek_api.batch_upload.models import DirectionComputation

        mock_driver = MagicMock()
        mock_gdb.driver.return_value = mock_driver
        mock_driver.execute_query.return_value = MagicMock(
            counters=MagicMock(nodes_created=0, nodes_matched=0, relationships_created=0),
            records=[],
        )

        neo4j_config = MagicMock(
            NEO4J_UPLOAD_ENABLED=True,
            URI="bolt://localhost",
            NEO4J_USER="u",
            PASSWORD="p",
            NEO4J_DB="testdb",
            NEO4J_NODE_CHUNK=500,
            NEO4J_REL_CHUNK=500,
        )

        upload_all(
            outcomes={},
            input_models=[],
            sql_conn=MagicMock(),
            neo4j_config=neo4j_config,
            direction_computation=DirectionComputation(
                parents_of={}, assays_by_uid={},
                direction_by_pair={}, child_uids_by_assay={},
                conflicts_by_assay={},
            ),
        )

        calls = [str(c) for c in mock_driver.execute_query.call_args_list]
        assert any("parent_titles" in c and "CREATE INDEX" in c for c in calls), (
            f"Expected CREATE INDEX for parent_titles in calls: {calls}"
        )


# ── TestBuildInStudyPayloadsEnriched ──────────────────────────────────────


class TestBuildInStudyPayloadsEnriched:
    """Tests for build_in_study_payloads_enriched."""

    def test_uses_input_model_study_id(self):
        """Route 1: Uses study_id from InputRowModel when provided."""
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [InputRowModel(UID="UID-1", SampleType="Blood", json_metadata="{}", study_id=5, study_title="MyStudy")]
        conn = MagicMock()
        rows, warnings, fallback = build_in_study_payloads_enriched(outcomes, models, conn)
        assert len(rows) == 1
        assert rows[0].sample_uuid == "UID-1"
        assert rows[0].study_id == 5
        assert fallback == {5: "MyStudy"}
        conn.execute.assert_not_called()  # no assay lookup needed

    def test_assay_route_when_no_study_id(self):
        """Route 2: Looks up study_id via assay_ids when study_id not provided."""
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [InputRowModel(UID="UID-1", SampleType="Blood", json_metadata="{}", assay_ids=[55, 58])]
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(55, 6), (58, 6)]
        rows, warnings, fallback = build_in_study_payloads_enriched(outcomes, models, conn)
        assert len(rows) == 1
        assert rows[0].study_id == 6
        assert warnings == 0

    def test_assay_route_multiple_studies(self):
        """Sample with assays in different studies gets IN_STUDY rel to each."""
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [InputRowModel(UID="UID-1", SampleType="Blood", json_metadata="{}", assay_ids=[55, 60])]
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(55, 6), (60, 12)]
        rows, warnings, fallback = build_in_study_payloads_enriched(outcomes, models, conn)
        assert len(rows) == 2
        study_ids = {r.study_id for r in rows}
        assert study_ids == {6, 12}

    def test_both_routes_deduplication(self):
        """Sample with study_id=6 AND assay pointing to study 6 -> one row, not two."""
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [InputRowModel(UID="UID-1", SampleType="Blood", json_metadata="{}", study_id=6, study_title="MetNet", assay_ids=[55])]
        conn = MagicMock()
        # Assay route not needed since study_id is provided, but if it were:
        rows, warnings, fallback = build_in_study_payloads_enriched(outcomes, models, conn)
        assert len(rows) == 1
        assert rows[0].study_id == 6

    def test_collects_fallback_titles(self):
        """study_title from InputRowModel collected in fallback dict."""
        outcomes = {
            "UID-1": _outcome("success", sample_id=100),
            "UID-2": _outcome("success", sample_id=200),
        }
        models = [
            InputRowModel(UID="UID-1", SampleType="Blood", json_metadata="{}", study_id=6, study_title="MetNet"),
            InputRowModel(UID="UID-2", SampleType="Blood", json_metadata="{}", study_id=12, study_title="GBM"),
        ]
        conn = MagicMock()
        rows, warnings, fallback = build_in_study_payloads_enriched(outcomes, models, conn)
        assert fallback == {6: "MetNet", 12: "GBM"}

    def test_skips_failed_outcomes(self):
        """Failed outcomes excluded from both routes."""
        outcomes = {"UID-1": _outcome("failed", sample_id=None)}
        models = [InputRowModel(UID="UID-1", SampleType="Blood", json_metadata="{}", study_id=5)]
        conn = MagicMock()
        rows, warnings, fallback = build_in_study_payloads_enriched(outcomes, models, conn)
        assert rows == []

    def test_empty_outcomes(self):
        """Empty outcomes -> empty result, no SQL."""
        conn = MagicMock()
        rows, warnings, fallback = build_in_study_payloads_enriched({}, [], conn)
        assert rows == []
        assert fallback == {}
        conn.execute.assert_not_called()

    def test_assay_with_null_study_id(self):
        """Assay exists but study_id is NULL -> sample gets warning."""
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [InputRowModel(UID="UID-1", SampleType="Blood", json_metadata="{}", assay_ids=[55])]
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(55, None)]  # null study_id
        rows, warnings, fallback = build_in_study_payloads_enriched(outcomes, models, conn)
        assert len(rows) == 0
        assert warnings == 1

    def test_no_study_id_no_assays_warns(self):
        """Sample with no study_id and no assay_ids -> warning."""
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [InputRowModel(UID="UID-1", SampleType="Blood", json_metadata="{}")]
        conn = MagicMock()
        rows, warnings, fallback = build_in_study_payloads_enriched(outcomes, models, conn)
        assert len(rows) == 0
        assert warnings == 1

    def test_chunking_assays(self):
        """More than 1000 assay_ids -> multiple SQL queries."""
        assay_ids = list(range(1, 1501))
        outcomes = {"UID-1": _outcome("success", sample_id=100)}
        models = [InputRowModel(UID="UID-1", SampleType="Blood", json_metadata="{}", assay_ids=assay_ids)]
        conn = MagicMock()
        chunk1 = [(i, 6) for i in range(1, 1001)]
        chunk2 = [(i, 6) for i in range(1001, 1501)]
        r1 = MagicMock(); r1.fetchall.return_value = chunk1
        r2 = MagicMock(); r2.fetchall.return_value = chunk2
        conn.execute.side_effect = [r1, r2]
        rows, warnings, fallback = build_in_study_payloads_enriched(outcomes, models, conn)
        assert len(rows) == 1  # deduplicated to one study
        assert rows[0].study_id == 6
        assert conn.execute.call_count == 2


# ── TestBuildStudyNodePayloadsFallback ────────────────────────────────────


class TestBuildStudyNodePayloadsFallback:
    """Tests for build_study_node_payloads with fallback_titles."""

    def test_fallback_title_used_when_db_missing(self):
        """Study not in DB but in fallback -> StudyNodeRow created with fallback title."""
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []  # not in DB
        fallback = {6: "MetNet"}
        study_rows, inv_rows, inv_rels = build_study_node_payloads({6}, conn, fallback_titles=fallback)
        assert len(study_rows) == 1
        assert study_rows[0].id == 6
        assert study_rows[0].title == "MetNet"

    def test_db_title_preferred_over_fallback(self):
        """Study found in DB -> DB title used, not fallback."""
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(6, "Full DB Title", "desc", None)]
        fallback = {6: "MetNet"}
        study_rows, inv_rows, inv_rels = build_study_node_payloads({6}, conn, fallback_titles=fallback)
        assert len(study_rows) == 1
        assert study_rows[0].title == "Full DB Title"

    def test_no_fallback_dict(self):
        """No fallback provided -> same behavior as before."""
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(6, "Title", "desc", None)]
        study_rows, inv_rows, inv_rels = build_study_node_payloads({6}, conn)
        assert len(study_rows) == 1
        assert study_rows[0].title == "Title"

    def test_db_empty_title_uses_fallback(self):
        """Study in DB with empty title -> fallback title used."""
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(6, "", "desc", None)]
        fallback = {6: "MetNet"}
        study_rows, inv_rows, inv_rels = build_study_node_payloads({6}, conn, fallback_titles=fallback)
        assert len(study_rows) == 1
        assert study_rows[0].title == "MetNet"

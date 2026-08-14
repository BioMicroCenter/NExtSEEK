"""Unit tests for neo4j_sync payload builders and merge functions."""
import json
import pytest
from unittest.mock import MagicMock, call, patch

from django.test import override_settings

from nextseek_api.batch_upload.errors import ErrorCollector, ErrorType, Severity
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
    bulk_merge_relationships,
    delete_derived_from_for_uuids,
    delete_stale_derived_from_for_uuids,
    find_missing_derived_from_endpoints,
    find_missing_in_study_endpoints,
    enrich_parent_titles,
    parents_declared_in_stored_metadata,
    refresh_assays_for_uuids,
)
from nextseek_api.batch_upload.identity import hash_identity


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


# ── #44: the silently-dropped IN_STUDY edge ──────────────────────────────────
#
# `build_in_study_payloads_enriched` warns and counts when it cannot determine a
# study_id at all. The gap is the row that HAS one: `MATCH (st:Study {id: row.study_id})`
# yields nothing when the Study node is absent, so UNWIND drops the row and the query
# returns a smaller `processed` count. No exception, no warning, no counter — the
# upload reports success while the relationship is missing from the graph.


class TestInStudyDropsAreVisible:
    def test_a_short_processed_count_is_warned_about(self, caplog):
        # 5 rows in, the driver reports only 2 merged: 3 endpoints did not exist.
        driver = _mock_driver([2])
        rows = [InStudyRelRow(sample_uuid=f"UID-{i}", study_id=1) for i in range(5)]

        with caplog.at_level("WARNING"):
            total = bulk_merge_in_study_relationships(driver, "testdb", rows)

        assert total == 2
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("IN_STUDY" in m and "3 of 5" in m for m in warnings), warnings

    def test_each_short_chunk_is_warned_about_separately(self, caplog):
        # 5 rows at chunk_size 2 -> chunks of 2, 2, 1.
        # chunk 0: 2 of 2 merged, silent. chunk 1: 0 of 2. chunk 2: 0 of 1.
        driver = _mock_driver([2, 0, 0])
        rows = [InStudyRelRow(sample_uuid=f"UID-{i}", study_id=1) for i in range(5)]

        with caplog.at_level("WARNING"):
            total = bulk_merge_in_study_relationships(driver, "testdb", rows, chunk_size=2)

        assert total == 2
        warnings = [r.getMessage() for r in caplog.records
                    if r.levelname == "WARNING" and "IN_STUDY" in r.getMessage()]
        assert len(warnings) == 2, warnings
        assert "2 of 2" in warnings[0]
        assert "1 of 1" in warnings[1]

    def test_a_fully_matched_merge_stays_silent(self, caplog):
        driver = _mock_driver([5])
        rows = [InStudyRelRow(sample_uuid=f"UID-{i}", study_id=1) for i in range(5)]

        with caplog.at_level("WARNING"):
            total = bulk_merge_in_study_relationships(driver, "testdb", rows)

        assert total == 5
        assert not [r for r in caplog.records if "IN_STUDY" in r.getMessage()]

    def test_a_driver_returning_no_records_counts_the_whole_chunk_as_dropped(self, caplog):
        driver = MagicMock()
        empty = MagicMock()
        empty.records = []
        driver.execute_query = MagicMock(return_value=empty)
        rows = [InStudyRelRow(sample_uuid=f"UID-{i}", study_id=1) for i in range(3)]

        with caplog.at_level("WARNING"):
            total = bulk_merge_in_study_relationships(driver, "testdb", rows)

        assert total == 0
        assert any("3 of 3" in r.getMessage() for r in caplog.records)


class TestFindMissingInStudyEndpoints:
    """The audit that names what the MERGE dropped. READ-ONLY by construction."""

    @staticmethod
    def _driver(missing_studies, missing_samples):
        driver = MagicMock()
        calls = [missing_studies, missing_samples]
        idx = [0]

        def _execute_query(cypher, params, database_=None):
            result = MagicMock()
            result.records = [{"missing": calls[idx[0]]}]
            idx[0] += 1
            return result

        driver.execute_query = MagicMock(side_effect=_execute_query)
        return driver

    def test_no_rows_short_circuits_without_querying(self):
        driver = MagicMock()
        assert find_missing_in_study_endpoints(driver, "testdb", []) == ([], [])
        driver.execute_query.assert_not_called()

    def test_missing_endpoints_are_reported_sorted(self):
        driver = self._driver([9, 7], ["UID-b", "UID-a"])
        rows = [InStudyRelRow(sample_uuid="UID-a", study_id=7),
                InStudyRelRow(sample_uuid="UID-b", study_id=9)]

        studies, samples = find_missing_in_study_endpoints(driver, "testdb", rows)

        assert studies == [7, 9]
        assert samples == ["UID-a", "UID-b"]

    def test_nothing_missing_returns_empty_lists(self):
        driver = self._driver([], [])
        rows = [InStudyRelRow(sample_uuid="UID-a", study_id=7)]
        assert find_missing_in_study_endpoints(driver, "testdb", rows) == ([], [])

    def test_the_audit_never_mutates_the_graph(self):
        driver = self._driver([7], [])
        rows = [InStudyRelRow(sample_uuid="UID-a", study_id=7)]
        find_missing_in_study_endpoints(driver, "testdb", rows)

        for call_args in driver.execute_query.call_args_list:
            cypher = call_args[0][0].upper()
            for mutating in ("MERGE", "CREATE", "DELETE", "SET ", "REMOVE"):
                assert mutating not in cypher, f"audit query mutates: {cypher}"

    def test_endpoints_are_deduplicated_before_querying(self):
        driver = self._driver([], [])
        rows = [InStudyRelRow(sample_uuid="UID-a", study_id=7),
                InStudyRelRow(sample_uuid="UID-a", study_id=7),
                InStudyRelRow(sample_uuid="UID-b", study_id=7)]

        find_missing_in_study_endpoints(driver, "testdb", rows)

        study_params = driver.execute_query.call_args_list[0][0][1]
        sample_params = driver.execute_query.call_args_list[1][0][1]
        assert study_params["ids"] == [7]
        assert sample_params["uuids"] == ["UID-a", "UID-b"]


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

    def test_external_file_based_parent_uses_uid_prefix_for_identity(self):
        """External file-based parent lookup should derive identity via shared extract_identity."""
        child_props = {"Name": "Child_Sample", "Parent": "D.IMG-260225MIT-99"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-1", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-1", "Blood", json.dumps({"Name": "Child_Sample", "Parent": "D.IMG-260225MIT-99"})),
        ]
        conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("D.IMG-260225MIT-99", json.dumps({"File_PrimartyData": "external_image.tiff"})),
        ]
        conn.execute.return_value = mock_result

        enrich_parent_titles(node_rows, input_models, sql_conn=conn)
        assert node_rows[0].parent_titles == ["external_image.tiff"]
        assert node_rows[0].properties["parent_titles"] == ["external_image.tiff"]

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

    def test_resolved_parent_populates_hash(self):
        """Hash is parallel to title and computed via hash_identity."""
        child_props = {"Name": "Child_Sample", "Parent": "NHP-260225MIT-1"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-2", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-2", "Blood", json.dumps({"Name": "Child_Sample", "Parent": "NHP-260225MIT-1"})),
            _input_model("NHP-260225MIT-1", "NHP", json.dumps({"Name": "Parent_Sample"})),
        ]
        enrich_parent_titles(node_rows, input_models, sql_conn=None)
        expected_hash = hash_identity("Parent_Sample")
        assert node_rows[0].parent_title_hashes == [expected_hash]
        assert node_rows[0].properties["parent_title_hashes"] == [expected_hash]

    def test_hashes_parallel_to_titles_for_mixed_parents(self):
        """Mixed resolved + unresolved parents yield hashes parallel to titles."""
        child_props = {"Name": "Child", "Parent": "NHP-260225MIT-3;Unresolved_Name"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-1", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-1", "Blood", json.dumps({"Name": "Child", "Parent": "NHP-260225MIT-3;Unresolved_Name"})),
            _input_model("NHP-260225MIT-3", "NHP", json.dumps({"Name": "Resolved_Parent"})),
        ]
        enrich_parent_titles(node_rows, input_models, sql_conn=None)
        titles = node_rows[0].parent_titles
        hashes = node_rows[0].parent_title_hashes
        assert len(titles) == len(hashes) == 2
        for title, h in zip(titles, hashes):
            assert h == hash_identity(title)
        assert node_rows[0].properties["parent_title_hashes"] == hashes

    def test_no_parents_no_parent_title_hashes_in_properties(self):
        """No parents -> parent_title_hashes empty AND not in properties."""
        child_props = {"Name": "Lonely_Sample"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-1", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-1", "Blood", json.dumps({"Name": "Lonely_Sample"})),
        ]
        enrich_parent_titles(node_rows, input_models, sql_conn=None)
        assert node_rows[0].parent_title_hashes == []
        assert "parent_title_hashes" not in node_rows[0].properties


class TestEnrichParentTitlesVariantKeys:
    """Test that enrich_parent_titles reads ALL parent-containing keys."""

    def test_treatment1parent_gets_title(self):
        """Treatment1Parent variant key should contribute to parent_titles."""
        child_props = {"Name": "Child_Sample", "Treatment1Parent": "NHP-260225MIT-1"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-2", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-2", "Blood", json.dumps({"Name": "Child_Sample", "Treatment1Parent": "NHP-260225MIT-1"})),
            _input_model("NHP-260225MIT-1", "NHP", json.dumps({"Name": "Treatment_Parent"})),
        ]
        enrich_parent_titles(node_rows, input_models, sql_conn=None)
        assert node_rows[0].parent_titles == ["Treatment_Parent"]
        assert node_rows[0].properties["parent_titles"] == ["Treatment_Parent"]

    def test_multiple_variant_keys_merged_in_titles(self):
        """Parent + Treatment1Parent both contribute to parent_titles."""
        child_props = {
            "Name": "Child_Sample",
            "Parent": "NHP-260225MIT-1",
            "Treatment1Parent": "NHP-260225MIT-3",
        }
        node_rows = [
            _node_row(100, "NHP-260225MIT-2", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-2", "Blood", json.dumps({
                "Name": "Child_Sample",
                "Parent": "NHP-260225MIT-1",
                "Treatment1Parent": "NHP-260225MIT-3",
            })),
            _input_model("NHP-260225MIT-1", "NHP", json.dumps({"Name": "Parent_A"})),
            _input_model("NHP-260225MIT-3", "NHP", json.dumps({"Name": "Parent_B"})),
        ]
        enrich_parent_titles(node_rows, input_models, sql_conn=None)
        assert set(node_rows[0].parent_titles) == {"Parent_A", "Parent_B"}
        assert len(node_rows[0].parent_titles) == 2

    def test_variant_key_unresolved_name_as_identity(self):
        """Unresolved name in variant key should be used as-is in parent_titles."""
        child_props = {"Name": "Child_Sample", "AntibodyParent": "My_Antibody_Parent"}
        node_rows = [
            _node_row(100, "NHP-260225MIT-1", "Blood", child_props),
        ]
        input_models = [
            _input_model("NHP-260225MIT-1", "Blood", json.dumps({
                "Name": "Child_Sample", "AntibodyParent": "My_Antibody_Parent",
            })),
        ]
        enrich_parent_titles(node_rows, input_models, sql_conn=None)
        assert node_rows[0].parent_titles == ["My_Antibody_Parent"]


class TestUploadAllInStudyCoverageMetrics:
    """#44: the drop must reach Metrics, not just the log.

    `in_study_warnings` already existed but only ever counted rows the BUILDER
    rejected. A row that reached the MERGE and was dropped by its MATCH left every
    counter untouched, so a caller reading Metrics saw a clean run.
    """

    @staticmethod
    def _run(merged_count, rows_count=5, missing=([7], ["UID-2"])):
        from nextseek_api.batch_upload.neo4j_sync import upload_all
        from nextseek_api.batch_upload.models import DirectionComputation

        rows = [InStudyRelRow(sample_uuid=f"UID-{i}", study_id=7)
                for i in range(rows_count)]

        with patch("neo4j.GraphDatabase") as mock_gdb, \
             patch("nextseek_api.batch_upload.neo4j_sync."
                   "build_in_study_payloads_enriched", return_value=(rows, 0, {})), \
             patch("nextseek_api.batch_upload.neo4j_sync."
                   "build_study_node_payloads", return_value=([], [], [])), \
             patch("nextseek_api.batch_upload.neo4j_sync."
                   "bulk_merge_in_study_relationships",
                   return_value=merged_count) as merge, \
             patch("nextseek_api.batch_upload.neo4j_sync."
                   "find_missing_in_study_endpoints",
                   return_value=missing) as audit:
            mock_driver = MagicMock()
            mock_gdb.driver.return_value = mock_driver
            mock_driver.execute_query.return_value = MagicMock(
                counters=MagicMock(nodes_created=0, nodes_matched=0,
                                   relationships_created=0),
                records=[],
            )

            metrics = upload_all(
                outcomes={},
                input_models=[],
                sql_conn=MagicMock(),
                neo4j_config=MagicMock(
                    NEO4J_UPLOAD_ENABLED=True, URI="bolt://localhost",
                    NEO4J_USER="u", PASSWORD="p", NEO4J_DB="testdb",
                    NEO4J_NODE_CHUNK=500, NEO4J_REL_CHUNK=500,
                ),
                direction_computation=DirectionComputation(
                    parents_of={}, assays_by_uid={}, direction_by_pair={},
                    child_uids_by_assay={}, conflicts_by_assay={},
                ),
            )
            return metrics, merge, audit

    def test_dropped_edges_are_counted(self):
        metrics, _merge, _audit = self._run(merged_count=2, rows_count=5)

        assert metrics.in_study_rels_attempted == 5
        assert metrics.in_study_rels_created == 2
        assert metrics.in_study_rels_dropped == 3

    def test_dropped_edges_reach_the_existing_warning_counter(self):
        """So a caller already reading in_study_warnings sees the silent drops."""
        metrics, _merge, _audit = self._run(merged_count=2, rows_count=5)
        assert metrics.in_study_warnings == 3

    def test_the_audit_runs_only_when_something_was_dropped(self):
        _metrics, _merge, audit = self._run(merged_count=5, rows_count=5)
        audit.assert_not_called()

        _metrics, _merge, audit = self._run(merged_count=1, rows_count=5)
        audit.assert_called_once()

    def test_a_complete_merge_reports_zero_dropped(self):
        metrics, _merge, _audit = self._run(merged_count=5, rows_count=5)

        assert metrics.in_study_rels_attempted == 5
        assert metrics.in_study_rels_dropped == 0
        assert metrics.in_study_warnings == 0

    def test_coverage_is_auditable_from_metrics_alone(self):
        """attempted == created + dropped, so a reader needs no log access."""
        metrics, _merge, _audit = self._run(merged_count=2, rows_count=5)
        assert (metrics.in_study_rels_created
                + metrics.in_study_rels_dropped) == metrics.in_study_rels_attempted


class TestParentTitlesIndex:
    """Tests for the parent_title_hashes index swap in upload_all."""

    def _run_upload_all_capturing_calls(self):
        from nextseek_api.batch_upload.neo4j_sync import upload_all
        from nextseek_api.batch_upload.models import DirectionComputation

        with patch("neo4j.GraphDatabase") as mock_gdb:
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

            return [str(c) for c in mock_driver.execute_query.call_args_list]

    def test_upload_all_drops_old_parent_titles_index(self):
        """upload_all should DROP the legacy sample_parent_titles index."""
        calls = self._run_upload_all_capturing_calls()
        assert any(
            "DROP INDEX" in c and "sample_parent_titles" in c and "IF EXISTS" in c
            for c in calls
        ), f"Expected DROP INDEX sample_parent_titles IF EXISTS in calls: {calls}"

    def test_upload_all_creates_parent_title_hashes_index(self):
        """upload_all should CREATE INDEX on Sample.parent_title_hashes."""
        calls = self._run_upload_all_capturing_calls()
        assert any(
            "CREATE INDEX" in c
            and "sample_parent_title_hashes" in c
            and "parent_title_hashes" in c
            and "IF NOT EXISTS" in c
            for c in calls
        ), f"Expected CREATE INDEX sample_parent_title_hashes IF NOT EXISTS in calls: {calls}"

    def test_upload_all_does_not_create_legacy_index(self):
        """upload_all must no longer create the legacy parent_titles index."""
        calls = self._run_upload_all_capturing_calls()
        legacy_creates = [
            c for c in calls
            if "CREATE INDEX" in c
            and "sample_parent_titles " in c  # trailing space distinguishes from sample_parent_title_hashes
        ]
        assert legacy_creates == [], (
            f"Legacy CREATE INDEX sample_parent_titles must be removed; found: {legacy_creates}"
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


# ── TestDerivedFromProtocolResolution ───────────────────────────────────────


class TestDerivedFromProtocolResolution:
    """Protocol -> protocol_id on the DERIVED_FROM edge.

    Production stores the SOP *title* in Protocol for 97,767 of 163,393
    samples and an internal ``/sops/<id>`` URL for only 4,446, so resolving by
    URL alone silently wrote a null protocol on almost every 4-sheet upload
    (that path has no ``sop_id`` column at all).
    """

    _LOCAL = dict(
        SEEK_PUBLIC_URL="http://localhost:3000",
        SEEK_URL="http://seek:3000",
        ALLOWED_HOSTS=["127.0.0.1"],
    )

    @staticmethod
    def _run(protocol, sop_rows=None, title_rows=None, model=None, ec=None):
        """One child (sample_id 101, UID C-1) with one parent, given Protocol.

        ``title_rows`` is the SELECT id,title FROM sops WHERE title IN (...)
        result; ``sop_rows`` is the SELECT id,title ... WHERE id IN (...) one.
        """
        conn = MagicMock()
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201)]
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, json.dumps({"Protocol": protocol}))]

        results = [parent_result, child_result]
        if title_rows is not None:
            by_title = MagicMock()
            by_title.fetchall.return_value = title_rows
            results.append(by_title)
        if sop_rows is not None:
            by_id = MagicMock()
            by_id.fetchall.return_value = sop_rows
            results.append(by_id)
        conn.execute.side_effect = results

        rows = build_derived_from_payloads_from_db(
            {"C-1": {"P-1"}},
            conn,
            {},
            {"C-1": _outcome("success", sample_id=101)},
            [model or _input("C-1")],
            error_collector=ec,
        )
        return rows, conn

    # ── format 1: /sops/<id> (the only shape that worked before) ────────
    def test_internal_sops_url_still_resolves(self):
        rows, _ = self._run("/sops/5", sop_rows=[(5, "SOP Five")])
        assert rows[0].protocol_id == 5
        assert rows[0].protocol_title == "SOP Five"

    # ── format 2: uid=<title> URL ──────────────────────────────────────
    def test_uid_url_resolves_by_title(self):
        with override_settings(**self._LOCAL):
            rows, _ = self._run(
                "http://127.0.0.1:8000/seek/sop/uid=P.FOR-200623-V1_x.docx/",
                title_rows=[(7, "P.FOR-200623-V1_x.docx")],
                sop_rows=[(7, "P.FOR-200623-V1_x.docx")],
            )
        assert rows[0].protocol_id == 7
        assert rows[0].protocol_title == "P.FOR-200623-V1_x.docx"

    # ── format 3: bare title — the production majority ─────────────────
    def test_bare_title_resolves(self):
        rows, _ = self._run(
            "P.FOR-200623-V1_x.docx",
            title_rows=[(7, "P.FOR-200623-V1_x.docx")],
            sop_rows=[(7, "P.FOR-200623-V1_x.docx")],
        )
        assert rows[0].protocol_id == 7
        assert rows[0].protocol_title == "P.FOR-200623-V1_x.docx"

    def test_bare_title_that_matches_nothing_yields_no_protocol(self):
        rows, _ = self._run("No Such SOP", title_rows=[])
        assert rows[0].protocol_id is None
        assert rows[0].protocol_title is None

    def test_ambiguous_title_is_not_resolved(self):
        """Two SOPs share the title: guess nothing (dbtable_sample's rule)."""
        rows, _ = self._run("Dup SOP", title_rows=[(7, "Dup SOP"), (9, "Dup SOP")])
        assert rows[0].protocol_id is None

    # ── the external-host mis-record path ──────────────────────────────
    def test_fairdomhub_url_does_not_stamp_a_local_sop(self):
        """1,855 live Protocol values are fairdomhub.org URLs. The unanchored
        regex turned https://fairdomhub.org/sops/795 into local sops.id 795."""
        with override_settings(**self._LOCAL):
            rows, conn = self._run("https://fairdomhub.org/sops/795", title_rows=[])
        assert rows[0].protocol_id is None
        # And no SOP was fetched by id either — 795 never became a local id.
        assert not any(
            "FROM sops WHERE id IN" in str(c[0][0])
            for c in conn.execute.call_args_list
        )

    # ── precedence: a sheet-supplied sop_id still wins ─────────────────
    def test_sheet_sop_id_beats_a_resolvable_title(self):
        model = InputRowModel(
            UID="C-1", SampleType="Blood",
            json_metadata=json.dumps({"Protocol": "P.FOR-200623-V1_x.docx"}),
            sop_id=42,
        )
        rows, conn = self._run(
            "P.FOR-200623-V1_x.docx", sop_rows=[(42, "Sheet SOP")], model=model,
        )
        assert rows[0].protocol_id == 42
        assert rows[0].protocol_title == "Sheet SOP"
        # No title lookup was issued at all — the sheet short-circuits it.
        assert not any(
            "WHERE title IN" in str(c[0][0]) for c in conn.execute.call_args_list
        )

    def test_sheet_sop_id_beats_an_external_url(self):
        """A local sop_id and a foreign SOP URL can legitimately disagree —
        unlike a LOCAL /sops/<id> URL, which InputRowModel already refuses to
        let contradict sop_id, so that pairing cannot distinguish anything."""
        model = InputRowModel(
            UID="C-1", SampleType="Blood",
            json_metadata=json.dumps({"Protocol": "https://fairdomhub.org/sops/795"}),
            sop_id=42,
        )
        with override_settings(**self._LOCAL):
            rows, _ = self._run(
                "https://fairdomhub.org/sops/795",
                sop_rows=[(42, "Sheet SOP")],
                model=model,
            )
        assert rows[0].protocol_id == 42
        assert rows[0].protocol_title == "Sheet SOP"

    # ── no Protocol at all: unchanged, and not reported ────────────────
    def test_absent_protocol_issues_no_title_lookup(self):
        conn = MagicMock()
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201)]
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, "{}")]
        conn.execute.side_effect = [parent_result, child_result]

        ec = ErrorCollector()
        rows = build_derived_from_payloads_from_db(
            {"C-1": {"P-1"}}, conn, {},
            {"C-1": _outcome("success", sample_id=101)}, [_input("C-1")],
            error_collector=ec,
        )
        assert rows[0].protocol_id is None
        assert conn.execute.call_count == 2
        assert ec.all_errors() == []

    # ── an unresolved Protocol must be visible, not a silent null ──────
    def test_unresolved_protocol_is_collected_as_an_error(self):
        ec = ErrorCollector()
        model = InputRowModel(
            UID="C-1", SampleType="Blood",
            json_metadata=json.dumps({"Protocol": "No Such SOP"}),
            original_row_index=3,
        )
        self._run("No Such SOP", title_rows=[], model=model, ec=ec)

        errs = ec.errors_for_uid("C-1")
        assert len(errs) == 1
        assert errs[0].error_type is ErrorType.PROTOCOL_UNRESOLVED
        assert errs[0].severity is Severity.WARNING
        assert errs[0].row_index == 3
        assert "No Such SOP" in errs[0].message

    def test_ambiguous_protocol_says_so(self):
        ec = ErrorCollector()
        self._run("Dup SOP", title_rows=[(7, "Dup SOP"), (9, "Dup SOP")], ec=ec)
        assert "2" in ec.errors_for_uid("C-1")[0].message

    def test_resolved_protocol_is_not_reported(self):
        ec = ErrorCollector()
        self._run(
            "P.FOR-200623-V1_x.docx",
            title_rows=[(7, "P.FOR-200623-V1_x.docx")],
            sop_rows=[(7, "P.FOR-200623-V1_x.docx")],
            ec=ec,
        )
        assert ec.all_errors() == []

    def test_no_collector_still_resolves(self):
        """The collector is optional; resolution must not depend on it."""
        rows, _ = self._run(
            "P.FOR-200623-V1_x.docx",
            title_rows=[(7, "P.FOR-200623-V1_x.docx")],
            sop_rows=[(7, "P.FOR-200623-V1_x.docx")],
            ec=None,
        )
        assert rows[0].protocol_id == 7


class TestUploadAllProtocolMetrics:
    """The unresolved count must reach Metrics, like #44's dropped IN_STUDY rows."""

    @staticmethod
    def _run(protocol_map_side_effect, error_collector=None, external=()):
        from nextseek_api.batch_upload.models import DirectionComputation
        from nextseek_api.batch_upload.neo4j_sync import upload_all

        def _fake_build(*args, **kwargs):
            ec = kwargs.get("error_collector")
            if ec is not None:
                for uid, msg in protocol_map_side_effect:
                    ec.add(0, uid, ErrorType.PROTOCOL_UNRESOLVED, msg)
                for uid, msg in external:
                    ec.add(0, uid, ErrorType.PROTOCOL_EXTERNAL_LINK, msg)
            return []

        with patch("neo4j.GraphDatabase") as mock_gdb, \
             patch("nextseek_api.batch_upload.neo4j_sync."
                   "build_derived_from_payloads_from_db", side_effect=_fake_build):
            mock_driver = MagicMock()
            mock_gdb.driver.return_value = mock_driver
            mock_driver.execute_query.return_value = MagicMock(
                counters=MagicMock(nodes_created=0, nodes_matched=0,
                                   relationships_created=0),
                records=[],
            )
            return upload_all(
                outcomes={},
                input_models=[],
                sql_conn=MagicMock(),
                neo4j_config=MagicMock(
                    NEO4J_UPLOAD_ENABLED=True, URI="bolt://localhost",
                    NEO4J_USER="u", PASSWORD="p", NEO4J_DB="testdb",
                    NEO4J_NODE_CHUNK=500, NEO4J_REL_CHUNK=500,
                ),
                direction_computation=DirectionComputation(
                    parents_of={}, assays_by_uid={}, direction_by_pair={},
                    child_uids_by_assay={}, conflicts_by_assay={},
                ),
                error_collector=error_collector,
            )

    def test_unresolved_protocols_are_counted(self):
        metrics = self._run([("C-1", "m1"), ("C-2", "m2")])
        assert metrics.protocols_unresolved == 2

    def test_external_links_are_counted_separately(self):
        """Same delta mechanism, a different counter — an external link must
        never inflate the number an operator reads as "problems"."""
        metrics = self._run(
            [("C-1", "m1")],
            external=[("C-2", "link"), ("C-3", "link")],
        )
        assert metrics.protocols_unresolved == 1
        assert metrics.protocols_external_links == 2

    def test_zero_when_everything_resolved(self):
        assert self._run([]).protocols_unresolved == 0

    def test_errors_reach_the_caller_s_collector(self):
        ec = ErrorCollector()
        metrics = self._run([("C-1", "m1")], error_collector=ec)
        assert [e.uid for e in ec.all_errors()] == ["C-1"]
        assert metrics.protocols_unresolved == 1

    def test_pre_existing_entries_are_not_double_counted(self):
        """A collector already carrying this error type from an earlier call
        must not inflate this run's count."""
        ec = ErrorCollector()
        ec.add(0, "OLD", ErrorType.PROTOCOL_UNRESOLVED, "from an earlier stage")
        metrics = self._run([("C-1", "m1")], error_collector=ec)
        assert metrics.protocols_unresolved == 1


class TestDanglingSopIds:
    """An id that resolves but names no `sops` row.

    /sops/9999 (or a sheet sop_id=9999) wrote protocol_id=9999 with
    protocol_title=None and reported nothing — the exact shape a wrong id would
    take, so it is the diagnostic you would most want to exist.
    """

    @staticmethod
    def _run(protocol, sop_rows, model=None, ec=None):
        conn = MagicMock()
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201)]
        child_result = MagicMock()
        child_result.fetchall.return_value = [(101, json.dumps({"Protocol": protocol}))]
        by_id = MagicMock()
        by_id.fetchall.return_value = sop_rows
        conn.execute.side_effect = [parent_result, child_result, by_id]

        rows = build_derived_from_payloads_from_db(
            {"C-1": {"P-1"}}, conn, {},
            {"C-1": _outcome("success", sample_id=101)},
            [model or _input("C-1")],
            error_collector=ec,
        )
        return rows, conn

    def test_dangling_local_sop_id_is_reported(self):
        ec = ErrorCollector()
        self._run("/sops/9999", sop_rows=[], ec=ec)
        errs = ec.errors_for_uid("C-1")
        assert len(errs) == 1
        assert errs[0].error_type is ErrorType.PROTOCOL_UNRESOLVED
        assert "9999" in errs[0].message

    def test_dangling_sheet_sop_id_is_reported(self):
        """The sheet still wins — but a sheet id can be wrong too."""
        ec = ErrorCollector()
        model = InputRowModel(
            UID="C-1", SampleType="Blood", json_metadata="{}", sop_id=9999,
        )
        self._run("", sop_rows=[], model=model, ec=ec)
        assert len(ec.errors_for_uid("C-1")) == 1

    def test_a_dangling_id_reads_differently_from_an_unmatched_title(self):
        """Otherwise a wrong-id write is indistinguishable from a typo'd SOP name."""
        dangling = ErrorCollector()
        self._run("/sops/9999", sop_rows=[], ec=dangling)

        conn = MagicMock()
        parent = MagicMock(); parent.fetchall.return_value = [("P-1", 201)]
        child = MagicMock()
        child.fetchall.return_value = [(101, json.dumps({"Protocol": "No Such SOP"}))]
        by_title = MagicMock(); by_title.fetchall.return_value = []
        conn.execute.side_effect = [parent, child, by_title]
        title = ErrorCollector()
        build_derived_from_payloads_from_db(
            {"C-1": {"P-1"}}, conn, {},
            {"C-1": _outcome("success", sample_id=101)}, [_input("C-1")],
            error_collector=title,
        )

        assert (dangling.errors_for_uid("C-1")[0].message
                != title.errors_for_uid("C-1")[0].message)

    def test_the_dangling_id_is_still_written_to_the_edge(self):
        """Deliberate: nulling it would silently discard what the sheet said.
        The edge is reported, not rewritten."""
        rows, _ = self._run("/sops/9999", sop_rows=[])
        assert rows[0].protocol_id == 9999
        assert rows[0].protocol_title is None

    def test_a_resolvable_id_is_not_reported(self):
        ec = ErrorCollector()
        rows, _ = self._run("/sops/5", sop_rows=[(5, "SOP Five")], ec=ec)
        assert rows[0].protocol_title == "SOP Five"
        assert ec.all_errors() == []


class TestExternalProtocolLinks:
    """An http Protocol that is not one of our SOPs is a legitimate external
    link (seek/dbtable_sample.py:3074-3081), not an ingest failure."""

    _LOCAL = TestDerivedFromProtocolResolution._LOCAL

    @staticmethod
    def _run(protocols, ec=None):
        conn = MagicMock()
        parent_result = MagicMock()
        parent_result.fetchall.return_value = [("P-1", 201)]
        child_result = MagicMock()
        child_result.fetchall.return_value = [
            (101 + i, json.dumps({"Protocol": p})) for i, p in enumerate(protocols)
        ]
        conn.execute.side_effect = [parent_result, child_result]

        uids = [f"C-{i + 1}" for i in range(len(protocols))]
        rows = build_derived_from_payloads_from_db(
            {u: {"P-1"} for u in uids},
            conn, {},
            {u: _outcome("success", sample_id=101 + i) for i, u in enumerate(uids)},
            [_input(u) for u in uids],
            error_collector=ec,
        )
        return rows, conn

    def test_an_external_link_issues_no_sops_query(self):
        """1,855 live values would otherwise ask sops for a URL as a title."""
        with override_settings(**self._LOCAL):
            _rows, conn = self._run(["https://fairdomhub.org/sops/795"])
        assert conn.execute.call_count == 2
        assert not any(
            "FROM sops" in str(c[0][0]) for c in conn.execute.call_args_list
        )

    def test_an_external_link_is_not_recorded_as_an_error(self):
        ec = ErrorCollector()
        with override_settings(**self._LOCAL):
            self._run(["https://fairdomhub.org/sops/795"], ec=ec)
        errs = ec.errors_for_uid("C-1")
        assert len(errs) == 1
        assert errs[0].error_type is ErrorType.PROTOCOL_EXTERNAL_LINK
        assert errs[0].severity is Severity.INFO

    def test_external_link_entries_share_one_message_so_they_group(self):
        """_group_errors keys on (type, message), so a per-row URL would defeat
        the grouping and reproduce the wall of entries this avoids."""
        ec = ErrorCollector()
        with override_settings(**self._LOCAL):
            self._run(
                ["https://fairdomhub.org/sops/795", "https://fairdomhub.org/sops/1"],
                ec=ec,
            )
        messages = {e.message for e in ec.all_errors()}
        assert len(ec.all_errors()) == 2
        assert len(messages) == 1

    def test_an_external_link_does_not_count_as_unresolved(self):
        ec = ErrorCollector()
        with override_settings(**self._LOCAL):
            self._run(["https://fairdomhub.org/sops/795"], ec=ec)
        assert ec.count_by_type().get(ErrorType.PROTOCOL_UNRESOLVED, 0) == 0

    def test_the_edge_carries_no_protocol_for_an_external_link(self):
        with override_settings(**self._LOCAL):
            rows, _ = self._run(["https://fairdomhub.org/sops/795"])
        assert rows[0].protocol_id is None
        assert rows[0].protocol_title is None


# ── DERIVED_FROM drop accounting ─────────────────────────────────────────────


def _df_row(child="C-1", parent="P-1", child_id=1, parent_id=2):
    from nextseek_api.batch_upload.models import DerivedFromRelRow
    return DerivedFromRelRow(
        child_id=child_id, child_uuid=child,
        parent_id=parent_id, parent_uuid=parent,
    )


class TestBulkMergeRelationships:
    """The DERIVED_FROM twin of the IN_STUDY drop accounting.

    The double MATCH yields nothing for a row whose Sample node is absent, so the
    edge is dropped with no error. `count(r)` short of the chunk size is the only
    evidence, and nothing compared the two.
    """

    def test_merges_and_returns_the_processed_count(self):
        driver = _mock_driver([3])
        rows = [_df_row(f"C-{i}", f"P-{i}") for i in range(3)]

        assert bulk_merge_relationships(driver, "testdb", rows) == 3
        assert driver.execute_query.call_count == 1
        cypher = driver.execute_query.call_args[0][0]
        assert "DERIVED_FROM" in cypher and "MERGE" in cypher
        params = driver.execute_query.call_args[0][1]
        assert [r["child_uuid"] for r in params["rows"]] == ["C-0", "C-1", "C-2"]

    def test_empty_rows_never_touch_the_driver(self):
        driver = MagicMock()
        assert bulk_merge_relationships(driver, "testdb", []) == 0
        driver.execute_query.assert_not_called()

    def test_chunking(self):
        driver = _mock_driver([2, 2, 1])
        rows = [_df_row(f"C-{i}", f"P-{i}") for i in range(5)]

        assert bulk_merge_relationships(driver, "testdb", rows, chunk_size=2) == 5
        assert driver.execute_query.call_count == 3

    def test_a_short_chunk_is_warned_about(self, caplog):
        driver = _mock_driver([2])
        rows = [_df_row(f"C-{i}", f"P-{i}") for i in range(5)]

        with caplog.at_level("WARNING"):
            total = bulk_merge_relationships(driver, "testdb", rows)

        assert total == 2
        warnings = [r.getMessage() for r in caplog.records
                    if r.levelname == "WARNING" and "DERIVED_FROM" in r.getMessage()]
        assert any("3 of 5" in m for m in warnings), warnings

    def test_each_short_chunk_is_warned_about_separately(self, caplog):
        # 5 rows at chunk_size 2 -> chunks of 2, 2, 1.
        # chunk 0: 2 of 2, silent. chunk 1: 0 of 2. chunk 2: 0 of 1.
        driver = _mock_driver([2, 0, 0])
        rows = [_df_row(f"C-{i}", f"P-{i}") for i in range(5)]

        with caplog.at_level("WARNING"):
            total = bulk_merge_relationships(driver, "testdb", rows, chunk_size=2)

        assert total == 2
        warnings = [r.getMessage() for r in caplog.records
                    if r.levelname == "WARNING" and "DERIVED_FROM" in r.getMessage()]
        assert len(warnings) == 2, warnings
        assert "2 of 2" in warnings[0]
        assert "1 of 1" in warnings[1]

    def test_a_fully_matched_merge_stays_silent(self, caplog):
        driver = _mock_driver([5])
        rows = [_df_row(f"C-{i}", f"P-{i}") for i in range(5)]

        with caplog.at_level("WARNING"):
            total = bulk_merge_relationships(driver, "testdb", rows)

        assert total == 5
        assert not [r for r in caplog.records if "DERIVED_FROM" in r.getMessage()]

    def test_a_driver_returning_no_records_counts_the_whole_chunk_as_dropped(self, caplog):
        driver = MagicMock()
        empty = MagicMock()
        empty.records = []
        driver.execute_query = MagicMock(return_value=empty)
        rows = [_df_row(f"C-{i}", f"P-{i}") for i in range(3)]

        with caplog.at_level("WARNING"):
            total = bulk_merge_relationships(driver, "testdb", rows)

        assert total == 0
        assert any("3 of 3" in r.getMessage() for r in caplog.records)


class TestFindMissingDerivedFromEndpoints:
    """Names what the MERGE dropped. READ-ONLY by construction."""

    @staticmethod
    def _driver(missing_children, missing_parents):
        driver = MagicMock()
        calls = [missing_children, missing_parents]
        idx = [0]

        def _execute_query(cypher, params, database_=None):
            result = MagicMock()
            result.records = [{"missing": calls[idx[0]]}]
            idx[0] += 1
            return result

        driver.execute_query = MagicMock(side_effect=_execute_query)
        return driver

    def test_no_rows_short_circuits_without_querying(self):
        driver = MagicMock()
        assert find_missing_derived_from_endpoints(driver, "testdb", []) == ([], [])
        driver.execute_query.assert_not_called()

    def test_missing_endpoints_are_reported_sorted(self):
        driver = self._driver(["C-b", "C-a"], ["P-b", "P-a"])
        rows = [_df_row("C-a", "P-a"), _df_row("C-b", "P-b")]

        children, parents = find_missing_derived_from_endpoints(driver, "testdb", rows)

        assert children == ["C-a", "C-b"]
        assert parents == ["P-a", "P-b"]

    def test_nothing_missing_returns_empty_lists(self):
        driver = self._driver([], [])
        rows = [_df_row("C-a", "P-a")]
        assert find_missing_derived_from_endpoints(driver, "testdb", rows) == ([], [])

    def test_the_audit_never_mutates_the_graph(self):
        driver = self._driver(["C-a"], [])
        rows = [_df_row("C-a", "P-a")]
        find_missing_derived_from_endpoints(driver, "testdb", rows)

        for call_args in driver.execute_query.call_args_list:
            cypher = call_args[0][0].upper()
            for mutating in ("MERGE", "CREATE", "DELETE", "SET ", "REMOVE"):
                assert mutating not in cypher, f"audit query mutates: {cypher}"

    def test_endpoints_are_deduplicated_before_querying(self):
        driver = self._driver([], [])
        rows = [_df_row("C-a", "P-a"), _df_row("C-a", "P-a"), _df_row("C-b", "P-a")]

        find_missing_derived_from_endpoints(driver, "testdb", rows)

        child_params = driver.execute_query.call_args_list[0][0][1]
        parent_params = driver.execute_query.call_args_list[1][0][1]
        assert child_params["uuids"] == ["C-a", "C-b"]
        assert parent_params["uuids"] == ["P-a"]


class TestSkippedChildrenMissingParents:
    """`Metrics.skipped_children_missing_parents` existed but was never assigned.

    `build_derived_from_payloads_from_db` drops a (child, parent) pair at a bare
    `continue` when the parent UUID names no row in `samples`. That is a
    lineage edge MySQL declares and the graph will never get, and it was silent.
    """

    @staticmethod
    @patch("nextseek_api.batch_upload.neo4j_sync._resolve_internal_assays", return_value={})
    def _run(parent_child_rels, found_parents, _mock_resolve, ec=None):
        conn = MagicMock()
        parent_result = MagicMock()
        parent_result.fetchall.return_value = found_parents
        child_result = MagicMock()
        child_result.fetchall.return_value = [
            (100 + i, "{}") for i in range(len(parent_child_rels))
        ]
        conn.execute.side_effect = [parent_result, child_result]

        outcomes = {uid: _outcome("success", sample_id=100 + i)
                    for i, uid in enumerate(parent_child_rels)}
        models = [_input(uid) for uid in parent_child_rels]
        return build_derived_from_payloads_from_db(
            parent_child_rels, conn, {}, outcomes, models, error_collector=ec,
        )

    def test_a_parent_absent_from_mysql_is_reported_not_dropped_silently(self):
        ec = ErrorCollector()
        rows = self._run({"C-1": {"P-1", "P-GONE"}}, [("P-1", 201)], ec=ec)

        assert [r.parent_uuid for r in rows] == ["P-1"]
        errs = [e for e in ec.all_errors() if e.error_type is ErrorType.PARENT_NOT_FOUND]
        assert len(errs) == 1
        assert errs[0].uid == "C-1"
        assert "P-GONE" in errs[0].message

    def test_the_counter_counts_children_not_pairs(self):
        """The field is named skipped_children_missing_parents, so one child
        that lost two parents is one child, not two."""
        ec = ErrorCollector()
        self._run({"C-1": {"P-GONE-A", "P-GONE-B"}}, [], ec=ec)

        errs = [e for e in ec.all_errors() if e.error_type is ErrorType.PARENT_NOT_FOUND]
        assert len(errs) == 1, [e.message for e in errs]
        assert "P-GONE-A" in errs[0].message and "P-GONE-B" in errs[0].message

    def test_every_parent_present_reports_nothing(self):
        ec = ErrorCollector()
        rows = self._run({"C-1": {"P-1"}}, [("P-1", 201)], ec=ec)

        assert len(rows) == 1
        assert ec.count_by_type().get(ErrorType.PARENT_NOT_FOUND, 0) == 0

    def test_a_missing_parent_is_a_warning_not_an_error(self):
        """The child still uploads; only this one edge is lost. ERROR severity
        here would drown the genuine row failures."""
        ec = ErrorCollector()
        self._run({"C-1": {"P-GONE"}}, [], ec=ec)
        errs = [e for e in ec.all_errors() if e.error_type is ErrorType.PARENT_NOT_FOUND]
        assert errs[0].severity is Severity.WARNING


# ── the narrowed parent-changed delete ───────────────────────────────────────


class TestParentsDeclaredInStoredMetadata:
    """Post-merge truth for the delete's keep-set.

    `parents_of` comes from the SHEET, but `deep_merge_metadata` preserves keys
    the sheet omits, so the STORED metadata can declare parents the rebuild set
    has never heard of. Same pattern as `refresh_assays_for_uuids`: go back to
    MySQL for exactly these uuids rather than trust the sheet.
    """

    @staticmethod
    def _run(rows_returned, uuids=("C-1",), sample_ids=(101,)):
        conn = MagicMock()
        result = MagicMock()
        result.fetchall.return_value = rows_returned
        conn.execute.return_value = result
        outcomes = {u: _outcome("success", sample_id=s)
                    for u, s in zip(uuids, sample_ids)}
        return parents_declared_in_stored_metadata(list(uuids), outcomes, conn)

    def test_no_uuids_short_circuits_without_querying(self):
        conn = MagicMock()
        assert parents_declared_in_stored_metadata([], {}, conn) == ({}, [])
        conn.execute.assert_not_called()

    def test_uid_parent_tokens_are_returned(self):
        meta = json.dumps({"Parent": "NHP-260225MIT-1;NHP-260225MIT-2"})
        parents, unreadable = self._run([(101, meta)])
        assert parents == {"C-1": {"NHP-260225MIT-1", "NHP-260225MIT-2"}}
        assert unreadable == []

    def test_variant_parent_keys_are_included(self):
        """The whole point: AntibodyParent survives a merge that only names Parent."""
        meta = json.dumps({
            "Parent": "NHP-260225MIT-1",
            "AntibodyParent": "NHP-260225MIT-9",
        })
        parents, _ = self._run([(101, meta)])
        assert parents == {"C-1": {"NHP-260225MIT-1", "NHP-260225MIT-9"}}

    def test_non_uid_tokens_are_not_treated_as_parent_uuids(self):
        """An unresolved name is not a Sample uuid, so it cannot protect an edge."""
        meta = json.dumps({"Parent": "Some Unresolved Name"})
        parents, unreadable = self._run([(101, meta)])
        assert parents == {"C-1": set()}
        assert unreadable == []

    def test_unparseable_metadata_is_reported_unreadable_not_parentless(self):
        """Silently reading it as 'no parents' would authorise deleting every edge."""
        parents, unreadable = self._run([(101, "{not json")])
        assert unreadable == ["C-1"]
        assert "C-1" not in parents

    def test_a_sample_with_no_row_is_unreadable(self):
        parents, unreadable = self._run([])
        assert unreadable == ["C-1"]
        assert parents == {}

    def test_null_metadata_is_read_as_no_parents(self):
        """NULL json_metadata is a real, readable state: this sample has none."""
        parents, unreadable = self._run([(101, None)])
        assert parents == {"C-1": set()}
        assert unreadable == []


class TestDeleteStaleDerivedFromForUuids:
    """The narrowed delete: everything EXCEPT the keep-set, per child."""

    def test_no_children_never_touches_the_driver(self):
        driver = MagicMock()
        assert delete_stale_derived_from_for_uuids(driver, "testdb", {}) == 0
        driver.execute_query.assert_not_called()

    def test_the_keep_set_is_sent_per_child_and_excluded_from_the_delete(self):
        driver = _mock_driver_deleted([1])
        result = delete_stale_derived_from_for_uuids(
            driver, "testdb", {"C-1": {"P-KEEP", "P-ALSO"}},
        )

        assert result == 1
        cypher = driver.execute_query.call_args[0][0]
        assert "DELETE" in cypher and "DERIVED_FROM" in cypher
        assert "NOT" in cypher and "keep_uuids" in cypher
        params = driver.execute_query.call_args[0][1]
        assert params["rows"] == [
            {"child_uuid": "C-1", "keep_uuids": ["P-ALSO", "P-KEEP"]}
        ]

    def test_an_empty_keep_set_clears_every_edge_for_that_child(self):
        """A parent genuinely removed leaves nothing to keep, so nothing is kept."""
        driver = _mock_driver_deleted([2])
        result = delete_stale_derived_from_for_uuids(driver, "testdb", {"C-1": set()})

        assert result == 2
        params = driver.execute_query.call_args[0][1]
        assert params["rows"] == [{"child_uuid": "C-1", "keep_uuids": []}]

    def test_chunking(self):
        driver = _mock_driver_deleted([2, 1])
        keep = {f"C-{i}": set() for i in range(5)}
        result = delete_stale_derived_from_for_uuids(driver, "testdb", keep, chunk_size=3)
        assert result == 3
        assert driver.execute_query.call_count == 2

    def test_the_delete_is_scoped_to_the_named_children_only(self):
        driver = _mock_driver_deleted([0])
        delete_stale_derived_from_for_uuids(driver, "testdb", {"C-1": {"P-1"}})
        cypher = driver.execute_query.call_args[0][0]
        assert "row.child_uuid" in cypher


class _UploadAllHarness:
    """Drive upload_all with every neighbour stubbed, so a test asserts on one thing."""

    @staticmethod
    def run(*, derived_from_rows, merged_count, outcomes=None,
            stored_parents=None, unreadable=None, missing=(["C-9"], ["P-9"]),
            build_side_effect=None, error_collector=None):
        from nextseek_api.batch_upload.neo4j_sync import upload_all
        from nextseek_api.batch_upload.models import DirectionComputation

        captured = {}

        def _capture_delete(driver, db_name, keep_by_child, chunk_size=10_000):
            captured["keep_by_child"] = keep_by_child
            return 0

        build_stub = (
            MagicMock(side_effect=build_side_effect) if build_side_effect
            else MagicMock(return_value=derived_from_rows)
        )

        stubs = dict(
            build_payloads=MagicMock(return_value=([], {})),
            enrich_parent_titles=MagicMock(),
            refresh_assays_for_uuids=MagicMock(return_value={}),
            parents_declared_in_stored_metadata=MagicMock(
                return_value=(stored_parents or {}, list(unreadable or []))),
            build_derived_from_payloads_from_db=build_stub,
            build_sample_type_node_payloads=MagicMock(return_value=[]),
            build_of_type_payloads=MagicMock(return_value=[]),
            build_in_study_payloads_enriched=MagicMock(return_value=([], 0, {})),
            build_study_node_payloads=MagicMock(return_value=([], [], [])),
            delete_stale_derived_from_for_uuids=MagicMock(side_effect=_capture_delete),
            bulk_merge_relationships=MagicMock(return_value=merged_count),
            find_missing_derived_from_endpoints=MagicMock(return_value=missing),
        )

        with patch("neo4j.GraphDatabase") as mock_gdb, \
             patch.multiple("nextseek_api.batch_upload.neo4j_sync", **stubs):
            mock_driver = MagicMock()
            mock_gdb.driver.return_value = mock_driver
            mock_driver.execute_query.return_value = MagicMock(records=[])

            metrics = upload_all(
                outcomes=outcomes or {},
                input_models=[],
                sql_conn=MagicMock(),
                neo4j_config=MagicMock(
                    NEO4J_UPLOAD_ENABLED=True, URI="bolt://localhost",
                    NEO4J_USER="u", PASSWORD="p", NEO4J_DB="testdb",
                    NEO4J_NODE_CHUNK=500, NEO4J_REL_CHUNK=500,
                ),
                direction_computation=DirectionComputation(
                    parents_of={}, assays_by_uid={}, direction_by_pair={},
                    child_uids_by_assay={}, conflicts_by_assay={},
                ),
                error_collector=error_collector,
            )
        return metrics, captured, stubs


class TestUploadAllDerivedFromCoverageMetrics:
    """#44's sibling: `rels_input` and `derived_from_rels_created` both existed
    and nothing ever compared them, so a dropped edge left a clean-looking run.
    """

    def test_dropped_edges_are_counted(self):
        rows = [_df_row(f"C-{i}", f"P-{i}") for i in range(5)]
        metrics, _cap, _stubs = _UploadAllHarness.run(
            derived_from_rows=rows, merged_count=2)

        assert metrics.rels_input == 5
        assert metrics.derived_from_rels_created == 2
        assert metrics.derived_from_rels_dropped == 3

    def test_a_complete_merge_reports_zero_dropped(self):
        rows = [_df_row(f"C-{i}", f"P-{i}") for i in range(5)]
        metrics, _cap, _stubs = _UploadAllHarness.run(
            derived_from_rows=rows, merged_count=5)

        assert metrics.derived_from_rels_dropped == 0

    def test_the_audit_runs_only_when_something_was_dropped(self):
        rows = [_df_row(f"C-{i}", f"P-{i}") for i in range(5)]

        _m, _c, stubs = _UploadAllHarness.run(derived_from_rows=rows, merged_count=5)
        stubs["find_missing_derived_from_endpoints"].assert_not_called()

        _m, _c, stubs = _UploadAllHarness.run(derived_from_rows=rows, merged_count=1)
        stubs["find_missing_derived_from_endpoints"].assert_called_once()

    def test_coverage_is_auditable_from_metrics_alone(self):
        rows = [_df_row(f"C-{i}", f"P-{i}") for i in range(5)]
        metrics, _cap, _stubs = _UploadAllHarness.run(
            derived_from_rows=rows, merged_count=2)
        assert (metrics.derived_from_rels_created
                + metrics.derived_from_rels_dropped) == metrics.rels_input

    def test_the_drop_is_named_in_the_log(self, caplog):
        rows = [_df_row(f"C-{i}", f"P-{i}") for i in range(5)]
        with caplog.at_level("WARNING"):
            _UploadAllHarness.run(derived_from_rows=rows, merged_count=2)
        msgs = [r.getMessage() for r in caplog.records if "DERIVED_FROM" in r.getMessage()]
        assert any("C-9" in m and "P-9" in m for m in msgs), msgs


class TestUploadAllNarrowedParentChangedDelete:
    """The delete used to remove EVERY outgoing edge for a parent-changed sample,
    then rebuild only what the SHEET declared. `deep_merge_metadata` preserves
    parent keys the sheet omits, so those parents survived into MySQL and lost
    their edge. The keep-set is stored-metadata parents UNION the rebuild set.
    """

    _OUTCOMES = {"C-1": RowOutcome(status="success", sample_id=101, parent_changed=True)}

    def test_a_parent_the_sheet_never_mentioned_keeps_its_edge(self):
        # stored: Parent=A, AntibodyParent=X.  sheet: Parent=A only.
        _m, cap, _s = _UploadAllHarness.run(
            derived_from_rows=[_df_row("C-1", "A")],
            merged_count=1,
            outcomes=self._OUTCOMES,
            stored_parents={"C-1": {"A", "X"}},
        )
        assert cap["keep_by_child"] == {"C-1": {"A", "X"}}

    def test_a_parent_dropped_from_the_stored_metadata_is_still_cleared(self):
        # stored: Parent=A (Z is gone).  The edge to Z must NOT be kept.
        _m, cap, _s = _UploadAllHarness.run(
            derived_from_rows=[_df_row("C-1", "A")],
            merged_count=1,
            outcomes=self._OUTCOMES,
            stored_parents={"C-1": {"A"}},
        )
        assert cap["keep_by_child"] == {"C-1": {"A"}}
        assert "Z" not in cap["keep_by_child"]["C-1"]

    def test_every_parent_removed_leaves_an_empty_keep_set(self):
        _m, cap, _s = _UploadAllHarness.run(
            derived_from_rows=[],
            merged_count=0,
            outcomes=self._OUTCOMES,
            stored_parents={"C-1": set()},
        )
        assert cap["keep_by_child"] == {"C-1": set()}

    def test_the_rebuild_set_is_never_deleted_even_if_stored_metadata_omits_it(self):
        """Deleting an edge the very next step recreates is pure churn."""
        _m, cap, _s = _UploadAllHarness.run(
            derived_from_rows=[_df_row("C-1", "A")],
            merged_count=1,
            outcomes=self._OUTCOMES,
            stored_parents={"C-1": set()},
        )
        assert cap["keep_by_child"] == {"C-1": {"A"}}

    def test_unreadable_stored_metadata_skips_the_delete_for_that_child(self):
        """Truth unknown -> delete nothing. Staleness is recoverable; the 90k
        missing edges this file has already cost are the other failure mode."""
        _m, cap, stubs = _UploadAllHarness.run(
            derived_from_rows=[_df_row("C-1", "A")],
            merged_count=1,
            outcomes=self._OUTCOMES,
            stored_parents={},
            unreadable=["C-1"],
        )
        # The only parent-changed child was skipped, so there is nothing left
        # to delete and the driver is not touched at all.
        stubs["delete_stale_derived_from_for_uuids"].assert_not_called()
        assert cap == {}

    def test_a_readable_child_is_still_deleted_alongside_an_unreadable_one(self):
        """One unreadable sample must not disable the delete for the rest."""
        outcomes = {
            "C-1": RowOutcome(status="success", sample_id=101, parent_changed=True),
            "C-2": RowOutcome(status="success", sample_id=102, parent_changed=True),
        }
        _m, cap, _s = _UploadAllHarness.run(
            derived_from_rows=[_df_row("C-2", "A")],
            merged_count=1,
            outcomes=outcomes,
            stored_parents={"C-2": {"A"}},
            unreadable=["C-1"],
        )
        assert cap["keep_by_child"] == {"C-2": {"A"}}

    def test_skipping_an_unreadable_child_is_warned_about(self, caplog):
        with caplog.at_level("WARNING"):
            _UploadAllHarness.run(
                derived_from_rows=[_df_row("C-1", "A")],
                merged_count=1,
                outcomes=self._OUTCOMES,
                stored_parents={},
                unreadable=["C-1"],
            )
        assert any("C-1" in r.getMessage() and "DERIVED_FROM" in r.getMessage()
                   for r in caplog.records if r.levelname == "WARNING")

    def test_no_parent_changed_samples_means_no_delete_at_all(self):
        _m, cap, stubs = _UploadAllHarness.run(
            derived_from_rows=[_df_row("C-1", "A")], merged_count=1)
        stubs["delete_stale_derived_from_for_uuids"].assert_not_called()
        assert cap == {}


class TestUploadAllSkippedChildrenMetric:
    """`Metrics.skipped_children_missing_parents` was declared and never assigned.

    The builder reports through the ErrorCollector; upload_all turns the delta
    into the metric, the same way it already does for protocols_unresolved.
    """

    def test_the_metric_carries_the_builders_skips(self):
        def _build(*args, **kwargs):
            kwargs["error_collector"].add(
                -1, "C-1", ErrorType.PARENT_NOT_FOUND, "missing parent")
            kwargs["error_collector"].add(
                -1, "C-2", ErrorType.PARENT_NOT_FOUND, "missing parent")
            return []

        metrics, _cap, stubs = _UploadAllHarness.run(
            derived_from_rows=[], merged_count=0, build_side_effect=_build)
        assert metrics.skipped_children_missing_parents == 2

    def test_a_clean_batch_reports_zero(self):
        metrics, _cap, _stubs = _UploadAllHarness.run(
            derived_from_rows=[], merged_count=0)
        assert metrics.skipped_children_missing_parents == 0

    def test_entries_already_on_a_shared_collector_are_not_recounted(self):
        """upload_all takes a delta, not a total: the collector is shared with
        earlier stages and a pre-existing entry is not this stage's skip."""
        ec = ErrorCollector()
        ec.add(-1, "EARLIER", ErrorType.PARENT_NOT_FOUND, "from another stage")

        def _build(*args, **kwargs):
            kwargs["error_collector"].add(
                -1, "C-1", ErrorType.PARENT_NOT_FOUND, "missing parent")
            return []

        metrics, _cap, _stubs = _UploadAllHarness.run(
            derived_from_rows=[], merged_count=0,
            build_side_effect=_build, error_collector=ec)
        assert metrics.skipped_children_missing_parents == 1

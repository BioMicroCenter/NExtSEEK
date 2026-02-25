"""Unit tests for neo4j_sync payload builders and merge functions."""
import pytest
from unittest.mock import MagicMock, call

from nextseek_api.batch_upload.models import (
    InStudyRelRow,
    InputRowModel,
    InsertableSample,
    OfTypeRelRow,
    RowOutcome,
)
from nextseek_api.batch_upload.neo4j_sync import (
    build_in_study_payloads,
    build_of_type_payloads,
    build_payloads,
    bulk_merge_in_study_relationships,
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

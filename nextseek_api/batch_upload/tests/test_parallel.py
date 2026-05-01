"""Tests for parallel.py — ThreadPoolExecutor parallelism."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.batch_upload.errors import ErrorCollector
from nextseek_api.batch_upload.models import (
    BatchResult,
    DirectionComputation,
    InsertableSample,
    RowOutcome,
)
from nextseek_api.batch_upload.parallel import (
    PARALLEL_THRESHOLD,
    SharedState,
    _split_equal,
    _worker_process_chunk,
    process_batches_parallel,
)
from nextseek_api.batch_upload.report import ProgressReporter


# ── _split_equal ─────────────────────────────────────────────────────────


class TestSplitEqual:
    def test_split_into_equal_chunks(self):
        rows = list(range(10))
        chunks = _split_equal(rows, 2)
        assert len(chunks) == 2
        assert len(chunks[0]) == 5
        assert len(chunks[1]) == 5

    def test_split_uneven(self):
        rows = list(range(11))
        chunks = _split_equal(rows, 3)
        assert len(chunks) <= 3
        # All elements preserved
        flattened = [item for chunk in chunks for item in chunk]
        assert sorted(flattened) == list(range(11))

    def test_split_n_zero(self):
        rows = [1, 2, 3]
        chunks = _split_equal(rows, 0)
        assert len(chunks) == 1
        assert chunks[0] == [1, 2, 3]

    def test_split_n_negative(self):
        rows = [1, 2, 3]
        chunks = _split_equal(rows, -1)
        assert len(chunks) == 1

    def test_split_empty_list(self):
        chunks = _split_equal([], 3)
        assert chunks == []

    def test_split_single_element(self):
        chunks = _split_equal([42], 3)
        assert len(chunks) == 1
        assert chunks[0] == [42]

    def test_n_greater_than_length(self):
        rows = [1, 2]
        chunks = _split_equal(rows, 10)
        # Should not produce more than len(rows) non-empty chunks
        flattened = [item for chunk in chunks for item in chunk]
        assert sorted(flattened) == [1, 2]

    def test_split_preserves_order(self):
        rows = list(range(100))
        chunks = _split_equal(rows, 4)
        flattened = [item for chunk in chunks for item in chunk]
        assert flattened == list(range(100))


# ── SharedState ──────────────────────────────────────────────────────────


class TestSharedState:
    def test_update_outcomes(self):
        state = SharedState()
        state.update_outcomes({"UID-1": RowOutcome(status="success", sample_id=1)})
        assert "UID-1" in state.outcomes
        assert state.outcomes["UID-1"].status == "success"

    def test_update_outcomes_merges(self):
        state = SharedState()
        state.update_outcomes({"UID-1": RowOutcome(status="success", sample_id=1)})
        state.update_outcomes({"UID-2": RowOutcome(status="failed")})
        assert len(state.outcomes) == 2

    def test_update_outcomes_thread_safety(self):
        state = SharedState()
        errors = []

        def worker(worker_id):
            try:
                for i in range(50):
                    uid = f"W{worker_id}-{i}"
                    state.update_outcomes(
                        {uid: RowOutcome(status="success", sample_id=i)}
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(state.outcomes) == 200  # 4 workers * 50 outcomes

    def test_default_factories(self):
        state = SharedState()
        assert state.outcomes == {}
        assert isinstance(state.outcomes_lock, threading.Lock)
        assert isinstance(state.reporter, ProgressReporter)
        assert isinstance(state.error_collector, ErrorCollector)


# ── _worker_process_chunk ────────────────────────────────────────────────


class TestWorkerProcessChunk:
    @patch("nextseek_api.batch_upload.parallel.process_batches")
    @patch("nextseek_api.batch_upload.parallel.get_connection")
    def test_processes_and_merges_outcomes(self, mock_get_conn, mock_process):
        chunk = [
            InsertableSample(uuid="UID-001", title="S1", sample_type_id=1, json_metadata="{}"),
        ]
        mock_result = BatchResult(
            inserted_count=1,
            outcomes={"UID-001": RowOutcome(status="success", sample_id=100)},
        )
        mock_process.return_value = mock_result

        shared_state = SharedState()
        config = MagicMock()
        direction = DirectionComputation(
            direction_by_pair={}, parents_of={}, assays_by_uid={},
            child_uids_by_assay={}, conflicts_by_assay={},
        )

        result = _worker_process_chunk(
            worker_id=0,
            chunk=chunk,
            shared_state=shared_state,
            project_id=1,
            contributor_id=1,
            config=config,
            direction_computation=direction,
        )

        assert result.inserted_count == 1
        assert "UID-001" in shared_state.outcomes


# ── process_batches_parallel ─────────────────────────────────────────────


class TestProcessBatchesParallel:
    @patch("nextseek_api.batch_upload.parallel.get_connection")
    @patch("nextseek_api.batch_upload.parallel.get_engine")
    @patch("nextseek_api.batch_upload.parallel.process_batches")
    @patch("nextseek_api.batch_upload.parallel.prefetch_assay_ids")
    def test_basic_parallel(self, mock_prefetch, mock_process, mock_engine, mock_get_conn):
        # Setup engine mock with pool.size()
        engine_mock = MagicMock()
        pool_mock = MagicMock()
        pool_mock.size.return_value = 20
        engine_mock.pool = pool_mock
        mock_engine.return_value = engine_mock

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        rows = [
            InsertableSample(uuid=f"UID-{i:03d}", title=f"S{i}", sample_type_id=1, json_metadata="{}")
            for i in range(10)
        ]
        mock_process.return_value = BatchResult(
            inserted_count=5,
            outcomes={r.uuid: RowOutcome(status="success", sample_id=i) for i, r in enumerate(rows[:5])},
            attempted_uids={r.uuid for r in rows[:5]},
        )

        direction = DirectionComputation(
            direction_by_pair={}, parents_of={}, assays_by_uid={},
            child_uids_by_assay={}, conflicts_by_assay={},
        )
        config = MagicMock()

        result = process_batches_parallel(
            rows=rows,
            project_id=1,
            contributor_id=1,
            config=config,
            direction_computation=direction,
        )

        assert isinstance(result, BatchResult)
        assert result.inserted_count > 0

    def test_parallel_threshold_constant(self):
        assert PARALLEL_THRESHOLD == 5000

"""Tests for the INSERT stage."""
import pytest

from nextseek_api.batch_upload.insert import (
    AdaptiveBatchSizer,
    _estimate_row_payload_bytes,
    _plan_next_batch,
)
from nextseek_api.batch_upload.insert_strategies import compute_first_letter
from nextseek_api.batch_upload.models import InsertableSample


class TestAdaptiveBatchSizer:
    def test_initial_size(self):
        sizer = AdaptiveBatchSizer(initial=2000)
        assert sizer.current_size == 2000

    def test_increase_when_fast(self):
        """Ratio > 1.1 should increase by 10%."""
        sizer = AdaptiveBatchSizer(target_rps=100.0, initial=1000)
        sizer.update(actual_rps=120.0)  # ratio = 1.2 > 1.1
        assert sizer.current_size == 1100

    def test_decrease_when_slow(self):
        """Ratio < 0.9 should decrease by 10%."""
        sizer = AdaptiveBatchSizer(target_rps=100.0, initial=1000)
        sizer.update(actual_rps=80.0)  # ratio = 0.8 < 0.9
        assert sizer.current_size == 900

    def test_no_change_in_range(self):
        """Ratio between 0.9 and 1.1 should not change."""
        sizer = AdaptiveBatchSizer(target_rps=100.0, initial=1000)
        sizer.update(actual_rps=100.0)  # ratio = 1.0
        assert sizer.current_size == 1000

    def test_clamp_min(self):
        sizer = AdaptiveBatchSizer(target_rps=100.0, min_size=500, initial=510)
        sizer.update(actual_rps=10.0)  # ratio = 0.1 -> decrease to 459
        assert sizer.current_size == 500  # clamped to min

    def test_clamp_max(self):
        sizer = AdaptiveBatchSizer(target_rps=100.0, max_size=5000, initial=4600)
        sizer.update(actual_rps=200.0)  # ratio = 2.0 -> increase to 5060
        assert sizer.current_size == 5000  # clamped to max

    def test_zero_rps(self):
        sizer = AdaptiveBatchSizer(initial=1000)
        sizer.update(actual_rps=0.0)
        assert sizer.current_size == 1000  # no change


class TestEstimateRowPayloadBytes:
    def test_basic(self):
        result = _estimate_row_payload_bytes("test title", '{"key":"value"}')
        assert result == len("test title".encode("utf-8")) + len('{"key":"value"}'.encode("utf-8")) + 128

    def test_unicode(self):
        result = _estimate_row_payload_bytes("\u00e9l\u00e8ve", '{"n":"\u00e9"}')
        assert result > 128  # some bytes + overhead


class TestPlanNextBatch:
    def _make_sample(self, uuid, json_len=100):
        meta = '{"x":"' + "a" * (json_len - 8) + '"}'
        return InsertableSample(
            uuid=uuid, title="T", sample_type_id=1,
            json_metadata=meta, assay_ids=[],
        )

    def test_respects_row_count(self):
        rows = [self._make_sample(f"U{i}") for i in range(10)]
        batch, next_idx, _ = _plan_next_batch(rows, 0, 3, 999_999)
        assert len(batch) == 3
        assert next_idx == 3

    def test_respects_payload_limit(self):
        rows = [self._make_sample(f"U{i}", json_len=5000) for i in range(10)]
        # Each row ~5000 bytes + title + overhead; 8MB limit should allow many
        batch, _, payload = _plan_next_batch(rows, 0, 100, 10000)
        assert len(batch) >= 1
        assert payload <= 10000 + 6000  # at most one row over

    def test_always_includes_one_row(self):
        rows = [self._make_sample("U0", json_len=100_000)]
        batch, _, _ = _plan_next_batch(rows, 0, 10, 1)  # 1 byte limit
        assert len(batch) == 1  # always at least 1

    def test_empty_rows(self):
        batch, next_idx, _ = _plan_next_batch([], 0, 10, 999_999)
        assert len(batch) == 0
        assert next_idx == 0


class TestComputeFirstLetter:
    def test_from_uuid(self):
        """first_letter should be computed from UUID (first char uppercase)."""
        assert compute_first_letter("A.ADCD-250312ALT-1-TEST") == "A"

    def test_from_uuid_lowercase(self):
        assert compute_first_letter("nhp-test") == "N"

    def test_empty_returns_empty(self):
        """Empty input returns empty string (not '?')."""
        assert compute_first_letter("") == ""

    def test_none_returns_empty(self):
        assert compute_first_letter(None) == ""

    def test_whitespace_returns_empty(self):
        assert compute_first_letter("   ") == ""


# ── Tests for _build_neo4j_only_outcomes helper ──────────────────────────

from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from nextseek_api.batch_upload.orchestrator import _build_neo4j_only_outcomes
from nextseek_api.batch_upload.errors import ErrorCollector, ErrorType


class TestBuildNeo4jOnlyOutcomes:
    """Unit tests for the neo4j-only INSERT-bypass helper."""

    UID_A = "NHP-260101TST-1"
    UID_B = "NHP-260101TST-2"
    UID_C = "NHP-260101TST-3"

    def _make_sample(self, uuid: str) -> InsertableSample:
        return InsertableSample(
            uuid=uuid, title="T", sample_type_id=1,
            json_metadata="{}", assay_ids=[],
        )

    @contextmanager
    def _mock_conn(self):
        yield MagicMock()

    def test_all_uids_found(self):
        """All UIDs exist in DB -> all outcomes success, inserted_count=0."""
        samples = [self._make_sample(uid) for uid in (self.UID_A, self.UID_B, self.UID_C)]
        uid_map = {self.UID_A: 10, self.UID_B: 20, self.UID_C: 30}
        ec = ErrorCollector()

        with patch("nextseek_api.batch_upload.orchestrator.load_existing_samples", return_value=uid_map), \
             patch("nextseek_api.batch_upload.orchestrator.get_connection", self._mock_conn):
            result = _build_neo4j_only_outcomes(samples, ec)

        assert result.inserted_count == 0
        assert result.updated_count == 0
        assert len(result.outcomes) == 3
        for uid, sid in uid_map.items():
            assert result.outcomes[uid].status == "success"
            assert result.outcomes[uid].sample_id == sid
        assert len(ec.all_errors()) == 0

    def test_some_uids_missing(self):
        """2 of 3 UIDs in DB -> 2 success, 1 failed with uid_not_found_in_db."""
        samples = [self._make_sample(uid) for uid in (self.UID_A, self.UID_B, self.UID_C)]
        uid_map = {self.UID_A: 10, self.UID_B: 20}  # UID_C missing
        ec = ErrorCollector()

        with patch("nextseek_api.batch_upload.orchestrator.load_existing_samples", return_value=uid_map), \
             patch("nextseek_api.batch_upload.orchestrator.get_connection", self._mock_conn):
            result = _build_neo4j_only_outcomes(samples, ec)

        assert result.outcomes[self.UID_A].status == "success"
        assert result.outcomes[self.UID_B].status == "success"
        assert result.outcomes[self.UID_C].status == "failed"
        assert result.outcomes[self.UID_C].reason == "uid_not_found_in_db"
        assert self.UID_C in result.attempted_uids
        errors = ec.all_errors()
        assert len(errors) == 1
        assert errors[0].error_type == ErrorType.VALIDATION_JSON
        assert "uid_not_found_in_db" in errors[0].message

    def test_all_uids_missing(self):
        """No UIDs in DB -> all failed, attempted_uids has both."""
        samples = [self._make_sample(uid) for uid in (self.UID_A, self.UID_B)]
        ec = ErrorCollector()

        with patch("nextseek_api.batch_upload.orchestrator.load_existing_samples", return_value={}), \
             patch("nextseek_api.batch_upload.orchestrator.get_connection", self._mock_conn):
            result = _build_neo4j_only_outcomes(samples, ec)

        assert all(o.status == "failed" for o in result.outcomes.values())
        assert result.attempted_uids == {self.UID_A, self.UID_B}
        assert len(ec.all_errors()) == 2

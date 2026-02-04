"""Tests for the INSERT stage."""
import pytest

from nextseek_api.batch_upload.insert import (
    AdaptiveBatchSizer,
    _estimate_row_payload_bytes,
    _plan_next_batch,
)
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

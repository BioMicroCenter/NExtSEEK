"""Tests for the INSERT stage."""
import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine, text

from nextseek_api.batch_upload.insert import (
    AdaptiveBatchSizer,
    _estimate_row_payload_bytes,
    _plan_next_batch,
)
from nextseek_api.batch_upload.config import BatchUploadConfig
from nextseek_api.batch_upload.errors import ErrorCollector, ErrorType
from nextseek_api.batch_upload.insert_strategies import compute_first_letter
from nextseek_api.batch_upload.models import DirectionComputation, InsertableSample, RowOutcome


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


# ── SEEK edit right on update ─────────────────────────


def _edit_conn_factory():
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE samples (id INTEGER PRIMARY KEY, uuid TEXT, policy_id INTEGER, "
                        "json_metadata TEXT, title TEXT, contributor_id INTEGER)"))
        c.execute(text("CREATE TABLE projects_samples (project_id INTEGER, sample_id INTEGER)"))
        c.execute(text("CREATE TABLE policies (id INTEGER PRIMARY KEY, access_type INTEGER)"))
        c.execute(text("CREATE TABLE permissions (id INTEGER PRIMARY KEY, policy_id INTEGER, "
                        "contributor_type TEXT, contributor_id INTEGER, access_type INTEGER)"))
        c.execute(text("INSERT INTO samples VALUES (1, 'VIEW-1', 11, '{}', 't', 99), (2, 'EDIT-1', 12, '{}', 't', 99), (3, 'GROUP-1', 13, '{}', 't', 99)"))
        c.execute(text("INSERT INTO projects_samples VALUES (7, 1), (7, 2), (7, 3)"))
        c.execute(text("INSERT INTO policies VALUES (11, 0), (12, 0), (13, 0)"))
        c.execute(text("INSERT INTO permissions (policy_id, contributor_type, contributor_id, access_type) "
                        "VALUES (11, 'Project', 7, 1), (12, 'Project', 7, 4), (13, 'WorkGroup', 3, 4)"))
    return engine.connect


def _run_update(scope):
    import nextseek_api.batch_upload.update as update_mod
    from nextseek_api.batch_upload.insert import process_batches

    def fake_bulk(rows, details, **kw):
        return {s.uuid: RowOutcome(status="success", sample_id=details[s.uuid]["sample_id"]) for s in rows}

    config = BatchUploadConfig()
    config.scope_project_ids = scope
    samples = [InsertableSample(uuid=u, title="n", sample_type_id=1, json_metadata="{}") for u in ("VIEW-1", "EDIT-1", "GROUP-1")]
    errors = ErrorCollector()
    with patch.object(update_mod, "bulk_update_samples", side_effect=fake_bulk) as bulk:
        result = process_batches(
            insertable_samples=samples, project_id=7, contributor_id=5, config=config,
            direction_computation=DirectionComputation({}, {}, {}, {}, {}), error_collector=errors,
            existing_samples={"VIEW-1": 1, "EDIT-1": 2, "GROUP-1": 3}, update_existing=True,
            conn_factory=_edit_conn_factory(),
        )
    return result, errors, bulk


def test_member_without_seek_edit_right_cannot_update():
    result, errors, bulk = _run_update((7,))
    assert [s.uuid for s in bulk.call_args.kwargs["rows"]] == ["EDIT-1"]
    assert result.outcomes["VIEW-1"].status == "failed"
    assert "does not let you edit" in result.outcomes["VIEW-1"].reason
    assert result.outcomes["EDIT-1"].status == "success"
    group = result.outcomes["GROUP-1"]
    assert group.status == "failed" and "cannot confirm" in group.reason
    assert [e.error_type for e in errors.all_errors()] == [ErrorType.PERMISSION_DENIED] * 2
    assert any("GROUP-1" in e.message and "ask the project's admin" in e.message for e in errors.all_errors())


def test_superuser_update_skips_the_edit_check():
    result, errors, bulk = _run_update(None)
    assert [s.uuid for s in bulk.call_args.kwargs["rows"]] == ["VIEW-1", "EDIT-1", "GROUP-1"]


def test_rolled_back_batch_reports_no_rows_saved():
    """A batch that fails after its samples were inserted is rolled back; the reporter counts no success."""
    from nextseek_api.batch_upload.insert import process_batches

    reporter = MagicMock()
    sample = InsertableSample(uuid="U-1", title="n", sample_type_id=1, json_metadata="{}")
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    with patch("nextseek_api.batch_upload.insert.insert_policies_for_uids", return_value=[("U-1", 1)]), \
         patch("nextseek_api.batch_upload.insert.insert_samples", return_value=[(10, "U-1")]), \
         patch("nextseek_api.batch_upload.insert.batch_insert_projects_samples",
                side_effect=RuntimeError("link failed")):
        result = process_batches(
            insertable_samples=[sample], project_id=7, contributor_id=5, config=BatchUploadConfig(),
            direction_computation=DirectionComputation({}, {}, {}, {}, {}), error_collector=ErrorCollector(), reporter=reporter,
            existing_samples={}, conn_factory=lambda: conn,
        )
    assert result.outcomes["U-1"].status == "failed"
    assert reporter.update_counts.call_args.kwargs == {"success": 0, "skipped": 0, "failed": 1}

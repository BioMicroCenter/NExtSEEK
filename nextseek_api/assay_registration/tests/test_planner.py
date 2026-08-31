"""Planning: which rows write, which already exist, which are skipped."""

from unittest.mock import MagicMock, patch

from nextseek_api.assay_registration.planner import Plan, plan_batch
from nextseek_api.assay_registration.resolver import ResolvedRow
from nextseek_api.assay_registration.schemas import RegistrationRow, RowError


def _ok(index, uid, sample_id, assay_id, project_id=3):
    return ResolvedRow(index=index, sample_uid=uid, sample_id=sample_id,
                       assay_id=assay_id, assay_title="A", project_id=project_id)


def _bad(index, uid, code="sample_uid_not_found"):
    return ResolvedRow(index=index, sample_uid=uid,
                       error=RowError(code=code, message="m"))


class TestPlanBatch:
    def test_splits_new_existing_and_skipped(self):
        rows = [
            RegistrationRow(sample_uid="A", assay_id=1),
            RegistrationRow(sample_uid="B", assay_id=1),
            RegistrationRow(sample_uid="C", assay_id=1),
        ]
        resolved = [_ok(0, "A", 100, 1), _ok(1, "B", 200, 1), _bad(2, "C")]
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.planner.resolve",
                   return_value=resolved), \
             patch("nextseek_api.assay_registration.planner.existing_membership_ids",
                   return_value={(1, 200): 219104}):
            plan = plan_batch(rows, conn)

        assert [r.sample_uid for r in plan.to_write] == ["A"]
        assert plan.already_present == {1: 219104}
        assert [r.sample_uid for r in plan.skipped] == ["C"]
        assert plan.total_rows == 3

    def test_a_bad_row_never_blocks_a_good_row(self):
        """Partial with honest receipts: a landmine does not stop the batch."""
        rows = [RegistrationRow(sample_uid=u, assay_id=1) for u in ("A", "DUP")]
        resolved = [_ok(0, "A", 100, 1), _bad(1, "DUP", "sample_uid_not_unique")]
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.planner.resolve",
                   return_value=resolved), \
             patch("nextseek_api.assay_registration.planner.existing_membership_ids",
                   return_value={}):
            plan = plan_batch(rows, conn)
        assert len(plan.to_write) == 1
        assert len(plan.skipped) == 1

    def test_already_present_is_keyed_by_submitted_row_index(self):
        """The receipt maps back to the CALLER's row, not to a position in `good`.

        Keying by position inside the filtered good-row list agrees with
        row.index only while no earlier row was skipped. Put one skipped row
        ahead of an already-present one and the response hangs a real
        assay_assets.id off the wrong receipt.
        """
        rows = [RegistrationRow(sample_uid=u, assay_id=1) for u in ("BAD", "B")]
        resolved = [_bad(0, "BAD"), _ok(1, "B", 200, 1)]
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.planner.resolve",
                   return_value=resolved), \
             patch("nextseek_api.assay_registration.planner.existing_membership_ids",
                   return_value={(1, 200): 219104}):
            plan = plan_batch(rows, conn)
        assert plan.already_present == {1: 219104}
        assert plan.to_write == []

    def test_duplicate_pairs_inside_one_request_write_once(self):
        rows = [RegistrationRow(sample_uid="A", assay_id=1)] * 2
        resolved = [_ok(0, "A", 100, 1), _ok(1, "A", 100, 1)]
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.planner.resolve",
                   return_value=resolved), \
             patch("nextseek_api.assay_registration.planner.existing_membership_ids",
                   return_value={}):
            plan = plan_batch(rows, conn)
        assert len(plan.to_write) == 1, "the same pair twice is one insert"


class TestExecutionMode:
    def test_small_batches_run_synchronously(self):
        plan = Plan(resolved=[], to_write=[], already_present={}, skipped=[],
                    total_rows=10)
        assert plan.execution_mode(threshold=5000) == "synchronous"

    def test_batches_above_the_threshold_get_a_job(self):
        plan = Plan(resolved=[], to_write=[], already_present={}, skipped=[],
                    total_rows=5001)
        assert plan.execution_mode(threshold=5000) == "asynchronous"

    def test_the_threshold_itself_is_synchronous(self):
        plan = Plan(resolved=[], to_write=[], already_present={}, skipped=[],
                    total_rows=5000)
        assert plan.execution_mode(threshold=5000) == "synchronous"

"""Planning: which rows write, which already exist, which are skipped."""

from unittest.mock import MagicMock, patch

from nextseek_api.assay_registration.planner import (
    Plan,
    existing_membership_ids,
    plan_batch,
)
from nextseek_api.assay_registration.resolver import ResolvedRow
from nextseek_api.assay_registration.schemas import RegistrationRow, RowError


class FakeConn:
    """Drives the one SELECT this module issues, hermetically.

    Deliberately local rather than imported from test_resolver: that fake
    dispatches across five statements, this module issues exactly one. It
    answers only the asset ids the call actually asked for, so a test cannot
    pass by being handed rows the query never selected, and it emulates the
    GROUP BY so removing MIN(id) from the SQL changes the answer.
    """

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        params = dict(params or {})
        self.calls.append((sql, params))
        wanted = {v for k, v in params.items() if k.startswith("s_")}
        aid = params.get("aid")
        rows = [(a, s, i) for (a, s, i) in self._rows if a == aid and s in wanted]
        if "MIN(" in sql.upper():
            lowest = {}
            for a, s, i in rows:
                if (a, s) not in lowest or i < lowest[(a, s)]:
                    lowest[(a, s)] = i
            rows = [(a, s, i) for (a, s), i in sorted(lowest.items())]
        result = MagicMock()
        result.fetchall.return_value = rows
        return result


class TestTheStatementItself:
    """`FakeConn` answers from `params` and one "MIN(" probe, never from the SQL
    text, so every test below it passes against a statement that does not say
    what this module needs it to say. That gap is not academic:

    * `planner.py:56` unpacks `(a_id, s_id, row_id)`. Transpose the select list
      and the map is keyed `(sample_id, assay_id)`, so every
      `confirmed.get((assay_id, sample_id))` in `executor.py:135` misses, and
      every CORRECTLY WRITTEN row is reported `failed` with
      `write_not_confirmed_by_readback` -- the founding defect run backwards,
      reporting successful writes as failures.
    * Drop `asset_type = 'Sample'` and a DataFile or Model sharing an asset_id
      answers for a sample, so a pair that is not present reads as
      already_present and never writes.
    * Drop `assay_id = :aid` and the query returns every assay's memberships for
      those samples, so a sample already in ANY assay reads as already_present
      in the one being registered.

    So assert the statement, not just the answer.
    """

    def _statement(self):
        conn = FakeConn([(1, 100, 5001)])
        existing_membership_ids([(1, 100)], conn)
        return " ".join(conn.calls[0][0].split())

    def test_the_select_list_is_in_the_order_the_unpack_expects(self):
        assert "SELECT assay_id, asset_id, MIN(id)" in self._statement()

    def test_both_where_predicates_are_present(self):
        sql = self._statement()
        assert "assay_id = :aid" in sql, \
            "without it the query answers for every assay these samples are in"
        assert "asset_type = 'Sample'" in sql, \
            "without it a non-Sample asset sharing an id answers for a sample"

    def test_the_asset_ids_are_bound_not_interpolated(self):
        """The one place this module builds SQL by f-string. The holes are
        generated names; the VALUES must arrive as parameters."""
        conn = FakeConn([(1, 100, 5001), (1, 200, 5002)])
        existing_membership_ids([(1, 100), (1, 200)], conn)
        sql, params = conn.calls[0]
        assert "100" not in sql and "200" not in sql
        assert sorted(v for k, v in params.items() if k.startswith("s_")) == [100, 200]
        assert params["aid"] == 1

    def test_the_aggregate_is_grouped_by_the_pair(self):
        """MIN(id) without the matching GROUP BY collapses the whole chunk to
        one row, so one pair answers for all of them."""
        assert "GROUP BY assay_id, asset_id" in self._statement()


class TestExistingMembershipIds:
    """The module's only database call, and the only one patched out of every
    plan_batch test. Untested, a transposed column in the SELECT list would
    invert the mapping silently and produce wrong receipts."""

    def test_no_pairs_issues_no_query(self):
        conn = FakeConn([])
        assert existing_membership_ids([], conn) == {}
        assert conn.calls == []

    def test_maps_each_pair_to_its_own_row_id(self):
        conn = FakeConn([(1, 100, 5001), (1, 200, 5002)])
        assert existing_membership_ids([(1, 100), (1, 200)], conn) == {
            (1, 100): 5001, (1, 200): 5002,
        }

    def test_two_assays_sharing_an_asset_id_do_not_cross_contaminate(self):
        """The unpack is (assay_id, asset_id, id). A transposed SELECT list
        inverts the mapping, and only a case where the two columns differ in
        value can catch it."""
        conn = FakeConn([(1, 100, 5001), (2, 100, 6001)])
        assert existing_membership_ids([(1, 100), (2, 100)], conn) == {
            (1, 100): 5001, (2, 100): 6001,
        }

    def test_one_statement_per_assay_not_per_pair(self):
        conn = FakeConn([(1, i, 5000 + i) for i in range(50)])
        existing_membership_ids([(1, i) for i in range(50)], conn)
        assert len(conn.calls) == 1, "grouped by assay_id, not one query per pair"

    def test_a_pair_that_is_absent_is_simply_absent(self):
        conn = FakeConn([(1, 100, 5001)])
        assert existing_membership_ids([(1, 100), (1, 999)], conn) == {(1, 100): 5001}

    def test_a_duplicated_membership_resolves_to_its_lowest_id(self):
        """assay_assets has no unique constraint on (assay_id, asset_id,
        asset_type) and production carries duplicate pairs. Rows are ordered
        here so that last-one-wins would answer 6001; MIN(id) answers 5001 on
        every run."""
        conn = FakeConn([(1, 100, 5001), (1, 100, 6001)])
        assert existing_membership_ids([(1, 100)], conn) == {(1, 100): 5001}


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
        # plan.resolved is what the receipt builder iterates, and it is the one
        # field the buckets cannot reconstruct: a within-request duplicate is
        # collapsed out of all three. Pin its order, count and identity, or
        # Plan(resolved=[]) passes every other assertion in this file.
        assert [r.index for r in plan.resolved] == [0, 1, 2]

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

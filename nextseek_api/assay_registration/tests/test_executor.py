"""Execution and the receipt rule.

The single most important property under test: a row is reported "written"
only when the database handed back a primary key for it. The legacy path set
status = 1 and never updated it from the DB call (dmac/dbtable.py:109), so a
hard failure printed "successful:". Here, success is read back, never asserted.
"""

from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.assay_registration.executor import (
    MEMBERSHIP_DIRECTION,
    execute,
    preview,
)
from nextseek_api.assay_registration.planner import Plan
from nextseek_api.assay_registration.resolver import ResolvedRow
from nextseek_api.assay_registration.schemas import RowError


def _ok(index, uid, sample_id, assay_id, project_id=3):
    return ResolvedRow(index=index, sample_uid=uid, sample_id=sample_id,
                       assay_id=assay_id, assay_title="Flow Cytometry",
                       project_id=project_id)


def _plan(to_write=(), already=None, skipped=(), total=None):
    to_write = list(to_write)
    skipped = list(skipped)
    already = already or {}
    return Plan(
        resolved=to_write + skipped,
        to_write=to_write,
        already_present=already,
        skipped=skipped,
        total_rows=total if total is not None else len(to_write) + len(skipped) + len(already),
    )


class TestReceiptRule:
    def test_a_row_is_written_only_when_a_key_comes_back(self):
        plan = _plan(to_write=[_ok(0, "A", 100, 351)])
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=1), \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={(351, 100): 414936}):
            result = execute(plan, conn)

        [row] = result.rows
        assert row.status == "written"
        assert row.assay_assets_id == 414936
        assert result.counts.written == 1

    def test_a_row_the_readback_cannot_find_is_failed_not_written(self):
        """Defect 1. The insert helper returning 1 is not evidence.

        batch_insert_assay_assets returns a count it computed itself. If the
        row is not in the table afterwards, the row failed, whatever the helper
        said.
        """
        plan = _plan(to_write=[_ok(0, "A", 100, 351)])
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=1), \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={}):
            result = execute(plan, conn)

        [row] = result.rows
        assert row.status == "failed", "insert count claimed success; readback did not"
        assert row.assay_assets_id is None
        assert row.error.code == "write_not_confirmed_by_readback"
        assert result.counts.failed == 1
        assert result.counts.written == 0

    def test_the_readback_runs_before_the_caller_sees_anything(self):
        plan = _plan(to_write=[_ok(0, "A", 100, 351)])
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=1) as insert, \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={(351, 100): 1}) as readback:
            execute(plan, conn)
        assert insert.called and readback.called


class TestDirection:
    def test_every_written_row_uses_direction_zero(self):
        """associations.py defaults direction to 1 when passed None, and
        dag.py computes 1/2 from lineage. Membership registration writes 0,
        matching the 25,765 verified production rows and ASSAY_ASSETS_DEFAULT.
        """
        plan = _plan(to_write=[_ok(0, "A", 100, 351)])
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=1) as insert, \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={(351, 100): 1}):
            execute(plan, conn)

        records = insert.call_args[0][0]
        assert MEMBERSHIP_DIRECTION == 0
        for assay_id, asset_id, asset_type, direction, rel_type, version in records:
            assert direction == 0
            assert asset_type == "Sample"
            assert rel_type is None


class TestStatuses:
    def test_already_present_rows_carry_their_existing_key(self):
        plan = _plan(to_write=[], already={0: 219104},
                     skipped=[], total=1)
        plan.resolved = [_ok(0, "A", 100, 351)]
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=0) as insert, \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={}):
            result = execute(plan, conn)
        [row] = result.rows
        assert row.status == "already_present"
        assert row.assay_assets_id == 219104
        insert.assert_not_called()

    def test_resubmitting_an_identical_batch_writes_nothing(self):
        """Unlike the sheet path, whose re-run rewrote created_at, updated_at
        and direction on every row it re-touched."""
        plan = _plan(to_write=[], already={0: 1, 1: 2}, skipped=[], total=2)
        plan.resolved = [_ok(0, "A", 100, 351), _ok(1, "B", 200, 351)]
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=0) as insert, \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={}):
            result = execute(plan, conn)
        insert.assert_not_called()
        assert result.counts.written == 0
        assert result.counts.already_present == 2
        assert result.overall_status == "succeeded"

    def test_skipped_rows_keep_their_resolver_error(self):
        bad = ResolvedRow(index=1, sample_uid="DUP",
                          error=RowError(code="sample_uid_not_unique",
                                         message="resolves to 2 rows"))
        plan = _plan(to_write=[_ok(0, "A", 100, 351)], skipped=[bad])
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=1), \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={(351, 100): 5}):
            result = execute(plan, conn)
        by_index = {r.index: r for r in result.rows}
        assert by_index[1].status == "skipped"
        assert by_index[1].error.code == "sample_uid_not_unique"
        assert result.overall_status == "partial"

    def test_rows_come_back_in_submission_order(self):
        bad = ResolvedRow(index=0, sample_uid="DUP",
                          error=RowError(code="sample_uid_not_found", message="m"))
        plan = _plan(to_write=[_ok(1, "A", 100, 351)], skipped=[bad])
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=1), \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={(351, 100): 5}):
            result = execute(plan, conn)
        assert [r.index for r in result.rows] == [0, 1]


class TestPreview:
    def test_dry_run_writes_nothing_and_reports_the_same_shape(self):
        plan = _plan(to_write=[_ok(0, "A", 100, 351)])
        result = preview(plan)
        [row] = result.rows
        assert row.status == "written", "preview reports what WOULD happen"
        assert row.assay_assets_id is None, "no key exists yet"
        assert result.counts.written == 1


class TestNoDeletePath:
    def test_the_executor_module_contains_no_delete_statement(self):
        """The third reason deletion is inexpressible: the only write call is
        batch_insert_assay_assets, and neither module contains a DELETE."""
        import inspect

        from nextseek_api.assay_registration import executor
        from nextseek_api.batch_upload import associations

        for module in (executor, associations):
            source = inspect.getsource(module).upper()
            assert "DELETE FROM" not in source
            assert "DELETEONERECORD" not in source


# ---------------------------------------------------------------------------
# Hardening. Everything below was added because a mutant survived the tests
# above. Each class names the mutation it kills, so a later reader can tell a
# real guard from a decorative one.
# ---------------------------------------------------------------------------


class TestOverallStatusOfATotalFailure:
    def test_a_batch_where_nothing_landed_is_failed_not_succeeded(self):
        """Mutant: `_overall`'s final `return "failed"` -> `"succeeded"`.

        It survived every test above, because no test asserted overall_status
        on an all-failed batch. That is the whole defect class this endpoint
        replaces: a batch that wrote nothing reporting success.
        """
        plan = _plan(to_write=[_ok(0, "A", 100, 351), _ok(1, "B", 200, 351)])
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=2), \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={}):
            result = execute(plan, conn)

        assert result.overall_status == "failed"
        assert result.counts.failed == 2
        assert result.counts.written == 0
        assert result.written_sample_ids == set()

    def test_a_batch_of_nothing_but_skips_is_failed(self):
        bad = [ResolvedRow(index=i, sample_uid=u,
                           error=RowError(code="sample_uid_not_found", message="m"))
               for i, u in enumerate("AB")]
        plan = _plan(to_write=[], skipped=bad, total=2)
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=0) as insert, \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={}):
            result = execute(plan, conn)
        insert.assert_not_called()
        assert result.overall_status == "failed"
        assert result.counts.skipped == 2


class TestWrittenSampleIds:
    """Mutant: drop `written_sample_ids.add(...)`. It survived; nothing asserted
    the field. It is not decorative -- the graph step consumes it, so a sample
    id in here that the database does not actually hold would push a membership
    edge into Neo4j for a row that never wrote.
    """

    def test_only_rows_the_readback_confirmed_reach_the_set(self):
        good = _ok(0, "A", 100, 351)
        lost = _ok(1, "B", 200, 351)          # insert claimed it; readback did not
        present = _ok(2, "C", 300, 351)       # already there before the request
        bad = ResolvedRow(index=3, sample_uid="D",
                          error=RowError(code="sample_uid_not_found", message="m"))
        plan = _plan(to_write=[good, lost], already={2: 77}, skipped=[bad], total=4)
        plan.resolved = [good, lost, present, bad]
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=2), \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={(351, 100): 900}):
            result = execute(plan, conn)

        assert result.written_sample_ids == {100}, \
            "only the confirmed row; not the lost one, the pre-existing one, or the skip"
        statuses = {r.index: r.status for r in result.rows}
        assert statuses == {0: "written", 1: "failed", 2: "already_present", 3: "skipped"}
        assert result.overall_status == "partial"

    def test_a_dry_run_tells_the_graph_nothing_wrote(self):
        plan = _plan(to_write=[_ok(0, "A", 100, 351)])
        assert preview(plan).written_sample_ids == set()


class TestCollapsedWithinRequestDuplicate:
    """Mutants: the duplicate branch reported as "written"; the branch made
    unreachable; `preview`'s `elif resolved.ok` removed. All three survived,
    because no test submitted the same pair twice -- the one case that is in
    NONE of the plan's three buckets.
    """

    def test_the_second_copy_is_already_present_and_carries_the_same_key(self):
        first = _ok(0, "A", 100, 351)
        second = _ok(1, "A", 100, 351)   # identical pair, submitted twice
        plan = _plan(to_write=[first], total=2)
        plan.resolved = [first, second]
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=1), \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={(351, 100): 8125}):
            result = execute(plan, conn)

        assert [r.index for r in result.rows] == [0, 1], \
            "one receipt row per SUBMITTED row, not per planned write"
        assert [r.status for r in result.rows] == ["written", "already_present"]
        assert [r.assay_assets_id for r in result.rows] == [8125, 8125]
        assert result.counts.written == 1
        assert result.counts.already_present == 1
        assert result.counts.submitted == 2
        assert result.written_sample_ids == {100}
        assert result.overall_status == "succeeded"

    def test_preview_reports_the_second_copy_too(self):
        first = _ok(0, "A", 100, 351)
        second = _ok(1, "A", 100, 351)
        plan = _plan(to_write=[first], total=2)
        plan.resolved = [first, second]

        result = preview(plan)
        assert [r.index for r in result.rows] == [0, 1]
        assert [r.status for r in result.rows] == ["written", "already_present"]
        assert result.counts.submitted == 2

    def test_the_counts_account_for_every_submitted_row_exactly_once(self):
        first = _ok(0, "A", 100, 351)
        second = _ok(1, "A", 100, 351)
        bad = ResolvedRow(index=2, sample_uid="C",
                          error=RowError(code="sample_uid_not_unique", message="m"))
        plan = _plan(to_write=[first], skipped=[bad], total=3)
        plan.resolved = [first, second, bad]
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=1), \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={(351, 100): 8125}):
            result = execute(plan, conn)

        c = result.counts
        assert c.submitted == 3
        assert c.written + c.already_present + c.skipped + c.failed == c.submitted
        assert len(result.rows) == c.submitted


class TestWhatReachesTheDatabase:
    """Mutants: read back a different pair set than the one written; build the
    insert records from `plan.resolved` instead of `plan.to_write`. Both
    survived, because every test patched the two helpers and then never looked
    at what they were handed.
    """

    def test_the_insert_and_the_readback_cover_exactly_the_planned_pairs(self):
        good = _ok(0, "A", 100, 351)
        bad = ResolvedRow(index=1, sample_uid="B",
                          error=RowError(code="sample_uid_not_found", message="m"))
        present = _ok(2, "C", 300, 351)
        plan = _plan(to_write=[good], already={2: 5}, skipped=[bad], total=3)
        plan.resolved = [good, bad, present]
        conn = MagicMock()
        with patch("nextseek_api.assay_registration.executor.batch_insert_assay_assets",
                   return_value=1) as insert, \
             patch("nextseek_api.assay_registration.executor.existing_membership_ids",
                   return_value={(351, 100): 9}) as readback:
            execute(plan, conn)

        records = insert.call_args[0][0]
        assert [(r[0], r[1]) for r in records] == [(351, 100)], (
            "a skipped row carries sample_id None; reaching the insert would "
            "write a NULL membership, and a pre-existing row would be rewritten"
        )
        assert insert.call_args[0][1] is conn

        pairs = readback.call_args[0][0]
        assert list(pairs) == [(351, 100)], \
            "the receipt must be read back for the pairs we actually wrote"
        assert readback.call_args[0][1] is conn


class TestNoDeletePathHardened:
    """The brief's guard is a substring hunt, and a substring hunt is only as
    good as its strings. Two mutants walked straight past it:
      * a DELETE broken over lines, so "DELETE FROM" never appears literally;
      * `conn.execute("DELE" "TE FROM assay_assets ...")`, adjacent string
        literals that the compiler joins but the source text does not.
    """

    def test_no_destructive_statement_survives_whitespace_normalisation(self):
        import inspect
        import re

        from nextseek_api.assay_registration import executor
        from nextseek_api.batch_upload import associations

        forbidden = ("DELETE FROM", "TRUNCATE ", "DROP TABLE",
                     "UPDATE ASSAY_ASSETS", "DELETEONERECORD")
        for module in (executor, associations):
            flat = re.sub(r"\s+", " ", inspect.getsource(module)).upper()
            for phrase in forbidden:
                assert phrase not in flat, f"{module.__name__} contains {phrase}"

    def test_the_executor_issues_no_sql_of_its_own(self):
        """The structural guard the substring hunt cannot be.

        Every database touch goes through `batch_insert_assay_assets` or
        `existing_membership_ids`. So the executor calls no `.execute()` and
        builds no `text()`; a hand-written statement here is a new code path,
        whatever it spells. A future read-back belongs in planner.py, where
        `existing_membership_ids` already lives and its SQL is covered.
        """
        import ast
        import inspect

        from nextseek_api.assay_registration import executor

        tree = ast.parse(inspect.getsource(executor))

        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    called.add(node.func.attr)
                elif isinstance(node.func, ast.Name):
                    called.add(node.func.id)
        assert "execute" not in called, "executor.py runs a statement of its own"
        assert "text" not in called, "executor.py builds SQL of its own"

        from_batch_upload = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
            and "batch_upload" in node.module
            for alias in node.names
        }
        assert from_batch_upload == {"batch_insert_assay_assets"}, (
            "the only batch_upload helper the executor may reach is the "
            f"idempotent insert; got {sorted(from_batch_upload)}"
        )

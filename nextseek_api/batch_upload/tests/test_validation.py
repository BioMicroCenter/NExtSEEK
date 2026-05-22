"""Integration tests for run_validation_multi (sync pre-INSERT validation flow).

Uses the same DB-level mocking harness as the orchestrator level tests: real
pipeline code runs against a FakeDB. Validation stops before INSERT, so no
process_batches / Neo4j / checkpoint patching is needed.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from nextseek_api.batch_upload.models import ConvertedBatch, InputRowModel, ValidationResult
from nextseek_api.batch_upload.prefetch import clear_caches as _clear_prefetch_caches
from nextseek_api.batch_upload.validation import run_validation_multi
from nextseek_api.batch_upload.tests.test_orchestrator_levels import (
    FakeConnection,
    FakeDB,
    _make_row,
)


@pytest.fixture(autouse=True)
def _isolate_prefetch_caches():
    """Clear module-level prefetch caches before/after each test."""
    _clear_prefetch_caches()
    yield
    _clear_prefetch_caches()


def _run_validation(rows, db, checks=frozenset({"structure"}), extra_patches=None):
    """Run run_validation_multi against a FakeDB."""
    @contextmanager
    def fake_conn():
        yield FakeConnection(db)

    def _mock_merge_files(paths):
        return ConvertedBatch(rows=rows, source_files=paths or ["dummy"])

    patches = {
        "nextseek_api.batch_upload.orchestrator.get_connection": fake_conn,
        "nextseek_api.batch_upload.orchestrator.merge_files": _mock_merge_files,
    }
    if extra_patches:
        patches.update(extra_patches)

    stack = []
    for target, mock_val in patches.items():
        p = patch(target, mock_val)
        stack.append(p)
        p.start()
    try:
        return run_validation_multi(
            xlsx_paths=["dummy.xlsx"],
            project_id=1,
            contributor_id=1,
            lababbv="TST",
            checks=checks,
        )
    finally:
        for p in stack:
            p.stop()


UID_A = "NHP-260101TST-1"
UID_B = "NHP-260101TST-2"


class TestRunValidationMulti:
    """run_validation_multi end-to-end behavior."""

    def test_happy_path_structure_only(self):
        """Valid rows, checks=structure -> valid=True, no errors, success count == rows."""
        rows = [_make_row(UID_A), _make_row(UID_B)]

        result = _run_validation(rows, FakeDB())

        assert isinstance(result, ValidationResult)
        assert result.valid is True
        assert result.errors == []
        assert result.totals.success == 2
        assert result.totals.failed == 0
        assert result.summary == "No issues"
        assert result.checks_run == ["structure"]
        assert result.checks_skipped == ["dag", "name_check"]
        # No CSV, no job for sync validation.
        assert result.job_id is None
        assert result.summary_path is None

    def test_attribute_name_error_surfaces_in_errors(self):
        """A row with an undeclared json_metadata key -> valid=False, error in errors[]."""
        good = _make_row(UID_A)
        bad = InputRowModel(
            UID=UID_B,
            SampleType="NHP_blood",
            json_metadata='{"BadKey":"x"}',
            assay_ids=[],
        )

        # FakeDB default: sample_type 1 declares only the "Parent" attribute.
        result = _run_validation([good, bad], FakeDB())

        assert result.valid is False
        assert result.totals.success == 1
        assert result.totals.failed == 1
        attr_errors = [e for e in result.errors if e.type == "VALIDATION_ATTRIBUTE_NAME"]
        assert len(attr_errors) == 1
        assert "BadKey" in attr_errors[0].message

    def test_all_errors_surface_uncapped(self):
        """Every error must surface — no silent cap.

        Regression guard for Issue B: `_project_errors` previously truncated
        the list at the first 50 entries, so a sheet with >50 bad rows lost
        the rest with no signal. A 60-bad-row sheet must yield 60 errors.
        """
        # One surviving good row so the pipeline does not abort empty.
        good = InputRowModel(
            UID=UID_A, SampleType="NHP_blood", json_metadata="{}",
            assay_ids=[], original_row_index=0,
        )
        bad = [
            InputRowModel(
                UID=f"NHP-260101TST-{i + 100}",
                SampleType="NHP_blood",
                json_metadata='{"BadKey":"x"}',
                assay_ids=[],
                original_row_index=i + 1,
            )
            for i in range(60)
        ]

        result = _run_validation([good, *bad], FakeDB())

        assert result.valid is False
        assert result.totals.failed == 60
        attr_errors = [e for e in result.errors if e.type == "VALIDATION_ATTRIBUTE_NAME"]
        assert len(attr_errors) == 60
        # The honest summary reflects the true total, not a capped count.
        assert "60 issue(s) found" in result.summary

    def test_errors_grouped_by_type_and_message(self):
        """Issue C: errors sharing a (type, message) collapse into one group
        listing every affected row; distinct errors stay separate groups."""
        good = InputRowModel(
            UID=UID_A, SampleType="NHP_blood", json_metadata="{}",
            assay_ids=[], original_row_index=0,
        )
        # 5 rows fail on the same undeclared key -> identical (type, message).
        same = [
            InputRowModel(
                UID=f"NHP-260101TST-{i + 100}", SampleType="NHP_blood",
                json_metadata='{"BadKey":"x"}', assay_ids=[],
                original_row_index=i + 1,
            )
            for i in range(5)
        ]
        # 1 row fails on a different key -> a second, distinct group.
        other = InputRowModel(
            UID="NHP-260101TST-200", SampleType="NHP_blood",
            json_metadata='{"OtherKey":"y"}', assay_ids=[],
            original_row_index=6,
        )

        result = _run_validation([good, *same, other], FakeDB())

        # Flat list stays complete and uncapped.
        assert len(result.errors) == 6
        # Grouped view: 2 distinct (type, message) pairs.
        assert len(result.error_groups) == 2
        big = next(g for g in result.error_groups if g.count == 5)
        assert big.type == "VALIDATION_ATTRIBUTE_NAME"
        assert "BadKey" in big.message
        assert sorted(big.rows) == [1, 2, 3, 4, 5]
        small = next(g for g in result.error_groups if g.count == 1)
        assert "OtherKey" in small.message
        assert small.rows == [6]
        # Honest summary names both numbers.
        assert result.summary == "6 issue(s) found in 2 distinct error(s)"

    def test_transform_error_carries_row_and_pre_assigned_uid(self):
        """A TRANSFORM error is attributed to its row index, and to its UID when
        the row had one pre-assigned (the update case)."""
        good = InputRowModel(
            UID=UID_A, SampleType="NHP_blood", json_metadata="{}",
            assay_ids=[], original_row_index=0,
        )
        bad = InputRowModel(
            UID=UID_B, SampleType="NHP_blood", json_metadata='{"BadKey":"x"}',
            assay_ids=[], original_row_index=1,
        )

        result = _run_validation([good, bad], FakeDB())

        attr_errors = [e for e in result.errors if e.type == "VALIDATION_ATTRIBUTE_NAME"]
        assert len(attr_errors) == 1
        assert attr_errors[0].row == 1
        assert attr_errors[0].uid == UID_B

    def test_new_sample_error_has_row_but_no_generated_uid(self):
        """For a new sample (no pre-assigned UID), the error carries `row` but not
        the throwaway UID that UID_GEN generated in validate mode."""
        good = InputRowModel(
            UID=UID_A, SampleType="NHP_blood", json_metadata="{}",
            assay_ids=[], original_row_index=0,
        )
        new_bad = InputRowModel(
            UID=None, SampleType="NHP_blood", json_metadata='{"BadKey":"x"}',
            assay_ids=[], original_row_index=3,
        )

        # UID_GEN injects the generated UID into json_metadata as a "UID" key;
        # the no-skip-list check then requires "UID" to be a declared attribute.
        result = _run_validation(
            [good, new_bad], FakeDB(sample_attributes={1: {"Parent", "UID"}})
        )

        attr_errors = [e for e in result.errors if e.type == "VALIDATION_ATTRIBUTE_NAME"]
        assert len(attr_errors) == 1
        assert attr_errors[0].row == 3
        assert attr_errors[0].uid is None

    def test_name_check_reports_existing_name_as_skipped(self):
        """checks=name_check: a row whose Name already exists -> counted as skipped."""
        # One UID-less row that NAME_CHECK will match against the DB, plus one
        # ordinary row that survives so the pipeline does not abort empty.
        matched_row = InputRowModel(
            UID=None,
            SampleType="NHP_blood",
            json_metadata='{"Name":"existing-sample"}',
            assay_ids=[],
        )
        surviving_row = _make_row(UID_A)

        def _fake_name_check(rows, conn):
            # (remaining_rows, name_matches, matched_rows, ambiguous_rows)
            matches = {"existing-sample": {"uid": UID_B, "sample_id": 42}}
            return [surviving_row], matches, [(matched_row, "existing-sample")], []

        result = _run_validation(
            [matched_row, surviving_row],
            FakeDB(),
            checks=frozenset({"structure", "name_check"}),
            extra_patches={
                "nextseek_api.batch_upload.orchestrator.check_name_exists_in_db": _fake_name_check,
            },
        )

        assert result.totals.skipped == 1
        assert result.totals.success == 1
        assert "name_check" in result.checks_run
        assert "name_check" not in result.checks_skipped

    def test_convert_warnings_propagate_to_result(self):
        """convert_warnings (e.g. dropped-column warnings) reach ValidationResult.warnings.

        Regression guard: the TRANSFORM loop must not rebind/clobber the
        `warnings` dict that carries `convert_warnings`. Before the fix, the
        dropped-column warnings added in CONVERT never reached the response.
        """
        rows = [_make_row(UID_A)]

        def _merge_with_warnings(paths):
            return ConvertedBatch(
                rows=rows,
                source_files=paths or ["dummy"],
                warnings=["SAMPLES column 'Foo' silently dropped — not declared in INSTRUCTIONS.Field."],
            )

        result = _run_validation(
            rows,
            FakeDB(),
            extra_patches={
                "nextseek_api.batch_upload.orchestrator.merge_files": _merge_with_warnings,
            },
        )

        assert "convert_warnings" in result.warnings
        assert any("Foo" in w for w in result.warnings["convert_warnings"])

    def test_empty_input_aborts_with_error_totals(self):
        """No rows after CONVERT -> aborted: valid=False, totals.error set."""
        result = _run_validation([], FakeDB())

        assert result.valid is False
        assert result.totals.error == "No valid rows after CONVERT"
        assert result.totals.processed == 0
        assert result.summary == "1 issue(s) found"

    def test_dag_check_reports_cycle_in_errors(self):
        """checks=dag: a dependency cycle is surfaced as CYCLE_UNRESOLVABLE errors."""
        rows = [
            _make_row(UID_A, parent_uid=UID_B),
            _make_row(UID_B, parent_uid=UID_A),
        ]

        result = _run_validation(rows, FakeDB(), checks=frozenset({"structure", "dag"}))

        assert result.valid is False
        cycle_errors = [e for e in result.errors if e.type == "CYCLE_UNRESOLVABLE"]
        assert len(cycle_errors) == 2
        assert "dag" in result.checks_run

"""Integration tests for run_validation_multi (sync pre-INSERT validation flow).

Uses the same DB-level mocking harness as the orchestrator level tests: real
pipeline code runs against a FakeDB. Validation stops before INSERT, so no
process_batches / Neo4j / checkpoint patching is needed.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from nextseek_api.batch_upload.errors import (
    ErrorCollector,
    ErrorType,
    Severity,
    classify_severity,
)
from nextseek_api.batch_upload.models import (
    BatchUploadTotals,
    ConvertedBatch,
    InputRowModel,
    ValidationResult,
)
from nextseek_api.batch_upload.prefetch import clear_caches as _clear_prefetch_caches
from nextseek_api.batch_upload.validation import _finalize, run_validation_multi
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
        # All 60 rows fail the same check -> one distinct (type, message) group.
        assert result.summary == "1 distinct error (60 total)"

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
        assert result.summary == "2 distinct errors (6 total)"

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
        assert result.summary == "Validation could not complete"

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


class TestSeverityDecidesValid:
    """Issue #105: `valid` must be computed from RowError.severity, not from the
    mere presence of an entry in the projected `errors[]` list.

    `_project_errors` drops `severity`, so a sheet carrying only WARNING/INFO
    entries — a forward reference to a not-yet-uploaded parent being the
    motivating case — used to be reported `valid=False`. The danger is a curator
    "fixing" the sheet by deleting the reference and destroying the lineage.
    """

    def test_orphan_parent_warning_does_not_invalidate_the_sheet(self):
        """A parent UID in neither the batch nor the DB is a WARNING, not a block.

        LEVELS records the child as an orphan and the pipeline treats it as a
        root sample; `orphan_resolution` reconciles it once the parent lands.
        The entry must still be reported, but must not fail the sheet.
        """
        rows = [
            _make_row(UID_A),
            # Forward reference: parent not in this batch and not in the DB.
            _make_row(UID_B, parent_uid="NHP-260101TST-999"),
        ]

        result = _run_validation(rows, FakeDB(), checks=frozenset({"structure", "dag"}))

        orphan = [e for e in result.errors if "treating as root sample" in e.message]
        assert len(orphan) == 1, "the warning must still be reported, not filtered"
        assert orphan[0].uid == UID_B
        assert result.totals.failed == 0
        assert result.valid is True

    def test_blocking_error_alongside_a_warning_still_invalidates(self):
        """A WARNING does not cancel out an ERROR sharing the same sheet.

        The blocking entry here is CYCLE_UNRESOLVABLE, which is graded ERROR and
        does NOT increment ``totals.failed`` — so this asserts the severity gate
        specifically, not the uninsertable-row guard that would mask it.
        """
        rows = [
            # A <-> B dependency cycle: two CYCLE_UNRESOLVABLE (ERROR) entries.
            _make_row(UID_A, parent_uid=UID_B),
            _make_row(UID_B, parent_uid=UID_A),
            # Plus a forward reference: one orphan WARNING.
            _make_row("NHP-260101TST-3", parent_uid="NHP-260101TST-999"),
        ]

        result = _run_validation(rows, FakeDB(), checks=frozenset({"structure", "dag"}))

        assert any("treating as root sample" in e.message for e in result.errors)
        assert len([e for e in result.errors if e.type == "CYCLE_UNRESOLVABLE"]) == 2
        assert classify_severity(ErrorType.CYCLE_UNRESOLVABLE) is Severity.ERROR
        # Nothing failed TRANSFORM: only the severity gate can make this False.
        assert result.totals.failed == 0
        assert result.totals.error is None
        assert result.valid is False

    def test_transform_failure_blocks_even_when_its_severity_is_non_blocking(self):
        """A row that could not be transformed blocks, whatever severity it got.

        `resolve_sample_type_id` raises ``ValueError("Sample type not found: …")``;
        `_classify_validation_error` sees "sample type" and labels it
        VALIDATION_SAMPLE_TYPE, which `_SEVERITY_MAP` grades WARNING. The row is
        nonetheless uninsertable (it is counted in ``totals.failed``), so the
        sheet is not valid. Guards the regression that a severity-only test
        would let through.
        """
        good = _make_row(UID_A)
        bad = InputRowModel(
            UID=UID_B,
            SampleType="NoSuchSampleType",
            json_metadata="{}",
            assay_ids=[],
            original_row_index=1,
        )

        result = _run_validation([good, bad], FakeDB())

        st_errors = [e for e in result.errors if e.type == "VALIDATION_SAMPLE_TYPE"]
        assert len(st_errors) == 1
        assert classify_severity(ErrorType.VALIDATION_SAMPLE_TYPE) is Severity.WARNING
        assert result.totals.failed == 1
        assert result.valid is False

    def test_summary_names_the_warnings_on_an_otherwise_valid_sheet(self):
        """A valid sheet carrying warnings must not read "No issues"."""
        rows = [
            _make_row(UID_A),
            _make_row(UID_B, parent_uid="NHP-260101TST-999"),
        ]

        result = _run_validation(rows, FakeDB(), checks=frozenset({"structure", "dag"}))

        assert result.valid is True
        assert result.summary == "No blocking errors (1 non-blocking issue)"


class TestFinalizeSeverityGate:
    """Unit-level coverage of the severity gate in `_finalize`.

    Exercised directly because CRITICAL is unreachable through
    `run_validation_multi`: the only CRITICAL type is DB_CONN and its sole emit
    site is in INSERT (`insert.py`), a stage validation never reaches.
    """

    @staticmethod
    def _finalize_with(*severities):
        collector = ErrorCollector()
        for i, sev in enumerate(severities):
            collector.add(
                row_index=i,
                uid=None,
                error_type=ErrorType.UNKNOWN,
                message=f"entry {i}",
                severity=sev,
            )
        totals = BatchUploadTotals(processed=1, success=1, skipped=0, failed=0)
        return _finalize(totals, collector, {}, ["structure"], [], set())

    @pytest.mark.parametrize(
        "severity,blocks",
        [
            (Severity.CRITICAL, True),
            (Severity.ERROR, True),
            (Severity.WARNING, False),
            (Severity.INFO, False),
        ],
    )
    def test_each_severity_blocks_or_does_not(self, severity, blocks):
        result = self._finalize_with(severity)

        assert len(result.errors) == 1, "every entry is reported regardless"
        assert result.valid is (not blocks)

    def test_one_critical_among_warnings_invalidates(self):
        result = self._finalize_with(
            Severity.WARNING, Severity.INFO, Severity.CRITICAL, Severity.WARNING
        )

        assert len(result.errors) == 4
        assert result.valid is False

    def test_grouped_warning_summary_names_distinct_and_total(self):
        """Warnings that collapse into groups keep the distinct/total phrasing."""
        collector = ErrorCollector()
        for i in range(5):
            collector.add(
                row_index=i, uid=None, error_type=ErrorType.UNKNOWN,
                message="same message", severity=Severity.WARNING,
            )
        totals = BatchUploadTotals(processed=5, success=5, skipped=0, failed=0)

        result = _finalize(totals, collector, {}, ["structure"], [], set())

        assert result.valid is True
        assert result.summary == "No blocking errors (1 distinct non-blocking issue, 5 total)"

    def test_pipeline_abort_still_invalidates_a_warning_only_sheet(self):
        """`totals.error` blocks independently of severity."""
        collector = ErrorCollector()
        collector.add(
            row_index=0, uid=None, error_type=ErrorType.UNKNOWN,
            message="w", severity=Severity.WARNING,
        )
        totals = BatchUploadTotals(
            processed=0, success=0, skipped=0, failed=0, error="No valid rows after CONVERT",
        )

        result = _finalize(totals, collector, {}, ["structure"], [], set())

        assert result.valid is False

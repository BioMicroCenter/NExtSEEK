import pytest
from pydantic import ValidationError


class TestResolvedDeleteTarget:

    def test_accepts_unresolved_state(self):
        from nextseek_api.batch_delete.models import ResolvedDeleteTarget
        target = ResolvedDeleteTarget(sample_identifier="NHP-001")
        assert target.sample_identifier == "NHP-001"
        assert target.seek_id is None

    def test_accepts_resolved_state(self):
        from nextseek_api.batch_delete.models import ResolvedDeleteTarget
        target = ResolvedDeleteTarget(sample_identifier="123", seek_id=123)
        assert target.seek_id == 123


class TestLoadedSampleDeleteContext:

    def test_rejects_non_positive_numeric_ids(self):
        from nextseek_api.batch_delete.models import LoadedSampleDeleteContext
        with pytest.raises(ValidationError):
            LoadedSampleDeleteContext(
                sample_identifier="NHP-001",
                seek_id=0,
                uuid="NHP-001",
                policy_id=1,
                contributor_id=1,
            )


class TestDeleteEligibility:

    def test_can_delete_true_requires_no_block_reason(self):
        from nextseek_api.batch_delete.models import DeleteEligibility
        with pytest.raises(ValidationError):
            DeleteEligibility(
                sample_identifier="NHP-001",
                seek_id=1,
                uuid="NHP-001",
                can_delete=True,
                block_reason="forbidden",
            )

    def test_has_children_can_include_child_uids(self):
        from nextseek_api.batch_delete.models import DeleteEligibility
        eligibility = DeleteEligibility(
            sample_identifier="NHP-001",
            seek_id=1,
            uuid="NHP-001",
            can_delete=False,
            block_reason="has_children",
            child_sample_uids=["NHP-002"],
        )
        assert eligibility.child_sample_uids == ["NHP-002"]

    def test_forbidden_block_reason_rejects_permission_basis(self):
        from nextseek_api.batch_delete.models import DeleteEligibility
        with pytest.raises(ValidationError):
            DeleteEligibility(
                sample_identifier="NHP-001",
                seek_id=1,
                uuid="NHP-001",
                can_delete=False,
                block_reason="forbidden",
                permission_basis="admin",
            )

    def test_can_delete_true_with_no_block_reason_is_valid(self):
        from nextseek_api.batch_delete.models import DeleteEligibility
        eligibility = DeleteEligibility(
            sample_identifier="NHP-001",
            seek_id=1,
            uuid="NHP-001",
            can_delete=True,
            permission_basis="owner",
        )
        assert eligibility.can_delete is True


class TestDeleteOutcomeInternal:

    def test_validates_allowed_status_and_reason_code(self):
        from nextseek_api.batch_delete.models import DeleteOutcomeInternal
        outcome = DeleteOutcomeInternal(
            sample_identifier="NHP-001",
            status="failed",
            reason_code="sql_failed",
            message="delete failed",
        )
        assert outcome.reason_code == "sql_failed"

    def test_rejects_invalid_reason_code(self):
        from nextseek_api.batch_delete.models import DeleteOutcomeInternal
        with pytest.raises(ValidationError):
            DeleteOutcomeInternal(
                sample_identifier="NHP-001",
                status="failed",
                reason_code="bad_reason",
                message="delete failed",
            )


class TestBatchDeleteSummaryInternal:

    def test_rejects_negative_counters(self):
        from nextseek_api.batch_delete.models import BatchDeleteSummaryInternal
        with pytest.raises(ValidationError):
            BatchDeleteSummaryInternal(requested=-1)


class TestBatchDeleteSummaryReportRow:

    def test_validates_csv_ready_row_data_with_optional_identifiers(self):
        from nextseek_api.batch_delete.models import BatchDeleteSummaryReportRow
        row = BatchDeleteSummaryReportRow(
            row_index=0,
            sample_identifier="NHP-001",
            status="deleted",
            message="deleted",
        )
        assert row.seek_id is None
        assert row.uuid is None


class TestBatchDeleteSummaryReport:

    def test_validates_rows_summary_and_warnings_together(self):
        from nextseek_api.batch_delete.models import BatchDeleteSummaryInternal, BatchDeleteSummaryReport
        report = BatchDeleteSummaryReport(
            rows=[],
            summary=BatchDeleteSummaryInternal(requested=1, deleted=1),
            warnings=["warn"],
        )
        assert report.summary.deleted == 1
        assert report.warnings == ["warn"]

    def test_round_trips_with_model_dump_and_model_validate(self):
        from nextseek_api.batch_delete.models import BatchDeleteSummaryInternal, BatchDeleteSummaryReport, BatchDeleteSummaryReportRow
        report = BatchDeleteSummaryReport(
            rows=[
                BatchDeleteSummaryReportRow(
                    row_index=0,
                    sample_identifier="NHP-001",
                    status="deleted",
                    message="deleted",
                )
            ],
            summary=BatchDeleteSummaryInternal(requested=1, deleted=1),
        )
        restored = BatchDeleteSummaryReport.model_validate(report.model_dump())
        assert restored.model_dump() == report.model_dump()


class TestDeleteEligibilityValidatorCoverage:

    def test_constructor_accepts_valid_model(self):
        from nextseek_api.batch_delete.models import DeleteEligibility
        eligibility = DeleteEligibility(
            sample_identifier="NHP-001",
            seek_id=1,
            uuid="NHP-001",
            can_delete=True,
            permission_basis="owner",
            child_sample_uids=[],
            block_reason=None,
        )
        assert eligibility.can_delete is True

    def test_constructor_raises_for_block_reason_when_can_delete_true(self):
        from nextseek_api.batch_delete.models import DeleteEligibility
        with pytest.raises(ValueError, match="block_reason"):
            DeleteEligibility(
                sample_identifier="NHP-001",
                seek_id=1,
                uuid="NHP-001",
                can_delete=True,
                permission_basis=None,
                child_sample_uids=[],
                block_reason="forbidden",
            )

    def test_constructor_raises_for_permission_basis_when_forbidden(self):
        from nextseek_api.batch_delete.models import DeleteEligibility
        with pytest.raises(ValueError, match="permission_basis"):
            DeleteEligibility(
                sample_identifier="NHP-001",
                seek_id=1,
                uuid="NHP-001",
                can_delete=False,
                permission_basis="admin",
                child_sample_uids=[],
                block_reason="forbidden",
            )

    def test_helper_accepts_valid_combination(self):
        from nextseek_api.batch_delete.models import _validate_delete_eligibility_consistency
        assert _validate_delete_eligibility_consistency(
            can_delete=True,
            block_reason=None,
            permission_basis="owner",
        ) is None

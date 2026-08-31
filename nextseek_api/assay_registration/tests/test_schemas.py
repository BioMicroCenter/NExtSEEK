"""Request and response contracts for batch assay registration.

The request shape is a safety property, not a convenience: there must be no
way to express a deletion. These tests pin that.
"""

import pytest
from pydantic import ValidationError

from nextseek_api.assay_registration.schemas import (
    RegistrationRequest,
    RegistrationRow,
    RowResult,
)


class TestRegistrationRow:
    def test_accepts_an_internal_assay_title(self):
        row = RegistrationRow(sample_uid="D.NHP-240115MIT-001", assay="Flow Cytometry")
        assert row.assay == "Flow Cytometry"
        assert row.assay_id is None

    def test_accepts_a_numeric_assay_id(self):
        row = RegistrationRow(sample_uid="D.NHP-240115MIT-001", assay_id=351)
        assert row.assay_id == 351
        assert row.assay is None

    def test_rejects_both_forms_at_once(self):
        with pytest.raises(ValidationError, match="exactly one"):
            RegistrationRow(sample_uid="X", assay="Flow Cytometry", assay_id=351)

    def test_rejects_neither_form(self):
        with pytest.raises(ValidationError, match="exactly one"):
            RegistrationRow(sample_uid="X")

    def test_rejects_a_blank_sample_uid(self):
        with pytest.raises(ValidationError):
            RegistrationRow(sample_uid="   ", assay_id=351)

    def test_rejects_a_nonpositive_assay_id(self):
        with pytest.raises(ValidationError):
            RegistrationRow(sample_uid="X", assay_id=0)

    @pytest.mark.parametrize(
        "field",
        ["direction", "current_assay_id", "delete", "remove", "samples"],
    )
    def test_deletion_is_not_expressible(self, field):
        """extra='forbid' is the first of three reasons deletion cannot be sent.

        There is no delete verb, no Current-column pair whose presence would
        select the sheet path's delete branch, and no complete-list array whose
        omissions could imply removal.
        """
        with pytest.raises(ValidationError):
            RegistrationRow(**{"sample_uid": "X", "assay_id": 351, field: 1})


class TestRegistrationRequest:
    def test_dry_run_defaults_to_false(self):
        req = RegistrationRequest(registrations=[{"sample_uid": "X", "assay_id": 1}])
        assert req.dry_run is False

    def test_rejects_an_empty_batch(self):
        with pytest.raises(ValidationError):
            RegistrationRequest(registrations=[])

    def test_rejects_unknown_top_level_fields(self):
        with pytest.raises(ValidationError):
            RegistrationRequest(
                registrations=[{"sample_uid": "X", "assay_id": 1}],
                update_existing=True,
            )


class TestRowResult:
    def test_written_row_carries_the_database_assigned_key(self):
        result = RowResult(
            index=0, sample_uid="X", status="written",
            sample_id=48213, assay_id=351, assay_title="Flow Cytometry",
            project_id=3, assay_assets_id=414936,
        )
        assert result.assay_assets_id == 414936

    def test_status_vocabulary_is_closed(self):
        with pytest.raises(ValidationError):
            RowResult(index=0, sample_uid="X", status="successful")

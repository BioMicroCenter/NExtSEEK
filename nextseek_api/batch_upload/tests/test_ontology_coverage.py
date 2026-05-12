"""Coverage tests for batch_upload/ontology.py — targeting uncovered lines.

Covers:
- load_ontology_from_sheet: vocab_name empty, column not found (case-insensitive lookup)
- _build_ontology_model: no controlled fields path, single-term literal
- _extract_ontology_violations: value extraction from sample_dicts, non-string value, ctx fallback
- validate_ontology_bulk: no violations (valid data), violations with auto-correction
"""

import pytest
from unittest.mock import MagicMock

from nextseek_api.batch_upload.models import OntologySpec, OntologyViolation
from nextseek_api.batch_upload.ontology import (
    load_ontology_from_sheet,
    _build_ontology_model,
    _extract_ontology_violations,
    validate_ontology_bulk,
)


class TestLoadOntologyFromSheet:

    def test_no_controlled_instructions(self):
        """Instructions without 'controlled ontology' type -> empty specs."""
        from nextseek_api.batch_upload.models import InstructionRow
        import polars as pl

        instructions = [
            InstructionRow(field="Name", database_field="NHP::Name", field_type="Text"),
        ]
        df_ont = pl.DataFrame({"Species": ["Monkey", "Mouse"]})
        specs = load_ontology_from_sheet(instructions, df_ont)
        assert specs == []

    def test_vocab_name_empty(self):
        """Empty ontology column name -> skip (line 34)."""
        from nextseek_api.batch_upload.models import InstructionRow
        import polars as pl

        instructions = [
            InstructionRow(
                field="Species", database_field="NHP::Species",
                field_type="Controlled Ontology", ontology="",
            ),
        ]
        df_ont = pl.DataFrame({"Species": ["Monkey"]})
        specs = load_ontology_from_sheet(instructions, df_ont)
        assert specs == []

    def test_column_not_found(self):
        """Ontology column not in sheet -> skip with warning (lines 47-53)."""
        from nextseek_api.batch_upload.models import InstructionRow
        import polars as pl

        instructions = [
            InstructionRow(
                field="Species", database_field="NHP::Species",
                field_type="Controlled Ontology", ontology="MissingColumn",
            ),
        ]
        df_ont = pl.DataFrame({"OtherCol": ["value"]})
        specs = load_ontology_from_sheet(instructions, df_ont)
        assert specs == []

    def test_case_insensitive_lookup(self):
        """Case-insensitive column lookup (lines 47-50)."""
        from nextseek_api.batch_upload.models import InstructionRow
        import polars as pl

        instructions = [
            InstructionRow(
                field="Species", database_field="NHP::Species",
                field_type="Controlled Ontology", ontology="species",
            ),
        ]
        df_ont = pl.DataFrame({"Species": ["Monkey", "Mouse"]})
        specs = load_ontology_from_sheet(instructions, df_ont)
        assert len(specs) == 1
        assert set(specs[0].allowed_terms) == {"Monkey", "Mouse"}


class TestBuildOntologyModel:

    def test_empty_terms_returns_any_model(self):
        """Empty canonical terms -> skip field (line 88)."""
        specs = [OntologySpec(attribute_name="field1", vocab_name="v1", allowed_terms=["  ", ""])]
        model, corrections = _build_ontology_model(specs)
        assert corrections == {}

    def test_no_specs_returns_any_model(self):
        """No specs -> model accepting anything (lines 100-106)."""
        model, corrections = _build_ontology_model([])
        assert corrections == {}
        # Model should accept any data
        instance = model()
        assert instance is not None

    def test_single_term_literal(self):
        """Single term creates Literal with one value (line 89-90)."""
        specs = [OntologySpec(attribute_name="status", vocab_name="Status", allowed_terms=["Active"])]
        model, corrections = _build_ontology_model(specs)
        assert "status" in corrections
        assert corrections["status"]["ACTIVE"] == "Active"


class TestExtractOntologyViolations:

    def test_value_from_sample_dict(self):
        """Extract value from sample_dicts (line 136)."""
        from pydantic import ValidationError, BaseModel

        try:
            # Simulate a validation error
            raise ValidationError.from_exception_data(
                title="OntologyCheck",
                line_errors=[{
                    "type": "literal_error",
                    "loc": (0, "Species"),
                    "msg": "not valid",
                    "input": "BadValue",
                    "ctx": {"expected": "'Monkey' or 'Mouse'"},
                }],
            )
        except ValidationError as e:
            specs = [OntologySpec(attribute_name="Species", vocab_name="Species", allowed_terms=["Monkey", "Mouse"])]
            violations = _extract_ontology_violations(e, specs, [{"Species": "BadValue"}])
            assert len(violations) == 1
            assert violations[0].value == "BadValue"

    def test_non_string_value(self):
        """Non-string value is converted (line 138)."""
        from pydantic import ValidationError

        try:
            raise ValidationError.from_exception_data(
                title="OntologyCheck",
                line_errors=[{
                    "type": "literal_error",
                    "loc": (0, "Count"),
                    "msg": "not valid",
                    "input": 42,
                    "ctx": {"expected": "'1' or '2'"},
                }],
            )
        except ValidationError as e:
            specs = [OntologySpec(attribute_name="Count", vocab_name="Count", allowed_terms=["1", "2"])]
            violations = _extract_ontology_violations(e, specs, [{"Count": 42}])
            assert len(violations) == 1
            assert violations[0].value == "42"


class TestValidateOntologyBulk:

    def test_valid_data(self):
        """All valid data -> is_valid=True."""
        specs = [OntologySpec(attribute_name="Species", vocab_name="Species", allowed_terms=["Monkey", "Mouse"])]
        result = validate_ontology_bulk(
            [{"Species": "Monkey"}, {"Species": "Mouse"}],
            specs,
        )
        assert result.is_valid

    def test_invalid_data(self):
        """Invalid data -> is_valid=False with violations."""
        specs = [OntologySpec(attribute_name="Species", vocab_name="Species", allowed_terms=["Monkey", "Mouse"])]
        result = validate_ontology_bulk(
            [{"Species": "Monkey"}, {"Species": "Cat"}],
            specs,
        )
        assert not result.is_valid
        assert len(result.violations) > 0

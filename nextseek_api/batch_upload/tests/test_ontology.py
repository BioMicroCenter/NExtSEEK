"""Tests for ontology.py: load from sheet, dynamic Literal model, bulk validation."""
import time

import polars as pl
import pytest

from nextseek_api.batch_upload.models import InstructionRow, OntologySpec
from nextseek_api.batch_upload.ontology import (
    load_ontology_from_sheet,
    validate_ontology_bulk,
)


class TestLoadOntologyFromSheet:
    def test_load_ontology_from_sheet(self):
        instructions = [
            InstructionRow(
                field="Tissue",
                database_field="A.Sample::Tissue",
                field_type="Controlled Ontology",
                ontology="Tissue",
            ),
            InstructionRow(
                field="Name",
                database_field="A.Sample::Name",
                field_type="Text",
                ontology=None,
            ),
        ]
        df_ont = pl.DataFrame({
            "Tissue": ["Blood", "Brain", "Liver"],
        })
        specs = load_ontology_from_sheet(instructions, df_ont)
        assert len(specs) == 1
        assert specs[0].attribute_name == "Tissue"
        assert specs[0].vocab_name == "Tissue"
        assert set(specs[0].allowed_terms) == {"Blood", "Brain", "Liver"}

    def test_no_controlled_attributes(self):
        instructions = [
            InstructionRow(field="Name", database_field="A.Sample::Name", field_type="Text", ontology=None),
        ]
        df_ont = pl.DataFrame({"X": []})
        specs = load_ontology_from_sheet(instructions, df_ont)
        assert specs == []


class TestValidateBulk:
    def test_validate_bulk_pass(self):
        specs = [
            OntologySpec(attribute_name="Tissue", vocab_name="T", allowed_terms=["Blood", "Brain"]),
        ]
        rows = [
            {"Tissue": "Blood"},
            {"Tissue": "Brain"},
            {"Other": "x"},
        ]
        result = validate_ontology_bulk(rows, specs)
        assert result.is_valid is True
        assert len(result.violations) == 0

    def test_validate_bulk_fail_strict(self):
        specs = [
            OntologySpec(attribute_name="Tissue", vocab_name="T", allowed_terms=["Blood", "Brain"]),
        ]
        rows = [
            {"Tissue": "Blood"},
            {"Tissue": "InvalidTerm"},
        ]
        result = validate_ontology_bulk(rows, specs)
        assert result.is_valid is False
        assert len(result.violations) >= 1
        assert result.violations[0].attribute == "Tissue"
        assert result.violations[0].value == "InvalidTerm"
        assert "Blood" in result.violations[0].allowed_terms or "Brain" in result.violations[0].allowed_terms

    def test_case_insensitive_auto_correction(self):
        specs = [
            OntologySpec(attribute_name="Tissue", vocab_name="T", allowed_terms=["RNA-Seq", "WGS"]),
        ]
        rows = [{"Tissue": "rna-seq"}]
        result = validate_ontology_bulk(rows, specs)
        # Pre-correction should canonicalize to RNA-Seq
        assert result.is_valid is True

    def test_empty_value_skipped(self):
        """None and empty string are treated as missing and pass (Optional field)."""
        specs = [
            OntologySpec(attribute_name="Tissue", vocab_name="T", allowed_terms=["Blood"]),
        ]
        rows = [
            {"Tissue": None},
            {},
        ]
        result = validate_ontology_bulk(rows, specs)
        assert result.is_valid is True
        # Empty string is normalized to None in pre-pass, so it passes
        rows_with_empty = [{"Tissue": ""}]
        result2 = validate_ontology_bulk(rows_with_empty, specs)
        assert result2.is_valid is True

    def test_no_controlled_attributes_always_valid(self):
        result = validate_ontology_bulk([{"Anything": "x"}], [])
        assert result.is_valid is True


class TestPerformanceBulk:
    def test_performance_10k_rows(self):
        """Regression: 10k rows with 3 controlled attrs should validate in < 500ms."""
        specs = [
            OntologySpec(attribute_name="A", vocab_name="A", allowed_terms=["x", "y"]),
            OntologySpec(attribute_name="B", vocab_name="B", allowed_terms=["1", "2"]),
            OntologySpec(attribute_name="C", vocab_name="C", allowed_terms=["p", "q"]),
        ]
        rows = [
            {"A": "x", "B": "1", "C": "p"}
            for _ in range(10000)
        ]
        t0 = time.perf_counter()
        result = validate_ontology_bulk(rows, specs)
        elapsed = time.perf_counter() - t0
        assert result.is_valid is True
        assert elapsed < 0.5, f"Bulk validation took {elapsed:.2f}s, expected < 0.5s"

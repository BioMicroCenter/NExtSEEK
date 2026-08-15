"""Pure, database-free unit tests for the DD-03 identifier grammar."""
from __future__ import annotations

import pytest

from nextseek_api.attributes.resolver import (
    Identifier,
    IdentifierKind,
    ResolutionError,
    classify_identifier,
    normalize_identifier,
    normalize_unique,
)


class TestClassifyIdentifier:
    def test_int_is_id(self):
        assert classify_identifier(7) == Identifier("id", 7)

    def test_negative_int_is_id(self):
        assert classify_identifier(-3) == Identifier("id", -3)

    def test_zero_is_id(self):
        assert classify_identifier(0) == Identifier("id", 0)

    def test_ascii_decimal_string_is_id(self):
        assert classify_identifier("7") == Identifier("id", 7)

    def test_ascii_decimal_string_with_leading_zeros_normalizes_to_int(self):
        assert classify_identifier("007") == Identifier("id", 7)

    def test_non_ascii_decimal_digits_are_title(self):
        # Arabic-Indic digit 7 (U+0667) is decimal but not ASCII.
        value = "\u0667"
        assert classify_identifier(value) == Identifier("title", value)

    def test_plain_title_string(self):
        assert classify_identifier("RNA") == Identifier("title", "RNA")

    def test_mixed_alnum_string_is_title(self):
        assert classify_identifier("7abc") == Identifier("title", "7abc")

    def test_negative_numeric_string_is_title_not_id(self):
        # "-3" is not `str.isdecimal()`; DD-03 grammar does not accept a
        # leading sign on a string identifier.
        assert classify_identifier("-3") == Identifier("title", "-3")

    def test_bool_true_is_rejected(self):
        with pytest.raises(ValueError, match="boolean"):
            classify_identifier(True)

    def test_bool_false_is_rejected(self):
        with pytest.raises(ValueError, match="boolean"):
            classify_identifier(False)

    def test_blank_string_is_rejected(self):
        with pytest.raises(ValueError, match="blank"):
            classify_identifier("")

    def test_whitespace_only_string_is_rejected(self):
        with pytest.raises(ValueError, match="blank"):
            classify_identifier("   ")

    def test_float_is_rejected(self):
        with pytest.raises(ValueError, match="int or str"):
            classify_identifier(1.5)

    def test_none_is_rejected(self):
        with pytest.raises(ValueError, match="int or str"):
            classify_identifier(None)

    def test_list_is_rejected(self):
        with pytest.raises(ValueError, match="int or str"):
            classify_identifier([1])

    def test_title_is_never_stripped_or_case_folded(self):
        assert classify_identifier("  RNA  ") == Identifier("title", "  RNA  ")
        assert classify_identifier("RnA") == Identifier("title", "RnA")


class TestNormalizeIdentifier:
    def test_int_normalizes_with_id_kind(self):
        normalized = normalize_identifier(7, submitted_index=2)
        assert normalized.kind is IdentifierKind.ID
        assert normalized.value == 7
        assert normalized.submitted == 7
        assert normalized.submitted_index == 2

    def test_numeric_string_normalizes_to_int_value_but_keeps_submitted_spelling(self):
        normalized = normalize_identifier("007")
        assert normalized.kind is IdentifierKind.ID
        assert normalized.value == 7
        assert normalized.submitted == "007"

    def test_title_normalizes_with_title_kind(self):
        normalized = normalize_identifier("RNA")
        assert normalized.kind is IdentifierKind.TITLE
        assert normalized.value == "RNA"
        assert normalized.submitted == "RNA"

    def test_default_submitted_index_is_zero(self):
        assert normalize_identifier("RNA").submitted_index == 0

    def test_key_property_is_kind_value_tuple(self):
        normalized = normalize_identifier("RNA")
        assert normalized.key == (IdentifierKind.TITLE, "RNA")

    def test_int_and_numeric_string_share_the_same_key(self):
        assert normalize_identifier(7).key == normalize_identifier("7").key
        assert normalize_identifier("07").key == normalize_identifier("7").key

    def test_normalized_identifier_is_frozen(self):
        normalized = normalize_identifier("RNA")
        with pytest.raises(AttributeError):
            normalized.value = "DNA"

    def test_invalid_value_propagates_value_error(self):
        with pytest.raises(ValueError):
            normalize_identifier(True)


class TestNormalizeUnique:
    def test_preserves_order_of_first_appearance(self):
        result = normalize_unique(["RNA", "DNA", "Protein"])
        assert [item.value for item in result] == ["RNA", "DNA", "Protein"]

    def test_deduplicates_int_and_numeric_string_equivalents(self):
        result = normalize_unique([7, "7", "007"])
        assert len(result) == 1
        assert result[0].value == 7
        assert result[0].submitted == 7  # first submitted spelling wins

    def test_deduplicates_exact_duplicate_titles(self):
        result = normalize_unique(["RNA", "RNA"])
        assert len(result) == 1
        assert result[0].submitted_index == 0

    def test_does_not_deduplicate_case_variant_titles(self):
        # Title equality is a real-database collation question (Section 11);
        # the pure grammar layer treats distinct Python strings as distinct.
        result = normalize_unique(["RNA", "rna"])
        assert len(result) == 2

    def test_submitted_index_reflects_original_submission_order(self):
        result = normalize_unique(["RNA", 7, "DNA"])
        assert [item.submitted_index for item in result] == [0, 1, 2]

    def test_empty_input_returns_empty_list(self):
        assert normalize_unique([]) == []

    def test_invalid_value_in_batch_raises(self):
        with pytest.raises(ValueError, match="blank"):
            normalize_unique(["RNA", ""])

    def test_mixed_types_preserve_first_occurrence_key_uniqueness(self):
        result = normalize_unique(["7", 7, 7, "07"])
        assert len(result) == 1
        assert result[0].submitted == "7"


class TestResolutionError:
    def test_default_message_is_code(self):
        error = ResolutionError("attribute_not_found")
        assert str(error) == "attribute_not_found"
        assert error.code == "attribute_not_found"
        assert error.target_index is None
        assert error.attribute_index is None
        assert error.field is None
        assert error.submitted_identifier is None

    def test_carries_full_provenance(self):
        error = ResolutionError(
            "attribute_ambiguous", "attribute is ambiguous",
            target_index=1, attribute_index=2, field="attribute", submitted_identifier="RNA",
        )
        assert str(error) == "attribute is ambiguous"
        assert error.code == "attribute_ambiguous"
        assert error.target_index == 1
        assert error.attribute_index == 2
        assert error.field == "attribute"
        assert error.submitted_identifier == "RNA"

    def test_is_a_value_error(self):
        assert isinstance(ResolutionError("x"), ValueError)

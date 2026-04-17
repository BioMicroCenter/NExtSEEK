"""Unit tests for nextseek_api.batch_upload.identity."""
from __future__ import annotations

import hashlib
import re

from nextseek_api.batch_upload.identity import (
    IDENTITY_FIELDS,
    canonicalize_identity_metadata,
    canonicalize_file_primary_data,
    extract_identity,
    hash_identity,
)


class TestIdentityFieldsExport:
    def test_contains_name(self):
        assert "Name" in IDENTITY_FIELDS

    def test_contains_canonical_file_primary_data(self):
        assert "File_PrimaryData" in IDENTITY_FIELDS

    def test_contains_typo_file_primary_data(self):
        assert "File_PrimartyData" in IDENTITY_FIELDS

    def test_contains_canonical_forward_reverse(self):
        assert "File_PrimaryData_Forward" in IDENTITY_FIELDS
        assert "File_PrimaryData_Reverse" in IDENTITY_FIELDS

    def test_contains_typo_forward_reverse(self):
        assert "File_PrimartyData_Forward" in IDENTITY_FIELDS
        assert "File_PrimartyData_Reverse" in IDENTITY_FIELDS

    def test_is_tuple_immutable(self):
        assert isinstance(IDENTITY_FIELDS, tuple)


class TestExtractIdentityNonFileBasedPrefix:
    def test_name_only(self):
        assert extract_identity({"Name": "sample-A"}, uid="NHP-260413NA-1") == "sample-A"

    def test_name_wins_over_file_primary_data(self):
        meta = {"Name": "sample-A", "File_PrimaryData": "other.csv"}
        assert extract_identity(meta, uid="NHP-260413NA-1") == "sample-A"

    def test_lowercase_name_fallback(self):
        assert extract_identity({"name": "lower"}, uid="NHP-260413NA-1") == "lower"

    def test_falls_through_to_file_primary_data_when_name_missing(self):
        assert extract_identity({"File_PrimaryData": "x.csv"}, uid="NHP-260413NA-1") == "x.csv"

    def test_strips_whitespace(self):
        assert extract_identity({"Name": "  sample  "}, uid="NHP-260413NA-1") == "sample"

    def test_empty_name_not_returned(self):
        assert extract_identity({"Name": "   "}, uid="NHP-260413NA-1") is None

    def test_numeric_name_coerced_to_str(self):
        assert extract_identity({"Name": 42}, uid="NHP-260413NA-1") == "42"


class TestExtractIdentityFileBasedPrefix:
    def test_file_primary_data_wins_over_name_for_d_prefix(self):
        meta = {"Name": "ignored", "File_PrimaryData": "real.csv"}
        assert extract_identity(meta, uid="D.SEQ-260413NA-1") == "real.csv"

    def test_file_primary_data_wins_over_name_for_a_prefix(self):
        meta = {"Name": "ignored", "File_PrimaryData": "real.csv"}
        assert extract_identity(meta, uid="A.GEX-260413NA-1") == "real.csv"

    def test_typo_variant_accepted(self):
        assert extract_identity({"File_PrimartyData": "typo.csv"}, uid="D.SEQ-260413NA-1") == "typo.csv"

    def test_canonical_forward_accepted(self):
        assert extract_identity({"File_PrimaryData_Forward": "fwd.fa"}, uid="D.SEQ-260413NA-1") == "fwd.fa"

    def test_canonical_reverse_accepted(self):
        assert extract_identity({"File_PrimaryData_Reverse": "rev.fa"}, uid="D.SEQ-260413NA-1") == "rev.fa"

    def test_typo_forward_accepted(self):
        assert extract_identity({"File_PrimartyData_Forward": "fwd.fa"}, uid="D.SEQ-260413NA-1") == "fwd.fa"

    def test_typo_reverse_accepted(self):
        assert extract_identity({"File_PrimartyData_Reverse": "rev.fa"}, uid="D.SEQ-260413NA-1") == "rev.fa"

    def test_falls_through_to_name_when_no_file_fields(self):
        assert extract_identity({"Name": "fallback"}, uid="D.SEQ-260413NA-1") == "fallback"

    def test_falls_through_to_lowercase_name_when_no_file_fields(self):
        assert extract_identity({"name": "fallback-lower"}, uid="D.SEQ-260413NA-1") == "fallback-lower"

    def test_canonical_spelling_preferred_over_typo(self):
        meta = {"File_PrimaryData": "good.csv", "File_PrimartyData": "typo.csv"}
        assert extract_identity(meta, uid="D.SEQ-260413NA-1") == "good.csv"


class TestExtractIdentityWithSampleTypeOnly:
    def test_sample_type_nhp_blood(self):
        assert extract_identity({"Name": "x"}, sample_type="NHP_blood") == "x"

    def test_sample_type_d_seq(self):
        meta = {"Name": "ignored", "File_PrimaryData": "real.csv"}
        assert extract_identity(meta, sample_type="D.SEQ_files") == "real.csv"

    def test_sample_type_a_gex_bare(self):
        meta = {"Name": "ignored", "File_PrimaryData": "real.csv"}
        assert extract_identity(meta, sample_type="A.GEX") == "real.csv"


class TestExtractIdentityEdgeCases:
    def test_empty_meta_returns_none(self):
        assert extract_identity({}, uid="NHP-260413NA-1") is None

    def test_none_meta_returns_none(self):
        assert extract_identity(None, uid="NHP-260413NA-1") is None

    def test_non_dict_meta_returns_none(self):
        assert extract_identity("not a dict", uid="NHP-260413NA-1") is None

    def test_no_uid_no_sample_type_defaults_to_name_first(self):
        meta = {"Name": "x", "File_PrimaryData": "y.csv"}
        assert extract_identity(meta) == "x"

    def test_malformed_uid_with_sample_type_falls_back_to_sample_type(self):
        meta = {"Name": "x"}
        assert extract_identity(meta, uid="MALFORMED_NO_DASH", sample_type="NHP_blood") == "x"

    def test_malformed_uid_without_sample_type_defaults_name_first(self):
        meta = {"Name": "x", "File_PrimaryData": "y.csv"}
        assert extract_identity(meta, uid="MALFORMED_NO_DASH") == "x"

    def test_no_identity_fields_returns_none(self):
        assert extract_identity({"SomeOther": "val"}, uid="NHP-260413NA-1") is None


class TestCanonicalizeFilePrimaryData:
    def test_typo_renamed_to_canonical(self):
        out = canonicalize_file_primary_data({"File_PrimartyData": "x.csv"})
        assert out == {"File_PrimaryData": "x.csv"}

    def test_typo_forward_renamed(self):
        out = canonicalize_file_primary_data({"File_PrimartyData_Forward": "f.fa"})
        assert out == {"File_PrimaryData_Forward": "f.fa"}

    def test_typo_reverse_renamed(self):
        out = canonicalize_file_primary_data({"File_PrimartyData_Reverse": "r.fa"})
        assert out == {"File_PrimaryData_Reverse": "r.fa"}

    def test_canonical_spelling_already_present_passes_through(self):
        out = canonicalize_file_primary_data({"File_PrimaryData": "x.csv"})
        assert out == {"File_PrimaryData": "x.csv"}

    def test_both_spellings_canonical_wins_typo_dropped(self):
        out = canonicalize_file_primary_data(
            {"File_PrimaryData": "canon.csv", "File_PrimartyData": "dropped.csv"}
        )
        assert out == {"File_PrimaryData": "canon.csv"}

    def test_input_not_mutated(self):
        original = {"File_PrimartyData": "x.csv"}
        _ = canonicalize_file_primary_data(original)
        assert original == {"File_PrimartyData": "x.csv"}

    def test_other_keys_preserved(self):
        out = canonicalize_file_primary_data(
            {"File_PrimartyData": "x.csv", "Name": "y", "Other": "z"}
        )
        assert out == {"File_PrimaryData": "x.csv", "Name": "y", "Other": "z"}

    def test_non_dict_input_returned_asis(self):
        assert canonicalize_file_primary_data("not a dict") == "not a dict"
        assert canonicalize_file_primary_data(None) is None

    def test_empty_dict(self):
        assert canonicalize_file_primary_data({}) == {}


class TestCanonicalizeIdentityMetadata:
    def test_non_dict_input_returned_asis(self):
        assert canonicalize_identity_metadata("not a dict") == "not a dict"


class TestHashIdentity:
    def test_hash_identity_none(self):
        assert hash_identity(None) is None

    def test_hash_identity_empty(self):
        assert hash_identity("") is None

    def test_hash_identity_whitespace_only(self):
        assert hash_identity("   \t\n ") is None

    def test_hash_identity_non_string_returns_none(self):
        assert hash_identity(123) is None
        assert hash_identity(["sample-a"]) is None
        assert hash_identity(b"sample-a") is None
        assert hash_identity({"Name": "sample-a"}) is None

    def test_hash_identity_strip_lower(self):
        assert hash_identity("  SAMPLE-A  ") == hashlib.sha256(b"sample-a").hexdigest()

    def test_hash_identity_ascii_deterministic(self):
        assert hash_identity("sample-a") == "a52a165a297d54aa3a93149f7ba66f00b6a200b599da12ca9b8cfc8a8954bdbf"
        assert hash_identity("sample-b") == "9f7b12aebcf0a3b504fc2912261643af1e3238760b98c1c6018ac45b791284ab"
        assert hash_identity("123") == "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3"

    def test_hash_identity_output_shape(self):
        value = hash_identity("sample-a")
        assert value is not None
        assert len(value) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", value)

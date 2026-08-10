"""Tests for nextseek_api.batch_upload.helpers."""
import pytest

from nextseek_api.batch_upload.helpers import UID_RE, collect_parent_tokens, split_parent_field


class TestSplitParentField:
    def test_single_uid(self):
        assert split_parent_field("NHP-260225MIT-1") == ["NHP-260225MIT-1"]

    def test_multiple_semicolon_separated(self):
        result = split_parent_field("NHP-260225MIT-1;NHP-260225MIT-2")
        assert result == ["NHP-260225MIT-1", "NHP-260225MIT-2"]

    def test_spaces_preserved_in_name(self):
        """Names with spaces must NOT be split."""
        assert split_parent_field("UtEC - 2015010902") == ["UtEC - 2015010902"]

    def test_spaces_and_numbers_preserved(self):
        assert split_parent_field("272 ESC 260C passage 5") == ["272 ESC 260C passage 5"]

    def test_semicolon_with_surrounding_spaces(self):
        result = split_parent_field("Parent A ; Parent B")
        assert result == ["Parent A", "Parent B"]

    def test_empty_string(self):
        assert split_parent_field("") == []

    def test_only_semicolons(self):
        assert split_parent_field(";;;") == []

    def test_leading_trailing_whitespace_stripped(self):
        result = split_parent_field("  NHP-260225MIT-1  ")
        assert result == ["NHP-260225MIT-1"]

    def test_commas_preserved(self):
        """Commas in names must NOT cause splitting."""
        assert split_parent_field("Doe, Jane sample") == ["Doe, Jane sample"]

    def test_mixed_uid_and_name(self):
        result = split_parent_field("NHP-260225MIT-1;UtEC - 2015010902")
        assert result == ["NHP-260225MIT-1", "UtEC - 2015010902"]


class TestUidRe:
    def test_standard_uid(self):
        assert UID_RE.match("NHP-260225MIT-1")

    def test_pub_suffix(self):
        assert UID_RE.match("NHP-260225MIT-1-PUB")

    def test_pub_with_number(self):
        assert UID_RE.match("NHP-260225MIT-1-PUB2")

    def test_dotted_prefix(self):
        assert UID_RE.match("D.IMG-260225MIT-3")

    def test_invalid_uid(self):
        assert not UID_RE.match("invalid-uid")

    def test_name_not_uid(self):
        assert not UID_RE.match("UtEC - 2015010902")

    def test_two_letter_type_ab(self):
        assert UID_RE.match("AB-230522GRI-1")

    def test_two_letter_type_ab_pub(self):
        assert UID_RE.match("AB-230522GRI-1-PUB")

    def test_m_dot_prefix_lmm(self):
        assert UID_RE.match("M.LMM-231208ALT-1")

    def test_m_dot_prefix_cnn(self):
        assert UID_RE.match("M.CNN-231208ALT-1")

    def test_a_gex_prefix(self):
        assert UID_RE.match("A.GEX-260101MIT-1")

    def test_free_text_not_uid(self):
        assert not UID_RE.match("See Protocol")

    def test_trailing_newline_rejected(self):
        assert not UID_RE.match("NHP-260225MIT-1\n")


class TestCollectParentTokens:
    """Tests for collect_parent_tokens: extracts parent tokens from all parent-containing keys."""

    def test_single_parent_key(self):
        """Standard 'Parent' key — same as legacy behavior."""
        meta = {"Parent": "NHP-260225MIT-1"}
        assert collect_parent_tokens(meta) == ["NHP-260225MIT-1"]

    def test_lowercase_parent_key(self):
        """Lowercase 'parent' key."""
        meta = {"parent": "NHP-260225MIT-1"}
        assert collect_parent_tokens(meta) == ["NHP-260225MIT-1"]

    def test_treatment_parent_variant(self):
        """Treatment1Parent variant key should be captured."""
        meta = {"Treatment1Parent": "NHP-260225MIT-1"}
        assert collect_parent_tokens(meta) == ["NHP-260225MIT-1"]

    def test_antibody_parent_variant(self):
        """AntibodyParent variant key should be captured."""
        meta = {"AntibodyParent": "AB-230327BOO-3"}
        assert collect_parent_tokens(meta) == ["AB-230327BOO-3"]

    def test_parent_m_and_parent_f_variants(self):
        """ParentM and ParentF should both be captured."""
        meta = {"ParentM": "NHP-260225MIT-1", "ParentF": "NHP-260225MIT-2"}
        result = collect_parent_tokens(meta)
        assert set(result) == {"NHP-260225MIT-1", "NHP-260225MIT-2"}
        assert len(result) == 2

    def test_multiple_variant_keys_merged(self):
        """Tokens from Parent + Treatment1Parent + AntibodyParent are merged."""
        meta = {
            "Parent": "NHP-260225MIT-1",
            "Treatment1Parent": "NHP-260225MIT-2",
            "AntibodyParent": "AB-230327BOO-3",
        }
        result = collect_parent_tokens(meta)
        assert set(result) == {"NHP-260225MIT-1", "NHP-260225MIT-2", "AB-230327BOO-3"}
        assert len(result) == 3

    def test_semicolon_values_split(self):
        """Semicolon-separated values in a variant key should be split."""
        meta = {"Treatment1Parent": "NHP-260225MIT-1; NHP-260225MIT-2"}
        result = collect_parent_tokens(meta)
        assert result == ["NHP-260225MIT-1", "NHP-260225MIT-2"]

    def test_deduplication_across_keys(self):
        """Same token in Parent and variant key should appear once."""
        meta = {"Parent": "NHP-260225MIT-1", "Treatment1Parent": "NHP-260225MIT-1"}
        result = collect_parent_tokens(meta)
        assert result == ["NHP-260225MIT-1"]

    def test_deduplication_preserves_order(self):
        """First-seen order preserved during deduplication."""
        meta = {
            "Parent": "BBB-260225MIT-2;AAA-260225MIT-1",
            "Treatment1Parent": "AAA-260225MIT-1;CCC-260225MIT-3",
        }
        result = collect_parent_tokens(meta)
        assert result == ["BBB-260225MIT-2", "AAA-260225MIT-1", "CCC-260225MIT-3"]

    def test_case_insensitive_key_matching(self):
        """PARENT, Parent, parent, pArEnT should all match."""
        meta = {"PARENT": "NHP-260225MIT-1"}
        assert collect_parent_tokens(meta) == ["NHP-260225MIT-1"]

    def test_compensation_fcs_parent_variant(self):
        """CompensationFCSParent variant key."""
        meta = {"CompensationFCSParent": "NHP-260225MIT-1"}
        assert collect_parent_tokens(meta) == ["NHP-260225MIT-1"]

    def test_bacteria_parent_variant(self):
        """BacteriaParent variant key."""
        meta = {"BacteriaParent": "NHP-260225MIT-1"}
        assert collect_parent_tokens(meta) == ["NHP-260225MIT-1"]

    def test_antibody_panel_parent_variant(self):
        """AntibodyPanelParent variant key."""
        meta = {"AntibodyPanelParent": "AB-230327BOO-3"}
        assert collect_parent_tokens(meta) == ["AB-230327BOO-3"]

    def test_non_string_value_skipped(self):
        """Non-string values (int, bool, None) are silently skipped."""
        meta = {"Parent": 12345, "Treatment1Parent": "NHP-260225MIT-1"}
        assert collect_parent_tokens(meta) == ["NHP-260225MIT-1"]

    def test_none_value_skipped(self):
        """None values are silently skipped."""
        meta = {"Parent": None, "Treatment1Parent": "NHP-260225MIT-1"}
        assert collect_parent_tokens(meta) == ["NHP-260225MIT-1"]

    def test_empty_string_value_skipped(self):
        """Empty string values contribute no tokens."""
        meta = {"Parent": "", "Treatment1Parent": "NHP-260225MIT-1"}
        assert collect_parent_tokens(meta) == ["NHP-260225MIT-1"]

    def test_empty_dict(self):
        """Empty metadata returns empty list."""
        assert collect_parent_tokens({}) == []

    def test_no_parent_keys(self):
        """Dict with no parent-containing keys returns empty list."""
        meta = {"Name": "test", "Protocol": "http://example.com"}
        assert collect_parent_tokens(meta) == []

    def test_non_parent_keys_ignored(self):
        """Keys that don't contain 'parent' are ignored."""
        meta = {"Name": "test", "Parent": "NHP-260225MIT-1", "Protocol": "http://example.com"}
        assert collect_parent_tokens(meta) == ["NHP-260225MIT-1"]

    def test_names_with_spaces_preserved(self):
        """Name tokens with spaces are not fragmented."""
        meta = {"Parent": "UtEC - 2015010902;272 ESC 260C passage 5"}
        result = collect_parent_tokens(meta)
        assert result == ["UtEC - 2015010902", "272 ESC 260C passage 5"]

    def test_mixed_uids_and_names(self):
        """UIDs and name tokens coexist."""
        meta = {"Parent": "NHP-260225MIT-1;FutureSample", "Treatment1Parent": "AnotherName"}
        result = collect_parent_tokens(meta)
        assert result == ["NHP-260225MIT-1", "FutureSample", "AnotherName"]

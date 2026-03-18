"""Tests for nextseek_api.batch_upload.helpers."""
import pytest

from nextseek_api.batch_upload.helpers import UID_RE, split_parent_field


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

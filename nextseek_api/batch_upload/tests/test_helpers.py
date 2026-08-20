"""Tests for nextseek_api.batch_upload.helpers."""
from unittest.mock import MagicMock

import pytest
from django.test import override_settings

from nextseek_api.batch_upload.helpers import (
    UID_RE,
    collect_parent_tokens,
    lookup_sop_ids_by_title,
    parse_protocol_value,
    split_parent_field,
)


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


# ── Protocol -> SOP resolution ────────────────────────────────────────────

_LOCAL = dict(
    SEEK_PUBLIC_URL="http://localhost:3000",
    SEEK_URL="http://seek:3000",
    ALLOWED_HOSTS=["127.0.0.1", "nextseek.example.edu"],
)


class TestParseProtocolValue:
    """The three production Protocol shapes, and the host anchoring.

    Ground truth: on the live database this rule reproduced the stored
    protocol_id on 200,000 of 200,000 sampled DERIVED_FROM edges.
    """

    # ── format 1: /sops/<id> on this instance ──────────────────────────
    def test_site_relative_sops_url_yields_the_id(self):
        assert parse_protocol_value("/sops/5") == (5, None, None)

    def test_absolute_local_seek_url_yields_the_id(self):
        with override_settings(**_LOCAL):
            assert parse_protocol_value("http://localhost:3000/sops/5") == (5, None, None)

    def test_a_local_host_on_another_port_still_yields_the_id(self):
        """Same instance, different published port — the id is still ours."""
        with override_settings(**_LOCAL):
            assert parse_protocol_value("https://localhost:8443/sops/5") == (5, None, None)

    def test_an_allowed_host_yields_the_id(self):
        with override_settings(**_LOCAL):
            assert parse_protocol_value(
                "https://nextseek.example.edu/sops/12"
            ) == (12, None, None)

    def test_loopback_is_local_even_with_nothing_configured(self):
        """dmac.settings leaves SEEK_PUBLIC_URL "" and ALLOWED_HOSTS [""] on a
        host with no env, and a loopback URL can only ever be this machine."""
        with override_settings(SEEK_PUBLIC_URL="", SEEK_URL="", ALLOWED_HOSTS=[""]):
            assert parse_protocol_value("http://127.0.0.1:8000/sops/5") == (5, None, None)
            assert parse_protocol_value("http://localhost/sops/5") == (5, None, None)

    # ── format 1, anchored: only a real PATH is scanned ────────────────
    def test_a_query_string_carrying_a_foreign_sops_url_yields_no_id(self):
        """urlsplit puts this after '?', so scanning the raw value read
        someone else's 795 as ours. No scheme and no netloc is not a licence
        to skip parsing."""
        with override_settings(**_LOCAL):
            ref = parse_protocol_value(
                "/redirect?url=https://fairdomhub.org/sops/795"
            )
        assert ref.sop_id is None

    def test_a_local_url_with_the_id_only_in_the_query_yields_no_id(self):
        with override_settings(**_LOCAL):
            assert parse_protocol_value(
                "http://localhost:3000/redirect?url=/sops/795"
            ).sop_id is None

    def test_free_text_containing_a_sops_path_yields_no_id(self):
        """A site-relative URL starts with '/'. Free text is a title."""
        ref = parse_protocol_value("Protocol/sops/12 notes")
        assert ref.sop_id is None
        assert ref.title == "Protocol/sops/12 notes"

    # ── format 1, anchored: a foreign host must NOT yield an id ────────
    def test_fairdomhub_url_does_not_yield_a_foreign_integer(self):
        """795 is FAIRDOMHub's id. Stamping local sops.id 795 is a mis-record."""
        with override_settings(**_LOCAL):
            ref = parse_protocol_value("https://fairdomhub.org/sops/795")
        assert ref.sop_id is None

    def test_foreign_host_falls_through_to_title_resolution_only(self):
        with override_settings(**_LOCAL):
            assert parse_protocol_value("https://other.seek.org/sops/1")[0] is None

    @pytest.mark.parametrize(
        "value",
        [
            # A local hostname is not enough — a non-HTTP service on this host
            # is a different service, and its ids are not our sops.id.
            "ftp://localhost/sops/5",
            # No host at all. Also the shape __formatSopUIDLink's 'http' prefix
            # check exists to block.
            "javascript:/sops/5",
        ],
    )
    def test_non_http_scheme_never_yields_an_id(self, value):
        with override_settings(**_LOCAL):
            assert parse_protocol_value(value)[0] is None

    # ── format 2: uid=<title> URL ──────────────────────────────────────
    def test_uid_url_with_trailing_slash_yields_the_title(self):
        assert parse_protocol_value(
            "http://localhost:8000/seek/sop/uid=P.FOR-200623-V1_x.docx/"
        ) == (None, "P.FOR-200623-V1_x.docx", None)

    def test_uid_url_without_trailing_slash_yields_the_title(self):
        assert parse_protocol_value(
            "http://localhost:8000/seek/sop/uid=P.FOR-200623-V1_x.docx"
        ) == (None, "P.FOR-200623-V1_x.docx", None)

    def test_uid_url_with_an_empty_uid_yields_nothing(self):
        assert parse_protocol_value("http://localhost:8000/seek/sop/uid=/") == (None, None, None)

    # ── an http URL that is not one of ours: a LINK, not an error ──────
    def test_external_http_url_is_classified_as_a_link_not_a_title(self):
        """dbtable_sample.__formatSopUIDLink treats an http-prefixed Protocol
        as a legitimate external SOP link. Returning it as a "title" made any
        upload carrying one of the 1,855 fairdomhub-shaped values query sops
        for something that cannot exist and then report a warning."""
        with override_settings(**_LOCAL):
            ref = parse_protocol_value("https://fairdomhub.org/sops/795")
        assert ref.sop_id is None
        assert ref.title is None
        assert ref.external_url == "https://fairdomhub.org/sops/795"

    def test_an_http_url_on_our_own_host_that_is_not_a_sop_link_is_a_link(self):
        with override_settings(**_LOCAL):
            ref = parse_protocol_value("http://localhost:3000/documents/5")
        assert ref.external_url == "http://localhost:3000/documents/5"
        assert ref.title is None

    def test_the_scheme_check_is_case_insensitive(self):
        with override_settings(**_LOCAL):
            assert parse_protocol_value(
                "HTTPS://fairdomhub.org/sops/795"
            ).external_url == "HTTPS://fairdomhub.org/sops/795"

    def test_a_uid_url_is_a_title_not_an_external_link(self):
        """Ordering: the uid= form wins over the http prefix, or every
        /seek/sop/uid=<title>/ value would stop resolving."""
        ref = parse_protocol_value(
            "http://127.0.0.1:8000/seek/sop/uid=P.FOR-200623-V1_x.docx/"
        )
        assert ref.title == "P.FOR-200623-V1_x.docx"
        assert ref.external_url is None

    def test_a_title_merely_starting_with_http_is_not_a_link(self):
        """The house rule tests only the first 4 characters; requiring the
        scheme separator is strictly safer and costs nothing."""
        ref = parse_protocol_value("httpd hardening SOP v2")
        assert ref.title == "httpd hardening SOP v2"
        assert ref.external_url is None

    # ── format 3: bare title (97,767 of 163,393 production samples) ────
    def test_bare_title_is_returned_for_title_lookup(self):
        assert parse_protocol_value("P.FOR-200623-V1_x.docx") == (
            None,
            "P.FOR-200623-V1_x.docx",
            None,
        )

    def test_title_is_stripped(self):
        assert parse_protocol_value("  P.FOR-200623-V1_x.docx  ") == (
            None,
            "P.FOR-200623-V1_x.docx",
            None,
        )

    def test_title_containing_a_colon_is_not_mistaken_for_a_url(self):
        assert parse_protocol_value("SOP: extraction v2") == (None, "SOP: extraction v2", None)

    # ── nothing at all ─────────────────────────────────────────────────
    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_empty_values_yield_neither(self, value):
        assert parse_protocol_value(value) == (None, None, None)


def _conn_returning(rows):
    conn = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = rows
    conn.execute.return_value = result
    return conn


class TestLookupSopIdsByTitle:
    """sops.title is unique in production (553 rows, 553 distinct titles), but
    the house rule in seek/dbtable_sample.py::__formatSopUIDLink keeps a
    ``len(records) == 1`` guard, so this keeps it too."""

    def test_resolves_a_unique_title(self):
        conn = _conn_returning([(7, "P.FOR-200623-V1_x.docx")])
        resolved, ambiguous = lookup_sop_ids_by_title(
            ["P.FOR-200623-V1_x.docx"], conn
        )
        assert resolved == {"P.FOR-200623-V1_x.docx": 7}
        assert ambiguous == {}

    def test_an_ambiguous_title_is_not_resolved(self):
        conn = _conn_returning([(7, "Dup SOP"), (9, "Dup SOP")])
        resolved, ambiguous = lookup_sop_ids_by_title(["Dup SOP"], conn)
        assert resolved == {}
        assert ambiguous == {"Dup SOP": 2}

    def test_an_unknown_title_resolves_to_nothing(self):
        conn = _conn_returning([])
        resolved, ambiguous = lookup_sop_ids_by_title(["No Such SOP"], conn)
        assert resolved == {}
        assert ambiguous == {}

    def test_collation_case_difference_still_resolves(self):
        """MySQL's default collation matches case-insensitively, so the row
        that comes back can differ in case from the value we asked for."""
        conn = _conn_returning([(7, "P.FOR-200623-V1_X.DOCX")])
        resolved, _ = lookup_sop_ids_by_title(["p.for-200623-v1_x.docx"], conn)
        assert resolved == {"p.for-200623-v1_x.docx": 7}

    def test_empty_input_issues_no_sql(self):
        conn = MagicMock()
        assert lookup_sop_ids_by_title([], conn) == ({}, {})
        conn.execute.assert_not_called()

    def test_blank_titles_are_never_looked_up(self):
        conn = MagicMock()
        assert lookup_sop_ids_by_title(["", "   ", None], conn) == ({}, {})
        conn.execute.assert_not_called()

    def test_titles_are_bound_as_parameters_not_interpolated(self):
        conn = _conn_returning([])
        lookup_sop_ids_by_title(["O'Brien SOP"], conn)
        _sql, params = conn.execute.call_args[0]
        assert "O'Brien SOP" in params.values()
        assert "O'Brien" not in str(_sql)

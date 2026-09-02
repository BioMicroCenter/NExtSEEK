"""The context catalog's parsers and its sample type loader."""

from unittest.mock import patch

import pytest

from nextseek_api.services.context_catalog import (
    SampleTypeContextEntry,
    load_sample_type,
    load_sample_types,
    parse_alternation,
    parse_list,
    slugify_name,
)

KNOWN = {"MUS", "PAV", "TIS", "CEX", "CEL", "AB", "ABP", "DNA", "RNA", "D.SEQ"}


class TestParseAlternation:
    def test_a_single_group_of_alternatives_stays_one_group(self):
        assert parse_alternation("MUS or PAV", KNOWN) == [["MUS", "PAV"]]

    def test_a_comma_starts_a_new_group(self):
        assert parse_alternation("TIS or CEX or CEL, AB or ABP", KNOWN) == [
            ["TIS", "CEX", "CEL"], ["AB", "ABP"]
        ]

    def test_a_lone_code_is_a_group_of_one(self):
        assert parse_alternation("TIS", KNOWN) == [["TIS"]]

    def test_a_period_followed_by_space_separates_too(self):
        """MUS.parent_sampletypes is literally "AB, BAC. CHM" in the live table."""
        assert parse_alternation("AB, TIS. CEL", KNOWN) == [["AB"], ["TIS"], ["CEL"]]

    def test_a_period_inside_a_code_does_not_split_it(self):
        assert parse_alternation("D.SEQ", KNOWN) == [["D.SEQ"]]

    def test_and_means_both_so_it_separates_groups(self):
        """Measured: "TIS and AB" appears in 9 assay_context rows and 5 sample
        type rows. `and` means both are required, which is what a comma already
        means here, so it is a group separator and not an alternative."""
        assert parse_alternation("TIS and AB", KNOWN) == [["TIS"], ["AB"]]

    def test_and_mixed_with_or_in_one_value(self):
        """D.ELSA.parent_sampletypes is literally "AB or ABP and CEL or TIS"."""
        assert parse_alternation("AB or ABP and CEL or TIS", KNOWN) == [
            ["AB", "ABP"], ["CEL", "TIS"]
        ]

    def test_a_code_ending_in_and_is_not_split(self):
        """The separator needs whitespace on both sides, so a code is safe."""
        assert parse_alternation("RNA", KNOWN) == [["RNA"]]

    def test_unknown_codes_are_dropped_and_an_emptied_group_disappears(self):
        assert parse_alternation("TIS, D.AD**", KNOWN) == [["TIS"]]

    def test_or_is_matched_case_insensitively(self):
        assert parse_alternation("MUS OR PAV", KNOWN) == [["MUS", "PAV"]]

    def test_blank_input_is_no_groups(self):
        assert parse_alternation(None, KNOWN) == []
        assert parse_alternation("", KNOWN) == []


class TestParseList:
    def test_a_comma_list_becomes_a_list(self):
        assert parse_list("UID, Scientist, Parent") == ["UID", "Scientist", "Parent"]

    def test_whitespace_is_stripped_and_blanks_dropped(self):
        assert parse_list("UID,  , Parent ") == ["UID", "Parent"]

    def test_blank_input_is_an_empty_list(self):
        assert parse_list(None) == []


class TestSlugifyName:
    def test_spaces_become_hyphens_and_case_is_folded(self):
        assert slugify_name("Flow Cytometry Analysis") == "flow-cytometry-analysis"

    def test_punctuation_is_dropped_not_transliterated(self):
        assert slugify_name("Polymerase Chain Reaction (PCR)") == "polymerase-chain-reaction-pcr"

    def test_a_slash_separates_rather_than_joining(self):
        assert slugify_name("PET/CT Scan") == "pet-ct-scan"

    def test_no_leading_or_trailing_hyphen_survives(self):
        assert slugify_name("  Imaging  ") == "imaging"


class TestLoadSampleTypes:
    ROW = {
        "sample_type": "D.FLOW", "sampletype_id": 13, "name": "Flow Cytometry Data",
        "description": "A flow cytometry file stores fluorescence...",
        "clade": "Raw", "Tags": "flow cytometry data, FACS data",
        "required_metadata": "UID, File_PrimaryData, Parent",
        "standard_metadata": "Instrument, Protocol",
        "possible_metadata_fields": "Stain, QC_notes",
        "parent_sampletypes": "TIS, CEX, ABP",
        "child_sampletypes": "A.FLOW",
        "associated_assay_parents": "Flow Cytometry",
        "associated_assay_children": "Flow Cytometry Analysis",
    }

    @patch("nextseek_api.services.context_catalog._sample_type_rows")
    def test_a_row_becomes_a_fully_parsed_entry(self, rows):
        rows.return_value = [self.ROW]
        entry, = load_sample_types()
        assert entry.code == "D.FLOW"
        assert entry.name == "Flow Cytometry Data"
        assert entry.clade == "Raw"
        assert entry.required_metadata == ["UID", "File_PrimaryData", "Parent"]
        assert entry.tags == ["flow cytometry data", "FACS data"]
        assert entry.assay_parents == ["Flow Cytometry"]

    @patch("nextseek_api.services.context_catalog._sample_type_rows")
    def test_relationship_codes_are_validated_against_the_catalog(self, rows):
        """TIS, CEX and ABP are not rows here, so none of them is a known code."""
        rows.return_value = [self.ROW]
        entry, = load_sample_types()
        assert entry.parent_types == []

    @patch("nextseek_api.services.context_catalog._sample_type_rows")
    def test_a_missing_table_yields_no_entries_and_does_not_raise(self, rows):
        rows.side_effect = Exception("Table 'dmac.sample_types_context' doesn't exist")
        assert load_sample_types() == []

    @patch("nextseek_api.services.context_catalog._sample_type_rows")
    def test_retired_types_are_omitted(self, rows):
        retired = dict(self.ROW, sample_type="A.SEQ",
                       description="Depcreciated. Do not use.")
        rows.return_value = [self.ROW, retired]
        assert [e.code for e in load_sample_types()] == ["D.FLOW"]

    @patch("nextseek_api.services.context_catalog._sample_type_rows")
    def test_a_row_with_no_code_is_skipped_rather_than_crashing(self, rows):
        rows.return_value = [dict(self.ROW, sample_type=None), self.ROW]
        assert [e.code for e in load_sample_types()] == ["D.FLOW"]

    @patch("nextseek_api.services.context_catalog._sample_type_rows")
    def test_entries_are_ordered_by_clade_then_code(self, rows):
        rows.return_value = [
            dict(self.ROW, sample_type="TIS", clade="Processed"),
            dict(self.ROW, sample_type="NHP", clade="Source"),
            dict(self.ROW, sample_type="D.SEQ", clade="Raw"),
        ]
        assert [e.code for e in load_sample_types()] == ["NHP", "D.SEQ", "TIS"]

    @patch("nextseek_api.services.context_catalog._sample_type_rows")
    def test_load_sample_type_finds_one_and_returns_none_for_a_stranger(self, rows):
        rows.return_value = [self.ROW]
        assert load_sample_type("D.FLOW").name == "Flow Cytometry Data"
        assert load_sample_type("NOPE") is None

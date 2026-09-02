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


def test_every_selected_column_is_a_real_model_field():
    """The one thing every other loader test mocks away.

    _sample_type_rows is patched in all of them, so a column name the ORM cannot
    resolve raises FieldError only against a real database -- where the loader's
    soft-dependency except swallows it and the page renders empty rather than
    failing. That is exactly what shipped on 2026-09-02: the field is `tags` and
    its db_column is capital-T Tags, so selecting the db_column raised and the
    sample type catalog was empty on every request while every unit test here
    stayed green.

    Needs no database: Django resolves field names from the model meta.
    """
    from seek.models import Sample_types_context

    from nextseek_api.services.context_catalog import _SAMPLE_TYPE_COLUMNS

    field_names = {f.name for f in Sample_types_context._meta.get_fields()}
    unresolvable = set(_SAMPLE_TYPE_COLUMNS) - field_names
    assert not unresolvable, (
        f"{sorted(unresolvable)} are not fields on Sample_types_context. "
        f"Available: {sorted(field_names)}"
    )


class TestLoadSampleTypes:
    ROW = {
        "sample_type": "D.FLOW", "sampletype_id": 13, "name": "Flow Cytometry Data",
        "description": "A flow cytometry file stores fluorescence...",
        "clade": "Raw", "tags": "flow cytometry data, FACS data",
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


from nextseek_api.services.context_catalog import (  # noqa: E402
    AssayEntry, load_assay, load_assays, load_project_context,
)

ASSAY_ROW = {
    "id": 30, "assay_name": "Flow Cytometry",
    "description": "Flow cytometry is a laser-based technique...",
    "tags": "flow cytometry, FACS", "alternative_assay_names": "",
    "required_parent_sample_types": "TIS or CEX or CEL, AB or ABP",
    "optional_parent_sample_types": "D.FCS",
    "children_sample_types": "D.FLOW",
    "parent_clade_type": "Processed", "child_clade_type": "Raw",
    "assaysheet_link": "(insert link here)",
    "associatedrepository": "Immport (link)",
    "critical_attributes": "TIS::Type, ABP::Parent",
    "internal_assay_id": 30,
}


class TestLoadAssays:
    KNOWN = {"TIS", "CEX", "CEL", "AB", "ABP", "D.FCS", "D.FLOW"}

    @patch("nextseek_api.services.context_catalog._known_sample_type_codes")
    @patch("nextseek_api.services.context_catalog._assay_rows")
    def test_a_row_becomes_an_entry_with_the_alternation_intact(self, rows, known):
        rows.return_value = [ASSAY_ROW]
        known.return_value = self.KNOWN
        entry, = load_assays()
        assert entry.slug == "flow-cytometry"
        assert entry.name == "Flow Cytometry"
        assert len(entry.rows) == 1
        assert entry.rows[0].required_parents == [["TIS", "CEX", "CEL"], ["AB", "ABP"]]
        assert entry.rows[0].children == [["D.FLOW"]]
        assert entry.rows[0].critical_attributes == ["TIS::Type", "ABP::Parent"]

    @patch("nextseek_api.services.context_catalog._known_sample_type_codes")
    @patch("nextseek_api.services.context_catalog._assay_rows")
    def test_two_rows_with_one_name_become_one_entry_with_two_rows(self, rows, known):
        """The 22 duplicate pairs. Nothing is merged; both rows are carried."""
        curated = dict(ASSAY_ROW, id=11, assay_name="Cell Culture",
                       required_parent_sample_types="CEL",
                       children_sample_types="CEL", internal_assay_id=None)
        registry = dict(ASSAY_ROW, id=85, assay_name="Cell Culture",
                        required_parent_sample_types=None,
                        children_sample_types=None, internal_assay_id=85)
        rows.return_value = [curated, registry]
        known.return_value = {"CEL"}
        entry, = load_assays()
        assert entry.slug == "cell-culture"
        assert [r.row_id for r in entry.rows] == [11, 85]

    @patch("nextseek_api.services.context_catalog._known_sample_type_codes")
    @patch("nextseek_api.services.context_catalog._assay_rows")
    def test_names_differing_only_by_a_hyphen_land_on_one_entry(self, rows, known):
        """Measured on the live table: 217 rows, 195 names, 193 slugs.

        'Long Read Sequencing' and 'Long-Read Sequencing' are one assay spelled
        two ways, and the slug is what makes a cross-link find both.
        """
        rows.return_value = [
            dict(ASSAY_ROW, id=1, assay_name="Long Read Sequencing"),
            dict(ASSAY_ROW, id=2, assay_name="Long-Read Sequencing"),
        ]
        known.return_value = self.KNOWN
        entry, = load_assays()
        assert entry.slug == "long-read-sequencing"
        assert len(entry.rows) == 2

    @patch("nextseek_api.services.context_catalog._known_sample_type_codes")
    @patch("nextseek_api.services.context_catalog._assay_rows")
    def test_the_display_name_is_the_first_row_in_query_order(self, rows, known):
        """_assay_rows orders by id, so "first" is deterministic in production.

        The mock replaces that ordering, so this pins the rule the loader
        applies to whatever order it is handed: first row seen wins the name.
        """
        rows.return_value = [
            dict(ASSAY_ROW, id=2, assay_name="Long-Read Sequencing"),
            dict(ASSAY_ROW, id=1, assay_name="Long Read Sequencing"),
        ]
        known.return_value = self.KNOWN
        entry, = load_assays()
        assert entry.name == "Long-Read Sequencing"
        assert [r.row_id for r in entry.rows] == [2, 1]

    @patch("nextseek_api.services.context_catalog._assay_rows")
    def test_a_missing_table_yields_no_entries_and_does_not_raise(self, rows):
        rows.side_effect = Exception("Table 'dmac.assay_context' doesn't exist")
        assert load_assays() == []

    @patch("nextseek_api.services.context_catalog._known_sample_type_codes")
    @patch("nextseek_api.services.context_catalog._assay_rows")
    def test_a_row_with_no_name_is_skipped(self, rows, known):
        rows.return_value = [dict(ASSAY_ROW, assay_name=None), ASSAY_ROW]
        known.return_value = self.KNOWN
        assert [e.slug for e in load_assays()] == ["flow-cytometry"]

    @patch("nextseek_api.services.context_catalog._known_sample_type_codes")
    @patch("nextseek_api.services.context_catalog._assay_rows")
    def test_load_assay_finds_one_and_returns_none_for_a_stranger(self, rows, known):
        rows.return_value = [ASSAY_ROW]
        known.return_value = self.KNOWN
        assert load_assay("flow-cytometry").name == "Flow Cytometry"
        assert load_assay("no-such-assay") is None


class TestLoadProjectContext:
    @patch("nextseek_api.services.context_catalog._project_context_row")
    def test_json_array_columns_are_decoded(self, row):
        row.return_value = {
            "name": "IMPAcTb", "pi": "A Person",
            "alternative_names": '["IMPACT", "IMPAcTb"]',
            "key_data_types": '["flow", "rnaseq"]',
            "research_focus": "TB", "parent_project": None,
            "nih_reporter_link": "https://reporter.nih.gov/x",
            "fairdomhub_published_link": None, "tags": "tb, nhp",
        }
        ctx = load_project_context(2)
        assert ctx["alternative_names"] == ["IMPACT", "IMPAcTb"]
        assert ctx["key_data_types"] == ["flow", "rnaseq"]

    @patch("nextseek_api.services.context_catalog._project_context_row")
    def test_a_pipe_delimited_column_is_accepted_as_a_fallback(self, row):
        row.return_value = {"name": "X", "alternative_names": "A|B",
                            "key_data_types": ""}
        assert load_project_context(2)["alternative_names"] == ["A", "B"]

    @patch("nextseek_api.services.context_catalog._project_context_row")
    def test_no_row_is_none_not_an_empty_dict(self, row):
        row.return_value = None
        assert load_project_context(2) is None

    @patch("nextseek_api.services.context_catalog._project_context_row")
    def test_a_missing_table_is_none_and_does_not_raise(self, row):
        row.side_effect = Exception("Table 'dmac.projects_context' doesn't exist")
        assert load_project_context(2) is None

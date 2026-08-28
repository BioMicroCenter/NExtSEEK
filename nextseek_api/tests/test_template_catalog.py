"""The template catalog: what types exist, their columns, and what they relate to."""

from unittest.mock import MagicMock, patch

import pytest


class TestAttributeSpecs:
    """getAttributeSpecsBySampleTypeIds mirrors the titles method, plus `required`."""

    def _table(self, rows):
        from seek.dbtable_sampleattribute import DBtable_sampleattribute

        table = DBtable_sampleattribute.__new__(DBtable_sampleattribute)
        table.tablemodel = MagicMock()
        qs = table.tablemodel.objects.filter.return_value.order_by.return_value
        qs.values_list.return_value = rows
        return table

    def test_returns_title_required_and_pos_per_sample_type(self):
        table = self._table([(41, "UID", 1, 0), (41, "Sex", 0, 1)])
        assert table.getAttributeSpecsBySampleTypeIds([41]) == {
            41: [
                {"title": "UID", "required": True, "pos": 0},
                {"title": "Sex", "required": False, "pos": 1},
            ]
        }

    def test_required_is_coerced_to_bool_from_tinyint(self):
        """MySQL hands back 1/0, not True/False."""
        table = self._table([(41, "UID", 1, 0)])
        assert table.getAttributeSpecsBySampleTypeIds([41])[41][0]["required"] is True

    def test_blank_and_none_titles_are_dropped(self):
        table = self._table([(41, "UID", 1, 0), (41, "   ", 0, 1), (41, None, 0, 2)])
        assert [s["title"] for s in table.getAttributeSpecsBySampleTypeIds([41])[41]] == ["UID"]

    def test_a_requested_id_with_no_rows_maps_to_an_empty_list(self):
        table = self._table([])
        assert table.getAttributeSpecsBySampleTypeIds([41]) == {41: []}

    def test_non_numeric_and_non_positive_ids_are_ignored(self):
        table = self._table([])
        assert table.getAttributeSpecsBySampleTypeIds(["x", 0, -3]) == {}

    def test_no_ids_short_circuits_without_querying(self):
        table = self._table([])
        assert table.getAttributeSpecsBySampleTypeIds([]) == {}
        table.tablemodel.objects.filter.assert_not_called()


_MOD = "nextseek_api.services.template_catalog"


class TestGrouping:
    def test_no_prefix_is_experimental(self):
        from nextseek_api.services.template_catalog import group_for

        assert group_for("TIS") == ""
        assert group_for("DNA") == ""

    def test_d_prefix_is_data(self):
        from nextseek_api.services.template_catalog import group_for

        assert group_for("D.SEQ") == "D."

    def test_a_prefix_is_analysis(self):
        from nextseek_api.services.template_catalog import group_for

        assert group_for("A.GEX") == "A."

    def test_m_prefix_gets_its_own_group_not_experimental(self):
        """getSampleTypes() files M. under Experimental; this page does not."""
        from nextseek_api.services.template_catalog import group_for

        assert group_for("M.LMM") == "M."

    def test_an_unrecognised_prefix_falls_back_to_experimental(self):
        from nextseek_api.services.template_catalog import group_for

        assert group_for("X.FOO") == ""


class TestLoadCatalog:
    def _types(self, rows):
        m = MagicMock()
        m.objects.all.return_value.values.return_value = rows
        return m

    def test_entries_carry_code_id_and_group(self):
        from nextseek_api.services.template_catalog import load_catalog

        with patch(f"{_MOD}.Sample_types", self._types([{"id": 2, "title": "TIS"}])), \
             patch(f"{_MOD}.load_sample_type_context", return_value={}):
            entries = load_catalog()

        assert len(entries) == 1
        assert entries[0].code == "TIS"
        assert entries[0].sample_type_id == 2
        assert entries[0].group == ""

    def test_name_and_description_come_from_context(self):
        from nextseek_api.services.template_catalog import load_catalog

        context = {"TIS": {"name": "Tissue", "description": "A tissue sample."}}
        with patch(f"{_MOD}.Sample_types", self._types([{"id": 2, "title": "TIS"}])), \
             patch(f"{_MOD}.load_sample_type_context", return_value=context):
            entries = load_catalog()

        assert entries[0].name == "Tissue"
        assert entries[0].description == "A tissue sample."

    def test_a_type_with_no_context_row_still_appears_with_blanks(self):
        """Three of the 104 types have no context row. They must not vanish."""
        from nextseek_api.services.template_catalog import load_catalog

        with patch(f"{_MOD}.Sample_types", self._types([{"id": 9, "title": "ZZZ"}])), \
             patch(f"{_MOD}.load_sample_type_context", return_value={}):
            entries = load_catalog()

        assert entries[0].code == "ZZZ"
        assert entries[0].name == ""
        assert entries[0].description == ""

    def test_a_failing_context_lookup_costs_names_not_the_catalog(self):
        from nextseek_api.services.template_catalog import load_catalog

        with patch(f"{_MOD}.Sample_types", self._types([{"id": 2, "title": "TIS"}])), \
             patch(f"{_MOD}.load_sample_type_context", side_effect=RuntimeError("db down")):
            entries = load_catalog()

        assert [e.code for e in entries] == ["TIS"]
        assert entries[0].name == ""

    def test_entries_sort_by_group_order_then_code(self):
        from nextseek_api.services.template_catalog import load_catalog

        rows = [
            {"id": 1, "title": "A.GEX"},
            {"id": 2, "title": "TIS"},
            {"id": 3, "title": "M.LMM"},
            {"id": 4, "title": "D.SEQ"},
            {"id": 5, "title": "DNA"},
        ]
        with patch(f"{_MOD}.Sample_types", self._types(rows)), \
             patch(f"{_MOD}.load_sample_type_context", return_value={}):
            entries = load_catalog()

        assert [e.code for e in entries] == ["DNA", "TIS", "D.SEQ", "A.GEX", "M.LMM"]

    def test_blank_titles_are_skipped(self):
        from nextseek_api.services.template_catalog import load_catalog

        rows = [{"id": 1, "title": "  "}, {"id": 2, "title": None}, {"id": 3, "title": "TIS"}]
        with patch(f"{_MOD}.Sample_types", self._types(rows)), \
             patch(f"{_MOD}.load_sample_type_context", return_value={}):
            entries = load_catalog()

        assert [e.code for e in entries] == ["TIS"]

    def test_a_code_that_cannot_be_a_sheet_name_is_dropped_not_fatal(self):
        """An illegal character, an over-length code, and a reserved-name
        collision must each cost only that one type -- not raise, and not
        take the legal codes down with them. This is what stands between a
        curator's `sample_types.title` and an HTTP 500 on every download.

        Replaces the old version of this test, which fed four hardcoded
        known-good codes through and asserted a property of those literals --
        it could never fail regardless of what load_catalog actually did.
        """
        from nextseek_api.services.template_catalog import load_catalog

        rows = [
            {"id": 1, "title": "TIS"},          # legal, survives
            {"id": 2, "title": "TIS/FFPE"},      # illegal character
            {"id": 3, "title": "A" * 32},        # over MAX_SHEET_NAME (31)
            {"id": 4, "title": "README"},        # collides with the README sheet
            {"id": 5, "title": "_NEXTSEEK"},     # collides with the manifest sheet
            {"id": 6, "title": "Controlled Vocabularies"},  # collides with the CV sheet
            {"id": 7, "title": "D.SEQ"},         # legal, survives
        ]
        with patch(f"{_MOD}.Sample_types", self._types(rows)), \
             patch(f"{_MOD}.load_sample_type_context", return_value={}):
            entries = load_catalog()

        assert sorted(e.code for e in entries) == ["D.SEQ", "TIS"]


KNOWN = {"DNA", "RNA", "BAC", "CEL", "TIS", "AB", "CHM", "D.TITR", "D.IMG",
         "A.GEX", "A.ALN", "D.SEQ", "PAV", "MUS"}


class TestParseRelated:
    """sample_types_context relationship columns are free text. Real values from
    the seed database, separators and all."""

    def test_splits_on_the_word_or_and_drops_wildcards(self):
        from nextseek_api.services.template_catalog import parse_related

        raw = "DNA or RNA or BAC or D.TITR or D.AD**, or D.IMG or CEL"
        assert parse_related(raw, KNOWN) == ["DNA", "RNA", "BAC", "D.TITR", "D.IMG", "CEL"]

    def test_splits_on_commas_and_a_stray_period(self):
        """MUS.parent_sampletypes is literally 'AB, BAC. CHM'."""
        from nextseek_api.services.template_catalog import parse_related

        assert parse_related("AB, BAC. CHM", KNOWN) == ["AB", "BAC", "CHM"]

    def test_a_clean_comma_list_is_unchanged(self):
        from nextseek_api.services.template_catalog import parse_related

        assert parse_related("A.GEX, A.ALN", KNOWN) == ["A.GEX", "A.ALN"]

    def test_unknown_codes_are_dropped_silently(self):
        from nextseek_api.services.template_catalog import parse_related

        assert parse_related("DNA, NOPE, A.GEX", KNOWN) == ["DNA", "A.GEX"]

    def test_duplicates_collapse_keeping_first_appearance(self):
        from nextseek_api.services.template_catalog import parse_related

        assert parse_related("DNA or RNA or DNA", KNOWN) == ["DNA", "RNA"]

    def test_empty_and_none_yield_an_empty_list(self):
        from nextseek_api.services.template_catalog import parse_related

        assert parse_related("", KNOWN) == []
        assert parse_related(None, KNOWN) == []

    def test_a_code_containing_or_is_not_split_apart(self):
        """'or' is only a separator when it stands alone.

        The input puts CORE between two real 'or' separators, so a tokenizer
        that split on the substring rather than the standalone word would
        return ['C', 'E'] here and fail.
        """
        from nextseek_api.services.template_catalog import parse_related

        known = KNOWN | {"CORE"}
        assert parse_related("DNA or CORE or RNA", known) == ["DNA", "CORE", "RNA"]
        assert parse_related("CORE, DNA", known) == ["CORE", "DNA"]


class TestLoadRelationships:
    def _context(self, rows):
        m = MagicMock()
        m.objects.filter.return_value.values.return_value = rows
        return m

    def test_maps_code_to_parents_and_children(self):
        from nextseek_api.services.template_catalog import load_relationships

        rows = [{"sample_type": "DNA",
                 "parent_sampletypes": "CEL or TIS",
                 "child_sampletypes": "D.SEQ"}]
        with patch(f"{_MOD}.Sample_types_context", self._context(rows)):
            out = load_relationships(["DNA"], KNOWN)

        assert out == {"DNA": {"parents": ["CEL", "TIS"], "children": ["D.SEQ"]}}

    def test_a_code_with_both_sides_empty_is_omitted(self):
        """So the README omits the line rather than printing an empty one."""
        from nextseek_api.services.template_catalog import load_relationships

        rows = [{"sample_type": "ZZZ", "parent_sampletypes": "", "child_sampletypes": None}]
        with patch(f"{_MOD}.Sample_types_context", self._context(rows)):
            assert load_relationships(["ZZZ"], KNOWN) == {}

    def test_a_failing_lookup_yields_no_relationships_not_an_error(self):
        from nextseek_api.services.template_catalog import load_relationships

        broken = MagicMock()
        broken.objects.filter.side_effect = RuntimeError("db down")
        with patch(f"{_MOD}.Sample_types_context", broken):
            assert load_relationships(["DNA"], KNOWN) == {}


class TestSuggest:
    REL = {
        "PAT": {"parents": [], "children": ["PAV"]},
        "TIS": {"parents": ["MUS"], "children": ["DNA", "RNA"]},
        "DNA": {"parents": ["TIS"], "children": ["D.SEQ"]},
        "D.SEQ": {"parents": ["DNA"], "children": ["A.GEX", "A.ALN"]},
    }

    def test_offers_children_of_everything_selected(self):
        from nextseek_api.services.template_catalog import suggest

        assert set(suggest(["D.SEQ"], self.REL)) == {"A.GEX", "A.ALN"}

    def test_never_offers_parents(self):
        """Selecting D.SEQ must not push DNA back at a user who already has it."""
        from nextseek_api.services.template_catalog import suggest

        assert "DNA" not in suggest(["D.SEQ"], self.REL)

    def test_excludes_what_is_already_selected(self):
        from nextseek_api.services.template_catalog import suggest

        assert suggest(["TIS", "DNA", "RNA"], self.REL) == ["D.SEQ"]

    def test_is_one_hop_only(self):
        """PAT -> PAV, and no further."""
        from nextseek_api.services.template_catalog import suggest

        assert suggest(["PAT"], self.REL) == ["PAV"]

    def test_orders_by_how_many_selected_types_name_it_then_by_code(self):
        from nextseek_api.services.template_catalog import suggest

        rel = {
            "A": {"parents": [], "children": ["SHARED", "ZONLY"]},
            "B": {"parents": [], "children": ["SHARED", "AONLY"]},
        }
        assert suggest(["A", "B"], rel) == ["SHARED", "AONLY", "ZONLY"]

    def test_caps_at_max_suggestions(self):
        from nextseek_api.services.template_catalog import MAX_SUGGESTIONS, suggest

        children = [f"C{i:02d}" for i in range(30)]
        rel = {"A": {"parents": [], "children": children}}
        out = suggest(["A"], rel)
        assert len(out) == MAX_SUGGESTIONS == 12
        assert out == children[:12]

    def test_no_selection_yields_no_suggestions(self):
        from nextseek_api.services.template_catalog import suggest

        assert suggest([], self.REL) == []

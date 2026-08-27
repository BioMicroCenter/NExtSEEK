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

    def test_every_seeded_code_is_a_legal_sheet_name(self):
        """Guards the assumption the writer relies on. Longest today is D.ADNKA (7)."""
        from nextseek_api.services.template_catalog import (
            ILLEGAL_SHEET_CHARS,
            MAX_SHEET_NAME,
            load_catalog,
        )

        rows = [{"id": i, "title": t} for i, t in enumerate(
            ["TIS", "D.ADNKA", "A.GEX", "M.LMM"], start=1)]
        with patch(f"{_MOD}.Sample_types", self._types(rows)), \
             patch(f"{_MOD}.load_sample_type_context", return_value={}):
            entries = load_catalog()

        for entry in entries:
            assert len(entry.code) <= MAX_SHEET_NAME
            assert not (set(entry.code) & ILLEGAL_SHEET_CHARS)

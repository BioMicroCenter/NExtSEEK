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

"""An assay's components: what a download for it would contain.

`context_catalog.load_assay` and `template_catalog.load_catalog` are both
patched, so these run with no database at all -- the same division
test_context_catalog.py already uses.
"""

from unittest.mock import patch

from nextseek_api.services.assay_templates import resolve_components
from nextseek_api.services.context_catalog import AssayEntry, AssayRow
from nextseek_api.services.template_catalog import SampleTypeEntry

# D.FCS is deliberately absent: it stands for a code that is curated in
# assay_context but that load_catalog does not offer a sheet for.
CATALOG = [
    SampleTypeEntry(code="TIS", sample_type_id=1, name="Tissue"),
    SampleTypeEntry(code="CEX", sample_type_id=2, name="Cell Extract"),
    SampleTypeEntry(code="CEL", sample_type_id=3, name="Cell Line"),
    SampleTypeEntry(code="AB", sample_type_id=4, name="Antibody"),
    SampleTypeEntry(code="ABP", sample_type_id=5, name="Antibody Panel"),
    SampleTypeEntry(code="D.FLOW", sample_type_id=6, name="Flow Data"),
]

FLOW_ROW = AssayRow(
    row_id=1,
    description="Laser-based single-cell analysis.",
    required_parents=[["TIS", "CEX", "CEL"], ["AB", "ABP"]],
    optional_parents=[["D.FCS"]],
    children=[["D.FLOW"]],
)


def _entry(rows):
    return AssayEntry(slug="flow-cytometry", name="Flow Cytometry", rows=rows)


def _patched(entry):
    """Patch both dependencies at the names assay_templates imports them under."""
    return (
        patch("nextseek_api.services.assay_templates.load_assay", return_value=entry),
        patch("nextseek_api.services.assay_templates.load_catalog", return_value=CATALOG),
    )


def _resolve(entry):
    p_assay, p_catalog = _patched(entry)
    with p_assay, p_catalog:
        return resolve_components("flow-cytometry")


class TestResolveComponents:
    def test_unknown_slug_is_none(self):
        p_assay, p_catalog = _patched(None)
        with p_assay, p_catalog:
            assert resolve_components("nope") is None

    def test_alternation_groups_are_preserved_not_flattened(self):
        """"TIS or CEX or CEL, AB or ABP" is two pick-one groups, not five codes."""
        got = _resolve(_entry([FLOW_ROW]))
        assert [len(g.alternatives) for g in got.required_parents] == [3, 2]

    def test_names_come_from_the_catalog(self):
        got = _resolve(_entry([FLOW_ROW]))
        assert got.required_parents[0].alternatives[0].name == "Tissue"

    def test_required_parents_default_to_the_first_alternative(self):
        got = _resolve(_entry([FLOW_ROW]))
        first = got.required_parents[0]
        assert [c.selected for c in first.alternatives] == [True, False, False]

    def test_produces_defaults_selected(self):
        got = _resolve(_entry([FLOW_ROW]))
        assert got.produces[0].alternatives[0].selected is True

    def test_optional_parents_default_unselected(self):
        got = _resolve(_entry([FLOW_ROW]))
        assert all(not c.selected for g in got.optional_parents
                   for c in g.alternatives)

    def test_a_code_the_catalog_cannot_download_is_kept_but_marked(self):
        """Kept and explained, never silently dropped: the user must see that
        the curator named a component no sheet exists for."""
        got = _resolve(_entry([FLOW_ROW]))
        fcs = got.optional_parents[0].alternatives[0]
        assert fcs.code == "D.FCS"
        assert fcs.downloadable is False
        assert fcs.reason
        assert fcs.selected is False

    def test_an_undownloadable_first_alternative_is_not_the_default(self):
        """Selecting it would produce a workbook missing that sheet."""
        row = AssayRow(row_id=2, required_parents=[["D.FCS", "TIS"]])
        got = _resolve(_entry([row]))
        picked = [c.code for c in got.required_parents[0].alternatives if c.selected]
        assert picked == ["TIS"]

    def test_a_group_with_nothing_downloadable_selects_nothing(self):
        row = AssayRow(row_id=3, required_parents=[["D.FCS"]])
        got = _resolve(_entry([row]))
        assert not any(c.selected for c in got.required_parents[0].alternatives)

    def test_a_split_slug_unions_both_rows_and_says_so(self):
        """24 slugs of 193 carry two unreconciled rows. A download must resolve
        to one set of sheets, so both rows' components are offered."""
        other = AssayRow(row_id=9, required_parents=[["CEL"]], children=[["D.FLOW"]])
        got = _resolve(_entry([FLOW_ROW, other]))
        assert got.is_split is True
        assert [g.alternatives[0].code for g in got.required_parents][-1] == "CEL"

    def test_the_union_does_not_repeat_an_identical_group(self):
        got = _resolve(_entry([FLOW_ROW, FLOW_ROW]))
        assert len(got.produces) == 1

    def test_a_single_row_is_not_split(self):
        assert _resolve(_entry([FLOW_ROW])).is_split is False

    def test_an_unreadable_catalog_costs_the_offer_not_the_page(self):
        """House rule: fail soft. Components still render, none downloadable."""
        with patch("nextseek_api.services.assay_templates.load_assay",
                   return_value=_entry([FLOW_ROW])), \
             patch("nextseek_api.services.assay_templates.load_catalog",
                   side_effect=RuntimeError("boom")):
            got = resolve_components("flow-cytometry")
        assert got is not None
        assert not any(c.downloadable for g in got.required_parents
                       for c in g.alternatives)

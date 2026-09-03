"""The sample type and assay catalog pages.

Login required, project membership not: these describe schema, not sample data.
Same rule templatesList already applies and states.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

import seek.views.catalog  # noqa: F401  -- so @patch can resolve the target
from nextseek_api.services.context_catalog import SampleTypeContextEntry

FLOW = SampleTypeContextEntry(
    code="D.FLOW", sample_type_id=13, name="Flow Cytometry Data",
    description="A flow cytometry file stores fluorescence measurements.",
    clade="Raw", tags=["FACS data"],
    required_metadata=["UID", "Parent"], standard_metadata=["Instrument"],
    possible_metadata_fields=["Stain"],
    parent_types=["TIS"], child_types=["A.FLOW"],
    assay_parents=["Flow Cytometry"], assay_children=["Flow Cytometry Analysis"],
)
NHP = SampleTypeContextEntry(code="NHP", sample_type_id=1, name="Non-Human Primate",
                             description="An animal.", clade="Source")


def _logged_in():
    db = MagicMock()
    db.getSeekLogin.return_value = {
        "status": True, "server": "https://seek.example",
        "username": "demo", "password": "demopassword",
    }
    return db


def _anonymous():
    db = MagicMock()
    db.getSeekLogin.return_value = {"status": False, "err": "not logged in"}
    return db


def _get(path):
    req = RequestFactory().get(path)
    req.user = MagicMock()
    return req


class TestSampleTypesList:
    @patch("seek.decorators.SeekDB")
    def test_anonymous_is_redirected_to_login(self, db):
        from seek.views.catalog import sampleTypesList

        db.return_value = _anonymous()
        resp = sampleTypesList(_get("/seek/sampletypes/"))
        assert resp.status_code == 302
        assert "/login/" in resp.url

    @patch("seek.views.catalog.load_sample_types", return_value=[NHP, FLOW])
    @patch("seek.decorators.SeekDB")
    def test_entries_are_grouped_by_clade_in_pipeline_order(self, db, _load):
        from seek.views.catalog import sampleTypesList

        db.return_value = _logged_in()
        resp = sampleTypesList(_get("/seek/sampletypes/"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert body.index("Source") < body.index("Raw")
        assert "D.FLOW" in body and "NHP" in body
        # Pipeline order is Source, Processed, Raw, Analyzed (one source of truth).
        from nextseek_api.services.context_catalog import CLADE_ORDER
        assert CLADE_ORDER == ["Source", "Processed", "Raw", "Analyzed"]

    @patch("seek.views.catalog.load_sample_types", return_value=[FLOW])
    @patch("seek.decorators.SeekDB")
    def test_every_code_links_to_its_detail_page(self, db, _load):
        from seek.views.catalog import sampleTypesList

        db.return_value = _logged_in()
        body = sampleTypesList(_get("/seek/sampletypes/")).content.decode()
        assert '/seek/sampletypes/D.FLOW/' in body

    @patch("seek.views.catalog.load_sample_types", return_value=[])
    @patch("seek.decorators.SeekDB")
    def test_an_empty_catalog_renders_a_page_not_a_crash(self, db, _load):
        """The soft-dependency rule: no context table is an empty state, not a 500."""
        from seek.views.catalog import sampleTypesList

        db.return_value = _logged_in()
        resp = sampleTypesList(_get("/seek/sampletypes/"))
        assert resp.status_code == 200
        assert "No sample types" in resp.content.decode()


class TestSampleTypeDetail:
    @patch("seek.views.catalog.load_sample_type", return_value=FLOW)
    @patch("seek.decorators.SeekDB")
    def test_every_populated_field_is_rendered(self, db, _load):
        from seek.views.catalog import sampleTypeDetail

        db.return_value = _logged_in()
        body = sampleTypeDetail(_get("/seek/sampletypes/D.FLOW/"), "D.FLOW").content.decode()
        for expected in ("Flow Cytometry Data", "UID", "Instrument", "Stain", "FACS data"):
            assert expected in body

    @patch("seek.views.catalog.load_sample_type", return_value=FLOW)
    @patch("seek.decorators.SeekDB")
    def test_related_types_link_into_the_catalog_and_assays_by_slug(self, db, _load):
        from seek.views.catalog import sampleTypeDetail

        db.return_value = _logged_in()
        body = sampleTypeDetail(_get("/seek/sampletypes/D.FLOW/"), "D.FLOW").content.decode()
        assert "/seek/sampletypes/TIS/" in body
        assert "/seek/sampletypes/A.FLOW/" in body
        assert "/seek/assays/flow-cytometry/" in body
        assert "/seek/assays/flow-cytometry-analysis/" in body

    @patch("seek.views.catalog.load_sample_type", return_value=None)
    @patch("seek.decorators.SeekDB")
    def test_an_unknown_code_is_a_404_not_a_500(self, db, _load):
        from django.http import Http404

        from seek.views.catalog import sampleTypeDetail

        db.return_value = _logged_in()
        with pytest.raises(Http404):
            sampleTypeDetail(_get("/seek/sampletypes/NOPE/"), "NOPE")

    @patch("seek.views.catalog.load_sample_type", return_value=NHP)
    @patch("seek.decorators.SeekDB")
    def test_empty_sections_are_omitted_not_rendered_blank(self, db, _load):
        from seek.views.catalog import sampleTypeDetail

        db.return_value = _logged_in()
        body = sampleTypeDetail(_get("/seek/sampletypes/NHP/"), "NHP").content.decode()
        assert "Standard metadata" not in body
        assert "Other possible fields" not in body


from nextseek_api.services.context_catalog import AssayEntry, AssayRow  # noqa: E402

FLOW_ROW = AssayRow(
    row_id=30, description="Flow cytometry is a laser-based technique.",
    tags=["FACS"], alternative_names=[],
    required_parents=[["TIS", "CEX", "CEL"], ["AB", "ABP"]],
    optional_parents=[["D.FCS"]], children=[["D.FLOW"]],
    parent_clade="Processed", child_clade="Raw",
    sheet_link="", repository="Immport", critical_attributes=["TIS::Type"],
    internal_assay_id=30,
)
FLOW_ASSAY = AssayEntry(slug="flow-cytometry", name="Flow Cytometry", rows=[FLOW_ROW])

CULTURE = AssayEntry(slug="cell-culture", name="Cell Culture", rows=[
    AssayRow(row_id=11, description="Cell culture maintains cells.",
             required_parents=[["CEL"]], children=[["CEL"]], internal_assay_id=None),
    AssayRow(row_id=85, description="This assay captures standardized metadata.",
             internal_assay_id=85),
])


class TestAssaysList:
    @patch("seek.decorators.SeekDB")
    def test_anonymous_is_redirected_to_login(self, db):
        from seek.views.catalog import assaysList

        db.return_value = _anonymous()
        resp = assaysList(_get("/seek/assays/"))
        assert resp.status_code == 302
        assert "/login/" in resp.url

    @patch("seek.views.catalog.load_assays", return_value=[CULTURE, FLOW_ASSAY])
    @patch("seek.decorators.SeekDB")
    def test_every_assay_links_to_its_detail_page_by_slug(self, db, _load):
        from seek.views.catalog import assaysList

        db.return_value = _logged_in()
        body = assaysList(_get("/seek/assays/")).content.decode()
        assert "/seek/assays/flow-cytometry/" in body
        assert "/seek/assays/cell-culture/" in body

    @patch("seek.views.catalog.load_assays", return_value=[])
    @patch("seek.decorators.SeekDB")
    def test_a_stack_without_the_table_renders_the_empty_state(self, db, _load):
        from seek.views.catalog import assaysList

        db.return_value = _logged_in()
        resp = assaysList(_get("/seek/assays/"))
        assert resp.status_code == 200
        assert "assay_context" in resp.content.decode()


class TestAssayDetail:
    @patch("seek.views.catalog.load_assay", return_value=FLOW_ASSAY)
    @patch("seek.decorators.SeekDB")
    def test_the_alternation_is_rendered_not_flattened(self, db, _load):
        """The load-bearing assertion of this whole page.

        "TIS or CEX or CEL" means an upload needs ONE of them. Rendering three
        chips side by side says it needs all three, which is a lie about the
        schema, so the word `or` has to survive into the markup.
        """
        from seek.views.catalog import assayDetail

        db.return_value = _logged_in()
        body = assayDetail(_get("/seek/assays/flow-cytometry/"), "flow-cytometry").content.decode()
        assert 'class="cat-or"' in body
        for code in ("TIS", "CEX", "CEL", "AB", "ABP"):
            assert f'/seek/sampletypes/{code}/' in body

    @patch("seek.views.catalog.load_assay", return_value=CULTURE)
    @patch("seek.decorators.SeekDB")
    def test_a_slug_with_two_rows_renders_both_and_says_so(self, db, _load):
        from seek.views.catalog import assayDetail

        db.return_value = _logged_in()
        body = assayDetail(_get("/seek/assays/cell-culture/"), "cell-culture").content.decode()
        assert "Cell culture maintains cells." in body
        assert "This assay captures standardized metadata." in body
        assert "twice in" in body  # the notice naming assay_context

    @patch("seek.views.catalog.load_assay", return_value=FLOW_ASSAY)
    @patch("seek.decorators.SeekDB")
    def test_a_single_row_entry_shows_no_duplication_notice(self, db, _load):
        from seek.views.catalog import assayDetail

        db.return_value = _logged_in()
        body = assayDetail(_get("/seek/assays/flow-cytometry/"), "flow-cytometry").content.decode()
        assert "twice in" not in body

    @patch("seek.views.catalog.load_assay", return_value=None)
    @patch("seek.decorators.SeekDB")
    def test_an_unknown_slug_is_a_404(self, db, _load):
        from django.http import Http404

        from seek.views.catalog import assayDetail

        db.return_value = _logged_in()
        with pytest.raises(Http404):
            assayDetail(_get("/seek/assays/nope/"), "nope")

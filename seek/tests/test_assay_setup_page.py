"""The assay setup page.

Login required, project membership not: an assay recipe is a schema definition
shared by every project, the same rule templatesList and the catalog pages
already apply.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

import seek.views.assay_setup  # noqa: F401  -- so @patch can resolve the target
from nextseek_api.services.assay_templates import (
    AssayComponents,
    ComponentCode,
    ComponentGroup,
)
from nextseek_api.services.context_catalog import AssayEntry, AssayRow

FLOW = AssayComponents(
    slug="flow-cytometry", name="Flow Cytometry", is_split=False,
    required_parents=[ComponentGroup([
        ComponentCode("TIS", "Tissue", True, "", True),
        ComponentCode("CEX", "Cell Extract", True, "", False),
    ])],
    optional_parents=[ComponentGroup([
        ComponentCode("D.FCS", "", False, "No template sheet exists for this "
                                          "sample type.", False),
    ])],
    produces=[ComponentGroup([ComponentCode("D.FLOW", "Flow Data", True, "", True)])],
)

# Every entry carries a row: assay_table_groups reads e.rows[0] to build the
# table, so an AssayEntry with no rows is not a shape the picker ever sees.
ASSAYS = [
    AssayEntry(slug="flow-cytometry", name="Flow Cytometry", rows=[
        AssayRow(row_id=1, required_parents=[["TIS"]], children=[["D.FLOW"]],
                 parent_clade="Processed"),
    ]),
    AssayEntry(slug="rna-extraction", name="RNA Extraction", rows=[
        AssayRow(row_id=2, required_parents=[["TIS"]], children=[["RNA"]],
                 parent_clade="Processed"),
    ]),
]


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


class TestAssaySetupPage:
    @patch("seek.decorators.SeekDB")
    def test_anonymous_is_redirected_to_login(self, db):
        from seek.views.assay_setup import assaySetup

        db.return_value = _anonymous()
        resp = assaySetup(_get("/seek/assay-setup/"))
        assert resp.status_code == 302
        assert "/login/" in resp.url

    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_it_lists_every_assay_to_pick_from(self, db, _assays):
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        resp = assaySetup(_get("/seek/assay-setup/"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Flow Cytometry" in body and "RNA Extraction" in body

    @patch("seek.views.assay_setup.resolve_components", return_value=FLOW)
    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_an_assay_query_renders_its_components(self, db, _assays, _resolve):
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        resp = assaySetup(_get("/seek/assay-setup/?assay=flow-cytometry"))
        body = resp.content.decode()
        assert "TIS" in body and "D.FLOW" in body

    @patch("seek.views.assay_setup.resolve_components", return_value=FLOW)
    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_an_undownloadable_component_says_why(self, db, _assays, _resolve):
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        resp = assaySetup(_get("/seek/assay-setup/?assay=flow-cytometry"))
        assert "No template sheet exists" in resp.content.decode()

    @patch("seek.views.assay_setup.resolve_components", return_value=None)
    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_an_unknown_assay_is_a_404(self, db, _assays, _resolve):
        from django.http import Http404

        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        with pytest.raises(Http404):
            assaySetup(_get("/seek/assay-setup/?assay=nope"))

    @patch("seek.views.assay_setup.load_assays", side_effect=RuntimeError("boom"))
    @patch("seek.decorators.SeekDB")
    def test_an_unreadable_table_renders_a_notice_not_a_500(self, db, _assays):
        """House rule: a missing table costs the offer, never the page."""
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        resp = assaySetup(_get("/seek/assay-setup/"))
        assert resp.status_code == 200

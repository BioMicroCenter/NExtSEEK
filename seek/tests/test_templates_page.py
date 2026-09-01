"""Download Templates: the picker page and the workbook download.

The project-membership gate that used to guard this page is gone -- templates
are schema definitions, not sample data. Login is still required.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory

from nextseek_api.services.template_catalog import SampleTypeEntry

TIS = SampleTypeEntry(code="TIS", sample_type_id=2, name="Tissue",
                      description="A tissue sample.", group="")
SEQ = SampleTypeEntry(code="D.SEQ", sample_type_id=11, name="Sequencing Data",
                      description="Reads.", group="D.")


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


def _get():
    req = RequestFactory().get("/seek/templates/")
    req.user = MagicMock()
    return req


def _post(codes):
    req = RequestFactory().post("/seek/templates/download/", {"codes": codes})
    req.user = MagicMock()
    return req


class TestPicker:
    @patch("seek.views.SeekDB")
    def test_anonymous_is_redirected_to_login(self, mock_db):
        from seek.views import templatesList

        mock_db.return_value = _anonymous()
        resp = templatesList(_get())
        assert resp.status_code == 302
        assert "/login/" in resp.url

    @patch("requests.adapters.HTTPAdapter.send")
    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_a_logged_in_user_gets_the_page_without_any_project_check(
            self, mock_db, _catalog, mock_send):
        """The behaviour change: membership no longer gates the page.

        A plain 200 can't distinguish "no project check" from "project check
        present and denying" -- the old denial branch also returned 200, just
        with a JSON error blob in the body. Pin the real behaviour instead:
        no SEEK HTTP call is made, and the body isn't that JSON denial blob.
        The picker template still expects the pre-rewrite `folders` context
        variable (next task), so we don't assert on picker markup here --
        only on the absence of the old denial response.

        Patches ``requests.adapters.HTTPAdapter.send`` -- the point where
        every ``requests`` call actually hits the network, regardless of
        which module holds the reference -- rather than a `seek.views`-local
        alias, so this keeps proving no SEEK HTTP call happens even now that
        `seek/views.py` no longer imports `requests` at all.
        """
        from seek.views import templatesList

        mock_db.return_value = _logged_in()
        resp = templatesList(_get())
        assert resp.status_code == 200
        mock_db.return_value.getSeekLogin.assert_called_once()
        mock_send.assert_not_called()

        body = resp.content.decode()
        assert "You are not in the correct project" not in body
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        assert not (isinstance(parsed, dict) and parsed.get("status") == 0)

    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_types_reach_the_template_grouped_in_display_order(self, mock_db, _catalog):
        from seek.views import templatesList

        mock_db.return_value = _logged_in()
        resp = templatesList(_get())
        body = resp.content.decode()
        assert "Experimental types" in body
        assert "Data types" in body
        assert "TIS" in body and "D.SEQ" in body

    @patch("seek.views.load_catalog")
    @patch("seek.views.SeekDB")
    def test_a_hostile_sample_type_name_cannot_break_out_of_the_script_block(
            self, mock_db, mock_catalog):
        """A curator-written name is untrusted by the time it reaches the page.

        children_json/meta_json are built from DB-sourced sample type names
        with no Django write path (out-of-band curation pipeline), so a name
        containing markup must still render inert. The raw `</script>`
        sequence must never appear unescaped in the response body -- that is
        exactly what would terminate the inline script block early and let
        the rest of the payload run as markup.
        """
        from seek.views import templatesList

        hostile = SampleTypeEntry(
            code="EVIL", sample_type_id=99,
            name="</script><img src=x onerror=alert(1)>",
            description="", group="")

        mock_catalog.return_value = [TIS, SEQ, hostile]
        mock_db.return_value = _logged_in()
        resp = templatesList(_get())
        body = resp.content.decode()

        assert "</script><img" not in body
        assert "<img src=x onerror=alert(1)>" not in body

    @patch("seek.views.load_requirements")
    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_requirements_are_embedded_for_the_page(self, mock_db, _cat, mock_req):
        from seek.views import templatesList

        mock_db.return_value = _logged_in()
        mock_req.return_value = {
            "D.SEQ": {"parents": ["TIS"], "assays": ["Short Read Sequencing"],
                      "coverage": 1.0}
        }
        body = templatesList(_get()).content.decode()
        assert "tpl-requirements-data" in body
        assert "Short Read Sequencing" in body

    @patch("seek.views.load_requirements", return_value={})
    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_no_requirements_still_renders_the_picker(self, mock_db, _cat, _req):
        """The table ships empty; the page must not depend on it."""
        from seek.views import templatesList

        mock_db.return_value = _logged_in()
        resp = templatesList(_get())
        assert resp.status_code == 200
        assert "Experimental types" in resp.content.decode()

    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_requirements_are_scoped_to_the_catalog(self, mock_db, _cat):
        from seek.views import templatesList

        mock_db.return_value = _logged_in()
        with patch("seek.views.load_requirements") as mock_req:
            templatesList(_get())
        assert mock_req.call_args[0][0] == {"TIS", "D.SEQ"}


class TestDownload:
    @patch("seek.views.SeekDB")
    def test_anonymous_is_redirected_to_login(self, mock_db):
        from seek.views import templatesDownload

        mock_db.return_value = _anonymous()
        resp = templatesDownload(_post(["TIS"]))
        assert resp.status_code == 302

    @patch("seek.views.write_template_workbook")
    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_returns_an_xlsx_attachment(self, mock_db, _catalog, mock_write):
        from seek.views import templatesDownload

        mock_db.return_value = _logged_in()
        resp = templatesDownload(_post(["TIS"]))
        assert resp.status_code == 200
        assert resp["Content-Type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert resp["Content-Disposition"].startswith("attachment; filename=")
        assert resp["Content-Disposition"].endswith(".xlsx")

    @patch("seek.views.write_template_workbook")
    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_only_the_selected_types_are_written(self, mock_db, _catalog, mock_write):
        from seek.views import templatesDownload

        mock_db.return_value = _logged_in()
        templatesDownload(_post(["D.SEQ"]))
        entries = mock_write.call_args[0][0]
        assert [e.code for e in entries] == ["D.SEQ"]

    @patch("seek.views.write_template_workbook")
    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_selection_order_is_preserved(self, mock_db, _catalog, mock_write):
        from seek.views import templatesDownload

        mock_db.return_value = _logged_in()
        templatesDownload(_post(["D.SEQ", "TIS"]))
        entries = mock_write.call_args[0][0]
        assert [e.code for e in entries] == ["D.SEQ", "TIS"]

    @patch("seek.views.write_template_workbook")
    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_an_unknown_code_is_dropped_and_the_rest_are_written(
            self, mock_db, _catalog, mock_write):
        from seek.views import templatesDownload

        mock_db.return_value = _logged_in()
        templatesDownload(_post(["TIS", "NOPE"]))
        entries = mock_write.call_args[0][0]
        assert [e.code for e in entries] == ["TIS"]

    @patch("seek.views.write_template_workbook")
    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_a_duplicated_code_is_written_only_once(
            self, mock_db, _catalog, mock_write):
        from seek.views import templatesDownload

        mock_db.return_value = _logged_in()
        templatesDownload(_post(["TIS", "TIS", "D.SEQ"]))
        entries = mock_write.call_args[0][0]
        assert [e.code for e in entries] == ["TIS", "D.SEQ"]

    @patch("seek.views.write_template_workbook")
    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_an_empty_selection_re_renders_the_page_and_writes_nothing(
            self, mock_db, _catalog, mock_write):
        from seek.views import templatesDownload

        mock_db.return_value = _logged_in()
        resp = templatesDownload(_post([]))
        assert resp.status_code == 200
        assert "spreadsheetml" not in resp["Content-Type"]
        mock_write.assert_not_called()

    @patch("seek.views.write_template_workbook")
    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_every_code_unknown_is_treated_as_an_empty_selection(
            self, mock_db, _catalog, mock_write):
        from seek.views import templatesDownload

        mock_db.return_value = _logged_in()
        resp = templatesDownload(_post(["NOPE"]))
        assert resp.status_code == 200
        mock_write.assert_not_called()

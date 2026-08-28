"""Download Templates: the picker page and the workbook download.

The project-membership gate that used to guard this page is gone -- templates
are schema definitions, not sample data. Login is still required.
"""

from unittest.mock import MagicMock, patch

import pytest
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

    @patch("seek.views.load_catalog", return_value=[TIS, SEQ])
    @patch("seek.views.SeekDB")
    def test_a_logged_in_user_gets_the_page_without_any_project_check(
            self, mock_db, _catalog):
        """The behaviour change: membership no longer gates the page."""
        from seek.views import templatesList

        mock_db.return_value = _logged_in()
        resp = templatesList(_get())
        assert resp.status_code == 200
        mock_db.return_value.getSeekLogin.assert_called_once()

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

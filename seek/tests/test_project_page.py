"""The project page and its connections route."""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

import seek.views.projects  # noqa: F401  -- so @patch can resolve the target


def _seekdb(project_ids):
    db = MagicMock()
    db.getSeekLogin.return_value = {"status": True, "server": "https://seek.example",
                                    "username": "demo", "password": "demopassword"}
    db.getCurrentUser.return_value = {
        "data": {"relationships": {"projects": {"data": [{"id": str(i)} for i in project_ids]}}}
    }
    return db


def _req(path, superuser=False):
    req = RequestFactory().get(path)
    req.user = MagicMock(is_authenticated=True, is_superuser=superuser)
    return req


class TestProjectConnections:
    @patch("seek.views.projects.connections_html", return_value="<html>d</html>")
    @patch("seek.views.projects.Projects")
    @patch("seek.decorators.SeekDB")
    def test_a_member_gets_the_diagram(self, db, projects, _html):
        from seek.views.projects import project_connections

        db.return_value = _seekdb([2])
        projects.objects.filter.return_value.first.return_value = MagicMock(title="IMPAcTb")
        resp = project_connections(_req("/seek/projects/2/connections/"), "2")
        assert resp.status_code == 200
        assert b"<html>d</html>" in resp.content

    @patch("seek.views.projects.connections_html", return_value="<html>d</html>")
    @patch("seek.views.projects.Projects")
    @patch("seek.decorators.SeekDB")
    def test_a_non_member_is_forbidden(self, db, projects, _html):
        from seek.views.projects import project_connections

        db.return_value = _seekdb([7])
        projects.objects.filter.return_value.first.return_value = MagicMock(title="IMPAcTb")
        resp = project_connections(_req("/seek/projects/2/connections/"), "2")
        assert resp.status_code == 403

    @patch("seek.views.projects.connections_html", return_value="<html>d</html>")
    @patch("seek.views.projects.Projects")
    @patch("seek.decorators.SeekDB")
    def test_a_superuser_who_is_not_a_member_still_gets_it(self, db, projects, _html):
        from seek.views.projects import project_connections

        db.return_value = _seekdb([7])
        projects.objects.filter.return_value.first.return_value = MagicMock(title="IMPAcTb")
        resp = project_connections(_req("/seek/projects/2/connections/", superuser=True), "2")
        assert resp.status_code == 200

    @patch("seek.views.projects.connections_html", return_value="")
    @patch("seek.views.projects.Projects")
    @patch("seek.decorators.SeekDB")
    def test_no_diagram_is_a_200_placeholder_not_a_404(self, db, projects, _html):
        from seek.views.projects import project_connections

        db.return_value = _seekdb([2])
        projects.objects.filter.return_value.first.return_value = MagicMock(title="IMPAcTb")
        resp = project_connections(_req("/seek/projects/2/connections/"), "2")
        assert resp.status_code == 200
        assert b"No sample-type connections" in resp.content

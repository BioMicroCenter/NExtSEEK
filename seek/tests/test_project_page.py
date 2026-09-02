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

    @patch("seek.views.projects.connections_html", return_value="<html>d</html>")
    @patch("seek.views.projects.Projects")
    @patch("seek.decorators.SeekDB")
    def test_the_response_may_be_framed_by_the_project_page(self, db, projects, _html):
        """Without this the browser refuses the frame and shows a broken-document
        icon, while the response is a clean 200 that no status sweep can fault.

        XFrameOptionsMiddleware is enabled and dmac/settings.py sets no
        X_FRAME_OPTIONS, so Django's default DENY applies and blocks even a
        same-origin frame. Measured in the browser 2026-09-02.
        """
        from seek.views.projects import project_connections

        db.return_value = _seekdb([2])
        projects.objects.filter.return_value.first.return_value = MagicMock(title="IMPAcTb")
        resp = project_connections(_req("/seek/projects/2/connections/"), "2")
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"

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


class TestProjectPage:
    """The reworked page. Each section degrades independently.

    project_page is NOT decorated: it constructs its own SeekDB and calls
    getSeekLogin itself, so the patch target is seek.views.projects.SeekDB and
    not seek.decorators.SeekDB the way every other view in this file works.
    """

    PATCHES = {
        "seekdb": "seek.views.projects.SeekDB",
        "clades": "seek.views.projects.DBtable_clades",
        "projects": "seek.views.projects.Projects",
        "rows": "seek.views.projects.connection_rows",
        "bundles": "seek.views.projects.project_bundles",
        "types": "seek.views.projects.types_in_use",
        "ctx": "seek.views.projects.load_project_context",
        "codes": "seek.views.projects.load_sample_types",
    }

    def _render(self, overrides=None, superuser=False):
        import contextlib

        with contextlib.ExitStack() as stack:
            m = {name: stack.enter_context(patch(target))
                 for name, target in self.PATCHES.items()}
            m["seekdb"].return_value = _seekdb([2])
            m["projects"].objects.get.return_value = MagicMock(
                id=2, title="IMPAcTb", description="A study.", avatar_id=9)
            m["clades"].return_value.getCladeProjectStats.return_value = []
            m["rows"].return_value = []
            m["bundles"].return_value = []
            m["types"].return_value = []
            m["ctx"].return_value = None
            m["codes"].return_value = []
            for name, value in (overrides or {}).items():
                m[name].return_value = value
            from seek.views.projects import project_page

            return project_page(_req("/seek/projects/2/", superuser=superuser), "2")

    def test_the_diagram_is_an_iframe_pointing_at_the_connections_route(self):
        body = self._render().content.decode()
        assert "<iframe" in body
        assert "/seek/projects/2/connections/" in body

    def test_a_project_with_no_context_row_renders_the_plain_header(self):
        body = self._render().content.decode()
        assert "IMPAcTb" in body
        assert "Principal investigator" not in body

    def test_context_fields_appear_when_the_row_exists(self):
        body = self._render({"ctx": {
            "name": "IMPAcTb", "pi": "A Person", "research_focus": "TB",
            "alternative_names": ["IMPACT"], "key_data_types": ["flow"],
            "parent_project": "", "tags": ["tb"],
            "nih_reporter_link": "https://reporter.nih.gov/x",
            "fairdomhub_published_link": "",
        }}).content.decode()
        assert "A Person" in body
        assert ">NIH Reporter<" in body
        assert "https://reporter.nih.gov/x" in body

    def test_an_empty_link_field_renders_no_link(self):
        body = self._render({"ctx": {
            "name": "IMPAcTb", "pi": "", "research_focus": "",
            "alternative_names": [], "key_data_types": [], "parent_project": "",
            "tags": [], "nih_reporter_link": "", "fairdomhub_published_link": "",
        }}).content.decode()
        # On the link TEXT, not the domain: the sidebar carries an unrelated
        # "Published Studies" link to fairdomhub.org on every page, so asserting
        # on the domain tests the nav rather than this block.
        assert ">NIH Reporter<" not in body
        assert ">FairDOMHub<" not in body
        assert "<dt>Links</dt>" not in body

    def test_each_bundle_is_a_form_posting_its_codes_to_the_download_route(self):
        body = self._render({
            "bundles": [{"label": "Tissue Collection", "codes": ["NHP", "PAV", "TIS"]}]
        }).content.decode()
        assert 'action="/seek/templates/download/"' in body
        assert "Tissue Collection" in body
        assert 'value="NHP"' in body and 'value="TIS"' in body

    def test_types_in_use_link_into_the_catalog(self):
        body = self._render({"types": ["NHP", "TIS"],
                             "codes": [MagicMock(code="NHP"), MagicMock(code="TIS")]}
                            ).content.decode()
        assert "/seek/sampletypes/NHP/" in body
        assert "/seek/sampletypes/TIS/" in body

    def test_a_dead_graph_costs_the_diagram_and_the_bundles_only(self):
        """connection_rows returns [] when Neo4j is unreachable. The page stands."""
        resp = self._render()
        assert resp.status_code == 200
        assert "IMPAcTb" in resp.content.decode()

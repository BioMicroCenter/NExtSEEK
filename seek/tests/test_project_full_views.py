"""Plan C: no duplicate stats, ranked bundles, modal-route helper, the /samples/ route."""

import re
from pathlib import Path

from django.conf import settings


def _css():
    return (Path(settings.BASE_DIR) / "themes" / "NextSeek" / "static" / "css" / "nextseek.css").read_text()


def _js():
    return (Path(settings.BASE_DIR) / "themes" / "NextSeek" / "static" / "js" / "nextseek.js").read_text()


class TestNoDuplicateStats:
    def test_m_stats_cards_flex_is_inside_a_mobile_media_query(self):
        css = _css()
        # The top-level .m-stats-cards rule must NOT force display:flex (that overrode
        # .m-only:none on desktop and rendered the stats twice).
        top = re.search(r"\.m-stats-cards\s*\{[^}]*\}", css).group(0)
        assert "display: flex" not in top and "display:flex" not in top
        # A mobile-scoped rule turns it on.
        assert re.search(
            r"@media[^{]*max-width:\s*767\.98px[^{]*\{[^@]*\.m-stats-cards[^}]*display:\s*flex",
            css, re.S,
        )


class TestBundleRanking:
    def test_bundles_are_ranked_by_edge_volume_desc_with_counts(self):
        from nextseek_api.services.project_connections import _derived_bundles
        rows = (
            [{"internal_assay": "Big", "parent_sample_type": "AB", "child_sample_type": "TIS"}] * 5 +
            [{"internal_assay": "Small", "parent_sample_type": "AB", "child_sample_type": "CEL"}] * 1
        )
        known = {"AB", "TIS", "CEL"}
        out = _derived_bundles(rows, known)
        assert [b["label"] for b in out] == ["Big", "Small"]      # ranked, not alphabetical
        assert out[0]["n_edges"] == 5 and out[1]["n_edges"] == 1


class TestModalRouteAssets:
    def test_js_defines_the_modal_route_handler(self):
        js = _js()
        assert "data-modal-route" in js
        assert "pushState" in js and "popstate" in js

    def test_css_defines_the_overlay(self):
        assert ".modal-route-overlay" in _css()


# --- /seek/projects/<id>/samples/ route (mirrors test_project_page.py helpers) ---
from unittest.mock import MagicMock, patch  # noqa: E402
from django.test import RequestFactory  # noqa: E402
import seek.views.projects  # noqa: F401,E402


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


class TestProjectSamplesRoute:
    @patch("seek.views.projects.Projects")
    @patch("seek.decorators.SeekDB")
    def test_a_non_member_is_forbidden(self, db, projects):
        from seek.views.projects import project_samples
        db.return_value = _seekdb([7])
        projects.objects.filter.return_value.first.return_value = MagicMock(title="IMPAcTb")
        resp = project_samples(_req("/seek/projects/2/samples/"), "2")
        assert resp.status_code == 403

    @patch("seek.views.projects._project_clade_data", return_value=[])
    @patch("seek.views.projects.Projects")
    @patch("seek.decorators.SeekDB")
    def test_a_member_sees_a_counts_table(self, db, projects, _clade):
        from seek.views.projects import project_samples
        db.return_value = _seekdb([2])
        projects.objects.filter.return_value.first.return_value = MagicMock(title="IMPAcTb")
        resp = project_samples(_req("/seek/projects/2/samples/"), "2")
        assert resp.status_code == 200
        assert b"project-samples" in resp.content


class TestSampleTreeFullscreen:
    def test_tree_partial_has_a_fullscreen_button(self):
        tpl = (Path(settings.BASE_DIR) / "seek" / "templates" / "pages"
               / "samples_tree_new.embed.html").read_text()
        assert "tree-fullscreen-btn" in tpl
        assert "tree-fullscreen" in tpl        # the toggle class

    def test_css_defines_the_fullscreen_state(self):
        css = _css()
        assert ".tree-fullscreen-btn" in css
        assert "#tree_container.tree-fullscreen" in css

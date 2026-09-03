"""The left navbar: USEFUL INFO dropdown, promoted Nessie button, removed dead items."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.template.loader import render_to_string
from django.test import RequestFactory


class TestNessieButtonPartial:
    def test_it_is_a_link_to_the_assistant(self):
        html = render_to_string("includes/nessie_button.html")
        assert 'href="/seek/assistant/"' in html

    def test_it_carries_the_logo_slot_and_label(self):
        html = render_to_string("includes/nessie_button.html")
        assert "nessie-btn__logo" in html          # the swap-in slot for the user's asset
        assert "nessie-btn__label" in html
        assert "Ask Nessie" in html

    def test_the_logo_hides_itself_when_the_asset_is_absent(self):
        html = render_to_string("includes/nessie_button.html")
        assert "onerror" in html                     # graceful text-only render until the asset lands


def _logged_in():
    db = MagicMock()
    db.getSeekLogin.return_value = {
        "status": True, "server": "https://seek.example",
        "username": "demo", "password": "demopassword",
    }
    return db


def _get(path):
    req = RequestFactory().get(path)
    req.user = MagicMock()
    req.user.is_superuser = False
    return req


def _render_a_page_with_the_nav():
    """The nav is included by base.html, which the catalog list extends.
    Render the sample-types list (login mocked, loader empty) to exercise the real nav."""
    from seek.views.catalog import sampleTypesList
    with patch("seek.decorators.SeekDB") as db, \
         patch("seek.views.catalog.load_sample_types", return_value=[]):
        db.return_value = _logged_in()
        resp = sampleTypesList(_get("/seek/sampletypes/"))
    return resp.content.decode()


class TestNavbarStructure:
    def test_useful_info_section_and_its_four_items_are_present(self):
        html = _render_a_page_with_the_nav()
        assert "USEFUL INFO" in html
        assert 'href="/seek/sampletypes/"' in html
        assert 'href="/seek/assays/"' in html
        assert 'href="/seek/templates/"' in html
        assert "Documentation" in html

    def test_nessie_button_is_promoted_into_the_nav(self):
        html = _render_a_page_with_the_nav()
        assert "nessie-btn" in html
        assert 'href="/seek/assistant/"' in html

    def test_dead_items_are_removed(self):
        html = _render_a_page_with_the_nav()
        assert 'id="ask-nessie"' not in html      # replaced by the button
        assert "Bookmarks" not in html            # placeholder deleted

    def test_search_by_uid_input_is_kept(self):
        html = _render_a_page_with_the_nav()
        assert 'id="search-uid"' in html


def _theme_file(rel):
    return Path(settings.BASE_DIR) / "themes" / "NextSeek" / rel


class TestNavbarAssetsCleanup:
    def test_js_no_longer_references_the_removed_input(self):
        js = _theme_file("static/js/nextseek.js").read_text()
        assert "ask-nessie" not in js
        assert "navNessie" not in js

    def test_js_keeps_the_uid_search(self):
        js = _theme_file("static/js/nextseek.js").read_text()
        assert "navUID" in js

    def test_css_defines_the_useful_info_toggle_and_the_button(self):
        css = _theme_file("static/css/nextseek.css").read_text()
        assert ".sidebar-section-toggle" in css
        assert ".nessie-btn" in css

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
        assert "Useful Info" in html
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


def _render_nav_and_shell(user):
    """base.html (with the nav and the phone top bar) through the home view."""
    from dmac.views import home
    req = RequestFactory().get("/seek/search/?tab=simple")
    req.user = user
    with patch("dmac.views._home_projects", return_value=[]):
        return home(req).content.decode()


class TestVisitorShell:
    """Logged out: only links that work without a sign-in, and a Sign in button that
    a phone user can reach without opening the drawer (UI-001, UI-002, UI-007)."""

    def _anon(self):
        from django.contrib.auth.models import AnonymousUser
        return _render_nav_and_shell(AnonymousUser())

    def _nav(self, html):
        return html[html.index('class="sidebar-nav-inner"'):html.index("</nav>")]

    def test_visitor_nav_hides_links_that_need_a_sign_in(self):
        nav = self._nav(self._anon())
        for href in ("/seek/search/", "/seek/samples/upload/", "/seek/datafile/query/",
                     "/seek/projects/", "/seek/templates/", "/seek/sampletypes/",
                     "/seek/assistant/"):
            assert f'href="{href}"' not in nav, href
        assert 'id="search-uid"' not in nav

    def test_visitor_nav_keeps_home_docs_and_resources(self):
        nav = self._nav(self._anon())
        assert 'href="/"' in nav
        assert "Documentation" in nav
        assert 'href="/seek/help/"' in nav

    def test_phone_top_bar_offers_sign_in_back_to_this_page(self):
        html = self._anon()
        bar = html[html.index('class="mobile-topbar'):html.index('<main')]
        assert 'class="mobile-toggle"' in bar
        assert 'href="/login/?next=/seek/search/%3Ftab%3Dsimple"' in bar

    def test_signed_in_user_gets_no_sign_in_in_the_top_bar(self):
        user = MagicMock()
        user.is_superuser = False
        html = _render_nav_and_shell(user)
        bar = html[html.index('class="mobile-topbar'):html.index('<main')]
        assert "Sign in" not in bar
        assert 'href="/seek/search/"' in self._nav(html)


class TestShellCss:
    def test_phone_top_bar_is_sticky(self):
        css = _theme_file("static/css/nextseek.css").read_text()
        block = css[css.index(".mobile-topbar {"):]
        block = block[:block.index("}")]
        assert "position: sticky" in block and "top: 0" in block

    def test_drawer_uses_the_dynamic_viewport_height(self):
        css = _theme_file("static/css/nextseek.css").read_text()
        block = css[css.index(".sidebar {"):]
        assert "height: 100dvh" in block[:block.index("}")]

    def test_footer_is_defined_once_and_wraps(self):
        import re
        css = _theme_file("static/css/nextseek.css").read_text()
        # one top-level rule each (media-query tweaks are indented)
        assert len(re.findall(r"^\.footer \{", css, re.M)) == 1
        assert len(re.findall(r"^\.footer-content \{", css, re.M)) == 1
        block = css[css.index(".footer-content {"):]
        assert "flex-wrap: wrap" in block[:block.index("}")]


class TestNewSampleOnPhones:
    """UI-040, UI-006: phones are told the upload is desktop-only before the tap,
    the drawer CTA keeps its full width, and the upload pages scroll instead of clipping."""

    def test_both_new_sample_controls_carry_the_desktop_only_hint(self):
        nav = _render_a_page_with_the_nav()
        cta = nav[nav.index('class="qa-cta"'):]
        assert "desktop-only-hint" in cta[:cta.index("</a>")]
        home = _theme_file("templates/index.html").read_text()
        tile = home[home.index('class="dash-action accent"'):]
        assert "desktop-only-hint" in tile[:tile.index("</a>")]

    def test_hint_shows_only_on_phones_and_the_cta_stays_full_width(self):
        css = _theme_file("static/css/nextseek.css").read_text()
        assert ".desktop-only-hint { display: none; }" in css
        touch = css[css.index("/* ---------- Touch targets"):]
        touch = touch[touch.index(".qa-cta {"):]
        assert "inline-flex" not in touch[:touch.index("}")]

    def test_upload_pages_have_no_fixed_minimum_width(self):
        root = Path(settings.BASE_DIR) / "seek" / "templates"
        for name in ("pages/batch_upload.embed.html", "dataFileUpload.html"):
            assert "min-width:600px" not in (root / name).read_text(), name
        assert "easyui-mobile-notice" in (root / "dataFileUpload.html").read_text()

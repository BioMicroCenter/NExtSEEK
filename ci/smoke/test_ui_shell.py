"""The site shell on a phone and on a desktop, and where signing in sends a visitor.

Guards the 2026-10-01 UI fixes (docs/ui/known-issues.md "Fix first", batches 1 to 7):

* HTTP, no browser: every signed-in page sends a visitor to /login/?next=<that page>;
  Mezzanine's blog, site search, account forms and local reset answer 404, and
  /accounts/login/ is the SEEK login.
* Browser (flow): a visitor on a phone can see Sign in and keep the menu after
  scrolling; no page scrolls sideways at phone width; "+ New sample" says desktop
  only; the project Sample flow opens full screen on a phone and never shows
  scrollbars on a desktop; the Nessie page is one full-height frame; signing in
  returns to the page asked for and never leaves the site; every inline button
  handler names a function the page defines.

Read-only everywhere: the only non-GET is the login form post, which the prod
profile grants /login/ (ci/routes.py). Failure messages name fixed paths only.
"""
from __future__ import annotations

from urllib.parse import quote, urlsplit

import pytest

from ci.smoke.conftest import _guard_context

PHONE = {
    "viewport": {"width": 390, "height": 844},
    "is_mobile": True,
    "has_touch": True,
    "device_scale_factor": 3,
    "user_agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
                   "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"),
}
DESKTOP = {"viewport": {"width": 1440, "height": 900}}

# Signed-in pages and their exact bounce. Fixed paths only: nothing discovered.
BOUNCES = [
    "/seek/search/?tab=advanced",
    "/seek/samples/upload/",
    "/seek/data/upload/",
    "/seek/datafile/query/",
    "/seek/sop/query/",
    "/seek/projects/",
    "/seek/sampletypes/",
    "/seek/assays/",
    "/seek/templates/",
    # the registry declares the admin pages for local and dev only
    pytest.param("/seek/admin/clades/", marks=pytest.mark.profiles("local", "dev")),
    pytest.param("/seek/admin/internal_assays/", marks=pytest.mark.profiles("local", "dev")),
    "/seek/assistant/",
]

# Pages that must not scroll sideways at 390px, signed in. The open ones are
# expected failures (strict=False), so a fix shows up as XPASS.
_UI085 = pytest.mark.xfail(reason="UI-085: catalog tables overflow on phones", strict=False)
PHONE_PAGES = [
    "/", "/seek/search/", "/seek/samples/upload/", "/seek/data/upload/",
    "/seek/datafile/query/", "/seek/sop/query/", "/seek/projects/", "/seek/assays/",
    "/seek/assistant/", "/seek/help/",
    pytest.param("/seek/templates/", marks=_UI085),
    pytest.param("/seek/sampletypes/", marks=_UI085),
]

_NO_SIDEWAYS = "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
_HANDLERS_DEFINED = """() => [...document.querySelectorAll('[onclick]')]
    .map(e => (e.getAttribute('onclick').match(/^\\s*([A-Za-z_]\\w*)\\s*\\(/) || [])[1])
    .filter(Boolean).filter(n => typeof window[n] !== 'function')"""


def _bounce_target(path: str) -> str:
    return "/login/?next=" + quote(path, safe="/=")


# --------------------------------------------------------------------------- #
# HTTP: where a visitor is sent
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", BOUNCES)
def test_visitor_is_sent_to_sign_in_and_back_to_the_page(anon, base_url, path):
    r = anon.get(base_url + path, timeout=60, allow_redirects=False)
    assert r.status_code == 302, f"{path}: a visitor got {r.status_code}, not a sign-in redirect"
    location = urlsplit(r.headers.get("location", ""))
    got = location.path + ("?" + location.query if location.query else "")
    assert got == _bounce_target(path), f"{path}: bounced to {got}, not {_bounce_target(path)}"


def test_visitor_home_offers_sign_in_and_hides_signed_in_links(anon, base_url):
    html = anon.get(base_url + "/", timeout=60).text
    assert 'class="mobile-topbar' in html, "the phone top bar is missing"
    assert 'href="/login/?next=/"' in html, "the home hero has no Sign in"
    nav = html[html.index('class="sidebar-nav-inner"'):]
    nav = nav[:nav.index("</nav>")]
    for href in ("/seek/search/", "/seek/samples/upload/", "/seek/assistant/"):
        assert f'href="{href}"' not in nav, f"a visitor's menu links to {href}"


@pytest.mark.parametrize("path", ["/blog/", "/search/", "/accounts/update/", "/password_reset/"])
def test_retired_mezzanine_pages_answer_404(anon, base_url, path):
    r = anon.get(base_url + path, timeout=60, allow_redirects=False)
    assert r.status_code == 404, f"{path} answered {r.status_code}; it is not part of NExtSEEK"


def test_accounts_login_is_the_seek_login(anon, base_url):
    r = anon.get(base_url + "/accounts/login/", timeout=60, allow_redirects=False)
    assert r.status_code == 200 and "auth-submit" in r.text, \
        "/accounts/login/ is not the SEEK sign-in form"


# --------------------------------------------------------------------------- #
# browser fixtures
# --------------------------------------------------------------------------- #

def _context(browser, profile, base_url, device, storage_state=None):
    ctx = browser.new_context(base_url=base_url, storage_state=storage_state, **device)
    _guard_context(ctx, profile)
    return ctx


@pytest.fixture
def phone_visitor(browser, profile, base_url):
    ctx = _context(browser, profile, base_url, PHONE)
    yield ctx.new_page()
    ctx.close()


@pytest.fixture
def phone_user(browser, profile, base_url, storage_state):
    ctx = _context(browser, profile, base_url, PHONE, storage_state)
    yield ctx.new_page()
    ctx.close()


@pytest.fixture
def desktop_user(browser, profile, base_url, storage_state):
    ctx = _context(browser, profile, base_url, DESKTOP, storage_state)
    yield ctx.new_page()
    ctx.close()


def _sign_in(page, creds):
    page.fill("input#username", creds[0])
    page.fill("input#password", creds[1])
    with page.expect_navigation(wait_until="domcontentloaded", timeout=120_000):
        page.click("button[type=submit].auth-submit")


def _in_view(locator, page) -> bool:
    box = locator.bounding_box()
    return bool(box) and box["y"] >= 0 and box["y"] + box["height"] <= page.viewport_size["height"] + 1


# --------------------------------------------------------------------------- #
# phone, visitor
# --------------------------------------------------------------------------- #

@pytest.mark.flow
def test_phone_visitor_can_sign_in_from_anywhere_on_home(phone_visitor):
    page = phone_visitor
    page.goto("/", wait_until="domcontentloaded")
    assert page.evaluate(_NO_SIDEWAYS), "home scrolls sideways at 390px"
    assert page.locator(".mobile-topbar .btn-signin").is_visible(), "no Sign in in the top bar"
    assert page.locator(".dash-hero .dash-signin").is_visible(), "no Sign in in the home hero"
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    assert _in_view(page.locator(".mobile-toggle"), page), "the menu scrolled away"
    page.locator(".mobile-toggle").click()
    page.wait_for_timeout(400)  # the drawer slides in
    assert _in_view(page.locator(".sidebar-foot .btn-signin"), page), \
        "the drawer's Sign in sits outside the visible area"


@pytest.mark.flow
@pytest.mark.parametrize("path", ["/login/", "/seek/help/"])
def test_phone_visitor_pages_do_not_scroll_sideways(phone_visitor, path):
    phone_visitor.goto(path, wait_until="domcontentloaded")
    assert phone_visitor.evaluate(_NO_SIDEWAYS), f"{path} scrolls sideways at 390px"


@pytest.mark.flow
def test_phone_login_shows_the_partner_logos(phone_visitor):
    phone_visitor.goto("/login/", wait_until="load")
    assert phone_visitor.locator(".auth-panel-foot img").is_visible()


@pytest.mark.flow
def test_signing_in_on_a_phone_returns_to_the_page_asked_for(phone_visitor, smoke_creds):
    page = phone_visitor
    page.goto("/seek/search/?tab=advanced", wait_until="domcontentloaded")
    assert urlsplit(page.url).path == "/login/", "a visitor was not sent to sign in"
    _sign_in(page, smoke_creds)
    got = urlsplit(page.url)
    assert (got.path, got.query) == ("/seek/search/", "tab=advanced"), \
        f"signed in to {got.path}?{got.query}, not the page asked for"


@pytest.mark.flow
def test_signing_in_never_follows_an_off_site_next(phone_visitor, smoke_creds, base_url):
    page = phone_visitor
    page.goto("/login/?next=https://example.org/", wait_until="domcontentloaded")
    _sign_in(page, smoke_creds)
    assert page.url.rstrip("/") == base_url.rstrip("/"), "an off-site next was followed"


# --------------------------------------------------------------------------- #
# phone, signed in
# --------------------------------------------------------------------------- #

@pytest.mark.flow
@pytest.mark.parametrize("path", PHONE_PAGES)
def test_phone_pages_do_not_scroll_sideways(phone_user, path):
    phone_user.goto(path, wait_until="load", timeout=120_000)
    assert phone_user.evaluate(_NO_SIDEWAYS), f"{path} scrolls sideways at 390px"


@pytest.mark.flow
def test_phone_new_sample_is_full_width_and_says_desktop_only(phone_user):
    page = phone_user
    page.goto("/", wait_until="domcontentloaded")
    page.locator(".mobile-toggle").click()
    page.wait_for_timeout(400)
    cta, box = page.locator(".qa-cta"), page.locator(".qa-input")
    assert cta.locator(".desktop-only-hint").is_visible(), "no desktop-only hint in the drawer"
    assert abs(cta.bounding_box()["width"] - box.bounding_box()["width"]) <= 4, \
        "+ New sample is narrower than the UID box above it"
    assert page.locator(".dash-action.accent .desktop-only-hint").is_visible()


@pytest.mark.flow
def test_phone_project_page_opens_the_sample_flow_full_screen(phone_user, discovered):
    if not discovered.get("seek_project_id"):
        pytest.skip("no project could be discovered in this environment")
    page = phone_user
    page.goto(f"/seek/projects/{discovered['seek_project_id']}/", wait_until="domcontentloaded")
    if page.locator(".project-diagram-open").count() == 0:
        pytest.skip("the smoke account cannot open the discovered project")
    assert page.locator(".project-diagram-open").is_visible()
    assert not page.locator("iframe.project-diagram").is_visible(), \
        "the cramped inline diagram still shows on a phone"


@pytest.mark.flow
def test_phone_nessie_is_one_full_height_frame(phone_user):
    page = phone_user
    page.goto("/seek/assistant/", wait_until="domcontentloaded", timeout=120_000)
    page.get_by_test_id("chat-input").wait_for(state="visible", timeout=60_000)
    assert page.evaluate("() => document.documentElement.scrollHeight <= window.innerHeight + 1"), \
        "the Nessie page scrolls on top of the chat"
    assert page.evaluate(_NO_SIDEWAYS), "the Nessie page scrolls sideways"
    assert _in_view(page.get_by_test_id("chat-input"), page), "the composer is out of view"
    assert page.get_by_role("button", name="Site menu").is_visible(), "no way to the site menu"
    assert not page.locator('aside[aria-label="Saved chats"]').is_visible(), \
        "the saved-chats rail takes the width on a phone"


# --------------------------------------------------------------------------- #
# desktop, signed in
# --------------------------------------------------------------------------- #

@pytest.mark.flow
@pytest.mark.parametrize("path", ["/seek/search/", "/seek/datafile/query/", "/seek/sop/query/",
                                  "/seek/samples/upload/", "/seek/data/upload/"])
def test_every_inline_button_handler_is_defined(desktop_user, path):
    desktop_user.goto(path, wait_until="load", timeout=120_000)
    missing = desktop_user.evaluate(_HANDLERS_DEFINED)
    assert missing == [], f"{path}: buttons call undefined functions {sorted(set(missing))}"


@pytest.mark.flow
def test_search_toolbars_have_no_publish_button(desktop_user):
    desktop_user.goto("/seek/search/", wait_until="load", timeout=120_000)
    assert desktop_user.get_by_text("Publish samples to FairdomHub").count() == 0
    assert desktop_user.evaluate("() => typeof advanced_deleteSamples === 'function'")


@pytest.mark.flow
@pytest.mark.parametrize("width", [1440, 1100])
def test_sample_flow_frame_never_shows_scrollbars(browser, profile, base_url, storage_state,
                                                  discovered, width):
    if not discovered.get("seek_project_id"):
        pytest.skip("no project could be discovered in this environment")
    device = {"viewport": {"width": width, "height": 900}}
    ctx = _context(browser, profile, base_url, device, storage_state)
    try:
        page = ctx.new_page()
        page.goto(f"/seek/projects/{discovered['seek_project_id']}/", wait_until="load", timeout=120_000)
        frame_el = page.locator("iframe.project-diagram")
        if frame_el.count() == 0:
            pytest.skip("the smoke account cannot open the discovered project")
        frame_el.scroll_into_view_if_needed()
        frame = frame_el.element_handle().content_frame()
        frame.wait_for_load_state("load", timeout=60_000)
        if frame.locator("#cy").count() == 0:
            pytest.skip("the discovered project has no recorded sample-type connections")
        frame.wait_for_function("() => window.cy && window.cy.nodes().length >= 0", timeout=60_000)
        page.wait_for_timeout(1500)  # past the debounced resize
        fits = frame.evaluate("""() => { const d = document.documentElement;
            return d.scrollHeight <= d.clientHeight && d.scrollWidth <= d.clientWidth; }""")
        assert fits, f"the Sample flow frame overflows at {width}px, so it can show scrollbars"
    finally:
        ctx.close()


@pytest.mark.flow
def test_desktop_nessie_page_does_not_scroll(desktop_user):
    page = desktop_user
    page.goto("/seek/assistant/", wait_until="domcontentloaded", timeout=120_000)
    page.get_by_test_id("chat-input").wait_for(state="visible", timeout=60_000)
    assert page.evaluate("() => document.documentElement.scrollHeight <= window.innerHeight + 1")
    assert not page.get_by_role("button", name="Site menu").is_visible(), \
        "the phone-only Menu button shows on a desktop"

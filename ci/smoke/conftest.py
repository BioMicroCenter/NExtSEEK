"""Shared fixtures for the NExtSEEK post-deploy smoke suite.

This suite runs OUTSIDE the application container, against a deployed stack, over
HTTP, the way a user does. It needs pytest, requests and playwright, and none of
the application's own dependencies:

    uv run --no-project --with pytest --with requests --with playwright \
      pytest ci/smoke/ --base-url http://127.0.0.1:8000

Two authentication modes exist and they are NOT interchangeable. Both were
verified against a live stack:

  /nextseek_api/*   HTTP Basic. DRF accepts it and forwards it to SEEK.
  /seek/*           A real session cookie from a POST to /login/. These views read
                    request.session['username'], which Basic auth never populates,
                    so a Basic-authenticated request to a /seek/ page returns a 302
                    to /login/ and a sweep that follows redirects calls it healthy.

They must never share a requests.Session: DRF stops at the first authenticator
that succeeds, and session auth outranks Basic in most viewsets, so a stray
sessionid cookie silently changes which identity is under test.

Both accounts must have logged in through /login/ at least once on each box before
anything here works. BasicAuthentication validates against Django's auth_user
table, and that row is only created by the login view.

The box declares its profile in CI_BOX_PROFILE, and an absent value means prod.
--profile may only narrow what the box declares; widening needs --force-profile
together with CI_FORCE_PROFILE_CONFIRM=yes, so it cannot happen by accident.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.routes import PROFILES
from ci.smoke.client import GuardedSession

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_CRED_FILE = Path.home() / ".config" / "nextseek" / "ci.env"

# Console messages that fire on a healthy page and would otherwise switch the
# console-error check off within a week. See the spec, section 7.2.
CONSOLE_ALLOWLIST = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
    "cdn.skypack.dev",
    # Vite bundle built with base:"/" injects modulepreloads at the site root.
    "/assets/js/",
    # Project-card avatars resolve to a same-origin path when SEEK_PUBLIC_URL is
    # empty; both tags carry onerror handlers and degrade cleanly.
    "avatar-images",
)


# --------------------------------------------------------------------------- #
# options
# --------------------------------------------------------------------------- #

def pytest_addoption(parser):
    g = parser.getgroup("nextseek-smoke")
    g.addoption("--base-url", default=os.environ.get("CI_BASE_URL", DEFAULT_BASE_URL),
                help=f"Stack to test. Must be nginx, not gunicorn. Default {DEFAULT_BASE_URL}")
    g.addoption("--wait-ready", action="store_true",
                help="Run the readiness gate before any test. Use after a rebuild; "
                     "skip it for local iteration.")
    g.addoption("--ready-floor", type=int, default=300,
                help="Seconds to wait before the first probe (default 300).")
    g.addoption("--ready-ceiling", type=int, default=600,
                help="Give up after this many seconds (default 600).")
    g.addoption("--ready-poll", type=int, default=10,
                help="Seconds between probes after the floor (default 10).")
    g.addoption("--ready-confirmations", type=int, default=3,
                help="Consecutive successes required (default 3). Sustained "
                     "readiness, not a momentary one.")
    g.addoption("--strict-console", action="store_true",
                help="Fail a flow on any uncaught console error not in "
                     "CONSOLE_ALLOWLIST. Off by default so the first runs report "
                     "what is actually there before the gate goes live.")
    g.addoption("--headed", action="store_true", help="Run the browser headed.")
    g.addoption("--profile", default=None,
                help="Narrow the profile below what the box declares. Cannot widen.")
    g.addoption("--force-profile", default=None,
                help="Widen the profile above what the box declares. Requires "
                     "CI_FORCE_PROFILE_CONFIRM=yes. Never use in a workflow file.")


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #

_PROFILE_RANK = {"prod": 0, "dev": 1, "local": 2}


def _valid_profile(source: str, value: str) -> str:
    """Return `value`, or stop the run naming what was allowed.

    Every input is checked, the box's own declaration included. Without this an
    unknown name reaches _PROFILE_RANK and raises a bare KeyError, which reads as
    a harness fault rather than as the typo it is.
    """
    if value not in PROFILES:
        pytest.exit(
            f"{source} {value!r} is not a profile. Allowed: {', '.join(PROFILES)}.",
            returncode=2,
        )
    return value


def resolve_profile(config) -> str:
    """Resolve the active profile.

    The box declares a default; the command line may only narrow it. Widening
    needs --force-profile AND an environment acknowledgement, so it cannot be
    reached by a typo or by copying a line out of a workflow file.

    A module-level function rather than only a fixture, because collection-time
    hooks need the same answer and no fixture exists yet at collection. The
    result is memoised on the config object so the forced-profile banner is
    printed once per run rather than once per caller.
    """
    cached = getattr(config, "_nextseek_profile", None)
    if cached is not None:
        return cached

    declared = _valid_profile(
        "CI_BOX_PROFILE",
        os.environ.get("CI_BOX_PROFILE", "prod"),   # absent means prod: fail closed
    )
    forced = config.getoption("--force-profile")
    if forced:
        forced = _valid_profile("--force-profile", forced)
        if os.environ.get("CI_FORCE_PROFILE_CONFIRM") != "yes":
            pytest.exit(
                f"--force-profile {forced} needs CI_FORCE_PROFILE_CONFIRM=yes. "
                f"The box declares {declared!r}.", returncode=2)
        print(f"\n*** FORCED PROFILE {forced!r} on a box declaring {declared!r} ***\n")
        resolved = forced
    else:
        asked = config.getoption("--profile")
        if not asked:
            resolved = declared
        else:
            asked = _valid_profile("--profile", asked)
            if _PROFILE_RANK[asked] > _PROFILE_RANK[declared]:
                pytest.exit(
                    f"--profile {asked!r} would widen past the box's {declared!r}. "
                    f"Use --force-profile if that is deliberate.", returncode=2)
            resolved = asked

    config._nextseek_profile = resolved
    return resolved


@pytest.fixture(scope="session")
def profile(pytestconfig) -> str:
    """The profile every client in this file is built for. See resolve_profile."""
    return resolve_profile(pytestconfig)


def pytest_configure(config):
    config.addinivalue_line("markers", "write: mutates the database.")
    config.addinivalue_line("markers", "flow: drives a real browser.")
    # Resolve before collection. A run whose tests happen not to request the
    # fixture would otherwise never evaluate the command line at all, so a
    # refusal has to happen here to be reliable -- and it costs nothing.
    resolve_profile(config)


def pytest_collection_modifyitems(config, items):
    """Deselect the write lane unless it was explicitly asked for."""
    if config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="write lane is opt-in: run with -m write")
    for item in items:
        if "write" in item.keywords:
            item.add_marker(skip)


# --------------------------------------------------------------------------- #
# credentials
# --------------------------------------------------------------------------- #

def _read_cred_file() -> dict[str, str]:
    path = Path(os.environ.get("NEXTSEEK_CI_ENV", DEFAULT_CRED_FILE))
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _cred(pair: tuple[str, str]) -> tuple[str, str] | None:
    """Environment wins over the file, so CI can override without editing it."""
    fromfile = _read_cred_file()
    user = os.environ.get(pair[0]) or fromfile.get(pair[0])
    pwd = os.environ.get(pair[1]) or fromfile.get(pair[1])
    return (user, pwd) if user and pwd else None


@pytest.fixture(scope="session")
def base_url(pytestconfig) -> str:
    value = pytestconfig.getoption("--base-url").rstrip("/")
    if not urlsplit(value).scheme:
        pytest.exit(
            f"--base-url {value!r} has no scheme. Every client is bound to this "
            f"URL and compares scheme, host and port against it, so a bare host "
            f"refuses every request without saying why. Write it as "
            f"http://{value}.", returncode=2)
    return value


@pytest.fixture(scope="session")
def smoke_creds() -> tuple[str, str]:
    c = _cred(("CI_SMOKE_USER", "CI_SMOKE_PASS"))
    if not c:
        pytest.skip("CI_SMOKE_USER/CI_SMOKE_PASS not set and not in ci.env")
    return c


@pytest.fixture(scope="session")
def write_creds() -> tuple[str, str]:
    c = _cred(("CI_WRITE_USER", "CI_WRITE_PASS"))
    if not c:
        pytest.skip("CI_WRITE_USER/CI_WRITE_PASS not set and not in ci.env")
    return c


# --------------------------------------------------------------------------- #
# readiness gate
# --------------------------------------------------------------------------- #

def _probe_once(base: str, creds: tuple[str, str]) -> tuple[bool, str]:
    """One readiness probe. Returns (ok, a description of what was seen).

    Two checks, deliberately different in what they prove:
      GET /login/                     nginx is routing and Django is serving
      GET /nextseek_api/people/current/ (auth)
                                      the database is reachable AND SEEK answers

    The second must be authenticated. An anonymous /nextseek_api/ returns 401,
    which proves only that DRF is alive. There is no CACHES block in
    dmac/settings.py, so sessions use Django's database backend, which makes an
    authenticated 200 the cheapest thing that actually proves MySQL is up.
    """
    try:
        r1 = requests.get(f"{base}/login/", timeout=15, allow_redirects=False)
    except requests.RequestException as exc:
        return False, f"/login/ raised {type(exc).__name__}: {exc}"
    if r1.status_code != 200:
        return False, f"/login/ returned {r1.status_code}"

    try:
        s = requests.Session()
        s.auth = creds
        r2 = s.get(f"{base}/nextseek_api/people/current/", timeout=30)
    except requests.RequestException as exc:
        return False, f"people/current raised {type(exc).__name__}: {exc}"
    if r2.status_code != 200:
        return False, f"people/current returned {r2.status_code}"
    return True, "login 200, people/current 200"


@pytest.fixture(scope="session", autouse=True)
def stack_ready(pytestconfig, base_url, request):
    """Block until the stack is sustainably up, or fail saying what was last seen.

    A blind sleep fails two ways: it wastes minutes when the stack is up quickly,
    and it still reports green if the stack comes up and dies moments later. This
    requires N consecutive successes.

    nginx answers 502 instantly, so a naive retry loop burns every attempt in about
    two seconds. The explicit sleep in the loop below is what prevents that.
    """
    if not pytestconfig.getoption("--wait-ready"):
        return
    creds = _cred(("CI_SMOKE_USER", "CI_SMOKE_PASS"))
    if not creds:
        pytest.skip("readiness gate needs CI_SMOKE_USER/CI_SMOKE_PASS")

    floor = pytestconfig.getoption("--ready-floor")
    ceiling = pytestconfig.getoption("--ready-ceiling")
    poll = pytestconfig.getoption("--ready-poll")
    need = pytestconfig.getoption("--ready-confirmations")

    reporter = request.config.pluginmanager.get_plugin("terminalreporter")

    def say(msg):
        if reporter:
            reporter.write_line(f"[readiness] {msg}")

    say(f"floor {floor}s, then polling every {poll}s for {need} consecutive "
        f"successes, ceiling {ceiling}s")
    time.sleep(floor)

    started = time.monotonic()
    streak = 0
    last = "no probe completed"
    while time.monotonic() - started < (ceiling - floor):
        ok, last = _probe_once(base_url, creds)
        streak = streak + 1 if ok else 0
        say(f"{'ok ' if ok else 'not ready'} ({streak}/{need}): {last}")
        if streak >= need:
            say(f"ready after {int(time.monotonic() - started) + floor}s")
            return
        time.sleep(poll)

    pytest.exit(
        f"stack not ready within {ceiling}s. Last status seen: {last}",
        returncode=1,
    )


# --------------------------------------------------------------------------- #
# HTTP clients
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def api(profile, base_url, smoke_creds) -> GuardedSession:
    """Basic-authenticated client for /nextseek_api/*. Never touches /login/.

    Keeping this session cookie-free is load-bearing: a sessionid would outrank
    the Basic header and silently change which identity is under test.
    """
    s = GuardedSession(profile=profile, base_url=base_url)
    s.auth = smoke_creds
    s.headers["Accept"] = "application/json"
    return s


@pytest.fixture(scope="session")
def anon(profile, base_url) -> GuardedSession:
    """Unauthenticated client, for the routes that must answer without credentials.

    No auth and no Accept header, so it looks like a visitor arriving with
    nothing. Sharing the api fixture instead would prove the opposite of what
    those checks claim.
    """
    return GuardedSession(profile=profile, base_url=base_url)


@pytest.fixture(scope="session")
def web(profile, base_url, smoke_creds) -> GuardedSession:
    """Session-cookie client for /seek/* pages.

    Those views read request.session['username'], which only the login view
    writes, so Basic auth is not sufficient for them.
    """
    s = GuardedSession(profile=profile, base_url=base_url)
    s.get(f"{base_url}/login/", timeout=30)
    token = s.cookies.get("csrftoken")
    assert token, "GET /login/ did not set a csrftoken cookie"
    r = s.post(
        f"{base_url}/login/",
        data={
            "username": smoke_creds[0],
            "password": smoke_creds[1],
            "no-expire": "yes",
            "csrfmiddlewaretoken": token,
        },
        headers={"Referer": base_url},
        allow_redirects=False,
        timeout=90,   # login shells out to curl against SEEK Rails
    )
    assert r.status_code == 302, (
        f"login as {smoke_creds[0]} returned {r.status_code}, expected 302. "
        "A 200 here means the credentials were rejected: this view re-renders "
        "the login page on failure rather than returning 4xx."
    )
    assert s.cookies.get("sessionid"), "login did not set a sessionid cookie"
    return s


# --------------------------------------------------------------------------- #
# browser
# --------------------------------------------------------------------------- #

def _guard_context(ctx, profile: str) -> None:
    """Apply the prod non-GET rule at the browser layer.

    Same rule as GuardedSession: a page that tries to POST under the prod profile
    gets an aborted request, not a live one. Method only, deliberately: a real
    page pulls fonts, bundles and XHR that the registry does not describe, and
    test_flows.py installs its own page-level handler on top of this one. The
    single carve-out is the login form post, which ci/routes.py grants /login/
    for the same reason -- authenticating is a precondition of reading.
    """
    if profile != "prod":
        return
    ctx.route("**/*", lambda route: (
        route.continue_()
        if route.request.method == "GET"
        or urlsplit(route.request.url).path == "/login/"
        else route.abort()
    ))


@pytest.fixture(scope="session")
def browser(pytestconfig):
    pw = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    with pw.sync_playwright() as p:
        b = p.chromium.launch(headless=not pytestconfig.getoption("--headed"))
        yield b
        b.close()


@pytest.fixture(scope="session")
def storage_state(browser, profile, base_url, smoke_creds, tmp_path_factory):
    """Log in once in a real browser and reuse the cookies for every flow.

    Do NOT set an HTTP Basic-Auth header on the browser context instead: it leaks
    onto the CDN font requests and triggers APPEND_SLASH redirects on /static/,
    which breaks the bundle. Session cookie only.
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    _guard_context(ctx, profile)
    page = ctx.new_page()
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("input#username", smoke_creds[0])
    page.fill("input#password", smoke_creds[1])
    with page.expect_navigation(wait_until="domcontentloaded", timeout=120_000):
        page.click("button[type=submit].auth-submit")
    assert "/login" not in page.url, (
        f"still on the login page after submitting: {page.url}"
    )
    path = tmp_path_factory.mktemp("auth") / "state.json"
    ctx.storage_state(path=str(path))
    ctx.close()
    return str(path)


@pytest.fixture
def page(browser, storage_state, profile, base_url, pytestconfig, request):
    """An authenticated page that records console errors and failed requests.

    Viewport is 1440x900 deliberately. searchAdvanced.html renders two separate
    UIs into one document and hides the desktop one below 768px, so a narrow
    viewport makes every EasyUI selector display:none.
    """
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        storage_state=storage_state,
        base_url=base_url,
    )
    p = ctx.new_page()
    _guard_context(ctx, profile)
    errors: list[str] = []
    p.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
         if m.type == "error" else None)
    p.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    p.console_errors = errors  # type: ignore[attr-defined]

    yield p

    unexpected = [e for e in errors if not any(a in e for a in CONSOLE_ALLOWLIST)]
    if unexpected:
        report = "\n  ".join(unexpected)
        if pytestconfig.getoption("--strict-console"):
            ctx.close()
            pytest.fail(f"{len(unexpected)} uncaught console error(s):\n  {report}")
        else:
            reporter = request.config.pluginmanager.get_plugin("terminalreporter")
            if reporter:
                reporter.write_line(
                    f"[console] {request.node.name}: {len(unexpected)} "
                    f"non-allowlisted error(s):\n  {report}"
                )
    ctx.close()

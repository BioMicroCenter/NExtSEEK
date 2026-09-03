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
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci import routes as ci_routes
from ci.routes import PLACEHOLDERS, PROFILES
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

# Derived, never restated. PROFILES is ordered widest-first -- ("local", "dev",
# "prod") -- so counting from the narrow end makes a HIGHER rank a WIDER profile,
# which is the comparison the narrowing rule needs. A profile added to the
# registry and forgotten here would pass _valid_profile and then raise a bare
# KeyError below; deriving it means there is nothing to forget.
_PROFILE_RANK = {name: len(PROFILES) - 1 - i for i, name in enumerate(PROFILES)}


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
    asked = config.getoption("--profile")
    if forced and asked:
        pytest.exit(
            f"--profile {asked!r} and --force-profile {forced!r} were both given. "
            f"Pass exactly one: which of them wins should not be a question anyone "
            f"has to look up.", returncode=2)
    if forced:
        forced = _valid_profile("--force-profile", forced)
        if os.environ.get("CI_FORCE_PROFILE_CONFIRM") != "yes":
            pytest.exit(
                f"--force-profile {forced} needs CI_FORCE_PROFILE_CONFIRM=yes. "
                f"The box declares {declared!r}.", returncode=2)
        print(f"\n*** FORCED PROFILE {forced!r} on a box declaring {declared!r} ***\n")
        resolved = forced
    else:
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
    config.addinivalue_line(
        "markers",
        "profiles(*names): only run under these box profiles; skipped under any other.",
    )
    # --help and --version reach _do_configure() from a caller that does not
    # catch Exit, so a refusal raised here surfaces as a traceback and rc=1
    # instead of the message. Printing the options must always work.
    if getattr(config.option, "help", False) or getattr(config.option, "version", False):
        return
    # Resolve before collection. A run whose tests happen not to request the
    # fixture would otherwise never evaluate the command line at all, so a
    # refusal has to happen here to be reliable -- and it costs nothing.
    resolve_profile(config)


def pytest_collection_modifyitems(config, items):
    """Two independent gates, in this order.

    The PROFILE gate runs unconditionally. GuardedSession refuses a non-GET under
    prod before it is sent, which is the right answer for a requests client and the
    wrong one for a browser flow: the page's POST is aborted at the network layer
    and the test then waits out its own response timeout and fails red, five
    minutes later, for a rule the suite is enforcing correctly. A test whose SHAPE
    is a write declares the profiles it belongs to and is skipped elsewhere.

    The WRITE-LANE gate is the pre-existing opt-in and stays subject to -m. The
    profile gate must not be: `-m write` on a prod box would otherwise re-admit
    every browser flow the first gate had just excluded.
    """
    active = resolve_profile(config)
    for item in items:
        marker = item.get_closest_marker("profiles")
        if marker and active not in marker.args:
            item.add_marker(pytest.mark.skip(
                reason=(
                    f"needs profile {' or '.join(marker.args)}; "
                    f"this box declares {active!r}"
                )
            ))

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
# terminal
# --------------------------------------------------------------------------- #

def report_to_terminal(config, line: str) -> None:
    """Write one line to the operator's terminal from inside a fixture.

    A fixture runs inside a test's setup or teardown phase, where pytest's output
    capture is on. On a pipe the terminal reporter's write slips through; on a
    real terminal it lands in the capture buffer and is thrown away with the
    passing test, which is how a five-minute readiness floor looked like a hang.
    Suspending capture around the write is pytest's own idiom for progress output
    from fixtures.
    """
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    capman = config.pluginmanager.get_plugin("capturemanager")
    if capman is None:
        reporter.write_line(line)
        return
    with capman.global_and_fixture_disabled():
        reporter.write_line(line)


def wait_out_floor(floor: int, *, say, sleep=time.sleep, step: int = 30) -> None:
    """Sleep out the readiness floor in steps, saying how much is left after each.

    The total slept is always exactly `floor`; nothing is said after the last
    step, because the probe lines take over from there.
    """
    remaining = floor
    while remaining > 0:
        chunk = min(step, remaining)
        sleep(chunk)
        remaining -= chunk
        if remaining > 0:
            say(f"floor: {remaining}s remaining")


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


def resolve_readiness_credentials(config) -> tuple[str, str] | None:
    """The credentials the readiness gate will probe with, or None if no gate runs.

    A module-level function rather than fixture-inline code, so the decision can
    be asserted without starting a session -- the same reason resolve_profile is
    one.

    The gate probes an AUTHENTICATED endpoint, so with no credentials it cannot
    do its job. Skipping there is the wrong answer: every other test skips for
    the same missing credentials, pytest exits 0, and the caller that asked for a
    readiness gate -- `startup rebuild`, which always passes --wait-ready -- reads
    that as "CI passed" having proved nothing about the deploy. Exit 2 instead, so
    a box with no ci.env fails loudly rather than green.

    Without --wait-ready there is no gate and nothing to be silent about: local
    iteration with no credentials keeps degrading to the per-test skips.
    """
    if not config.getoption("--wait-ready"):
        return None
    creds = _cred(("CI_SMOKE_USER", "CI_SMOKE_PASS"))
    if not creds:
        pytest.exit(
            "readiness gate needs CI_SMOKE_USER/CI_SMOKE_PASS (env or "
            "~/.config/nextseek/ci.env); a gate with no credentials cannot do "
            "its job", returncode=2)
    return creds


@pytest.fixture(scope="session", autouse=True)
def stack_ready(pytestconfig, base_url, request, record_testsuite_property):
    """Block until the stack is sustainably up, or fail saying what was last seen.

    A blind sleep fails two ways: it wastes minutes when the stack is up quickly,
    and it still reports green if the stack comes up and dies moments later. This
    requires N consecutive successes.

    nginx answers 502 instantly, so a naive retry loop burns every attempt in about
    two seconds. The explicit sleep in the loop below is what prevents that.
    """
    creds = resolve_readiness_credentials(pytestconfig)
    if creds is None:
        return

    floor = pytestconfig.getoption("--ready-floor")
    ceiling = pytestconfig.getoption("--ready-ceiling")
    poll = pytestconfig.getoption("--ready-poll")
    need = pytestconfig.getoption("--ready-confirmations")

    def say(msg):
        report_to_terminal(request.config, f"[readiness] {msg}")

    say(f"floor {floor}s, then polling every {poll}s for {need} consecutive "
        f"successes, ceiling {ceiling}s")
    wait_out_floor(floor, say=say)

    started = time.monotonic()
    streak = 0
    last = "no probe completed"
    while time.monotonic() - started < (ceiling - floor):
        ok, last = _probe_once(base_url, creds)
        streak = streak + 1 if ok else 0
        say(f"{'ok ' if ok else 'not ready'} ({streak}/{need}): {last}")
        if streak >= need:
            ready_after = int(time.monotonic() - started) + floor
            say(f"ready after {ready_after}s")
            # Into the junit file, so the shim can report it in its summary line.
            record_testsuite_property("readiness_seconds", str(ready_after))
            record_testsuite_property("readiness_floor", str(floor))
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

    No credentials, and no JSON Accept header -- requests still sends its default
    `Accept: */*` -- so it looks like a visitor arriving with nothing. Sharing the
    api fixture instead would prove the opposite of what those checks claim.
    """
    return GuardedSession(profile=profile, base_url=base_url)


@pytest.fixture(scope="session")
def web(profile, base_url, smoke_creds) -> GuardedSession:
    """Session-cookie client for /seek/* pages.

    Those views read request.session['username'], which only the login view
    writes, so Basic auth is not sufficient for them.
    """
    s = GuardedSession(profile=profile, base_url=base_url)
    # Not followed: send() guards every hop, so a /login/ that ever redirected
    # would fail session setup with a ProfileViolation about wherever it landed,
    # in place of the csrftoken assertion two lines below that says what is wrong.
    s.get(f"{base_url}/login/", timeout=30, allow_redirects=False)
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
# run-time discovery of the registry's {placeholders}
# --------------------------------------------------------------------------- #

# Where each PLACEHOLDERS name comes from. Eight of the thirteen are the first
# id of a JSON:API list, so they are declared as data rather than as eight
# copies of the same three lines; the other five need their own request and are
# resolved below. Keyed by placeholder name so ci/smoke/test_registry_contents.py can
# assert, with no stack running, that this covers exactly the vocabulary
# ci/routes.py declares. The two cannot drift apart silently: an undeclared name
# is a KeyError mid-sweep and an unresolved one is a route that skips forever.
_JSONAPI_LIST_SOURCE: dict[str, str] = {
    "assay_id":         "/nextseek_api/assays/",
    "data_file_id":     "/nextseek_api/data_files/",
    "investigation_id": "/nextseek_api/investigations/",
    "person_id":        "/nextseek_api/people/",
    "sample_type_id":   "/nextseek_api/sample_types/",
    "seek_project_id":  "/nextseek_api/projects/",
    "sop_id":           "/nextseek_api/sops/",
    "study_id":         "/nextseek_api/studies/",
}

# The attributes endpoint answers {"attributes": [...]}, not a JSON:API document.
_ATTRIBUTE_SOURCE = "/nextseek_api/attributes/"

# Both sample names come from one request: the query the search page itself makes.
_SAMPLE_SOURCE = "/seek/searchAdvanced/"

# The value every sample-discovery search looks for. It has ONE constraint and
# it is not "exists in the database": advanced search is project-scoped, so the
# term must appear in a project CI_SMOKE_USER is a member of. Getting that wrong
# does not error -- the search returns 0 rows and the failure reads as a broken
# search rather than as a term nobody can see.
#
# Measured 2026-09-03 on the production-seeded local stack: the previous term
# "Uterus" matches 436 samples, all of them in projects 7, 6 and 4, while
# CI_SMOKE_USER (charlie-test-3, person 84) is a member of projects 2 and 13
# only. Zero overlap, so the search correctly returned 0 and
# test_advanced_search_returns_rendered_results failed while project gating was
# working exactly as designed. "Lung" appears 627 times in project 2.
#
# To re-derive after a reseed: find the projects the smoke account belongs to
# (group_memberships -> work_groups.project_id in the seek DB), then pick a
# common json_metadata value from samples in those projects.
SMOKE_SEARCH_TERM = "Lung"

# Both catalog placeholders come from the list page that publishes them. Scraped,
# not read from an API, for the reason ci.routes.PLACEHOLDERS states: a value
# taken off the list page is by construction one the detail route can resolve,
# where a code from /nextseek_api/sample_types/ could name a type with no curated
# context row and would 404.
_SAMPLE_TYPE_CATALOG = "/seek/sampletypes/"
_ASSAY_CATALOG = "/seek/assays/"

DISCOVERED_KEYS = frozenset(_JSONAPI_LIST_SOURCE) | {
    "attribute_id", "sample_id", "sample_uid",
    "sample_type_code", "assay_slug",
}


def _profile_permits(profile: str, base_url: str, path: str) -> bool:
    """Is this discovery request one the active profile permits?

    Asked before the request rather than caught after it. The guard raises on a
    route the profile does not enable, and a raise inside a session fixture takes
    every test in the run down with it -- where returning None here costs only the
    routes that needed that one value. Every source below happens to be enabled for
    all three profiles today, /seek/searchAdvanced/ included, so nothing is skipped
    for this reason; the check is what keeps that a property of the registry rather
    than an assumption this fixture is making about it.
    """
    route = ci_routes.match(base_url + path)
    return route is not None and profile in route.profiles


def _first_id(client, base_url: str, path: str, key: str) -> str | None:
    """The first id of a list endpoint, or None when there is nothing to take.

    None rather than a failure for both the empty list and the unhappy status:
    every one of these endpoints is itself a registry route with its own T0 case,
    so a broken list is already reported there. Failing here instead would report
    it once, as a session error covering every route in the sweep, which says less.
    """
    r = client.get(base_url + path, timeout=90, allow_redirects=False,
                   headers={"Accept": "application/json"})
    if r.status_code != 200:
        return None
    try:
        items = r.json().get(key) or []
    except ValueError:
        return None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    value = items[0].get("id")
    return None if value is None else str(value)


def _first_catalog_segment(client, base_url: str, path: str) -> str | None:
    """The first path segment linked from a catalog list page, or None.

    None for every ordinary reason a catalog can be empty: the context table is
    absent on this stack, or it is present and holds no rows. Both are declared
    states, and both should skip the detail route rather than fail the sweep.
    """
    r = client.get(base_url + path, timeout=90, allow_redirects=False)
    if r.status_code != 200:
        return None
    match = re.search(r'href="' + re.escape(path) + r'([^"/]+)/"', r.text)
    return match.group(1) if match else None


@pytest.fixture(scope="session")
def discovered(profile, api, web, base_url) -> dict[str, str | None]:
    """Real values for the registry's {placeholders}, found at run time.

    Ids are deployment-specific -- the seed, dev and production disagree about
    all of them -- so nothing here may be hard-coded. A name this environment has
    no value for is None, and T0 skips only the routes whose path needs it; the
    seed carries no data files, so data_file_id is routinely None.

    Requests go through the guarded clients, so every path touched here is a
    declared route and is checked against the profile before it is sent.
    """
    found: dict[str, str | None] = {
        name: (_first_id(api, base_url, path, "data")
               if _profile_permits(profile, base_url, path) else None)
        for name, path in _JSONAPI_LIST_SOURCE.items()
    }
    found["attribute_id"] = (
        _first_id(api, base_url, _ATTRIBUTE_SOURCE, "attributes")
        if _profile_permits(profile, base_url, _ATTRIBUTE_SOURCE) else None
    )

    # The sample pair. This is the query the advanced-search grid itself issues,
    # and the same one test_flows.py's a_sample fixture uses: a bare GET of this
    # view is a 500, so the full filter set is not decoration.
    found["sample_id"] = found["sample_uid"] = None
    if _profile_permits(profile, base_url, _SAMPLE_SOURCE):
        r = web.get(
            base_url + _SAMPLE_SOURCE,
            params={
                "sampletype_id": "", "attribute": "none", "filter_logic": "AND",
                "filter_searchValue": "", "filter_searchText": SMOKE_SEARCH_TERM,
                "filter_matchType": "PARTIAL",
            },
            timeout=180,
        )
        rows = []
        if r.status_code == 200:
            try:
                rows = r.json().get("rows") or []
            except ValueError:
                rows = []
        # Guarded the same way as _first_id, and for the same reason: a malformed
        # row is one placeholder resolving to None, not a KeyError inside a
        # session fixture that would take every test in the run with it.
        if rows and isinstance(rows[0], dict) and rows[0].get("id") is not None:
            found["sample_id"] = str(rows[0]["id"])
            # The grid renders the UID as a link, so the raw field carries markup.
            found["sample_uid"] = re.sub(r"<[^>]+>", "", str(rows[0].get("uid", ""))).strip() or None

    # The two catalog pages publish their own detail links, so the placeholder is
    # scraped from the page it will be used against. `web`, not `api`: these are
    # HTML pages behind the session login.
    found["sample_type_code"] = (
        _first_catalog_segment(web, base_url, _SAMPLE_TYPE_CATALOG)
        if _profile_permits(profile, base_url, _SAMPLE_TYPE_CATALOG) else None
    )
    found["assay_slug"] = (
        _first_catalog_segment(web, base_url, _ASSAY_CATALOG)
        if _profile_permits(profile, base_url, _ASSAY_CATALOG) else None
    )

    assert set(found) == set(PLACEHOLDERS), (
        "this fixture and ci.routes.PLACEHOLDERS have drifted apart.\n"
        f"  resolved but not declared: {sorted(set(found) - set(PLACEHOLDERS))}\n"
        f"  declared but not resolved: {sorted(set(PLACEHOLDERS) - set(found))}"
    )
    return found


# --------------------------------------------------------------------------- #
# browser
# --------------------------------------------------------------------------- #

_URL_IN_TEXT = re.compile(r"https?://\S+")


def _redact_console(text: str, profile: str) -> str:
    """Strip URLs out of a console line before it is printed. Prod only.

    A console error is application text, and the application writes its own URLs
    into it: a failed XHR reports the URL it asked for, and under prod that URL
    carries the identifiers the discovery fixture resolved out of production data.
    The line is printed to a CI log either way -- by the reporter, or inside a
    failure message under --strict-console -- so the redaction has to happen before
    it is formatted, not at whichever of the two exits happens to be taken.

    Only under prod: on local and dev the URL is the most useful half of the
    message and there is nothing in it to protect.
    """
    return _URL_IN_TEXT.sub("<url>", text) if profile == "prod" else text


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
    _guard_context(ctx, profile)
    p = ctx.new_page()
    errors: list[str] = []
    p.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
         if m.type == "error" else None)
    p.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    p.console_errors = errors  # type: ignore[attr-defined]

    yield p

    # Allowlisted against the RAW text -- the allowlist is a list of URL fragments,
    # and matching it against a redacted line would allowlist nothing. Redaction is
    # a property of the output, so it happens once, here, on the way to both exits.
    unexpected = [e for e in errors if not any(a in e for a in CONSOLE_ALLOWLIST)]
    if unexpected:
        report = "\n  ".join(_redact_console(e, profile) for e in unexpected)
        if pytestconfig.getoption("--strict-console"):
            ctx.close()
            pytest.fail(f"{len(unexpected)} uncaught console error(s):\n  {report}")
        else:
            report_to_terminal(
                request.config,
                f"[console] {request.node.name}: {len(unexpected)} "
                f"non-allowlisted error(s):\n  {report}",
            )
    ctx.close()

"""HTTP health sweep. Seconds, not minutes.

Three assertions per URL, never just a status code:
  * a sane status, and specifically not a dead gateway
  * not a silent redirect to /login/ while authenticated
  * for JSON endpoints, a shape assertion, because a great many of them return
    200 on failure

Nothing here is under /seek/admin/ and nothing here authenticates as a superuser.
Several admin routes act on request.GET with no method check and at least one
deletes rows in response to a bare GET, so a sweep is exactly the wrong program to
point at them. See the private findings note, not committed.
"""
from __future__ import annotations

import pytest
import requests


def check_gateway(r: requests.Response) -> None:
    """Distinguish a dead stack from an application-level 502.

    nginx returns 502 as HTML when gunicorn is not answering: that is a dead
    stack and always a failure. The application also returns 502, as a JSON
    envelope, for upstream and data conditions. Treating both the same either
    misses a real outage or paints a data problem permanently red.
    """
    if r.status_code != 502:
        return
    ctype = r.headers.get("content-type", "")
    if "json" not in ctype:
        pytest.fail(
            f"gateway is down: {r.request.method} {r.request.url} returned a "
            f"non-JSON 502 ({ctype or 'no content-type'}). This is nginx, not the "
            f"application: gunicorn is not answering."
        )


def assert_not_bounced(r: requests.Response) -> None:
    """Catch an authenticated request being quietly sent to the login page."""
    assert r.status_code != 302 or "/login" not in r.headers.get("location", ""), (
        f"{r.request.url} redirected an authenticated client to "
        f"{r.headers.get('location')}. The session is not what the test thinks."
    )


# --------------------------------------------------------------------------- #
# the API sweep
# --------------------------------------------------------------------------- #

# (path, key that must be present in the JSON body or None)
API_SWEEP = [
    ("/nextseek_api/sops/", "data"),
    ("/nextseek_api/data_files/", "data"),
    ("/nextseek_api/projects/", "data"),
    ("/nextseek_api/people/", "data"),
    ("/nextseek_api/investigations/", "data"),
    ("/nextseek_api/studies/", "data"),
    ("/nextseek_api/assays/", "data"),
    ("/nextseek_api/sample_types/", "data"),
    ("/nextseek_api/attributes/", "attributes"),
    ("/nextseek_api/batch-upload/", None),
]


@pytest.mark.parametrize("path,key", API_SWEEP, ids=[p for p, _ in API_SWEEP])
def test_api_endpoint_is_healthy(api, base_url, path, key):
    r = api.get(base_url + path, timeout=60, allow_redirects=False)
    check_gateway(r)
    assert_not_bounced(r)
    assert r.status_code == 200, f"{path} returned {r.status_code}: {r.text[:300]}"
    body = r.json()
    if key:
        assert key in body, f"{path} 200 but body has no {key!r}: {sorted(body)[:8]}"


def test_api_root_advertises_exactly_the_expected_viewsets(api, base_url):
    """A changed router registration is a real regression and this is the cheapest
    way to see it. Measured: exactly 15 keys."""
    r = api.get(f"{base_url}/nextseek_api/", timeout=30)
    check_gateway(r)
    assert r.status_code == 200
    expected = {
        "assay-registrations", "assays", "attributes", "batch-upload",
        "data_files", "investigations", "people", "projects", "sample_types",
        "sample_types/connections", "samples", "samples/advanced_search",
        "sops", "studies", "users",
    }
    got = set(r.json())
    assert got == expected, (
        f"API root changed.\n  added:   {sorted(got - expected)}\n"
        f"  removed: {sorted(expected - got)}"
    )


def test_openapi_schema_generates(api, base_url):
    """The single highest-value check in the suite.

    drf-spectacular walks every annotated endpoint to build this document, so one
    request validates all of them at once. A malformed @extend_schema anywhere in
    the codebase turns this into a 500.
    """
    r = api.get(f"{base_url}/nextseek_api/schema/", timeout=120)
    check_gateway(r)
    assert r.status_code == 200, f"schema generation failed: {r.text[:500]}"

    # Content negotiation: YAML by default, JSON when Accept asks for it. The api
    # fixture sets Accept: application/json, so handle both rather than assuming.
    text = r.text.lstrip()
    if text.startswith("{"):
        paths = r.json().get("paths", {})
    else:
        assert text.startswith("openapi:"), f"not an OpenAPI document: {text[:120]!r}"
        paths = {ln for ln in r.text.splitlines() if ln.startswith("  /")}
    # Guard against the schema silently collapsing to a near-empty document.
    assert len(paths) >= 50, f"schema has only {len(paths)} paths; expected around 67"


def test_identity_probe_responds(api, base_url, smoke_creds):
    """Run this first when triaging. It proves MySQL and SEEK Rails are both up.

    It deliberately does NOT assert *which* person comes back; that is the next
    test, which currently fails for a real reason.
    """
    r = api.get(f"{base_url}/nextseek_api/people/current/", timeout=60)
    check_gateway(r)
    assert r.status_code == 200, (
        f"identity probe failed with {r.status_code}. If this is a 401, the "
        f"account {smoke_creds[0]!r} has probably never logged in through /login/ "
        f"on this box: BasicAuthentication validates against Django's auth_user "
        f"table and only the login view creates that row."
    )
    assert r.json().get("data", {}).get("id"), "no person in the response body"


@pytest.mark.xfail(
    reason=(
        "Two different authenticated accounts are reported as the same SEEK "
        "person. Measured 2026-09-01 on the local stack. Cause and fix are "
        "recorded in the private findings note, which this public repo does "
        "not carry. Flips to XPASS when the proxy client is fixed."
    ),
    strict=False,
)
def test_seek_identity_matches_the_authenticated_caller(api, base_url, smoke_creds):
    """The API must report the caller's own SEEK identity, not somebody else's.

    This is not cosmetic. Project scoping and the participating-project
    permission both key off the SEEK person, so resolving the wrong one decides
    authorization against the wrong account.
    """
    r = api.get(f"{base_url}/nextseek_api/people/current/", timeout=60)
    check_gateway(r)
    assert r.status_code == 200
    login = r.json().get("data", {}).get("attributes", {}).get("login")
    assert login == smoke_creds[0], (
        f"authenticated as {smoke_creds[0]!r} but SEEK reports {login!r}"
    )


def test_ci_account_is_not_a_superuser(api, base_url):
    """A guard on the suite itself, not on the product.

    The sweep issues GETs at many URLs. Several admin routes perform destructive
    work on a bare superuser GET, so running the sweep with superuser rights is
    the actual hazard. is_admin here reflects is_superuser only: login sets
    is_staff=1 on every SEEK user, so is_staff admits everyone.
    """
    r = api.get(f"{base_url}/nextseek_api/assistant/me/", timeout=60)
    check_gateway(r)
    assert r.status_code == 200
    assert r.json().get("is_admin") is False, (
        "the smoke account is a superuser. Point CI_SMOKE_USER at a non-superuser "
        "before running the sweep."
    )


def test_neo4j_is_answering(api, base_url):
    """Cheapest liveness proof for neo4j that is also a real product surface."""
    r = api.get(f"{base_url}/nextseek_api/entity_tree/edges/", timeout=90)
    check_gateway(r)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    assert r.json().get("count", 0) > 0, "no lineage edges returned; neo4j may be empty or down"


def test_edge_attributes_are_enriched(api, base_url):
    """A 200 with every internal_assay_id null means the MySQL enrichment failed
    and was swallowed. The status code cannot tell you that."""
    r = api.get(f"{base_url}/nextseek_api/entity_tree/edge_attributes/", timeout=90)
    check_gateway(r)
    assert r.status_code == 200
    edges = r.json().get("results", {}).get("edges", [])
    if not edges:
        pytest.skip("no edges to inspect")
    assert any(e.get("internal_assay_id") is not None for e in edges), (
        "every edge has a null internal_assay_id: the MySQL enrichment step "
        "failed silently behind a 200"
    )


@pytest.mark.xfail(
    reason="34 sample types have no attribute definitions, so this endpoint "
           "refuses to emit metadata_fields and returns an application-level 502. "
           "Measured on the local stack 2026-09-01. A data condition, not a "
           "deployment failure. Flips to XPASS when the data is fixed.",
    strict=False,
)
def test_entity_tree_nodes(api, base_url):
    r = api.get(f"{base_url}/nextseek_api/entity_tree/nodes/", timeout=90)
    check_gateway(r)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    assert "total" in r.json().get("results", {}), "envelope is doubly wrapped: expected results.total"


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #

# Authenticated via a real session cookie. Basic auth does NOT work for these:
# they read request.session['username'], so a Basic-authenticated request gets a
# 302 to /login/ that an allow_redirects=True sweep reports as 200.
SEEK_PAGES = [
    "/seek/search/",
    "/seek/projects/",
    "/seek/samples/query/",
    "/seek/samples/upload/",
    "/seek/samples/attributes/",
    "/seek/assistant/",
]


@pytest.mark.parametrize("path", SEEK_PAGES)
def test_seek_page_renders_for_a_logged_in_user(web, base_url, path):
    r = web.get(base_url + path, timeout=120, allow_redirects=False)
    check_gateway(r)
    assert_not_bounced(r)
    assert r.status_code == 200, f"{path} returned {r.status_code}"
    assert "text/html" in r.headers.get("content-type", "")
    assert len(r.text) > 500, f"{path} returned a suspiciously short body"


# /seek/assistant/ is excluded deliberately: it does not redirect. It renders an
# error template at HTTP 200 for anonymous visitors, which the next test pins.
BOUNCING_PAGES = [p for p in SEEK_PAGES if p != "/seek/assistant/"]


@pytest.mark.parametrize("path", BOUNCING_PAGES)
def test_seek_page_bounces_an_anonymous_visitor(base_url, path):
    """The other half of the same claim. allow_redirects=False is mandatory:
    followed, every one of these reports 200, because that is the status of the
    login page."""
    r = requests.get(base_url + path, timeout=60, allow_redirects=False)
    check_gateway(r)
    assert r.status_code in (301, 302), f"{path} served an anonymous visitor {r.status_code}"
    assert "/login" in r.headers.get("location", ""), (
        f"{path} redirected to {r.headers.get('location')!r}, not the login page"
    )


def test_assistant_denies_anonymous_visitors_at_status_200(base_url):
    """Pins a known wart rather than pretending it is not there.

    smartSearch checks request.user.is_authenticated and renders an error
    template with HTTP 200 instead of redirecting. Nothing sensitive is served,
    but it means a status-code sweep cannot tell allowed from denied here. If
    this ever starts returning 302 the assertion should be tightened, not
    deleted.
    """
    r = requests.get(f"{base_url}/seek/assistant/", timeout=60, allow_redirects=False)
    check_gateway(r)
    assert r.status_code == 200
    body = r.text.lower()
    assert "chat-assistant-root" not in body, (
        "the assistant mount point was served to an anonymous visitor"
    )


PUBLIC = ["/login/", "/admin/login/", "/seek/help/", "/static/css/nextseek.css"]


@pytest.mark.parametrize("path", PUBLIC)
def test_public_url_is_served(base_url, path):
    r = requests.get(base_url + path, timeout=30, allow_redirects=False)
    check_gateway(r)
    assert r.status_code == 200, f"{path} returned {r.status_code}"


def test_login_page_issues_a_csrf_token(base_url):
    r = requests.get(f"{base_url}/login/", timeout=30)
    check_gateway(r)
    assert r.status_code == 200
    assert r.cookies.get("csrftoken"), "no csrftoken cookie: the login form cannot be posted"
    assert 'name="username"' in r.text

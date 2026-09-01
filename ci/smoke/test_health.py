"""HTTP health sweep. Seconds, not minutes.

Three assertions per URL, never just a status code:
  * a sane status, and specifically not a dead gateway
  * not a silent redirect to /login/ while authenticated
  * for JSON endpoints, a shape assertion, because a great many of them return
    200 on failure

The sweep is, by construction, a program that issues GETs at every URL it knows
about. So it never holds rights it does not need: the health sweep and the flows
authenticate as the non-superuser, and the sweep never requests any path under
/seek/admin/, at any privilege level. Which routes make that rule necessary, and
why, is recorded in the private findings note, which this public repository does
not carry.

What is left here is what a registry row cannot express. The parametrised sweeps
that used to live in this file are now `ci/routes.py` entries, swept by
`test_reachability.py`.
"""
from __future__ import annotations

import pytest

from ci.smoke.assertions import check_gateway


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

    The sweep issues GETs at many URLs, so the rights it runs with are the
    hazard rather than the URLs; which routes make that so is in the private
    findings note, not here. is_admin reflects is_superuser only: login sets
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

def test_assistant_denies_anonymous_visitors_at_status_200(anon, base_url):
    """Pins a known wart rather than pretending it is not there.

    smartSearch checks request.user.is_authenticated and renders an error
    template with HTTP 200 instead of redirecting. Nothing sensitive is served,
    but it means a status-code sweep cannot tell allowed from denied here. If
    this ever starts returning 302 the assertion should be tightened, not
    deleted.
    """
    r = anon.get(f"{base_url}/seek/assistant/", timeout=60, allow_redirects=False)
    check_gateway(r)
    assert r.status_code == 200
    body = r.text.lower()
    assert "chat-assistant-root" not in body, (
        "the assistant mount point was served to an anonymous visitor"
    )


def test_login_page_issues_a_csrf_token(anon, base_url):
    r = anon.get(f"{base_url}/login/", timeout=30, allow_redirects=False)
    check_gateway(r)
    assert r.status_code == 200
    assert r.cookies.get("csrftoken"), "no csrftoken cookie: the login form cannot be posted"
    assert 'name="username"' in r.text

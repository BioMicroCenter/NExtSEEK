"""The NExtSEEK REST tool must be read-only.

`tool_nextseek_api_request` takes `method` straight from the api_agent's plan and
hands it to `requests.request`. The endpoint catalog advertises
`DELETE /nextseek_api/samples/{uid}/`, `PATCH /nextseek_api/samples/{uid}/` and
`POST /nextseek_api/samples/` (create), so a single mis-parsed turn could destroy or
mutate a real sample record. Neo4j has been hard-blocked by a regex since forever
(`helpers/tools/neo4j.py`, `_WRITE_KEYWORDS`); the REST path was not. That asymmetry
is the bug these tests lock shut.

Policy: writes belong on the container_cc path, where `nextseek-api-write` exits
WRITE_BLOCKED without an explicit `--confirmed-write`. On the NS REST path there is
no confirmation step at all, so the answer is read-only, full stop.

**How these tests are written, and why.** The transport is a *recording* stub, and
the assertion that matters is `transport.calls == []` — "no request left the
process" — not merely "the result says ok=False". A stub that `raise`d instead
would be useless here: `tool_nextseek_api_request` wraps the call in
`except Exception` and returns `{"ok": False, "error": repr(e)}`, so a raising stub
makes every deny test pass for the wrong reason even with no guard at all. The
config stub likewise always validates OK, so `validate_request_body` can never be
the thing that produced the refusal.

No test in this file performs real I/O.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import chat_nextseek.helpers.tools.nextseek_api as api
from chat_nextseek.helpers.tools.nextseek_api import (
    _READ_POST_PATHS,
    _is_read_only_request,
    tool_nextseek_api_request,
)

# A UID shaped like the real thing — the catalog's DELETE/PATCH paths are
# `/nextseek_api/samples/{uid}/` and callers substitute a live UID.
REAL_UID = "NHP-220524FLY-1-PUB"
SAMPLE_UID_PATH = f"/nextseek_api/samples/{REAL_UID}/"

ADMIN_RETRIEVE = "/nextseek_api/admin/samples/retrieve/"
PARENTS_BY_CHILD = "/nextseek_api/sample_types/get_parents/parents_by_child_types/"
ADVANCED_SEARCH = "/nextseek_api/samples/advanced_search/"
READ_POSTS = [ADMIN_RETRIEVE, PARENTS_BY_CHILD, ADVANCED_SEARCH]


class _Resp:
    """Minimal stand-in for requests.Response."""

    ok = True
    status_code = 200
    text = '{"total": 0, "rows": []}'

    def json(self):
        return {"total": 0, "rows": []}


class _RecordingTransport:
    """Records every call instead of making one. Returns a benign 200 so that a
    request which *does* get through looks successful — a guard failure must show
    up as `calls != []`, never as an incidental error."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp()


class _Cfg:
    """Config stub that never refuses anything of its own accord.

    `NEXTSEEK_BASE_URL` is set (so the missing-base-URL early return cannot fire)
    and `validate_request_body` always passes (so schema validation cannot be the
    source of a refusal). Any `ok: False` therefore comes from the write guard.
    """

    NEXTSEEK_BASE_URL = "http://nextseek.invalid"
    API_USER = None
    API_PASS = None

    def validate_request_body(self, endpoint, body, method=None):
        return True, None


@pytest.fixture
def transport(monkeypatch):
    stub = _RecordingTransport()
    monkeypatch.setattr(api.requests, "request", stub)
    return stub


def _assert_refused(result, transport, method, endpoint):
    """A refusal is: nothing left the process, ok is False, and the operator can
    read both the method and the path out of the error."""
    assert transport.calls == [], f"{method} {endpoint} reached the network"
    assert result["ok"] is False
    assert method in result["error"], result["error"]
    assert endpoint in result["error"], result["error"]
    assert result["endpoint"] == endpoint
    assert result["method"] == method


# --------------------------------------------------------------- the denial set


def test_delete_a_sample_is_refused_and_never_reaches_the_network(transport):
    """The reachable one: DELETE on a live sample UID."""
    result = tool_nextseek_api_request(_Cfg(), endpoint=SAMPLE_UID_PATH, method="DELETE")
    _assert_refused(result, transport, "DELETE", SAMPLE_UID_PATH)


def test_patch_a_sample_is_refused_and_never_reaches_the_network(transport):
    result = tool_nextseek_api_request(
        _Cfg(), endpoint=SAMPLE_UID_PATH, method="PATCH", requestBody={"title": "x"}
    )
    _assert_refused(result, transport, "PATCH", SAMPLE_UID_PATH)


def test_post_to_samples_collection_is_refused(transport):
    """POST /nextseek_api/samples/ creates a sample. It is one character away from
    POST /nextseek_api/samples/advanced_search/, which is a read."""
    result = tool_nextseek_api_request(
        _Cfg(), endpoint="/nextseek_api/samples/", method="POST", requestBody={"uid": REAL_UID}
    )
    _assert_refused(result, transport, "POST", "/nextseek_api/samples/")


def test_put_is_refused(transport):
    result = tool_nextseek_api_request(_Cfg(), endpoint=SAMPLE_UID_PATH, method="PUT")
    _assert_refused(result, transport, "PUT", SAMPLE_UID_PATH)


# ------------------------------------------------------------------ default-deny


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_unknown_path_with_a_mutating_method_is_denied(transport, method):
    """Default-deny is the property that survives the catalog changing. An
    endpoint nobody has heard of must not be writable just because no rule
    names it."""
    unknown = "/nextseek_api/whatever/"
    result = tool_nextseek_api_request(_Cfg(), endpoint=unknown, method=method)
    _assert_refused(result, transport, method, unknown)


def test_an_unrecognised_verb_is_denied(transport):
    """Anything outside {GET, HEAD, OPTIONS, allowlisted POST} is denied, including
    verbs the tool has never seen."""
    result = tool_nextseek_api_request(_Cfg(), endpoint=ADVANCED_SEARCH, method="TRACE")
    assert transport.calls == []
    assert result["ok"] is False


# ---------------------------------------------------------------- reads still work


@pytest.mark.parametrize("endpoint", READ_POSTS)
def test_the_three_read_posts_still_reach_the_transport(transport, endpoint):
    """POST is used for both search and create in this API, which is why the rule
    is (method, path) pairs. These three POSTs are reads and must be unaffected."""
    result = tool_nextseek_api_request(
        _Cfg(), endpoint=endpoint, method="POST", requestBody={"filter_searchText": "mice"}
    )
    assert len(transport.calls) == 1
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"].endswith(endpoint)
    assert result["ok"] is True


def test_a_get_still_reaches_the_transport(transport):
    result = tool_nextseek_api_request(_Cfg(), endpoint="/nextseek_api/assays/", method="GET")
    assert len(transport.calls) == 1
    assert transport.calls[0]["method"] == "GET"
    assert result["ok"] is True


def test_a_get_on_an_unknown_path_still_reaches_the_transport(transport):
    """Reads are allowed unconditionally; the guard is about mutation, not about
    maintaining a path whitelist for GET."""
    result = tool_nextseek_api_request(
        _Cfg(), endpoint="/nextseek_api/something_new/", method="GET"
    )
    assert len(transport.calls) == 1
    assert result["ok"] is True


# ------------------------------------------------------- normalisation / predicate


@pytest.mark.parametrize(
    "endpoint",
    [
        ADVANCED_SEARCH,
        "nextseek_api/samples/advanced_search/",  # no leading slash (tool lstrips it)
        "/nextseek_api/samples/advanced_search",  # no trailing slash
        "nextseek_api/samples/advanced_search",
    ],
)
def test_read_post_allowlist_tolerates_slash_variance(endpoint):
    assert _is_read_only_request(endpoint, "POST") is True


@pytest.mark.parametrize("method", ["get", "Get", "GET"])
def test_method_matching_is_case_insensitive(method):
    assert _is_read_only_request("/nextseek_api/assays/", method) is True


@pytest.mark.parametrize("method", ["delete", "Delete", "DELETE"])
def test_deny_is_case_insensitive_too(method):
    assert _is_read_only_request(SAMPLE_UID_PATH, method) is False


@pytest.mark.parametrize("method", ["post", "Post"])
def test_lowercase_post_to_the_create_endpoint_is_denied(method):
    """Case-folding the method must not become a bypass in either direction."""
    assert _is_read_only_request("/nextseek_api/samples/", method) is False


def test_missing_method_is_denied():
    assert _is_read_only_request(ADVANCED_SEARCH, "") is False
    assert _is_read_only_request(ADVANCED_SEARCH, None) is False


def test_the_read_post_allowlist_is_exactly_the_three_search_posts():
    assert set(_READ_POST_PATHS) == set(READ_POSTS)


# --------------------------------------------------------------- catalog crosscheck


def _catalog() -> list[dict]:
    path = (
        Path(__file__).resolve().parent.parent
        / "src" / "chat_nextseek" / "context" / "min_api_endpoints_enriched.json"
    )
    return json.loads(path.read_text())


def test_no_mutating_catalog_endpoint_is_allowed_through_the_guard():
    """Survives catalog drift: whatever the catalog advertises, anything the
    api_agent could select that is not a read must be denied at the chokepoint.
    Adding a mutating endpoint to the catalog does not open the door; adding one
    to the allowlist as well would fail this test."""
    for entry in _catalog():
        method = (entry.get("method") or "").upper()
        path = entry.get("path") or ""
        allowed = _is_read_only_request(path, method)
        if method in {"GET", "HEAD", "OPTIONS"} or path in READ_POSTS:
            assert allowed is True, f"read endpoint {method} {path} was blocked"
        else:
            assert allowed is False, f"mutating endpoint {method} {path} was allowed"

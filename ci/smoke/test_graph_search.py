"""graph_search answers a request. The one smoke test that sends it one.

T0 sweeps GET routes, so POST /nextseek_api/samples/graph_search/ is declared in
ci/routes.py and requested nowhere else. This sends the smallest search that
reaches the graph: one sample type, found at run time (see a_sample_type), one
page of five rows, as the smoke client.

It asserts advanced_search's envelope and the page size, never a count: totals are
environment-specific, and a type the graph does not hold filters to nothing, which
is a 200 with total 0. Local and dev only, like the route: under prod the guard
refuses the POST before it is sent.

Two things about the request are deliberate. `page` and `page_size` go in the query
string, the way the Sample Search page sends them: the body is advanced_search's
model, which forbids unknown keys, so either one in the body is a 422. And
`filter_searchText` is required by that model, so a search for every sample of one
type sends it empty, as the page's Simple box does.
"""
from __future__ import annotations

import pytest

from ci.smoke.assertions import check_gateway, describe_shape

pytestmark = pytest.mark.profiles("local", "dev")

GRAPH_SEARCH = "/nextseek_api/samples/graph_search/"
SAMPLE = "/nextseek_api/samples/{sample_id}/"
SAMPLE_TYPES = "/nextseek_api/sample_types/"
PAGE_SIZE = 5
# advanced_search's envelope, which graph_search returns unchanged.
ENVELOPE = ("total", "rows", "footer", "sampleTypes")


def _type_of_sample(api, base_url: str, sample_id: str) -> str | None:
    """The SEEK id of one sample's type, or None when its detail cannot say.

    None rather than a failure: GET /nextseek_api/samples/{sample_id}/ is a registry
    route with its own T0 case, so a broken detail is already reported there, and
    the caller falls back to a listed type.
    """
    r = api.get(base_url + SAMPLE.format(sample_id=sample_id), timeout=90)
    if r.status_code != 200:
        return None
    try:
        value = r.json()["data"]["relationships"]["sample_type"]["data"]["id"]
    except (ValueError, KeyError, TypeError):
        return None
    return None if value is None else str(value)


@pytest.fixture(scope="module")
def a_sample_type(api, base_url, discovered) -> str:
    """The SEEK id of the sample type to search.

    Never hard-coded: ids are deployment-specific. The first choice is the type of
    the sample the conftest discovered, which matched SMOKE_SEARCH_TERM in a project
    the smoke account belongs to, so the graph is likely to hold rows of it. Failing
    that, the first type GET /nextseek_api/sample_types/ lists, which may hold none.
    A digit string is taken as an id with no lookup, so the search needs no title
    resolution to reach the graph.
    """
    if discovered.get("sample_id"):
        found = _type_of_sample(api, base_url, discovered["sample_id"])
        if found:
            return found
    r = api.get(base_url + SAMPLE_TYPES, timeout=90)
    check_gateway(r)
    assert r.status_code == 200, f"GET {SAMPLE_TYPES}: {describe_shape(r)}"
    data = r.json().get("data") or []
    if not data or not isinstance(data[0], dict) or data[0].get("id") is None:
        pytest.skip("the smoke account can list no sample type on this box")
    return str(data[0]["id"])


def test_graph_search_answers_one_page_in_advanced_search_envelope(api, base_url, a_sample_type):
    r = api.post(
        base_url + GRAPH_SEARCH,
        params={"page": 1, "page_size": PAGE_SIZE},
        json={"sampletype": [a_sample_type], "filter_searchText": ""},
        timeout=120,   # graph_search's own statement timeout is 60 s
    )
    check_gateway(r)
    assert r.status_code == 200, f"POST {GRAPH_SEARCH}: {describe_shape(r)}"

    body = r.json()
    missing = [key for key in ENVELOPE if key not in body]
    assert not missing, f"advanced_search's envelope lacks {missing}: {describe_shape(r)}"
    assert isinstance(body["total"], int), f"total is not an integer: {describe_shape(r)}"
    rows = body["rows"]
    assert isinstance(rows, list), f"rows is not a list: {describe_shape(r)}"
    assert len(rows) <= PAGE_SIZE, (
        f"asked for {PAGE_SIZE} rows and got {len(rows)}: page_size was not honoured"
    )
    assert len(rows) <= body["total"], (
        f"{len(rows)} rows on the page against a reported total of {body['total']}"
    )

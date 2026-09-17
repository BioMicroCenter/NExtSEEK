"""Assert a graph change over HTTP, with no Neo4j driver.

This lane holds pytest, requests and playwright and nothing else (`ci/writers.py` states the rule), so
nothing here opens a bolt connection. Everything is asserted through two endpoints:

- ``POST /nextseek_api/samples/graph_search/``, which **matches in the graph** and hydrates the returned
  page from MySQL (``nextseek_api/graph_search/hydrate.py``). So a graph assertion is a FILTER only the
  graph can satisfy, never a field read off a returned row. A hit proves the graph carries that attribute
  with that value; no hit proves it does not.
- ``GET /nextseek_api/admin/graph-sync/status/``, which carries the outbox, the freshness of each job and
  the last drift run's ``stats.graphmeta`` (``schema_version``, ``catalog_hash``, ``synced_at``,
  ``label_maps_hash``).

Contract pinned live on 2026-09-17 rather than assumed:

- the request body is advanced_search's model **plus** an optional ``extensions``, and it FORBIDS unknown
  keys, so ``page``, ``page_size``, ``filter_logic`` and friends are a 422;
- ``extensions.where`` items are exact, case-sensitive, ANDed on one sample type, and validated against the
  catalog;
- an attribute the catalog does not declare is **422**, not 200 with no rows. That distinction is the point:
  it separates "the attribute is gone" from "the attribute is there and matches nothing".
"""
import time

STATUS_PATH = "/nextseek_api/admin/graph-sync/status/"
SEARCH_PATH = "/nextseek_api/samples/graph_search/"

#: What ``graph_holds`` returns when the catalog does not declare the attribute at all.
NOT_DECLARED = "not-declared"


def graph_holds(api, base_url, *, sample_type, attribute, value, timeout=180):
    """UIDs of the samples the graph matches for exactly this attribute and value.

    Returns a list of uuids, ``[]`` when the attribute is declared and nothing matches, and
    ``NOT_DECLARED`` when the catalog does not declare it (the endpoint answers 422). Callers that only
    care whether a value is present should compare against a uuid they created.
    """
    body = {"filter_searchText": "",
            "extensions": {"where": [{"sample_type": sample_type, "attribute": attribute,
                                      "op": "=", "value": value}]}}
    r = api.post(f"{base_url}{SEARCH_PATH}", json=body, timeout=timeout)
    if r.status_code == 422:
        return NOT_DECLARED
    assert r.status_code == 200, f"graph_search answered {r.status_code}: {r.text[:300]}"
    payload = r.json()
    return [row.get("uuid") for row in (payload.get("rows") or [])]


def graph_total(api, base_url, *, sample_type, attribute, value, timeout=180):
    """The graph's total for that condition, which is exact where the row list is one page."""
    body = {"filter_searchText": "",
            "extensions": {"where": [{"sample_type": sample_type, "attribute": attribute,
                                      "op": "=", "value": value}]}}
    r = api.post(f"{base_url}{SEARCH_PATH}", json=body, timeout=timeout)
    if r.status_code == 422:
        return NOT_DECLARED
    assert r.status_code == 200, f"graph_search answered {r.status_code}: {r.text[:300]}"
    return int(r.json().get("total") or 0)


def graph_meta(api, base_url, timeout=60):
    """``GraphMeta`` as the status endpoint reports it, from the last drift run's stats.

    **As fresh as the last drift run, and no fresher.** The status endpoint does not read the graph; it
    reports what the last recorded drift run saw. So this is right for asking "what did the graph look
    like when it was last checked" and WRONG for asking "did the graph just change": between two calls
    with a write in the middle, the value does not move unless a drift run happened in between. Measured
    2026-09-17, an assertion built on that difference failed against a catalog that had demonstrably
    changed. Assert a change through graph_holds, which queries the graph itself.

    Empty when no drift run has been recorded yet, which is itself worth asserting on.
    """
    r = api.get(f"{base_url}{STATUS_PATH}", timeout=timeout)
    assert r.status_code == 200, f"the status endpoint answered {r.status_code}: {r.text[:300]}"
    drift = ((r.json().get("runs") or {}).get("drift") or {}).get("drift") or {}
    return (drift.get("stats") or {}).get("graphmeta") or {}


def status(api, base_url, timeout=60):
    """The whole status body: ``schema_version``, ``runs``, ``freshness``, ``outbox``."""
    r = api.get(f"{base_url}{STATUS_PATH}", timeout=timeout)
    assert r.status_code == 200, f"the status endpoint answered {r.status_code}: {r.text[:300]}"
    return r.json()


def wait_for_drain(api, base_url, *, timeout_s=300, poll_s=5):
    """Poll the status endpoint until the outbox holds nothing pending and nothing claimed.

    Never issues a sync command: the point is that the loop drains it without help. Raises with the
    outbox contents on timeout, because "it did not drain" is only useful with what was left in it.
    """
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = status(api, base_url)
        outbox = last.get("outbox") or {}
        if not (outbox.get("pending") or {}) and not (outbox.get("claimed") or {}):
            return last
        time.sleep(poll_s)
    raise AssertionError(
        f"the outbox did not drain in {timeout_s}s, so the sync loop is not draining it. "
        f"outbox={(last or {}).get('outbox')}; freshness={(last or {}).get('freshness')}"
    )

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

**``total`` and ``rows`` can disagree, and only ``total`` is the graph's own answer.** ``total`` is counted
in Cypher; ``rows`` are that page hydrated from MySQL. So a node the graph still holds whose MySQL row is
gone answers ``total: 1`` with ``rows: []``. Measured 2026-09-17 on three samples: an empty ``rows`` is
therefore NOT evidence that the graph dropped a node, and an absence assertion must read ``graph_total``
(see ``wait_for_total``). ``graph_holds`` is for presence, where a hit needs the graph AND MySQL to agree.
"""
import time

STATUS_PATH = "/nextseek_api/admin/graph-sync/status/"
SEARCH_PATH = "/nextseek_api/samples/graph_search/"

#: What ``graph_holds`` returns when the catalog does not declare the attribute at all.
NOT_DECLARED = "not-declared"


def _search(api, base_url, *, sample_type, attribute, value, timeout):
    """The raw graph_search answer for one exact attribute condition, or ``NOT_DECLARED``.

    One request shape for every reader below, so a contract change lands in one place.
    """
    body = {"filter_searchText": "",
            "extensions": {"where": [{"sample_type": sample_type, "attribute": attribute,
                                      "op": "=", "value": value}]}}
    r = api.post(f"{base_url}{SEARCH_PATH}", json=body, timeout=timeout)
    if r.status_code == 422:
        return NOT_DECLARED
    assert r.status_code == 200, f"graph_search answered {r.status_code}: {r.text[:300]}"
    return r.json()


def graph_rows(api, base_url, *, sample_type, attribute, value, timeout=180):
    """The rows the graph matches for exactly this attribute and value.

    Each row carries both identifiers a behavioural case needs: ``uuid`` is the NExtSEEK UID
    (``TIS-220114ENG-193``) and ``id`` is the SEEK sample id the sample proxy's path takes. Measured
    2026-09-17 against the live 1.2 graph; ``hydrate.py`` selects both.
    """
    payload = _search(api, base_url, sample_type=sample_type, attribute=attribute, value=value,
                      timeout=timeout)
    if payload is NOT_DECLARED:
        return NOT_DECLARED
    return list(payload.get("rows") or [])


def graph_holds(api, base_url, *, sample_type, attribute, value, timeout=180):
    """UIDs of the samples the graph matches for exactly this attribute and value.

    Returns a list of uuids, ``[]`` when the attribute is declared and nothing matches, and
    ``NOT_DECLARED`` when the catalog does not declare it (the endpoint answers 422). Callers that only
    care whether a value is present should compare against a uuid they created.

    **Use this for presence, never for absence.** The uuids come from the hydrated rows, so a node the
    graph still holds whose MySQL row has been deleted returns nothing here while the graph's own
    ``total`` is 1 (module docstring). Assert absence with ``graph_total`` or ``wait_for_total``.
    """
    rows = graph_rows(api, base_url, sample_type=sample_type, attribute=attribute, value=value,
                      timeout=timeout)
    if rows is NOT_DECLARED:
        return NOT_DECLARED
    return [row.get("uuid") for row in rows]


def graph_total(api, base_url, *, sample_type, attribute, value, timeout=180):
    """The graph's total for that condition, which is exact where the row list is one page."""
    payload = _search(api, base_url, sample_type=sample_type, attribute=attribute, value=value,
                      timeout=timeout)
    if payload is NOT_DECLARED:
        return NOT_DECLARED
    return int(payload.get("total") or 0)


def wait_for_total(api, base_url, *, sample_type, attribute, value, want, timeout_s=300, poll_s=5):
    """Poll the graph's own count for this condition until it is ``want``; return the last count seen.

    The count, not the rows, because this is the absence-safe reader (module docstring). Returns rather
    than raising, so the caller writes the assertion message that names the behaviour under test.
    """
    deadline = time.monotonic() + timeout_s
    seen = None
    while True:
        seen = graph_total(api, base_url, sample_type=sample_type, attribute=attribute, value=value)
        if seen == want or time.monotonic() >= deadline:
            return seen
        time.sleep(poll_s)


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


def dead_kinds(body) -> dict:
    """The outbox's dead rows by kind, from a status body.

    A dead row is one that reached ``max_attempts`` and is in nobody's hands, so its work never
    happened and never will without an operator. ``wait_for_drain`` deliberately does NOT wait for
    these: ``state.outbox_summary`` counts a dead row under ``dead`` and not under ``pending``, so an
    outbox holding nothing but dead rows reports drained. Every case that enqueues therefore checks
    this as well, or a loop that refuses a whole kind reads as a clean drain.
    """
    return dict((body.get("outbox") or {}).get("dead") or {})


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


# --- driving the write paths -----------------------------------------------------------------------

UPLOAD_START = "/nextseek_api/batch-upload/start/"
UPLOAD_STATUS = "/nextseek_api/batch-upload/status/"
USERS_PATH = "/nextseek_api/users/"

#: Celery states ``batch-upload/status/`` reports that will not change again.
TERMINAL_STATES = ("SUCCESS", "FAILURE", "REVOKED")


def upload_rows(api, base_url, *, project_id, rows, update_existing=False, timeout_s=900, poll_s=5):
    """Run a batch upload from JSON rows and return the finished job's ``result``.

    The **rows** mode, not a workbook: ``BatchUploadStartRequest.rows`` takes a list of
    ``InputRowModel`` and bypasses Excel parsing entirely (``batch_upload/views.py``, mode 1, which
    wins over an attached file). So this lane needs no openpyxl and no template fetch, and a schema
    change to a sample type cannot make it reject a workbook for the wrong reason. Each row needs
    only ``SampleType`` and ``json_metadata`` (a JSON **string**, per ``InputRowModel``); the UID is
    generated when absent.

    ``start`` answers 202 with a Celery ``job_id`` and nothing else, so the job's own numbers -- the
    ``totals`` that say what stage 6 did with the graph -- come from polling ``status/<job_id>/``.
    Raises with the state and meta on a non-success terminal state or a timeout.
    """
    body = {"rows": rows, "project_id": int(project_id), "update_existing": bool(update_existing)}
    r = api.post(f"{base_url}{UPLOAD_START}", json=body, timeout=180)
    assert r.status_code in (200, 202), f"batch-upload/start answered {r.status_code}: {r.text[:400]}"
    job_id = (r.json() or {}).get("job_id")
    assert job_id, f"batch-upload/start returned no job_id: {r.text[:300]}"

    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        s = api.get(f"{base_url}{UPLOAD_STATUS}{job_id}/", timeout=90)
        assert s.status_code == 200, f"batch-upload/status answered {s.status_code}: {s.text[:300]}"
        last = s.json()
        state = last.get("state")
        if state in TERMINAL_STATES:
            if state != "SUCCESS":
                raise AssertionError(f"batch upload {job_id} ended {state}: meta={last.get('meta')}")
            return last.get("result") or {}
        time.sleep(poll_s)
    raise AssertionError(f"batch upload {job_id} did not finish in {timeout_s}s; last={last}")


def admin_user_record(api, base_url, login, timeout=120):
    """One row of ``/nextseek_api/users/``, the admin list, by login; None when it is not there.

    This endpoint, not ``/nextseek_api/people/current/``, is how a case learns who it is and which
    project it may use. ``UsersViewSet.list`` reads SEEK's own ``users`` and ``people`` tables through
    the Django ORM, so the answer is per-caller correct. Measured 2026-09-17: ``people/current/``
    goes through the SEEK proxy, whose session is shared between callers
    (``nextseek_api/CLAUDE.md``: "Proxy ViewSets share one SEEK session"), and six consecutive calls
    alternating the two smoke accounts answered with ONE identity for both, whichever the shared
    session happened to hold. A scope case built on that endpoint would compare an account with
    itself.
    """
    r = api.get(f"{base_url}{USERS_PATH}", timeout=timeout)
    assert r.status_code == 200, f"the users admin list answered {r.status_code}: {r.text[:300]}"
    for row in (r.json() or {}).get("data") or []:
        if row.get("login") == login:
            return row
    return None


PEOPLE_PATH = "/nextseek_api/people/"


def person_projects(api, base_url, person_id, timeout=120):
    """Every SEEK project a person belongs to, as a set of ints.

    ``/nextseek_api/people/<id>/`` carries the FULL membership set in
    ``relationships.projects``; measured 2026-09-17 against person 84 it answered {2, 13}, which is
    exactly what ``group_memberships`` joined to ``work_groups`` gives, and what
    ``graph_search/scope.py`` reads per request. ``/nextseek_api/users/`` is not a substitute: its
    ``project_id`` is one work group's project, not the set.

    Safe to ask by id even though the SEEK proxy shares a session between callers: the shared session
    changes WHO the server thinks is asking, so it breaks ``people/current/`` ("who am I") and not a
    lookup that names its subject in the path.
    """
    r = api.get(f"{base_url}{PEOPLE_PATH}{person_id}/", timeout=timeout)
    assert r.status_code == 200, f"people/{person_id}/ answered {r.status_code}: {r.text[:300]}"
    rel = ((r.json() or {}).get("data") or {}).get("relationships") or {}
    return {int(item["id"]) for item in (rel.get("projects") or {}).get("data") or [] if item.get("id")}

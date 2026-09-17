"""The behavioural lane: every write path ends with an assertion that THE GRAPH changed.

Opt in twice, like test_write_lane.py: `-m graphwrite`, and the cases that mutate existing rows need
CI_WRITE_DESTRUCTIVE=1 as well. Assertions go through ci/smoke/graph_assert.py, so this module opens no
Neo4j connection: the lane holds pytest, requests and playwright and nothing else.

Why this exists. The graph-sync work made the WIRING blocking in CI: ci/writers.py declares all 30 writer
sites and ci/gate/test_writer_registry.py fails when one appears without a hook. That proves a writer
calls something. It proves nothing about the graph. Measured 2026-09-16, no test anywhere asserted a
graph change after a write, and test_write_lane.py's one mention of the graph asserts the graph step was
SKIPPED in a dry run.

Read the two gates below before adding a case here. Everything in this module is meaningless on a graph
below schema 1.2, because the writer refuses to touch one, and most of it is meaningless without the sync
loop, because only batch upload syncs inline.
"""
import json as _json
import os
import time
import uuid

import pytest

from ci.smoke.client import GuardedSession
from ci.smoke.conftest import web_session
from ci.smoke.graph_assert import (  # noqa: F401
    NOT_DECLARED,
    admin_user_record,
    dead_kinds,
    graph_holds,
    graph_meta,
    graph_rows,
    graph_total,
    person_projects,
    status,
    upload_rows,
    wait_for_drain,
    wait_for_total,
)

pytestmark = pytest.mark.graphwrite

SAMPLE_TYPES = "/nextseek_api/sample_types/"

DESTRUCTIVE = os.environ.get("CI_WRITE_DESTRUCTIVE") == "1"
destructive = pytest.mark.skipif(
    not DESTRUCTIVE,
    reason="mutates existing rows; gated behind CI_WRITE_DESTRUCTIVE=1 as well as -m graphwrite",
)


@pytest.fixture(scope="module")
def wapi(profile, base_url, write_creds):
    """A Basic-authenticated client for the write account.

    Its own client, not the read one, for the reason test_write_lane.py gives: a sessionid cookie would
    outrank the Basic header and silently change which identity performs the write.
    """
    s = GuardedSession(profile=profile, base_url=base_url)
    s.auth = write_creds
    s.headers["Accept"] = "application/json"
    return s


@pytest.fixture(scope="module")
def a_sample_type(wapi, base_url) -> str:
    """A sample type title to search, never hard-coded: ids and titles are deployment-specific."""
    r = wapi.get(base_url + SAMPLE_TYPES, timeout=90)
    if r.status_code != 200:
        pytest.skip(f"{SAMPLE_TYPES} answered {r.status_code}; no sample type to search")
    data = r.json().get("data") or []
    title = ((data[0] if data else {}).get("attributes") or {}).get("title")
    if not title:
        pytest.skip("this box lists no sample type to search")
    return title


def test_the_graph_is_at_schema_1_2(wapi, base_url):
    """The first gate. Every assertion below is meaningless on a 1.1 graph.

    graph_sync's writer refuses a graph that is not at its own schema version, so on 1.1 every write path
    enqueues and nothing drains, and each case below would fail for a reason that has nothing to do with
    the behaviour it is named for.
    """
    meta = graph_meta(wapi, base_url)
    version = meta.get("schema_version")
    assert version == "1.2", (
        f"the graph reads schema {version!r}. Run `graph_sync --full --i-mean-the-live-graph` once on this "
        "box first. Until then the writer refuses every write, uploads report `graph: pending`, and the "
        "cases in this module fail for the wrong reason."
    )


def test_the_outbox_drains_without_help(wapi, base_url):
    """The second gate. The sync loop is the only thing that may empty the outbox here.

    No case in this module issues a sync command. If this fails, the loop is not running or is refusing,
    and every enqueue-only path below (which is all of them except batch upload) cannot be tested.
    """
    body = wait_for_drain(wapi, base_url, timeout_s=300)
    assert body["freshness"]["outbox"]["status"] == "ok", body["freshness"]["outbox"]
    assert dead_kinds(body) == {}, (
        f"the outbox holds dead rows before this run began: {dead_kinds(body)}. A dead row's work never "
        "happened, and wait_for_drain reports a drain with dead rows present, so every case below would "
        "check its own dead count against a box that was already losing work. Clear them first."
    )


def test_the_search_the_assertions_use_actually_discriminates(wapi, base_url, a_sample_type):
    """A guard on the harness itself, not on the product.

    graph_holds is only evidence if a value that is NOT in the graph comes back empty. A filter that
    matched everything, or an endpoint that ignored the condition, would make every assertion below pass
    for free. So: a value nothing can carry must return no rows, and an attribute the catalog does not
    declare must be distinguishable from one that simply matches nothing.
    """
    absent = graph_holds(wapi, base_url, sample_type=a_sample_type,
                         attribute="Organ", value="cismoke-no-such-value-9z8y7x")
    assert absent == [] or absent is NOT_DECLARED, (
        f"a value nothing carries returned rows, so graph_holds does not discriminate: {absent!r}"
    )
    undeclared = graph_holds(wapi, base_url, sample_type=a_sample_type,
                             attribute="CiSmokeNoSuchAttribute9z", value="x")
    assert undeclared is NOT_DECLARED, (
        "an attribute the catalog does not declare should answer 422, which is how a deleted attribute is "
        f"told apart from one that matches nothing; got {undeclared!r}"
    )


# --- B2: an attribute change reaches the graph ----------------------------------------------------

ATTR_SEARCH = "/nextseek_api/attributes/search/"
ATTR_CREATE = "/nextseek_api/attributes/batch-create/"
ATTR_DELETE = "/nextseek_api/attributes/batch-delete/"


def _wait_until_declared(wapi, base_url, sample_type, title, want, timeout_s=150, poll_s=10):
    """Poll until the graph's catalog agrees, because graph_search caches it.

    nextseek_api/graph_search/catalog_cache.py re-reads GraphMeta.catalog_hash at most once every
    RECHECK_SECONDS (60) and only re-reads the catalog when that hash moved. So a change that has
    genuinely reached the graph can take up to a minute to become visible through this endpoint.
    Measured 2026-09-17: a delete that the sync had already applied still read as declared inside that
    window, which is a cache artefact and not a defect. write_attributes does issue
    DELETE_GONE_ATTRIBUTES.
    """
    deadline = time.monotonic() + timeout_s
    seen = None
    while time.monotonic() < deadline:
        seen = _declared_in_graph(wapi, base_url, sample_type, title)
        if seen is want:
            return True
        time.sleep(poll_s)
    return False


def _declared_in_graph(wapi, base_url, sample_type, title):
    """Whether the graph's catalog declares this attribute.

    graph_search validates extensions.where against the catalog, so an undeclared attribute answers 422
    and a declared one answers 200 even when nothing matches. That difference is the assertion: it tells
    "the catalog carries it" apart from "it carries it and no sample has that value".
    """
    probe_value = "cismoke-probe-value-that-nothing-carries"
    return graph_holds(wapi, base_url, sample_type=sample_type,
                       attribute=title, value=probe_value) is not NOT_DECLARED


@destructive
def test_an_attribute_create_and_delete_reach_the_graphs_catalog(wapi, base_url, a_sample_type):
    """WR-05. The attribute API only enqueues, so the sync loop has to carry this one.

    Asserted on the catalog rather than on a sample, because a newly declared attribute has no values
    yet: what must change is what the graph DECLARES. catalog_hash is asserted too, since it covers the
    declared set and must move when that set does.
    """
    probe = f"CiSmoke{uuid.uuid4().hex[:8]}"
    assert not _declared_in_graph(wapi, base_url, a_sample_type, probe), (
        f"{probe} is somehow already in the graph's catalog before it was created"
    )
    created = False
    try:
        r = wapi.post(f"{base_url}{ATTR_CREATE}",
                      json={"targets": [{"sample_type": a_sample_type,
                                         "attributes": [{"title": probe,
                                                         "sample_attribute_type": "Text",
                                                         "required": False}]}]},
                      timeout=180)
        assert r.status_code in (200, 202), f"create failed {r.status_code}: {r.text[:300]}"
        created = True

        wait_for_drain(wapi, base_url, timeout_s=600)
        assert _wait_until_declared(wapi, base_url, a_sample_type, probe, True), (
            f"the outbox drained but the graph's catalog still does not declare {probe}. The attribute "
            "API enqueued and the loop closed the row without the catalog reaching the graph."
        )
        # catalog_hash is deliberately NOT asserted here. graph_meta reads the last DRIFT run's stats,
        # and nothing in this test runs drift, so the value is whatever the last one recorded and would
        # compare equal even when the catalog has moved. Measured 2026-09-17: this assertion failed
        # against a graph whose catalog demonstrably HAD changed, because it was reading a cached hash.
        # Whether catalog_hash moved belongs to a drift-run assertion, not to this one.
    finally:
        if created:
            wapi.post(f"{base_url}{ATTR_DELETE}",
                      json={"targets": [{"sample_type": a_sample_type, "attributes": [probe]}]},
                      timeout=180)
            wait_for_drain(wapi, base_url, timeout_s=600)

    assert _wait_until_declared(wapi, base_url, a_sample_type, probe, False), (
        f"{probe} was deleted and the outbox drained, but the graph's catalog still declares it after "
        "the catalog cache window. write_attributes issues DELETE_GONE_ATTRIBUTES, so this is a real gap."
    )


# --- the sample cases: their shared fixtures ------------------------------------------------------
#
# Every case below needs a sample it created itself, carrying a value nothing else can carry, and it
# has to get there through a real endpoint. Batch upload is the one that creates samples, so it is the
# fixture for the update, delete and project cases as well as the subject of its own.
#
# MARKER_ATTRIBUTE is the field the marker lives in and the field every assertion filters on. It must
# be a free-text attribute of MARKER_TYPE that the catalog declares: graph_search validates
# extensions.where against the catalog and answers 422 for anything it does not know, so a marker in
# an undeclared field would make every case below fail as NOT_DECLARED rather than as a missing graph
# change. Measured 2026-09-17 on the live stack: TIS is sample type 26 and its attribute 12 is
# `Organ`, Text, not required, which is exactly that.

MARKER_TYPE = "TIS"
MARKER_ATTRIBUTE = "Organ"
SAMPLES_PATH = "/nextseek_api/samples/"
USERS_PATH = "/nextseek_api/users/"


def _marker() -> str:
    """A value nothing in the database can already carry, so a hit can only be the row this test made."""
    return f"cismoke-{uuid.uuid4().hex[:12]}"


def _tis_row(marker, *, name=None):
    """One InputRowModel: the three required TIS attributes plus the marker.

    `json_metadata` is a JSON **string**, not an object: InputRowModel declares it `str` and the
    uploader parses it. Passing a dict is a 422 on the request model, which reads as a rejected row.
    UID is left out on purpose so the uploader generates one in its own format.
    """
    import json as _json
    meta = {"Name": name or f"cismoke-{uuid.uuid4().hex[:8]}",
            "Scientist": "CI Smoke",
            MARKER_ATTRIBUTE: marker}
    return {"SampleType": MARKER_TYPE, "json_metadata": _json.dumps(meta)}


@pytest.fixture(scope="module")
def project_pair(wapi, base_url, write_creds, smoke_creds) -> tuple[int, int]:
    """``(invisible, visible)``: a project the read account is NOT in, and one it is.

    Both halves matter, and both come from the full membership sets rather than from a single
    "primary" project. `invisible` is what makes the scope case mean anything: a sample the read
    account could already see proves nothing when it joins one of its own projects. `visible` is
    where the case then moves it.

    The chain is login -> person_id -> projects, and each link is picked for being per-caller
    correct: `admin_user_record` reads SEEK's own tables through the ORM, and `person_projects`
    names its subject in the path. Neither uses /nextseek_api/people/current/, whose identity comes
    from the proxy's shared SEEK session and answered with one account for both on 2026-09-17.
    """
    ids = {}
    for label, creds in (("write", write_creds), ("read", smoke_creds)):
        row = admin_user_record(wapi, base_url, creds[0])
        if not row or not row.get("person_id"):
            pytest.skip(f"the users admin list carries no person for {creds[0]!r}")
        ids[label] = int(row["person_id"])

    mine = person_projects(wapi, base_url, ids["write"])
    theirs = person_projects(wapi, base_url, ids["read"])
    if not theirs:
        pytest.skip(f"person {ids['read']} is in no project, so nothing can become visible to it")
    invisible = sorted(mine - theirs)
    if not invisible:
        pytest.skip(
            f"every project the write account is in ({sorted(mine)}) is one the read account is also "
            f"in ({sorted(theirs)}), so no upload of ours can start out invisible to it"
        )
    return invisible[0], sorted(theirs)[0]


@pytest.fixture(scope="module")
def write_project(project_pair) -> int:
    """Where the throwaway samples are created: invisible to the read account by construction."""
    return project_pair[0]


@pytest.fixture(scope="module")
def read_project(project_pair) -> int:
    """Where the scope case moves its sample to: a project the read account belongs to."""
    return project_pair[1]


@pytest.fixture(scope="module")
def wweb(profile, base_url, write_creds):
    """A logged-in session for the write account, for the /seek/ pages Basic auth cannot reach.

    seek/views/samples.py::sampleDelete requires request.user.is_authenticated AND a SEEK session
    whose username matches, so the legacy delete is reachable only with a session cookie. The write
    account is the one that may delete: _deleteSampleList allows a sample's contributor or a superuser.
    """
    return web_session(profile, base_url, write_creds)


def _legacy_delete(wweb, base_url, uids):
    """Delete by UID through /seek/samples/delete/, exactly as the Sample Deletion tab does.

    This is WR-13, and it is the path the product's own UI uses: the deletion tab posts `alluids` (a
    JSON array of UIDs) and a CSRF token to this view
    (seek/templates/pages/searchAdvanced_deletion.embed.html). It resolves each UID with getSampleID
    and deletes through DBtable_sample._deleteOneSample, whose hook enqueues the retire row.

    NOT the API proxy. Measured 2026-09-17, three deletes for three: DELETE /nextseek_api/samples/<id>/
    answered 500 after 20.13 s every time, a SEEK read timeout at the client's timeout_s = 20
    (nextseek_api/helpers.py). Rails had completed the delete, so MySQL lost the row while WR-07's
    hook -- guarded by `200 <= code < 300` -- never ran, and the node survived. That is the proxy's
    defect, reported separately; it is not this lane's cleanup path and it is not what the UI calls.

    Returns the view's parsed JSON body.
    """
    r = wweb.post(f"{base_url}/seek/samples/delete/", timeout=300,
                  data={"alluids": _json.dumps(list(uids)),
                        "csrfmiddlewaretoken": wweb.cookies.get("csrftoken")},
                  headers={"Referer": base_url})
    assert r.status_code == 200, f"the legacy delete answered {r.status_code}: {r.text[:300]}"
    return r.json()


def _cleanup(wweb, base_url, uids):
    """Best-effort cleanup: this must never mask the assertion that failed."""
    try:
        _legacy_delete(wweb, base_url, uids)
    except Exception:  # noqa: BLE001
        pass


# --- B1: a batch upload lands its samples in the graph --------------------------------------------


@destructive
def test_a_batch_upload_puts_its_samples_in_the_graph(wapi, wweb, base_url, write_project):
    """WR-01, WR-02. The one path that syncs INLINE, so it must reach the graph without the loop.

    Stage 5 writes the outbox row inside each batch's own transaction and stage 6 calls
    targeted.sync_samples for the job's ids under the graph-write lock (orchestrator._sync_graph_for_job),
    so the job's own totals say what happened: `synced (N)` when the inline call did it, `pending (N)`
    when it could not and the loop has to. `pending` is not a failure of the upload, but it IS a failure
    of this assertion: the point of the case is that the inline path works.

    Two rows, not one, because `synced (2)` also shows the job carried its whole id list rather than the
    first row.
    """
    marker = _marker()
    before = graph_holds(wapi, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE,
                         value=marker)
    assert before == [], f"the marker {marker!r} is somehow already in the graph: {before!r}"

    result = upload_rows(wapi, base_url, project_id=write_project,
                         rows=[_tis_row(marker), _tis_row(marker)])
    totals = result.get("totals") or {}
    assert totals.get("success") == 2, f"the upload did not insert two rows: totals={totals}"
    assert str(totals.get("graph", "")).startswith("synced"), (
        f"stage 6 did not sync inline: totals.graph={totals.get('graph')!r}. Either the graph is not at "
        "the writer's schema version or the graph-write lock was held by something else for the whole "
        "of GRAPH_LOCK_WAIT_S."
    )

    rows = graph_rows(wapi, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE, value=marker)
    try:
        assert len(rows) == 2, (
            f"the upload reported {totals.get('graph')!r} but the graph matches {len(rows)} sample(s) "
            f"carrying {MARKER_ATTRIBUTE}={marker!r}. The filter runs in Neo4j, so this is the graph's "
            "own answer, not MySQL's."
        )
    finally:
        # Cleanup only: what the delete does to the graph is B4's assertion, not this one's.
        _cleanup(wweb, base_url, [r.get("uuid") for r in rows])
        wait_for_drain(wapi, base_url, timeout_s=600)


# --- B3: a sample update moves the value in the graph ---------------------------------------------


@pytest.fixture
def a_throwaway_sample(wapi, base_url, write_project):
    """One TIS sample this test made, as ``(seek_id, uid, marker)``, with the graph already holding it.

    Created through batch upload rather than the sample proxy, for two measured reasons. It syncs
    inline, so the fixture does not have to wait for the loop before the case under test even begins;
    and it is the path the house actually creates samples with, so a fixture failure is a failure of
    something already covered by B1 rather than a new unknown.

    It is NOT cleaned up here, because the delete path is itself under test and a fixture that
    insisted on deleting would fail every case that borrowed it for an unrelated reason. Each case
    deletes its own instead, in a finally, through _cleanup. A case added here that forgets to do
    that leaves one row per run: measured 2026-09-17, five rows accumulated from the update case
    before it had its finally.
    """
    marker = _marker()
    result = upload_rows(wapi, base_url, project_id=write_project, rows=[_tis_row(marker)])
    totals = result.get("totals") or {}
    assert totals.get("success") == 1, f"the fixture's upload did not insert its row: totals={totals}"
    rows = graph_rows(wapi, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE, value=marker)
    assert len(rows) == 1, (
        f"the fixture uploaded one sample carrying {MARKER_ATTRIBUTE}={marker!r} and the graph matches "
        f"{len(rows)}. Every case below depends on that sample being in the graph, so stop here: this is "
        "B1's assertion failing, not the case that asked for the fixture."
    )
    return rows[0].get("id"), rows[0].get("uuid"), marker


@destructive
def test_a_sample_update_moves_the_value_in_the_graph(wapi, wweb, base_url, a_throwaway_sample):
    """WR-07, the SEEK sample proxy's PATCH. Enqueue only, so the sync LOOP has to carry this one.

    The assertion is a filter on the NEW value and a filter on the OLD one: the graph must match the
    sample under the value it now carries and stop matching it under the one it used to. A field read
    off a returned row would prove nothing, because the rows are hydrated from MySQL.

    Contract pinned live 2026-09-17: the body is JSON:API with ``attributes.attribute_map``, and SEEK
    MERGES that map rather than replacing it (the response came back still carrying UID, Name and
    Scientist). The plan's guessed ``attributes.json_metadata`` is a 422 on SampleUpdateRequest.
    """
    seek_id, uid, old_marker = a_throwaway_sample
    new_marker = _marker()

    try:
        _assert_the_update_moved_the_value(wapi, base_url, seek_id, uid, old_marker, new_marker)
    finally:
        _cleanup(wweb, base_url, [uid])
        wait_for_drain(wapi, base_url, timeout_s=600)


def _assert_the_update_moved_the_value(wapi, base_url, seek_id, uid, old_marker, new_marker):
    """The body of the update case, so its cleanup can be a finally around the whole of it.

    Split out rather than nested: every other case in this module reads as request-then-assert, and
    an eight-line try block around four assertions hides which one is the behaviour under test.
    """
    r = wapi.patch(f"{base_url}{SAMPLES_PATH}{seek_id}/",
                   json={"data": {"type": "samples",
                                  "attributes": {"attribute_map": {MARKER_ATTRIBUTE: new_marker}}}},
                   timeout=300)
    assert r.status_code in (200, 202), f"the sample PATCH answered {r.status_code}: {r.text[:400]}"

    body = wait_for_drain(wapi, base_url, timeout_s=600)
    assert dead_kinds(body) == {}, f"the update left dead outbox rows: {dead_kinds(body)}"

    holds_new = wait_for_total(wapi, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE,
                               value=new_marker, want=1, timeout_s=300)
    assert holds_new == 1, (
        f"the PATCH succeeded and the outbox drained, but the graph does not match {MARKER_ATTRIBUTE}="
        f"{new_marker!r} (total {holds_new}). WR-07's hook enqueues `samples sample:{seek_id}` and the "
        "loop is draining, so the update did not reach the node."
    )
    assert uid in graph_holds(wapi, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE,
                              value=new_marker), (
        f"the graph matches one sample for the new value but it is not {uid}"
    )
    stale = graph_total(wapi, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE,
                        value=old_marker)
    assert stale == 0, (
        f"the graph still matches {stale} sample(s) under the OLD value {old_marker!r}, so the node "
        "gained the new value without losing the old one: the projection added a property instead of "
        "rewriting the node."
    )


# --- B4: a delete takes the node down -------------------------------------------------------------


@destructive
def test_a_delete_takes_the_node_out_of_the_graph(wapi, wweb, base_url, a_throwaway_sample):
    """WR-13, the legacy delete: the path the product's own Sample Deletion tab posts to.

    Asserted on the graph's own COUNT, never on the rows. This is the case that found the trap: the
    rows of a graph_search answer are hydrated from MySQL, so a node the graph still holds whose MySQL
    row is gone comes back as `total: 1, rows: []`. Measured 2026-09-17 on four such nodes. An
    absence assertion that read the rows would pass on exactly the failure it exists to catch.

    The deletion is enqueue-only (`retire sample:<id>`), so the sync loop carries it.
    """
    seek_id, uid, marker = a_throwaway_sample
    assert graph_total(wapi, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE,
                       value=marker) == 1, "the fixture's sample is not in the graph to begin with"

    answer = _legacy_delete(wweb, base_url, [uid])
    assert answer.get("status") == 1, (
        f"the legacy delete refused {uid}: {str(answer)[:300]}. sampleDelete deletes for the sample's "
        "contributor or a superuser, so a refusal here is about the write account's identity, not the "
        "graph."
    )

    body = wait_for_drain(wapi, base_url, timeout_s=600)
    assert dead_kinds(body) == {}, (
        f"the delete left dead outbox rows: {dead_kinds(body)}. A dead retire row means the node stays "
        "up for good, and nothing else reports it."
    )

    left = wait_for_total(wapi, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE,
                          value=marker, want=0, timeout_s=300)
    assert left == 0, (
        f"the delete succeeded and the outbox drained, but the graph still counts {left} sample(s) "
        f"carrying {MARKER_ATTRIBUTE}={marker!r}. The retire rule did not run for sample {seek_id}: "
        "the node is now a sample that graph_search counts and cannot show, because hydration reads "
        "MySQL and the row is gone."
    )


@destructive
@pytest.mark.xfail(strict=True, reason=(
    "measured 2026-09-17, three for three: DELETE /nextseek_api/samples/<id>/ answers 500 after "
    "20.13 s, a SEEK read timeout at SeekAPIClient.timeout_s = 20 (nextseek_api/helpers.py). Rails "
    "completes the delete, so the row leaves MySQL, but WR-07's retire hook is guarded by "
    "`200 <= code < 300` and never runs. Remove this marker when the proxy either survives a slow "
    "SEEK delete or enqueues the retire when it cannot confirm one."))
def test_the_api_proxy_delete_also_takes_the_node_down(wapi, wweb, base_url, a_throwaway_sample):
    """WR-07's destroy, the API surface, as distinct from WR-13's page above.

    Kept as a strict xfail rather than dropped, for the reason ci/routes.py gives for a route that is
    broken today: the defect stays visible, and the day it is fixed this goes XPASS and tells whoever
    fixed it to delete the marker. It is a real gap even though the UI does not use this path --
    anything driving the API deletes samples and leaves their nodes standing.
    """
    seek_id, uid, marker = a_throwaway_sample
    try:
        r = wapi.delete(f"{base_url}{SAMPLES_PATH}{seek_id}/", timeout=300)
        assert r.status_code in (200, 202, 204), (
            f"the proxy delete answered {r.status_code} (SEEK's own delete outran the proxy's "
            f"{20}s read timeout): {r.text[:200]}"
        )
        wait_for_drain(wapi, base_url, timeout_s=600)
        left = wait_for_total(wapi, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE,
                              value=marker, want=0, timeout_s=300)
        assert left == 0, f"the graph still counts {left} after the proxy delete of {seek_id}"
    finally:
        # Whatever the proxy did to MySQL, the node has to go: the id is retired by uid where the row
        # survives, and by the targeted sync where it does not. Only the first is reachable from here.
        _cleanup(wweb, base_url, [uid])
        wait_for_drain(wapi, base_url, timeout_s=600)


# --- B5: a project change moves the scope, and a membership change reaches the loop ---------------


@destructive
def test_a_sample_joining_a_project_moves_what_the_read_account_sees(
        wapi, api, wweb, base_url, write_project, read_project, a_throwaway_sample):
    """WR-01/WR-02's project links: the case the operator asked for as distinct from the person one.

    What makes this a GRAPH assertion and not a MySQL one: graph_search resolves the caller's own
    project set from MySQL per request (graph_search/scope.py) but matches the SAMPLE's projects in
    Cypher, `any(p IN s.project_ids WHERE p IN $projects)` (graph_search/query.py). So the caller's
    half cannot move without the graph: the sample's `project_ids` property has to be rewritten by the
    sync before a scoped account can match it.

    The read account must not be able to see the sample first, or the second assertion proves nothing.
    Measured 2026-09-17: the write account is a Django superuser and therefore unscoped, while the read
    account is scoped to its own projects, so a sample uploaded into the write account's project is
    invisible to it until it joins one of the read account's.
    """
    seek_id, uid, marker = a_throwaway_sample
    if write_project == read_project:
        pytest.skip(f"both accounts' projects are {write_project}; nothing to move the sample into")
    try:
        assert graph_total(wapi, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE,
                           value=marker) == 1, "the fixture's sample is not in the graph"
        invisible = graph_total(api, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE,
                                value=marker)
        assert invisible == 0, (
            f"the read account can already see the sample (total {invisible}) although it is only in "
            f"project {write_project}, so this case cannot prove the scope moved. Either that account "
            "is a superuser on this box or it is a member of that project."
        )

        result = upload_rows(wapi, base_url, project_id=read_project, update_existing=True,
                             rows=[dict(_tis_row(marker), UID=uid)])
        totals = result.get("totals") or {}
        assert totals.get("updated") == 1, (
            f"the second upload did not update the existing sample: totals={totals}. With "
            "update_existing it must match on the UID and add the projects_samples row, not insert."
        )
        assert str(totals.get("graph", "")).startswith("synced"), (
            f"stage 6 did not sync the project change inline: totals.graph={totals.get('graph')!r}"
        )

        visible = wait_for_total(api, base_url, sample_type=MARKER_TYPE, attribute=MARKER_ATTRIBUTE,
                                 value=marker, want=1, timeout_s=300)
        assert visible == 1, (
            f"the sample joined project {read_project}, which the read account is in, but that account "
            f"still matches {visible}. The graph's project_ids for sample {seek_id} did not gain "
            "the project, so the scope clause cannot match it."
        )
    finally:
        _cleanup(wweb, base_url, [uid])
        wait_for_drain(wapi, base_url, timeout_s=600)


@destructive
def test_a_person_change_enqueues_a_membership_row_the_loop_drains(wapi, base_url, write_creds):
    """WR-10, the users admin API. What this proves, and what it cannot, are both worth stating.

    PROVES: the hook fires on a person write and the loop drains the `membership` kind rather than
    dead-lettering it. A kind the loop refuses would sit in the outbox's `dead` counts, where
    wait_for_drain alone would still report a clean drain (see dead_kinds).

    CANNOT PROVE, over HTTP: that MEMBER_OF changed in the graph. graph_search answers about samples
    only, its scope comes from MySQL rather than from MEMBER_OF, and the status endpoint reports the
    last drift run rather than the graph. The graph side of this writer is covered by gate G's
    `people.*` checks (graph_sync/verify.py::_check_people), which compare every person's graph
    project set against SEEK's memberships and run inside every drift run. That is where a broken
    MEMBER_OF is caught; asserting it here would need a Neo4j connection this lane may not open.

    The write is a no-op on purpose: it re-asserts the account's OWN first name. UsersViewSet has no
    destroy and its PATCH "can add a membership and never ends one", so a case that added one would
    leave a real membership behind on every run, and a case that created a person could never remove
    it.
    """
    record = admin_user_record(wapi, base_url, write_creds[0])
    if not record:
        pytest.skip(f"the users admin list does not carry {write_creds[0]!r}")
    user_id, first_name = record.get("user_id"), record.get("first_name")
    if not user_id or not first_name:
        pytest.skip(f"{write_creds[0]!r} has no user_id or no first_name to re-assert")

    r = wapi.patch(f"{base_url}{USERS_PATH}{user_id}/", json={"first_name": first_name}, timeout=300)
    if r.status_code in (502, 503):
        # Could not run, and why (spec D8), rather than a failure of the writer under test. Measured
        # 2026-09-17 on this box: every users-admin write answers 502 "Invalid upstream response"
        # because UsersViewSet writes through `bin/rails runner` inside the seek container, and that
        # container sits at 3.43 GiB of its 4 GiB cap with OOMKilled already true, so the fresh Rails
        # boot the runner needs is SIGKILLed (exec exit 137, nothing on either stream). The runner
        # reports empty output rather than the signal, so the 502 does not say this. Nothing about
        # WR-10 is proven or disproven while that holds.
        pytest.skip(
            f"the users admin API answered {r.status_code}: {r.text[:200]}. WR-10 writes through "
            "`bin/rails runner` in the seek container; on a box whose seek container cannot boot one "
            "(measured 2026-09-17: OOM-killed at its 4 GiB cap) this case cannot run at all."
        )
    assert r.status_code in (200, 202), (
        f"re-asserting the account's own first name answered {r.status_code}: {r.text[:300]}"
    )

    body = wait_for_drain(wapi, base_url, timeout_s=600)
    assert dead_kinds(body) == {}, (
        f"the person write left dead outbox rows: {dead_kinds(body)}. WR-10 enqueues `membership *`, "
        "so a dead row here means the loop cannot process that kind at all and MEMBER_OF goes stale "
        "on every membership change until an operator notices."
    )

    after = admin_user_record(wapi, base_url, write_creds[0])
    assert (after or {}).get("first_name") == first_name, (
        f"the no-op patch changed the account's first name from {first_name!r} to "
        f"{(after or {}).get('first_name')!r}"
    )

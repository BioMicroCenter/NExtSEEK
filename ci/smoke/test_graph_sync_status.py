"""The graph sync says what state it is in, and a synced graph answers like MySQL (the spec's CI-5 and CI-3).

Two claims, in one file because the second is only meaningful once the first says it is:

  * GET /nextseek_api/admin/graph-sync/status/ answers a superuser with the latest run of each kind, freshness per
    job, the outbox and the latest drift result, AND the run fails when any of those jobs is stale. That last claim
    is what CI-5 exists for: a box whose sync loop has stopped, or whose drain has left a row waiting for an hour,
    must not report a green smoke run. A deployed box that has never run a sync answers `never`, which stays green,
    so the surrounding tests assert the vocabulary rather than any particular value.
  * parity-lite: WHEN the status reports a successful full sync at the writer's schema version, the same small body
    sent to advanced_search and to graph_search reports the same `total`. That condition is the whole point. Before
    the first full sync the graph is at another version and the two are expected to disagree, so the check skips
    rather than failing a box that is simply not synced yet.

Local and dev only, like the route: production runs a v1.0 graph with no migration 0021, so the tables this endpoint
reads are not there. It only ever sends GET and two searches, so it carries no `write` marker; it authenticates as
the superuser account because nothing else can call the endpoint at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.smoke.assertions import check_gateway, describe_shape
from ci.smoke.client import GuardedSession
from ci.smoke.conftest import _cred

pytestmark = pytest.mark.profiles("local", "dev")

STATUS = "/nextseek_api/admin/graph-sync/status/"
SAMPLE_TYPES = "/nextseek_api/sample_types/"
GRAPH_SEARCH = "/nextseek_api/samples/graph_search/"
ADVANCED_SEARCH = "/nextseek_api/samples/advanced_search/"

PARTS = ("generated_at", "schema_version", "runs", "freshness", "outbox", "drift")
JOB_STATUSES = {"ok", "stale", "never"}
OUTBOX_STATUSES = {"ok", "stale"}
PAGE_SIZE = 5


@pytest.fixture(scope="module")
def admin_api(profile, base_url) -> GuardedSession:
    """Basic-authenticated client for the superuser account, failing rather than skipping when it is missing.

    Not conftest's `write_creds` fixture, which skips: the opt-in write lane relies on that, but this module reads
    only, so it runs by default. A skip here would let a box with no superuser credentials report green having
    proved nothing about the one endpoint no other account can reach.
    """
    creds = _cred(("CI_WRITE_USER", "CI_WRITE_PASS"))
    if creds is None:
        pytest.fail(
            "the graph sync status endpoint is superuser-only and needs CI_WRITE_USER/CI_WRITE_PASS "
            "(environment or ~/.config/nextseek/ci.env). The account must be a Django superuser and must have "
            "logged in through /login/ on this box at least once."
        )
    session = GuardedSession(profile=profile, base_url=base_url)
    session.auth = creds
    session.headers["Accept"] = "application/json"
    return session


@pytest.fixture(scope="module")
def status_body(admin_api, base_url) -> dict:
    r = admin_api.get(base_url + STATUS, timeout=60)
    check_gateway(r)
    assert r.status_code == 200, (
        f"GET {STATUS}: {describe_shape(r)}. A 403 means the account is not a Django superuser; a 503 means the "
        f"box has no migration 0021, so the outbox and run tables it reads are missing."
    )
    return r.json()


def test_the_status_reports_every_part(status_body):
    missing = [part for part in PARTS if part not in status_body]
    assert not missing, f"the status body lacks {missing}"
    assert isinstance(status_body["runs"], dict)
    assert isinstance(status_body["schema_version"], str) and status_body["schema_version"]


def test_freshness_reports_a_known_status_for_every_job(status_body):
    """`never` is a legitimate answer on a box that has not been synced yet; a value outside the vocabulary is not."""
    freshness = status_body["freshness"]
    for job in ("full", "reconcile"):
        assert freshness[job]["status"] in JOB_STATUSES, (
            f"freshness.{job}.status is {freshness[job]['status']!r}, not one of {sorted(JOB_STATUSES)}"
        )
        assert freshness[job]["threshold_s"] > 0
    assert freshness["outbox"]["status"] in OUTBOX_STATUSES
    assert freshness["outbox"]["threshold_s"] > 0


def test_a_stale_job_names_the_run_that_would_refresh_it(status_body):
    """Whatever the box's state, a job reported `ok` or `stale` has a run behind it and `never` has none."""
    for job in ("full", "reconcile"):
        part = status_body["freshness"][job]
        if part["status"] == "never":
            assert part["last_ok_started_at"] is None and part["age_s"] is None
        else:
            assert part["last_ok_started_at"], f"freshness.{job} is {part['status']!r} with no run behind it"
            assert part["satisfied_by"], f"freshness.{job} does not say which kind of run satisfied it"


def test_no_job_is_stale(status_body):
    """CI-5's own claim: every job the box has ever run is within its threshold.

    `never` stays green deliberately. A box the operator has not yet run `graph_sync --full` on is not behind
    schedule, it has no schedule yet, and the parity check below skips itself there for the same reason. `stale` is
    different: the run happened once and has not happened since, so the graph is drifting away from MySQL.
    """
    freshness = status_body["freshness"]
    behind = {job: freshness[job] for job in ("full", "reconcile", "outbox") if freshness[job]["status"] == "stale"}
    assert not behind, (
        "the graph sync is behind: "
        + "; ".join(f"{job} is {part['age_s']} s old against a {part['threshold_s']} s threshold"
                    for job, part in sorted(behind.items()))
        + ". Either the sync loop has stopped (NEXTSEEK_GRAPH_SYNC_LOOP=0 is the usual cause) or the drain is not "
          "keeping up with the outbox."
    )


def test_the_outbox_is_summarised_by_kind(status_body):
    outbox = status_body["outbox"]
    for part in ("pending", "dead", "claimed"):
        assert isinstance(outbox[part], dict), f"outbox.{part} is not a mapping of kind to count"
        assert all(isinstance(n, int) for n in outbox[part].values()), f"outbox.{part} holds a non-integer count"
    assert outbox["max_attempts"] > 0
    oldest = outbox["oldest_pending"]
    assert oldest is None or {"kind", "key", "enqueued_at", "age_s"} <= set(oldest)


def test_nothing_is_dead_in_the_outbox(status_body):
    """A dead row is work that was tried to the attempt limit and never done, so the graph is behind by that much."""
    dead = status_body["outbox"]["dead"]
    assert not dead, (
        f"the graph sync outbox holds rows at the attempt limit: {dead}. Their work never happened; read "
        f"last_error on those rows."
    )


@pytest.fixture(scope="module")
def a_sample_type(admin_api, base_url) -> str:
    """The SEEK id of a sample type to search, never hard-coded: ids are deployment-specific."""
    r = admin_api.get(base_url + SAMPLE_TYPES, timeout=90)
    check_gateway(r)
    assert r.status_code == 200, f"GET {SAMPLE_TYPES}: {describe_shape(r)}"
    data = r.json().get("data") or []
    if not data or not isinstance(data[0], dict) or data[0].get("id") is None:
        pytest.skip("this box lists no sample type to search")
    return str(data[0]["id"])


def test_graph_search_and_advanced_search_agree_once_a_full_sync_has_run(status_body, admin_api, base_url,
                                                                        a_sample_type):
    """The parity claim, asserted only where the status says the graph is the writer's to compare.

    Before the first `graph_sync --full` on a box the graph is at an earlier schema version and holds whatever the
    POC left, so a disagreement there says nothing about this code. The status endpoint is what makes the condition
    checkable from outside the container.
    """
    full = status_body["runs"].get("full") or {}
    counts = full.get("counts") or {}
    if full.get("status") != "ok":
        pytest.skip("no successful full sync recorded on this box, so there is nothing to compare against")
    if counts.get("schema_version") != status_body["schema_version"]:
        pytest.skip(
            f"the last full sync wrote schema {counts.get('schema_version')!r}, not the writer's "
            f"{status_body['schema_version']!r}"
        )

    body = {"sampletype": [a_sample_type], "filter_searchText": ""}
    params = {"page": 1, "page_size": PAGE_SIZE}
    totals = {}
    for path, timeout in ((ADVANCED_SEARCH, 300), (GRAPH_SEARCH, 120)):
        r = admin_api.post(base_url + path, params=params, json=body, timeout=timeout)
        check_gateway(r)
        assert r.status_code == 200, f"POST {path}: {describe_shape(r)}"
        totals[path] = r.json()["total"]

    assert totals[GRAPH_SEARCH] == totals[ADVANCED_SEARCH], (
        f"the same search reports {totals[GRAPH_SEARCH]} samples from the graph and "
        f"{totals[ADVANCED_SEARCH]} from MySQL for sample type {a_sample_type}. The graph is behind, or the sync "
        f"wrote something MySQL does not have."
    )

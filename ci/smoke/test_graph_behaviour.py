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
import os

import pytest

from ci.smoke.client import GuardedSession
from ci.smoke.graph_assert import NOT_DECLARED, graph_holds, graph_meta, status, wait_for_drain  # noqa: F401

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

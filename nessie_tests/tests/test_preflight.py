"""force_route is admin-only and a non-admin's value is DROPPED SILENTLY.

Without this check the harness sends force_route on all 300 turns, the server
ignores every one of them, the router picks whatever it likes, and the run
completes looking perfectly healthy while measuring nothing it claims to measure.

The discriminator is cheap and exact: `_decide_route` returns ROUTE_NS/ROUTE_CC
for a forced decision and NEVER ROUTE_UNRELATED. So send an out-of-scope question
forced to `ns`. If it comes back `unrelated`, the force was dropped.
"""
import pytest

from nessie_tests import preflight


def _fakes(route, source="forced"):
    def post_query(body):
        post_query.bodies.append(body)
        return {"task_id": "t", "session_id": "s"}
    post_query.bodies = []

    def get_progress(_):
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": route, "source": source}},
            {"event": "query_complete", "data": {"reply": "r", "session_id": "s"}},
        ]}
    return post_query, get_progress


def test_passes_when_the_force_is_honoured():
    post_query, get_progress = _fakes("nextseek_query")
    preflight.assert_force_route_works(post_query, get_progress)
    assert post_query.bodies[0]["force_route"] == "ns"


def test_raises_when_the_force_was_dropped():
    """`unrelated` is only reachable through the router, so it proves the drop."""
    post_query, get_progress = _fakes("unrelated", source="baml")
    with pytest.raises(preflight.ForceRouteRejected) as e:
        preflight.assert_force_route_works(post_query, get_progress)
    assert "is_staff" in str(e.value)


def test_raises_when_the_source_is_not_forced():
    """Belt and braces: the route can coincidentally match while the force was
    still ignored. `source` is the direct evidence, `route` is the fallback."""
    post_query, get_progress = _fakes("nextseek_query", source="baml")
    with pytest.raises(preflight.ForceRouteRejected):
        preflight.assert_force_route_works(post_query, get_progress)


def test_uses_exactly_one_turn():
    post_query, get_progress = _fakes("nextseek_query")
    preflight.assert_force_route_works(post_query, get_progress)
    assert len(post_query.bodies) == 1


def test_raises_when_no_route_decided_event_arrived():
    """A turn that errors out emits no `route_decided`, so `route_obs.route` and
    `.source` are both None. The guard must take the raising path on that, not
    trip over the Nones -- an inconclusive probe is exactly as unsafe to proceed
    from as a refused one. `_fakes` cannot express this case: it always emits the
    event, so no other test here pins it."""
    def post_query(body):
        return {"task_id": "t", "session_id": "s"}

    def get_progress(_):
        return {"status": "error", "progress": [
            {"event": "error", "data": {"error": "engine blew up"}},
        ]}

    with pytest.raises(preflight.ForceRouteRejected) as e:
        preflight.assert_force_route_works(post_query, get_progress)
    assert "route=None" in str(e.value)

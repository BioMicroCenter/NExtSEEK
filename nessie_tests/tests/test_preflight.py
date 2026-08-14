"""force_route is admin-only and a non-admin's value is DROPPED SILENTLY.

Without this check the harness sends force_route on all 300 turns, the server
ignores every one of them, the router picks whatever it likes, and the run
completes looking perfectly healthy while measuring nothing it claims to measure.

The discriminator is cheap and exact: `_decide_route` returns ROUTE_NS/ROUTE_CC
for a forced decision and NEVER ROUTE_UNRELATED. So send an out-of-scope question
forced to `ns`. If it comes back `unrelated`, the force was dropped.

The SAME probe turn is then followed to its end for the `ns_run_root` event,
which is product code added on this branch and therefore absent from any image
built before it. See the second half of this file.
"""
import pytest

from nessie_tests import preflight

RUN_ROOT_EVENT = {"event": "ns_run_root", "data": {"run_root": "/app/outputs/x"}}


def _fakes(route, source="forced", run_root=True):
    def post_query(body):
        post_query.bodies.append(body)
        return {"task_id": "t", "session_id": "s"}
    post_query.bodies = []

    def get_progress(_):
        get_progress.calls += 1
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": route, "source": source}},
            {"event": "query_complete", "data": {"reply": "r", "session_id": "s"}},
        ] + ([RUN_ROOT_EVENT] if run_root else [])}
    get_progress.calls = 0
    return post_query, get_progress


def _no_sleep(_):
    return None


def test_passes_when_the_force_is_honoured():
    post_query, get_progress = _fakes("nextseek_query")
    preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)
    assert post_query.bodies[0]["force_route"] == "ns"


def test_raises_when_the_force_was_dropped():
    """`unrelated` is only reachable through the router, so it proves the drop."""
    post_query, get_progress = _fakes("unrelated", source="baml")
    with pytest.raises(preflight.ForceRouteRejected) as e:
        preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)
    assert "is_staff" in str(e.value)


def test_raises_when_the_source_is_not_forced():
    """Belt and braces: the route can coincidentally match while the force was
    still ignored. `source` is the direct evidence, `route` is the fallback."""
    post_query, get_progress = _fakes("nextseek_query", source="baml")
    with pytest.raises(preflight.ForceRouteRejected):
        preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)


def test_uses_exactly_one_turn():
    post_query, get_progress = _fakes("nextseek_query")
    preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)
    assert len(post_query.bodies) == 1


def test_raises_when_the_force_landed_on_unrelated_anyway():
    """Pins `and route != "unrelated"`, the whole basis of the discriminator.

    A forced decision is ROUTE_NS or ROUTE_CC and NEVER ROUTE_UNRELATED, so
    `source='forced'` paired with `route='unrelated'` is a self-contradictory
    observation: something downstream of the force reclassified the turn. Delete
    the clause and this pairing sails through as a healthy force, which is the
    one shape the probe query was chosen to catch. No other test pairs them.
    """
    post_query, get_progress = _fakes("unrelated", source="forced")
    with pytest.raises(preflight.ForceRouteRejected) as e:
        preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)
    assert "is_staff" in str(e.value), "a contradicted force is a DROPPED force, not an inconclusive probe"


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
        preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)
    assert "route=None" in str(e.value)


def test_a_probe_that_never_routed_is_not_diagnosed_as_a_dropped_force():
    """The two failures have DIFFERENT remedies, so they must not share a message.

    With no `route_decided` event there is no routing observation to contradict,
    so "force_route was not honoured ... run as a staff account" asserts a cause
    the probe cannot see -- and sends an operator whose endpoint is hung or down
    off to change accounts, which will not help. Same refusal `cost_summary`
    makes when it reports `unmeasured` rather than $0.00. It still RAISES: an
    unproven force is as unsafe to spend a 300-turn run on as a refused one.
    """
    def post_query(body):
        return {"task_id": "t", "session_id": "s"}

    def get_progress(_):
        return {"status": "error", "progress": [
            {"event": "error", "data": {"error": "engine blew up"}},
        ]}

    with pytest.raises(preflight.ForceRouteRejected) as e:
        preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)
    msg = str(e.value)
    assert "INCONCLUSIVE" in msg and "no `route_decided` event arrived" in msg
    assert "is_staff" not in msg and "staff account" not in msg, \
        "an unobserved routing decision must not be reported as a dropped force"


def test_a_demonstrably_dropped_force_keeps_the_staff_account_guidance():
    """The other side of the split: where the force really was dropped, the
    actionable remedy must survive. `unrelated` is reachable only through the
    router, so this one IS proof."""
    post_query, get_progress = _fakes("unrelated", source="baml")
    with pytest.raises(preflight.ForceRouteRejected) as e:
        preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)
    msg = str(e.value)
    assert "is_staff" in msg and "staff account" in msg
    assert "INCONCLUSIVE" not in msg


def test_the_probe_runs_at_route_tier_so_a_hang_costs_60s_not_600s():
    """`route_decided` is emitted before either engine runs (cc_assistant.py:403),
    so the route tier is an IDENTICAL discriminator -- it just breaks the poll
    loop at the event instead of waiting for a terminal status. At `full` a hung
    probe blocks for `full_timeout_s=600`; at `route` the ceiling is
    `route_timeout_s=60`. Pinned on the argument because the timeout itself
    cannot be exercised without a 10-minute test.
    """
    seen = {}
    real_drive = preflight.http_driver.drive

    def spy(query, **kw):
        seen.update(kw)
        return real_drive(query, **kw)

    post_query, get_progress = _fakes("nextseek_query")
    preflight.http_driver.drive = spy
    try:
        preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)
    finally:
        preflight.http_driver.drive = real_drive
    assert seen["tier"] == "route"


# --------------------------------------------------------------------------- #
# The ns_run_root event. Task 1 added it to nextseek_api/services/cc_assistant.py
# and a running container serves a BAKED image, so an operator who pulled the
# branch and did not `./startup.sh rebuild` gets an endpoint without it. Nothing
# checked. The run completes, looks healthy, and all 127 NS arms lose the only
# key joining a task_id to the engine's output directory -- discovered at
# collection, tens of dollars and many hours later, recoverable only by paying
# for the whole run again.
# --------------------------------------------------------------------------- #

def test_the_probe_turn_proves_the_join_key_is_deployed():
    """Non-vacuity for the test below: the happy path really does read the event
    rather than ignoring the question."""
    post_query, get_progress = _fakes("nextseek_query", run_root=True)
    preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)
    assert get_progress.calls >= 1


def test_a_turn_that_emits_no_ns_run_root_refuses_the_run_and_names_the_rebuild():
    """The whole finding. The message must name the fix, because the operator's
    instinct on "the harness cannot see an event" is to change the harness."""
    post_query, get_progress = _fakes("nextseek_query", run_root=False)

    with pytest.raises(preflight.NsRunRootMissing) as e:
        preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)

    msg = str(e.value)
    assert "ns_run_root" in msg
    assert "./startup.sh rebuild" in msg
    assert "127" in msg, "the cost of proceeding is not stated"
    assert "INCONCLUSIVE" not in msg, "a finished turn with no event is PROOF"


def test_the_refusal_is_a_preflight_refusal_and_not_a_dropped_force():
    """Two refusals, two remedies -- "rebuild the image" and "use a staff
    account" -- so they are distinct types; one consequence (refused, one probe
    turn billed, no paired arm), so they share a base the CLI maps to one exit."""
    post_query, get_progress = _fakes("nextseek_query", run_root=False)

    with pytest.raises(preflight.PreflightRefused) as e:
        preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)

    assert not isinstance(e.value, preflight.ForceRouteRejected)
    assert "is_staff" not in str(e.value)


def test_a_late_ns_run_root_is_not_reported_as_a_missing_one():
    """`ns_run_root` necessarily lands AFTER `query_complete`: `run_query` emits
    the latter from inside itself, the join key comes from the `finally` around
    the call, and they reach `QueryTask.progress` through separate `task.save()`
    calls. A reader that stopped at the terminal status would call the event
    missing in the one-save-wide window between them and send an operator to
    rebuild an image that is already correct. `collect` retries once for exactly
    this; so does the preflight, on the same constant."""
    def _payload(*extra):
        return {"status": "completed", "progress": [
            {"event": "route_decided",
             "data": {"route": "nextseek_query", "source": "forced"}},
            {"event": "query_complete", "data": {"reply": "r"}}, *extra]}

    # poll 0 is `drive`'s, poll 1 is the first run-root read, poll 2 is the retry
    payloads = [_payload(), _payload(), _payload(RUN_ROOT_EVENT)]

    def post_query(body):
        return {"task_id": "t", "session_id": "s"}

    def get_progress(_):
        i = get_progress.calls
        get_progress.calls += 1
        return payloads[min(i, len(payloads) - 1)]
    get_progress.calls = 0

    slept = []
    # A poll interval that is NOT `RETRY_DELAY_S`, because the two are both 2.0s
    # and an assertion that cannot tell them apart passes when the retry is gone
    # and the ordinary poll loop happens to come round again.
    preflight.assert_force_route_works(post_query, get_progress, sleep=slept.append,
                                       poll_interval_s=7.0)

    assert get_progress.calls == 3, "the retry did not happen exactly once"
    assert slept == [preflight.collect.RETRY_DELAY_S], \
        ("the terminal row was not re-read after collect's documented retry "
         f"delay; slept {slept}")


def test_a_turn_that_never_finishes_is_inconclusive_not_a_missing_event():
    """The split the force-route guard already makes, for the same reason: a hung
    endpoint is not evidence about the deployed image, and telling an operator
    whose stack is wedged to rebuild sends them somewhere that cannot help. It
    still raises -- an UNPROVEN join key is as unsafe to pay for as an absent
    one."""
    def post_query(body):
        return {"task_id": "t", "session_id": "s"}

    def get_progress(_):
        return {"status": "pending", "progress": [
            {"event": "route_decided",
             "data": {"route": "nextseek_query", "source": "forced"}}]}

    clock = iter([0.0, 0.0, 1.0, 2.0, 99.0])

    with pytest.raises(preflight.NsRunRootMissing) as e:
        preflight.assert_force_route_works(
            post_query, get_progress, sleep=_no_sleep,
            clock=lambda: next(clock), ns_run_root_timeout_s=5.0)

    msg = str(e.value)
    assert "INCONCLUSIVE" in msg
    assert "./startup.sh rebuild" not in msg, \
        "a hung endpoint must not be diagnosed as a stale image"


def test_the_force_route_check_comes_first_and_fails_before_the_long_wait():
    """A dropped force makes everything after it meaningless, and its probe is
    the one that fails fast at 60s. So a turn that fails BOTH checks reports the
    force -- and never enters the poll loop that waits out `full_timeout_s`."""
    post_query, get_progress = _fakes("unrelated", source="baml", run_root=False)

    with pytest.raises(preflight.ForceRouteRejected):
        preflight.assert_force_route_works(post_query, get_progress, sleep=_no_sleep)

    assert get_progress.calls == 1, "the run-root poll ran despite a dropped force"


def test_the_route_tier_probe_still_observes_the_routing_decision():
    """The tier swap is only safe if `route_obs` is still populated when the loop
    breaks early. `drive` builds the observation from the payload it broke on, and
    it breaks BECAUSE `route_decided` is in that payload -- so the fields the guard
    reads are exactly the ones that ended the loop. Pins that, rather than assuming
    it: a route-tier break that returned an empty observation would send every
    probe down the new inconclusive path and no other test would notice.
    """
    from nessie_tests import http_driver
    post_query, get_progress = _fakes("nextseek_query")
    res = http_driver.drive("q", tier="route", post_query=post_query,
                            get_progress=get_progress, force_new=True, force_route="ns")
    assert res.aborted_early is True, "route tier must break at route_decided"
    assert (res.route_obs.route, res.route_obs.source) == ("nextseek_query", "forced")

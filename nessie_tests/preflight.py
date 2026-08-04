"""Prove the endpoint is the one this run needs, before it spends anything.

TWO things are checked on ONE probe turn, and both are unrecoverable after the
fact:

1.  `force_route` is honoured. `_decide_route` (nextseek_api/services/
    cc_assistant.py:245-251) drops a non-admin's `force_route` and falls back to
    the router. Nothing in the response says so. A whole 300-turn run would
    complete, cost real money, and measure the router instead of the engines.

2.  The turn emits `ns_run_root`. That event is a PRODUCT change made on this
    branch, and a running container serves a BAKED image, so an operator who
    pulled the branch and did not `./startup.sh rebuild` gets an endpoint with
    no such event. The run completes, looks healthy, and every one of the 127 NS
    arms loses the only key that joins its task_id to the engine's output
    directory -- discoverable at collection, hours and tens of dollars later,
    and fixable only by paying for the run again.

Both raise before a paired arm is billed. Neither is folded into the other: the
remedies are "run as a staff account" and "rebuild the image", and telling an
operator the wrong one sends them somewhere that cannot help.
"""
from __future__ import annotations

import time

from nessie_tests import collect, http_driver
from nessie_tests import route_observer as ro

# Imported, never restated: `collect` owns the task-row status vocabulary and a
# second copy here is the drift the rest of this harness refuses.
from nessie_tests.collect import _TERMINAL

PROBE_QUERY = "What is the weather in Boston tomorrow?"

# The event Task 1 added to `nextseek_api/services/cc_assistant.py`. Named once;
# `collect._run_roots` reads the same string off the collected rows.
NS_RUN_ROOT_EVENT = "ns_run_root"

# The probe turn is a real NS turn and has to finish before the event can exist.
# It is the harness's `full_timeout_s` by construction -- the endpoint's own
# ceiling for one turn -- and is only ever waited out when something is wrong.
NS_RUN_ROOT_TIMEOUT_S = 600.0


class PreflightRefused(RuntimeError):
    """Base for every refusal here, so a caller can map them to one exit code.

    Distinct SUBCLASSES because the remedies differ; one base because the
    consequence does not -- the run is refused, the probe turn was billed, and
    no paired arm was.
    """


class ForceRouteRejected(PreflightRefused):
    """The server ignored `force_route`. The account is almost certainly not staff."""


class NsRunRootMissing(PreflightRefused):
    """The endpoint emitted no `ns_run_root`. The image predates this branch."""


def assert_force_route_works(post_query, get_progress, *, sleep=time.sleep,
                             clock=time.monotonic, poll_interval_s: float = 2.0,
                             ns_run_root_timeout_s: float = NS_RUN_ROOT_TIMEOUT_S
                             ) -> None:
    """One out-of-scope turn forced to `ns`, checked twice. Raises on either.

    Out-of-scope on purpose. A forced decision is ROUTE_NS or ROUTE_CC and never
    ROUTE_UNRELATED, so a question the router WOULD call unrelated gives a clean
    two-valued answer: `nextseek_query` means the force landed, `unrelated` means
    it did not. Cheapest possible discriminator, and it is an NS turn either way.

    A turn that never emits `route_decided` leaves both fields None, which is
    inconclusive rather than refused — it takes the raising path too, because
    proceeding on an unproven force is the exact failure this guard exists for.
    It gets its own message: the two conditions have different remedies, and
    telling an operator whose endpoint is hung to switch accounts sends them
    somewhere that cannot help.

    Driven at `route` tier, not `full`. `route_decided` is emitted before either
    engine runs (cc_assistant.py:403, immediately after `_decide_route`), so the
    discriminator is identical — but the poll loop breaks at the event in ~2s
    and a hung probe hits `route_timeout_s=60` instead of `full_timeout_s=600`.

    THE FORCE IS CHECKED FIRST AND THE TURN IS THEN FOLLOWED TO ITS END. The
    route check is the cheap one and a dropped force makes everything after it
    meaningless, so it fails fast at 60s; only once it passes does
    `_await_ns_run_root` keep polling the SAME task_id for the join key. One
    turn, two facts, no second probe to pay for.
    """
    res = http_driver.drive(PROBE_QUERY, tier="route", post_query=post_query,
                            get_progress=get_progress, force_new=True, force_route="ns")
    route, source = res.route_obs.route, res.route_obs.source
    if source != "forced" or route == "unrelated":
        observed = f"route={route!r} source={source!r}"
        if not ro.has_route_decided(res.payload):
            # No `route_decided` event at all: the turn never reported a routing
            # decision, so there is no observation to contradict. Claiming the
            # force was dropped here would be asserting a cause we cannot see —
            # the same refusal `cost_summary` makes when it reports `unmeasured`
            # over $0.00.
            raise ForceRouteRejected(
                f"the probe turn produced NO routing decision: {observed}, "
                f"status={res.status!r}, no `route_decided` event arrived.\n"
                f"This is INCONCLUSIVE, not evidence that force_route was dropped: a "
                f"hung, erroring or unreachable endpoint looks exactly like this, and "
                f"so does a turn that died before it routed. Check the stack is up and "
                f"that one turn completes at all before suspecting the account.\n"
                f"Raising regardless — an UNPROVEN force is as unsafe to spend a "
                f"300-turn run on as a refused one.")
        raise ForceRouteRejected(
            f"force_route was not honoured: {observed}, expected "
            f"route='nextseek_query' source='forced'.\n"
            f"force_route is gated on is_staff/is_superuser and a non-admin's value is "
            f"dropped silently. Run --bayesian as a staff account; the harness default "
            f"'demo' is not one. Without this the whole run measures the router, not "
            f"the engines.")

    found, payload = _await_ns_run_root(
        get_progress, res.task_id, sleep=sleep, clock=clock,
        poll_interval_s=poll_interval_s, timeout_s=ns_run_root_timeout_s)
    if found:
        return
    status = payload.get("status")
    if status not in _TERMINAL:
        # The turn never finished, so the event's absence proves nothing about
        # the image. Same split as the inconclusive force above, and it still
        # raises: an unproven join key is as unsafe to spend a paid run on.
        raise NsRunRootMissing(
            f"the probe turn never finished: status={status!r} after "
            f"{ns_run_root_timeout_s:g}s, so no `{NS_RUN_ROOT_EVENT}` event could "
            f"be observed.\n"
            f"This is INCONCLUSIVE, not evidence that the event is missing from "
            f"the image: a hung or saturated endpoint looks exactly like this. "
            f"Check that one NS turn completes at all before rebuilding.\n"
            f"Raising regardless — a run whose join key is UNPROVEN is as unsafe "
            f"to pay for as one whose join key is known absent.")
    raise NsRunRootMissing(
        f"the forced-NS probe turn reached status={status!r} and emitted NO "
        f"`{NS_RUN_ROOT_EVENT}` event.\n"
        f"That event is PRODUCT code added on this branch "
        f"(nextseek_api/services/cc_assistant.py, `_emit_ns_run_root`), and a "
        f"running container serves a BAKED image, so it is absent until the image "
        f"is rebuilt:\n"
        f"    ./startup.sh rebuild\n"
        f"...then run this again. It is the ONLY key joining a task_id to the NS "
        f"engine's output directory: without it `collect` records "
        f"`ns_run_root_event` absent for all 127 NS arms, every NS `artifact_count` "
        f"is unobserved, and the only way to recover is to pay for the whole run "
        f"a second time.")


def _await_ns_run_root(get_progress, task_id, *, sleep, clock, poll_interval_s,
                       timeout_s) -> tuple[bool, dict]:
    """`(the event was seen, the last payload)` for one already-issued turn.

    Polls the SAME task the route probe issued, so this costs no extra turn.

    ONE retry after the row goes terminal, for the reason `collect` documents as
    fact 3: `ns_run_root` necessarily lands AFTER `query_complete`, because
    `run_query` emits `query_complete` from inside itself while the join key is
    emitted from the `finally` around the call, through a separate `task.save()`.
    A reader that stopped at the terminal status would call the event missing in
    the one-save-wide window between them, and send an operator to rebuild an
    image that is already correct. The delay is `collect.RETRY_DELAY_S`, the same
    constant, so the two cannot drift.

    Poll exceptions are NOT swallowed. The route probe has already driven this
    endpoint successfully by the time this runs, so a failure here is a real
    change in the endpoint's health and not a transient to be papered over --
    and `cli` maps the URLError it raises to its own exit code.
    """
    deadline = clock() + timeout_s
    while True:
        payload = get_progress(task_id)
        if _has_event(payload, NS_RUN_ROOT_EVENT):
            return True, payload
        if payload.get("status") in _TERMINAL:
            sleep(collect.RETRY_DELAY_S)
            payload = get_progress(task_id)
            return _has_event(payload, NS_RUN_ROOT_EVENT), payload
        if clock() >= deadline:
            return False, payload
        sleep(poll_interval_s)


def _has_event(payload, name: str) -> bool:
    return any((e or {}).get("event") == name
               for e in (payload.get("progress") or []))

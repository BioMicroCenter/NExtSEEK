"""Prove `force_route` is honoured before a paired run spends anything.

`_decide_route` (nextseek_api/services/cc_assistant.py:245-251) drops a
non-admin's `force_route` and falls back to the router. Nothing in the response
says so. A whole 300-turn run would complete, cost real money, and measure the
router instead of the engines.
"""
from __future__ import annotations

from nessie_tests import http_driver
from nessie_tests import route_observer as ro

PROBE_QUERY = "What is the weather in Boston tomorrow?"


class ForceRouteRejected(RuntimeError):
    """The server ignored `force_route`. The account is almost certainly not staff."""


def assert_force_route_works(post_query, get_progress) -> None:
    """One out-of-scope turn forced to `ns`. Raises if the force was dropped.

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
    """
    res = http_driver.drive(PROBE_QUERY, tier="route", post_query=post_query,
                            get_progress=get_progress, force_new=True, force_route="ns")
    route, source = res.route_obs.route, res.route_obs.source
    if source == "forced" and route != "unrelated":
        return
    observed = f"route={route!r} source={source!r}"
    if not ro.has_route_decided(res.payload):
        # No `route_decided` event at all: the turn never reported a routing
        # decision, so there is no observation to contradict. Claiming the force
        # was dropped here would be asserting a cause we cannot see — the same
        # refusal `cost_summary` makes when it reports `unmeasured` over $0.00.
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

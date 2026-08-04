"""Prove `force_route` is honoured before a paired run spends anything.

`_decide_route` (nextseek_api/services/cc_assistant.py:245-251) drops a
non-admin's `force_route` and falls back to the router. Nothing in the response
says so. A whole 300-turn run would complete, cost real money, and measure the
router instead of the engines.
"""
from __future__ import annotations

from nessie_tests import http_driver

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
    """
    res = http_driver.drive(PROBE_QUERY, tier="full", post_query=post_query,
                            get_progress=get_progress, force_new=True, force_route="ns")
    route, source = res.route_obs.route, res.route_obs.source
    if source == "forced" and route != "unrelated":
        return
    raise ForceRouteRejected(
        f"force_route was not honoured: route={route!r} source={source!r}, expected "
        f"route='nextseek_query' source='forced'.\n"
        f"force_route is gated on is_staff/is_superuser and a non-admin's value is "
        f"dropped silently. Run --bayesian as a staff account; the harness default "
        f"'demo' is not one. Without this the whole run measures the router, not "
        f"the engines.")

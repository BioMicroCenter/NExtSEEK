"""An admin's force_route wins over every guard that can move a turn, and never asks the router.

The Debug panel's Router dropdown pins a turn to ``ns`` or ``cc``. Four of the turns below are moved
by a guard after the router (an open wizard, a follow-up in a CC chat, "my run is done", a named SOP
file); the how-to and the out-of-scope question are turns the router itself would send elsewhere or
refuse. A pinned turn takes none of it, and for a member the same turn is never pinned.
"""
from types import SimpleNamespace

import pytest

from NessieAI.router import policy
from NessieAI.router import router as cc_router

ADMIN = SimpleNamespace(is_staff=True, is_superuser=True)
# is_staff=True: the SEEK login sets it on every account (policy.py), so a gate that read it would admit them.
MEMBER = SimpleNamespace(is_staff=True, is_superuser=False)

_CC_TURN = {"turn_id": 1, "ts": "t", "mode": "cc", "user_query": "plot the mice",
            "assistant_reply": "done", "router_choice": cc_router.ROUTE_CC, "status": "completed"}

# name -> (query, policy kwargs): the situations that move an unpinned turn.
SCENARIOS = {
    "open_pipeline_wizard": ("how many mice are there?", {"session": {"pipeline_agent": {"active": True}}}),
    "follow_up_in_a_cc_chat": ("and of those, which are lung?", {"chat_log": [_CC_TURN]}),
    "run_is_done": ("my run is done", {}),
    "named_sop_file": ("download the file for SOP 12", {}),
    "how_to": ("How do I upload samples?", {}),
    "out_of_scope": ("who won the world series", {}),
}
WANT = {"ns": cc_router.ROUTE_NS, "cc": cc_router.ROUTE_CC}


@pytest.fixture
def router_calls(monkeypatch):
    """The router says unrelated (so a turn that reached it is visibly not the pin) and counts its calls."""
    calls = []

    def decide(query, history=None):
        calls.append(query)
        return cc_router.RouteDecision(route=cc_router.ROUTE_UNRELATED, model_class=None, model_id=None,
                                       reasoning="router", source="baml")

    monkeypatch.setattr(cc_router, "decide", decide)
    monkeypatch.setattr(cc_router, "_resolve_cc_model_id", lambda: "model-x")
    return calls


def _decide(user, query, pin, kwargs):
    req = SimpleNamespace(query=query, force_route=pin)
    return policy._decide_route(user, req, force_cc=False, **kwargs)


@pytest.mark.parametrize("pin", ["ns", "cc"])
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_a_pin_is_taken_whatever_the_turn_looks_like(router_calls, scenario, pin):
    query, kwargs = SCENARIOS[scenario]
    d = _decide(ADMIN, query, pin, kwargs)
    assert (d.route, d.source) == (WANT[pin], "forced")
    assert router_calls == [], "a pinned turn must not spend a router call"


@pytest.mark.parametrize("pin", ["ns", "cc"])
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_a_member_is_never_pinned(router_calls, scenario, pin):
    query, kwargs = SCENARIOS[scenario]
    d = _decide(MEMBER, query, pin, kwargs)
    assert d.source != "forced"
    assert router_calls == [query], "a member's turn goes through the router, pin or not"

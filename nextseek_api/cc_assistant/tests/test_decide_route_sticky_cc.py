"""A1 (sticky container_cc): once a chat routes to CC and the turn completes,
the next turn stays on CC even when the router classifies it as NExtSEEK.

Seed 6 showed the failure this pins: "Find samples from a 4 week study." routed
container_cc and gave the best answer in the run; the follow-up "Just the 4 week
ones." routed nextseek_query and then failed, because no NS bundle existed to
refine. The conversation broke mid-thread.

The rule is deliberately dumb -- previous turn was container_cc AND completed ->
stay. A predicate-based variant that let "obvious" catalog lookups break out was
designed, measured and rejected as over-complex; do not reintroduce it.
"""
import pytest

import nextseek_api.services.cc_assistant as cc_svc
from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant import router_context


class _Req:
    def __init__(self, query, force_route=None):
        self.query = query
        self.force_route = force_route


class _User:
    is_staff = False
    is_superuser = False


class _Admin:
    is_staff = True
    is_superuser = False


def _turn(choice, status="completed", position=1):
    return router_context.HistoryTurn(
        position=position, user_message="prior question",
        router_choice=choice, status=status,
    )


def _ns_decision():
    return cc_router.RouteDecision(route=cc_router.ROUTE_NS, model_class=None,
                                   model_id=None, reasoning="looks like a lookup",
                                   source="baml")


@pytest.fixture
def router_says_ns(monkeypatch):
    """Pin the router to an NS decision and hand the test that exact object."""
    sentinel = _ns_decision()
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: sentinel)
    return sentinel


# --------------------------------------------------------------- the rule
def test_completed_cc_turn_makes_the_next_ns_decision_sticky(monkeypatch, router_says_ns):
    monkeypatch.setattr(cc_router, "_resolve_cc_model_id", lambda: "opus-id")
    d = cc_svc._decide_route(_User(), _Req("Just the 4 week ones."), force_cc=False,
                             history=[_turn(cc_router.ROUTE_CC)])
    assert d.route == cc_router.ROUTE_CC
    assert d.source == "sticky"          # exact literal: a probe + a test corpus reference it
    assert d.model_class == "opus"
    assert d.model_id == "opus-id"
    assert "sticky_cc" in d.reasoning
    assert "looks like a lookup" in d.reasoning   # the router's reasoning is carried


def test_a_failed_cc_turn_does_not_trap_the_chat(router_says_ns):
    d = cc_svc._decide_route(_User(), _Req("how many mice"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_CC, status="error")])
    assert d is router_says_ns


def test_empty_history_leaves_the_router_alone(router_says_ns):
    d = cc_svc._decide_route(_User(), _Req("how many mice"), force_cc=False, history=[])
    assert d is router_says_ns


def test_none_history_leaves_the_router_alone(router_says_ns):
    d = cc_svc._decide_route(_User(), _Req("how many mice"), force_cc=False, history=None)
    assert d is router_says_ns


def test_prior_ns_turn_leaves_the_router_alone(router_says_ns):
    d = cc_svc._decide_route(_User(), _Req("how many mice"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_NS)])
    assert d is router_says_ns


def test_only_the_last_turn_matters(router_says_ns):
    """Pins history[-1], not "any turn in this chat was CC"."""
    d = cc_svc._decide_route(
        _User(), _Req("how many mice"), force_cc=False,
        history=[_turn(cc_router.ROUTE_CC, position=1),
                 _turn(cc_router.ROUTE_NS, position=2)])
    assert d is router_says_ns


# ------------------------------------------------- what the guard must NOT touch
def test_a_cc_decision_is_returned_untouched(monkeypatch):
    sentinel = cc_router.RouteDecision(route=cc_router.ROUTE_CC, model_class="opus",
                                       model_id=None, reasoning="agentic", source="baml")
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: sentinel)
    d = cc_svc._decide_route(_User(), _Req("write me a script"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_CC)])
    assert d is sentinel
    assert d.source == "baml"          # not relabelled "sticky"


def test_unrelated_is_never_converted_to_cc(monkeypatch):
    """Out-of-scope questions must keep getting the canned refusal.

    If sticky captured `unrelated` too, "what's the weather" inside a CC chat
    would spin up an Opus container instead of returning UNRELATED_CANNED_TEXT.
    """
    sentinel = cc_router.RouteDecision(route=cc_router.ROUTE_UNRELATED, model_class=None,
                                       model_id=None, reasoning="out of scope",
                                       source="baml")
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: sentinel)
    d = cc_svc._decide_route(_User(), _Req("what's the weather in Boston"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_CC)])
    assert d is sentinel
    assert d.route == cc_router.ROUTE_UNRELATED


# ------------------------------------------------------------ precedence
def test_force_route_cc_beats_sticky(router_says_ns):
    d = cc_svc._decide_route(_Admin(), _Req("x", force_route="cc"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_CC)])
    assert d.route == cc_router.ROUTE_CC
    assert d.source == "forced"


def test_force_route_ns_beats_sticky(router_says_ns):
    """The admin escape hatch out of a sticky chat -- the only one there is."""
    d = cc_svc._decide_route(_Admin(), _Req("x", force_route="ns"), force_cc=False,
                             history=[_turn(cc_router.ROUTE_CC)])
    assert d.route == cc_router.ROUTE_NS
    assert d.source == "forced"


def test_active_pipeline_beats_sticky(router_says_ns):
    """A mid-flow samplesheet build keeps its confirm/tweak turns on NS."""
    d = cc_svc._decide_route(_User(), _Req("yes, launch it"), force_cc=False,
                             session={"pipeline_agent": {"active": True}},
                             history=[_turn(cc_router.ROUTE_CC)])
    assert d.route == cc_router.ROUTE_NS
    assert d.source == "pipeline"


# ------------------------------------------------------------ never crash
def test_broken_history_falls_through_to_the_router(router_says_ns):
    """router.py's contract is that routing never crashes on bad history."""
    class _Exploding:
        @property
        def router_choice(self):
            raise RuntimeError("boom")

        @property
        def status(self):
            raise RuntimeError("boom")

    d = cc_svc._decide_route(_User(), _Req("how many mice"), force_cc=False,
                             history=[_Exploding()])
    assert d is router_says_ns

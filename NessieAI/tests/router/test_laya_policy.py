"""Policy after laya: the follow-up split, pipeline guard and CC-unavailable fallback keep the laya record (SPEC test 5)."""
from __future__ import annotations

import pytest

from NessieAI.router import followup, policy
from NessieAI.router import router as cc_router
from NessieAI.router import router_context

REC = {"mode": "live", "gate": "pass", "route": "nextseek_query", "revision": "20261003-abcdef123456"}


class _Req:
    def __init__(self, query):
        self.query, self.force_route = query, None


class _User:
    is_superuser = False


@pytest.fixture
def laya_says_ns(monkeypatch):
    d = cc_router.RouteDecision(route="nextseek_query", model_class=None, model_id=None, reasoning="laya p=0.95",
                                source="laya", router_model="laya:20261003-abcdef123456", laya=REC)
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: d)
    monkeypatch.setattr(cc_router, "_resolve_cc_model_id", lambda: "opus-id")
    monkeypatch.delenv(followup.FOLLOWUP_ROUTING_ENV, raising=False)
    return d


def _turns():
    h = [router_context.HistoryTurn(position=1, user_message="prior", router_choice="nextseek_query")]
    log = [{"turn_id": 1, "user_query": "q", "router_choice": "nextseek_query", "status": "completed", "mode": "graph_query"}]
    return h, log


def test_followup_split_acts_on_a_laya_ns_decision(laya_says_ns):
    h, log = _turns()
    d = policy._decide_route(_User(), _Req("Plot the species of those."), force_cc=False, history=h, chat_log=log)
    assert (d.route, d.source, d.attempted_source) == ("container_cc", "followup", "laya")
    assert d.router_model == laya_says_ns.router_model and d.laya == REC


def test_ns_shaped_followup_keeps_the_laya_decision(laya_says_ns):
    h, log = _turns()
    d = policy._decide_route(_User(), _Req("Break those down by sex."), force_cc=False, history=h, chat_log=log)
    assert d is laya_says_ns


def test_pipeline_guard_keeps_model_and_block(laya_says_ns):
    d = policy._decide_route(_User(), _Req("anything"), force_cc=False, session={"pipeline_agent": {"active": True}})
    assert d.source == "pipeline" and d.attempted_source is None
    assert d.router_model == laya_says_ns.router_model and d.laya == REC


def test_cc_unavailable_fallback_keeps_model_and_block(laya_says_ns):
    h, log = _turns()
    d = policy._decide_route(_User(), _Req("Plot the species of those."), force_cc=False, history=h, chat_log=log)
    back = policy._fallback_when_cc_unavailable(d, lambda: (False, "down"))
    assert back.source == "cc_unavailable" and back.route == "nextseek_query"
    assert back.router_model == laya_says_ns.router_model and back.laya == REC

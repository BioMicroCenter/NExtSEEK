"""route_decided carries the laya block, and no query text lands anywhere (SPEC tests 5, 8)."""
from __future__ import annotations

import inspect
import json
import logging

from NessieAI.cc import turn
from NessieAI.router import router as cc_router
from NessieAI.router.laya import RECORD_KEYS
from NessieAI.tests.router._laya_support import REV, fake_post, reply, setup

SECRET = "zebrafish-xyzzy-secret-query"


def _d(**kw):
    return cc_router.RouteDecision(route="nextseek_query", model_class=None, model_id=None, reasoning="r",
                                   source="baml", **kw)


def test_laya_is_a_router_record_field():
    assert "laya" in cc_router.ROUTER_RECORD_FIELDS
    assert cc_router.router_record(_d(laya={"mode": "shadow"}))["laya"] == {"mode": "shadow"}


def test_route_decided_fields_carry_laya_only_when_present():
    base = {"router_usage": {}, "router_cost_usd": 0.0}
    assert "laya" not in cc_router.router_cost_fields(_d(**base))
    assert cc_router.router_cost_fields(_d(laya={"mode": "shadow"}, **base))["laya"] == {"mode": "shadow"}


def test_route_decided_fields_carry_the_attempted_route():
    """A follow-up or sticky turn's teacher is the router's own answer, which only attempted_route holds."""
    base = {"router_usage": {}, "router_cost_usd": 0.0}
    assert cc_router.router_cost_fields(_d(attempted_route="nextseek_query", **base))["attempted_route"] \
        == "nextseek_query"
    assert cc_router.router_cost_fields(_d(**base))["attempted_route"] is None
    assert cc_router.router_cost_fields(_d(attempted_route="nextseek_query")) == {}  # a forced turn: no fields


def test_route_decided_event_still_has_one_spread():
    src = inspect.getsource(turn)
    event = src[src.index('send_event("route_decided"'):].split("_record_ledger_row")[0]
    assert event.count("router_cost_fields(decision)") == 1


def test_record_and_logs_hold_no_query_text(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    laya = setup(tmp_path, monkeypatch, live=REV)
    fake_post(monkeypatch, reply())
    monkeypatch.setattr("NessieAI.router.laya_common.condense", lambda q, h: "STATE")
    monkeypatch.setattr(cc_router, "_legacy_decide", lambda q, h=None: _d())
    monkeypatch.setattr("random.random", lambda: 0.99)
    d = cc_router.decide(SECRET, [])
    assert set(d.laya) == set(RECORD_KEYS)
    assert SECRET not in json.dumps(d.laya) and SECRET not in caplog.text
    fake_post(monkeypatch, RuntimeError(SECRET))
    d = cc_router.decide(SECRET, [])
    assert d.laya["error"] == "RuntimeError" and SECRET not in json.dumps(d.laya) + caplog.text

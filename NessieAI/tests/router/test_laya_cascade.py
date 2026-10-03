"""decide() with laya: live fast path, shadow, audit, fall-through (SPEC s2; tests 1, 2, 4, 9)."""
from __future__ import annotations

import socket

import pytest

from NessieAI.router import laya, policy
from NessieAI.router import router as cc_router
from NessieAI.tests.router._laya_support import REV, fake_post, reply, setup

HIST = ["h"]


class _Baml:
    def __init__(self, route="nextseek_query"):
        self.route, self.calls = route, []

    def __call__(self, query, history=None):
        self.calls.append((query, history))
        return cc_router.RouteDecision(route=self.route, model_class="opus" if self.route == "container_cc" else None,
                                       model_id=None, reasoning="baml says", source="baml", router_model="gemini")


@pytest.fixture
def baml(monkeypatch):
    b = _Baml()
    monkeypatch.setattr(cc_router, "_legacy_decide", b)
    monkeypatch.setattr(cc_router, "_resolve_cc_model_id", lambda: "opus-id")
    return b


def test_off_is_todays_decision_and_makes_no_call(tmp_path, monkeypatch, baml):
    setup(tmp_path, monkeypatch)
    calls = fake_post(monkeypatch, reply())
    d = cc_router.decide("find samples", HIST)
    assert d.laya is None and d.source == "baml" and d.route == "nextseek_query"
    assert calls == [] and baml.calls == [("find samples", HIST)]


def test_live_pass_is_laya_and_baml_not_called(tmp_path, monkeypatch, baml):
    setup(tmp_path, monkeypatch, live=REV)
    fake_post(monkeypatch, reply())
    monkeypatch.setattr("random.random", lambda: 0.99)
    d = cc_router.decide("write a script", HIST)
    assert (d.source, d.route, d.model_class, d.model_id) == ("laya", "container_cc", "opus", "opus-id")
    assert d.router_model == "laya:" + REV and d.reasoning.startswith("laya p=")
    assert d.laya["gate"] == "pass" and d.laya["mode"] == "live" and baml.calls == []


def test_live_ns_pick_has_no_model(tmp_path, monkeypatch, baml):
    setup(tmp_path, monkeypatch, live=REV)
    fake_post(monkeypatch, reply(ns=0.97, cc=0.02))
    monkeypatch.setattr("random.random", lambda: 0.99)
    d = cc_router.decide("find samples", HIST)
    assert (d.source, d.route, d.model_class, d.model_id) == ("laya", "nextseek_query", None, None)


@pytest.mark.parametrize("outcome,gate", [
    (TimeoutError(), "timeout"), (socket.timeout(), "timeout"), (ValueError(), "bad_json"),
    (OSError(), "http_error"), (RuntimeError("x"), "exception"),
    (reply(revision="x"), "revision_mismatch"), (reply(truncated=True), "truncated"),
    (reply(state_tokens=999), "too_many_tokens"), (reply(ns=.5, cc=.45, un=.05), "below_threshold"),
    (reply(ns=0, cc=0, un=1), "unrelated"),
])
def test_live_gate_failure_runs_baml_once_with_same_input(tmp_path, monkeypatch, baml, outcome, gate):
    setup(tmp_path, monkeypatch, live=REV)
    fake_post(monkeypatch, outcome)
    d = cc_router.decide("find samples", HIST)
    assert baml.calls == [("find samples", HIST)] and d.source == "baml" and d.route == "nextseek_query"
    assert d.laya["gate"] == gate and d.laya["mode"] == "live"


def test_live_hash_mismatch_nonlatin_followup_fall_through(tmp_path, monkeypatch, baml):
    for kw, q, gate in [({"cal_prompt_hash": "x"}, "q", "hash_mismatch"), ({}, "样本", "non_latin"),
                        ({"followup": "cc"}, "q", "followup_cc")]:
        baml.calls.clear()
        setup(tmp_path, monkeypatch, live=REV, **kw)
        fake_post(monkeypatch, reply())
        d = cc_router.decide(q, HIST)
        assert d.source == "baml" and d.laya["gate"] == gate and len(baml.calls) == 1


def test_missing_calibration_file_and_posterior_are_todays_path(tmp_path, monkeypatch, baml):
    setup(tmp_path, monkeypatch, live=REV, write_files=False)
    calls = fake_post(monkeypatch, reply())
    d = cc_router.decide("q", HIST)
    assert d.source == "baml" and d.laya is None and calls == [] and len(baml.calls) == 1
    setup(tmp_path, monkeypatch, live=REV, posterior=True)
    monkeypatch.setattr(cc_router, "_posterior_enabled_decide", lambda q, h=None: baml(q, h))
    d = cc_router.decide("q", HIST)
    assert d.source == "baml" and d.laya is None and calls == []


def test_exception_inside_laya_code_returns_baml_with_error(tmp_path, monkeypatch, baml):
    setup(tmp_path, monkeypatch, live=REV)

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(laya, "start", boom)
    d = cc_router.decide("q", HIST)
    assert d.source == "baml" and len(baml.calls) == 1
    assert d.laya["error"] == "RuntimeError" and d.laya["gate"] == "exception"


def test_live_refused_values_never_route(tmp_path, monkeypatch, baml):
    for live in ("1", "true", "yes", "20250101-000000000000"):
        setup(tmp_path, monkeypatch, shadow="1", live=live)
        fake_post(monkeypatch, reply())
        d = cc_router.decide("q", HIST)
        assert d.source == "baml" and d.laya["mode"] == "shadow"


@pytest.mark.parametrize("baml_route", ["nextseek_query", "container_cc", "unrelated"])
def test_shadow_never_changes_the_route(tmp_path, monkeypatch, baml_route):
    b = _Baml(baml_route)
    monkeypatch.setattr(cc_router, "_legacy_decide", b)
    setup(tmp_path, monkeypatch, shadow="1")
    fake_post(monkeypatch, reply(ns=0, cc=0.99, un=0.01))
    d = cc_router.decide("q", HIST)
    assert d.route == baml_route and d.source == "baml" and d.router_model == "gemini"
    assert d.laya["mode"] == "shadow" and d.laya["route"] == "container_cc" and d.laya["gate"] == "pass"
    assert len(b.calls) == 1


def test_shadow_timeout_and_error_leave_baml_route(tmp_path, monkeypatch, baml):
    setup(tmp_path, monkeypatch, shadow="1")
    fake_post(monkeypatch, TimeoutError())
    d = cc_router.decide("q", HIST)
    assert d.source == "baml" and d.laya["gate"] == "timeout"


def test_live_audit_runs_baml_on_the_turn_and_returns_laya(tmp_path, monkeypatch, baml):
    setup(tmp_path, monkeypatch, live=REV)
    fake_post(monkeypatch, reply())
    baml.route = "nextseek_query"
    monkeypatch.setattr("random.random", lambda: 0.01)
    d = cc_router.decide("q", HIST)
    assert d.source == "laya" and d.route == "container_cc"
    assert baml.calls == [("q", HIST)]
    assert d.laya["mode"] == "audit" and d.laya["baml_route"] == "nextseek_query"


def test_audit_not_drawn_at_the_rate(tmp_path, monkeypatch, baml):
    setup(tmp_path, monkeypatch, live=REV)
    fake_post(monkeypatch, reply())
    monkeypatch.setattr("random.random", lambda: 0.05)
    d = cc_router.decide("q", HIST)
    assert baml.calls == [] and d.laya["mode"] == "live" and d.laya["baml_route"] is None


# --- test 1: a forced turn returns before any laya code ----------------------------------------

class _Req:
    def __init__(self, force_route=None):
        self.query, self.force_route = "q", force_route


class _User:
    def __init__(self, admin):
        self.is_superuser = admin


@pytest.mark.parametrize("mode_kw", [{"shadow": "1"}, {"live": REV}])
@pytest.mark.parametrize("req,user,force_cc", [
    (_Req(), _User(False), True), (_Req("cc"), _User(True), False), (_Req("ns"), _User(True), False)])
def test_forced_turns_never_touch_laya(tmp_path, monkeypatch, baml, mode_kw, req, user, force_cc):
    setup(tmp_path, monkeypatch, **mode_kw)

    def boom(*a, **k):
        raise AssertionError("laya touched on a forced turn")
    monkeypatch.setattr(laya, "mode", boom)
    monkeypatch.setattr(laya, "start", boom)
    d = policy._decide_route(user, req, force_cc=force_cc)
    assert d.source == "forced" and d.laya is None and baml.calls == []

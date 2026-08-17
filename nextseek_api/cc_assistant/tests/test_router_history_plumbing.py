"""F §12.6/§12.6b/§12.8: history threaded through decide() + real reasoning."""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

from dmac_assistant.router.baml_client.types import Route

from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant import router_context as rc

_REPO = Path(__file__).resolve().parents[3]


def _sample_history() -> list[rc.HistoryTurn]:
    return [
        rc.HistoryTurn(
            position=1,
            user_message="find NHP seq data",
            router_choice="nextseek_query",
            result_count=139,
            sample_uids=["D.SEQ-1"],
            assistant_reply="139 records",
        ),
    ]


def test_decide_passes_history_through(monkeypatch):
    captured: dict = {}

    def capturing(query, history=None):
        captured["query"] = query
        captured["history"] = history
        return cc_router.RouteDecision(
            route=cc_router.ROUTE_NS,
            model_class=None,
            model_id=None,
            reasoning="ok",
            source="baml",
        )

    monkeypatch.setattr(cc_router.posterior_selector, "posterior_routing_enabled", lambda: False)
    monkeypatch.setattr(cc_router, "_route_query", capturing)
    hist = _sample_history()
    cc_router.decide("counts of those monkeys", history=hist)
    assert captured["query"] == "counts of those monkeys"
    assert captured["history"] is hist


def test_decide_default_empty_history(monkeypatch):
    captured: dict = {}

    def capturing(query, history=None):
        captured["history"] = history
        return None

    monkeypatch.setattr(cc_router.posterior_selector, "posterior_routing_enabled", lambda: False)
    monkeypatch.setattr(cc_router, "_route_query", capturing)
    cc_router.decide("hello")
    assert captured["history"] is None


def test_heuristic_ignores_history(monkeypatch):
    monkeypatch.setattr(cc_router.posterior_selector, "posterior_routing_enabled", lambda: False)
    monkeypatch.setattr(cc_router, "_route_query", lambda q, history=None: None)
    d_none = cc_router.decide("Find me all mice treated with NDMA.")
    d_hist = cc_router.decide(
        "Find me all mice treated with NDMA.", history=_sample_history()
    )
    assert d_none.route == d_hist.route == cc_router.ROUTE_NS
    assert d_none.source == d_hist.source == "heuristic"


def test_route_query_surfaces_real_reasoning(monkeypatch):
    class FakeDecision:
        route = Route.NextseekQuery
        reasoning = "follow-up to turn 1"
        model_class = None

    class FakeAgent:
        def __init__(self, capabilities):
            pass

        async def route(self, user_query, history=None):
            return FakeDecision()

    class FakeBamlClient:
        async def RouteQuery(self, input):
            return FakeDecision()

    def fake_load():
        return FakeAgent, lambda path=None: [], Route, FakeBamlClient()

    monkeypatch.setattr(cc_router, "_load_router_deps", fake_load)
    d = cc_router._route_query("q", history=[])
    assert d is not None
    assert d.reasoning == "follow-up to turn 1"
    assert d.source == "baml"


def test_thread_through_call_site_ast():
    """§12.8 gate, comment-proof: the LIVE decide() call in services/cc_assistant.py
    binds history= to a name assigned from router_context.build_history(...),
    and the route_decided dict literal carries a reasoning key bound to
    decision.reasoning."""
    src = (
        Path(__file__).resolve().parents[3]
        / "nextseek_api"
        / "services"
        / "cc_assistant.py"
    ).read_text()
    tree = ast.parse(src)

    decide_calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "decide"
    ]
    assert decide_calls, "no cc_router.decide(...) call found"
    assert any(
        kw.arg == "history" for c in decide_calls for kw in c.keywords
    ), "decide() is called without history= — Component F plumbing dropped"

    bh_assigns = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute)
        and n.value.func.attr == "build_history"
    ]
    assert bh_assigns, "router_context.build_history(...) is never assigned"

    reasoning_keys = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Dict)
        and any(
            isinstance(k, ast.Constant) and k.value == "reasoning" for k in n.keys
        )
    ]
    assert reasoning_keys, "route_decided payload has no reasoning key"


def test_history_to_baml_maps_all_routes_and_empty():
    ns = rc.HistoryTurn(position=1, user_message="a", router_choice="nextseek_query", assistant_reply="r")
    cc = rc.HistoryTurn(position=2, user_message="b", router_choice="container_cc", assistant_reply="r")
    un = rc.HistoryTurn(position=3, user_message="c", router_choice="unrelated", assistant_reply="r")
    out = cc_router._history_to_baml([ns, cc, un], Route)
    assert out[0].router_choice == Route.NextseekQuery
    assert out[1].router_choice == Route.ContainerCC
    assert out[2].router_choice == Route.Unrelated
    assert cc_router._history_to_baml(None, Route) == []


def test_route_from_baml_sentinel_and_unrelated():
    class D:
        def __init__(self, route, reasoning):
            self.route = route
            self.reasoning = reasoning

    assert cc_router._route_from_baml(D(Route.NextseekQuery, cc_router._FALLBACK_SENTINEL), Route) is None
    unrelated = cc_router._route_from_baml(D(Route.Unrelated, "nope"), Route)
    assert unrelated.route == cc_router.ROUTE_UNRELATED
    cc = cc_router._route_from_baml(D(Route.ContainerCC, ""), Route)
    assert cc.route == cc_router.ROUTE_CC
    assert cc.reasoning == "baml"


def test_resolve_model_id_none_and_failure(monkeypatch):
    assert cc_router._resolve_model_id(None) is None
    boom = types.ModuleType("dmac_assistant.router.models")
    boom.load_model_class_map = lambda path=None: (_ for _ in ()).throw(RuntimeError("no map"))
    boom.resolve_cc_model = lambda: (_ for _ in ()).throw(RuntimeError("no opus"))
    monkeypatch.setitem(sys.modules, "dmac_assistant.router.models", boom)
    assert cc_router._resolve_model_id("opus") is None
    assert cc_router._resolve_cc_model_id() is None


def test_heuristic_mixed_signals_and_neither():
    mixed_ns = cc_router._heuristic("find a python file")
    assert mixed_ns.route in (cc_router.ROUTE_NS, cc_router.ROUTE_CC)
    neither = cc_router._heuristic("hello there")
    assert neither.route == cc_router.ROUTE_NS
    leading = cc_router._heuristic("write samples to disk")
    assert leading.route == cc_router.ROUTE_CC

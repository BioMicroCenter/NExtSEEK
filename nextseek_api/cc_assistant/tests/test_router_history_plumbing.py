"""F §12.6/§12.6b/§12.8: history threaded through decide() + real reasoning."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "dmac_assistant" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import router as cc_router  # noqa: E402
import router_context as rc  # noqa: E402
from dmac_assistant.router.baml_client.types import Route  # noqa: E402


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

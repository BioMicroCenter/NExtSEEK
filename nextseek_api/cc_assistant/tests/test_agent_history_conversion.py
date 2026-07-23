"""G-2: the LAST hop — router_context turns → BAML RouterInput — actually
executes. Seam at the BAML call (b.RouteQuery), NOT above agent.py."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "dmac_assistant" / "src"))
sys.path.insert(0, str(_REPO / "nextseek_api" / "cc_assistant"))

from dmac_assistant.router import agent as agent_mod  # noqa: E402
from dmac_assistant.router.baml_client.types import Route, RouterDecision  # noqa: E402
import router_context as rc  # noqa: E402


def _hist():
    return [
        rc.HistoryTurn(
            position=1,
            user_message="find NHP seq data",
            router_choice="nextseek_query",
            result_count=139,
            sample_uids=["D.SEQ-1"],
            assistant_reply="139 records",
        ),
        rc.HistoryTurn(
            position=2,
            user_message="off topic",
            router_choice="unrelated",
            assistant_reply=None,
        ),
        rc.HistoryTurn(
            position=3,
            user_message="crashed",
            router_choice="container_cc",
            status="error",
            error="timeout",
            assistant_reply=None,
        ),
    ]


def test_to_baml_history_converts_all_kinds_and_reverses_aliases():
    out = agent_mod._to_baml_history(_hist())
    assert [t.position for t in out] == [1, 2, 3]
    assert out[0].router_choice == Route.NextseekQuery
    assert out[1].router_choice == Route.Unrelated
    assert out[2].router_choice == Route.ContainerCC
    assert out[0].result_count == 139 and out[0].sample_uids == ["D.SEQ-1"]
    assert out[1].assistant_reply is None
    assert out[2].status == "error" and out[2].error == "timeout"


def test_to_baml_history_drops_malformed_never_raises():
    out = agent_mod._to_baml_history(
        [{"user_message": "no position"}, None, _hist()[0]]
    )
    assert [t.position for t in out] == [1]


def test_route_builds_router_input_with_converted_history(monkeypatch):
    captured = {}

    async def fake_route_query(*, input):
        captured["input"] = input
        return RouterDecision(
            route=Route.NextseekQuery,
            model_class=None,
            reasoning="follows turn 1",
        )

    monkeypatch.setattr(agent_mod.b, "RouteQuery", fake_route_query)
    agent = agent_mod.RouterAgent(capabilities=[])
    decision = asyncio.run(
        agent.route("counts of those monkeys", history=_hist())
    )
    ri = captured["input"]
    assert ri.user_query == "counts of those monkeys"
    assert [t.position for t in ri.history] == [1, 2, 3]
    assert decision.reasoning == "follows turn 1"


def test_route_empty_history_default(monkeypatch):
    captured = {}

    async def fake_route_query(*, input):
        captured["input"] = input
        return RouterDecision(route=Route.NextseekQuery, model_class=None, reasoning="r")

    monkeypatch.setattr(agent_mod.b, "RouteQuery", fake_route_query)
    asyncio.run(agent_mod.RouterAgent(capabilities=[]).route("q"))
    assert captured["input"].history == []

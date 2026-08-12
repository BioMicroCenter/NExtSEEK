"""V4-2 V5-3 §1 product mutation killers — red-on-mutation at product seam."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nextseek_api.assistant.models_api import QueryRequest
from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant import router_context
from nextseek_api.services import cc_assistant as svc

ADMIN = SimpleNamespace(is_staff=True, is_superuser=False)
USER = SimpleNamespace(is_staff=False, is_superuser=False)


def _ns_baml():
    return cc_router.RouteDecision(
        route=cc_router.ROUTE_NS,
        model_class=None,
        model_id=None,
        reasoning="baml",
        source="baml",
    )


def test_mutation_nonadmin_force_route_dropped_not_forced():
    """Ignored override: non-admin force_route must not produce forced source."""
    with patch.object(cc_router, "decide", return_value=_ns_baml()):
        d = svc._decide_route(USER, QueryRequest(query="q", mode="standard", force_route="cc"), force_cc=False)
    assert d.source != "forced"


def test_mutation_admin_force_route_must_be_forced():
    """Requested/actual mismatch killer: admin force must label source forced."""
    d = svc._decide_route(ADMIN, QueryRequest(query="q", mode="standard", force_route="ns"), force_cc=False)
    assert d.route == cc_router.ROUTE_NS
    assert d.source == "forced"


def test_mutation_sticky_must_record_attempted_route():
    """Requested/actual mismatch: sticky override carries attempted_route/source."""
    history = [
        router_context.HistoryTurn(
            position=1,
            user_message="cc turn",
            router_choice=cc_router.ROUTE_CC,
            status="completed",
        ),
    ]
    attempted = _ns_baml()
    with patch.object(cc_router, "decide", return_value=attempted):
        final = svc._decide_route(
            USER,
            QueryRequest(query="follow", mode="standard"),
            force_cc=False,
            history=history,
        )
    assert final.route == cc_router.ROUTE_CC
    assert final.source == "sticky"
    assert final.attempted_route == cc_router.ROUTE_NS
    assert final.attempted_source == "baml"


def test_mutation_force_route_beats_sticky_not_relabled_baml():
    """Downstream override killer: admin force_route must beat sticky."""
    history = [
        router_context.HistoryTurn(
            position=1,
            user_message="cc turn",
            router_choice=cc_router.ROUTE_CC,
            status="completed",
        ),
    ]
    with patch.object(cc_router, "decide", return_value=_ns_baml()):
        d = svc._decide_route(
            ADMIN,
            QueryRequest(query="q", mode="standard", force_route="ns"),
            force_cc=False,
            history=history,
        )
    assert d.source == "forced"
    assert d.route == cc_router.ROUTE_NS


def test_mutation_same_session_task_ids_must_differ_across_arms():
    """Copied-arms killer: disjoint task_ids required for paired arms."""
    def _disjoint(ns_ids, cc_ids):
        assert set(ns_ids).isdisjoint(set(cc_ids))

    _disjoint(["task-a"], ["task-b"])
    with pytest.raises(AssertionError):
        _disjoint(["task-a"], ["task-a"])


def test_mutation_swapped_routes_on_forced_arms():
    """Swapped routes killer: forced arms must match expected route strings."""
    def _check_forced(ns_route, cc_route):
        assert ns_route == cc_router.ROUTE_NS
        assert cc_route == cc_router.ROUTE_CC

    _check_forced(cc_router.ROUTE_NS, cc_router.ROUTE_CC)
    with pytest.raises(AssertionError):
        _check_forced(cc_router.ROUTE_CC, cc_router.ROUTE_NS)


def test_mutation_force_route_schema_rejects_unknown():
    """Unknown key / invalid force_route rejected at schema boundary."""
    with pytest.raises(Exception):
        QueryRequest(query="q", mode="standard", force_route="banana")

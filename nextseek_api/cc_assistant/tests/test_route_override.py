"""Admin-only route override: QueryRequest.force_route + _decide_route gate.

Mirrors the use_prod pattern — the override is enforced server-side, not just
hidden in the UI: a non-admin's force_route is ignored and falls back to the
BAML router.
"""
from types import SimpleNamespace

import pytest

from nextseek_api.assistant.models_api import QueryRequest
from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.services import cc_assistant as svc


# --- the request field (hermetic, pydantic only) ---

def test_force_route_defaults_none():
    assert QueryRequest(query="hi", mode="standard").force_route is None


@pytest.mark.parametrize("val", ["auto", "ns", "cc"])
def test_force_route_accepts_valid(val):
    assert QueryRequest(query="hi", mode="standard", force_route=val).force_route == val


def test_force_route_rejects_bogus():
    with pytest.raises(Exception):
        QueryRequest(query="hi", mode="standard", force_route="banana")


# --- the admin-gated dispatch helper ---

ADMIN = SimpleNamespace(is_staff=True, is_superuser=False)
SUPER = SimpleNamespace(is_staff=False, is_superuser=True)
USER = SimpleNamespace(is_staff=False, is_superuser=False)


@pytest.fixture(autouse=True)
def _stub_router(monkeypatch):
    """decide() returns a BAML-sourced sentinel so tests can tell 'forced' from
    'fell back to the router', and _resolve_cc_model_id is offline."""
    sentinel = cc_router.RouteDecision(
        route=cc_router.ROUTE_NS, model_class=None, model_id=None,
        reasoning="baml-sentinel", source="baml",
    )
    monkeypatch.setattr(cc_router, "decide", lambda q: sentinel)
    monkeypatch.setattr(cc_router, "_resolve_cc_model_id", lambda: "model-x")


def _req(force_route=None):
    return QueryRequest(query="find mice", mode="standard", force_route=force_route)


def test_admin_forces_ns():
    d = svc._decide_route(ADMIN, _req("ns"), force_cc=False)
    assert d.route == cc_router.ROUTE_NS
    assert d.source == "forced"


def test_admin_forces_cc():
    d = svc._decide_route(SUPER, _req("cc"), force_cc=False)
    assert d.route == cc_router.ROUTE_CC
    assert d.source == "forced"
    assert d.model_class == "opus" and d.model_id == "model-x"


def test_nonadmin_force_route_is_ignored():
    # non-admin's force_route dropped -> falls back to the BAML router sentinel
    d = svc._decide_route(USER, _req("cc"), force_cc=False)
    assert d.source == "baml"


@pytest.mark.parametrize("val", [None, "auto"])
def test_auto_or_none_uses_router(val):
    d = svc._decide_route(ADMIN, _req(val), force_cc=False)
    assert d.source == "baml"


def test_cc_endpoint_still_forces_cc_for_anyone():
    # the dedicated cc/query/async endpoint (force_cc=True) is unchanged
    d = svc._decide_route(USER, _req(None), force_cc=True)
    assert d.route == cc_router.ROUTE_CC
    assert d.source == "forced"

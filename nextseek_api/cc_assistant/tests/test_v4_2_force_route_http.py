"""V4-2 V5-3 §1: authenticated HTTP cross for admin force_route.

Crosses POST → CCAssistantViewSet → _decide_route → sticky guard → dispatch
observation via progress events (no live provider).
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant.cc_provision import ProjectIdentity
from nextseek_api.services import cc_assistant as svc

pytestmark = pytest.mark.django_db(transaction=True)

CC_QUERY_URL = "/nextseek_api/cc-assistant/query/async/"
_FAKE_PROJECT = ProjectIdentity(id="1", slug="testproj", title="Test Project")


def _wait_terminal(task_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = QueryTask.objects.get(task_id=task_id)
        if t.status in ("completed", "error"):
            return t
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} not terminal after {timeout}s")


@pytest.fixture(autouse=True)
def _patch_dispatch(monkeypatch):
    def fake_run_query(session, config, query, send_event, credentials=None):
        send_event("query_complete", {"reply": "ns ok", "bundle_id": 1})

    def fake_cc_turn(**kw):
        kw["send_event"]("query_complete", {"reply": "cc ok"})

    monkeypatch.setattr(svc, "run_query", fake_run_query)
    monkeypatch.setattr(svc, "run_query_plan", fake_run_query)
    monkeypatch.setattr(cc_engine, "cc_runner_available", lambda: (True, "ok"))
    monkeypatch.setattr(cc_engine, "run_cc_turn", fake_cc_turn)
    monkeypatch.setattr(svc, "_record_ledger_row", lambda *a, **k: None)
    _patch_cc_project(monkeypatch)
    yield


@pytest.fixture(autouse=True)
def _patch_cc_model(monkeypatch):
    monkeypatch.setattr(cc_router, "_resolve_cc_model_id", lambda: "model-x")
    yield


@pytest.fixture(autouse=True)
def _patch_chat_config(monkeypatch):
    cfg = type("Cfg", (), {"API_USER": "", "API_PASS": ""})()
    monkeypatch.setattr(
        "nextseek_api.services.cc_assistant._select_chat_config",
        lambda request, req: cfg,
    )
    yield


@pytest.fixture(autouse=True)
def _assistant_project_permission():
    with patch(
        "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
        return_value=True,
    ):
        yield


@pytest.fixture
def admin_user(db):
    return get_user_model().objects.create_user(
        "v42-admin", password="x", is_staff=True,
    )


@pytest.fixture
def regular_user(db):
    return get_user_model().objects.create_user("v42-user", password="x")


def _client_for(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _patch_cc_project(monkeypatch):
    monkeypatch.setattr(
        "nextseek_api.cc_assistant.cc_provision.resolve_user_project",
        lambda *a, **k: _FAKE_PROJECT,
    )


def _post_query(client, text, session_id=None, **extra):
    body = {"query": text, "mode": "standard", **extra}
    if session_id:
        body["session_id"] = str(session_id)
    resp = client.post(CC_QUERY_URL, body, format="json")
    assert resp.status_code == 202, resp.content
    payload = resp.json()
    return payload["task_id"], payload.get("session_id")


def _route_decided(task):
    return next(p for p in task.progress if p["event"] == "route_decided")["data"]


def test_http_admin_force_route_ns_crosses_to_dispatch(admin_user, monkeypatch):
    """Admin force_route=ns via HTTP → route_decided forced → run_query dispatch."""
    client = _client_for(admin_user)
    dispatched = []

    def tracking_run_query(session, config, query, send_event, credentials=None):
        dispatched.append(query)
        send_event("query_complete", {"reply": "ns ok", "bundle_id": 1})

    monkeypatch.setattr(svc, "run_query", tracking_run_query)

    tid, _ = _post_query(client, "find mice", force_route="ns")
    task = _wait_terminal(tid)
    rd = _route_decided(task)
    assert rd["route"] == cc_router.ROUTE_NS
    assert rd["source"] == "forced"
    assert dispatched == ["find mice"]


def test_http_admin_force_route_cc_crosses_to_dispatch(admin_user):
    client = _client_for(admin_user)

    tid, _ = _post_query(client, "write code", force_route="cc")
    task = _wait_terminal(tid)
    rd = _route_decided(task)
    assert rd["route"] == cc_router.ROUTE_CC
    assert rd["source"] == "forced"


def _baml_ns(reasoning="baml-wins"):
    return cc_router.RouteDecision(
        route=cc_router.ROUTE_NS,
        model_class=None,
        model_id=None,
        reasoning=reasoning,
        source="baml",
    )


def test_http_nonadmin_force_route_ignored_at_controller(regular_user, monkeypatch):
    """Non-admin force_route dropped at _decide_route; BAML router wins."""
    client = _client_for(regular_user)
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: _baml_ns())

    tid, _ = _post_query(client, "find mice", force_route="cc")
    task = _wait_terminal(tid)
    rd = _route_decided(task)
    assert rd["source"] == "baml"
    assert rd["route"] == cc_router.ROUTE_NS


def test_http_force_route_beats_sticky_cc(admin_user, monkeypatch):
    """Admin force_route=ns escapes sticky CC via full HTTP stack."""
    client = _client_for(admin_user)
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: _baml_ns("would-sticky"))

    sid = ChatSession.objects.create(
        user=admin_user,
        extra_state={
            "chat_log": [{
                "turn_id": 1,
                "ts": "t",
                "mode": "cc",
                "user_query": "prior cc",
                "assistant_reply": "done",
                "router_choice": cc_router.ROUTE_CC,
                "status": "completed",
            }],
        },
    ).session_id

    tid, _ = _post_query(client, "escape sticky", session_id=sid, force_route="ns")
    task = _wait_terminal(tid)
    rd = _route_decided(task)
    assert rd["route"] == cc_router.ROUTE_NS
    assert rd["source"] == "forced"


def test_http_sticky_cc_applies_without_force_route(regular_user, monkeypatch):
    """Sticky guard runs after controller: CC history → sticky source."""
    client = _client_for(regular_user)
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: _baml_ns("ns-classified"))

    sid = ChatSession.objects.create(
        user=regular_user,
        extra_state={
            "chat_log": [{
                "turn_id": 1,
                "ts": "t",
                "mode": "cc",
                "user_query": "prior cc",
                "assistant_reply": "done",
                "router_choice": cc_router.ROUTE_CC,
                "status": "completed",
            }],
        },
    ).session_id

    tid, _ = _post_query(client, "follow up", session_id=sid)
    task = _wait_terminal(tid)
    rd = _route_decided(task)
    assert rd["route"] == cc_router.ROUTE_CC
    assert rd["source"] == "sticky"

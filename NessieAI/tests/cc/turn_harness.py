"""Drive NessieAI.cc.turn.start_task in the test thread with real rows; every host seam is a stub.

The turn body runs inline (``InlineThread``), so the turn thread is the test's own; the vocabulary pre-run runs on its
real pool thread. Used by plan 04's tests (pre-run, prelude events, the Container-CC hand-off and its cost). Tests that
use it need ``pytest.mark.django_db(transaction=True)``: the pool thread writes to the database.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from django.contrib.auth import get_user_model

from NessieAI.cc import turn as cc_turn
from NessieAI.router import router as cc_router
from nextseek_api.assistant.models_db import ChatSession, QueryTask


class InlineThread:
    def __init__(self, target, daemon=None):
        self._target = target

    def start(self):
        self._target()


class Adapter(dict):
    """The session adapter: a dict with the two methods start_task calls."""

    def reload(self):
        pass

    def save(self):
        pass


def decision(route: str, **fields) -> cc_router.RouteDecision:
    record = dict(router_model="gemini-3.1-pro-preview", router_cost_usd=0.004, router_usage={"calls": []},
                  router_cost_partial=False)
    record.update(fields)
    model_class, model_id = ("opus", "model-x") if route == cc_router.ROUTE_CC else (None, None)
    return cc_router.RouteDecision(route=route, model_class=model_class, model_id=model_id, reasoning="r",
                                   source="baml", **record)


def rows(username: str, query: str = "how many mice", extra_state: dict | None = None):
    user = get_user_model().objects.create_user(username, password="x")
    chat = ChatSession.objects.create(user=user, extra_state=extra_state or {})
    task = QueryTask.objects.create(session=chat, user=user, query=query, status="running")
    return user, chat, task


class Events(list):
    """Every event start_task sends, with the name of the thread that sent it."""

    def send(self, event, data):
        self.append({"event": event, "data": dict(data), "thread": threading.current_thread().name})

    def named(self, event):
        return [e for e in self if e["event"] == event]

    def labels(self):
        return [e["data"].get("label") for e in self.named("prelude_step")]

    def order(self):
        return [e["event"] if e["event"] != "prelude_step" else e["data"]["label"] for e in self]


def cc_seams(monkeypatch, tmp_path, run_cc_turn):
    """The Container-CC branch's host seams: a users root in tmp, one project, a runner that is up."""
    from NessieAI.cc import cc_engine
    from NessieAI.cc.cc_provision import ProjectIdentity

    root = tmp_path / "users"
    root.mkdir()
    monkeypatch.setenv("DMAC_USER_ROOT_MOUNT", str(root))
    monkeypatch.setattr("NessieAI.cc.cc_provision.resolve_user_project",
                        lambda *a, **k: ProjectIdentity(id="1", slug="testproj", title="Test Project"))
    monkeypatch.setattr(cc_engine, "cc_runner_available", lambda: (True, "ok"))
    monkeypatch.setattr(cc_engine, "run_cc_turn", run_cc_turn)
    return root


def drive(monkeypatch, *, user, chat, task, route, query="how many mice", mode="standard", use_prod=False,
          adapter=None, decide=None, config=None, api_user="caller", api_pass="caller-pw", graph_scope=None):
    """Run one start_task inline and return the Events it sent. ``decide`` replaces the router call."""
    config = config if config is not None else SimpleNamespace(API_USER="", API_PASS="")
    monkeypatch.setattr(cc_turn, "threading", SimpleNamespace(Thread=InlineThread))
    monkeypatch.setattr(cc_turn, "_select_chat_config", lambda request, req: config)
    monkeypatch.setattr(cc_turn, "_record_ledger_row", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "_auto_title_if_unset", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "_emit_ns_run_root", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "_decide_route", decide or (lambda *a, **k: route))
    events = Events()
    cc_turn.start_task(
        SimpleNamespace(user=user),
        SimpleNamespace(query=query, mode=mode, max_turn_length_s=None, use_prod=use_prod, fresh_session=True),
        force_cc=False, chat_session=chat, query_task=task, send_event=events.send,
        adapter=adapter if adapter is not None else Adapter(), api_user=api_user, api_pass=api_pass,
        resolved_session_id=str(chat.session_id), graph_scope=graph_scope)
    return events

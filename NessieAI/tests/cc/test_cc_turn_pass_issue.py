"""Spec piece 1 through the real start_task: a Container-CC turn issues its pass, hands the container the pass
(never the password), holds the caller's own login, and revokes it on every way out. The router, the engine and
SEEK are stubbed; the database is real."""
from __future__ import annotations

import base64
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from NessieAI.cc import cc_engine, safe_fs
from NessieAI.cc import turn as cc_turn
from NessieAI.cc.cc_provision import ProjectIdentity, ProjectResolutionError
from NessieAI.router import router as cc_router
from nextseek_api.assistant import turn_pass
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask
from nextseek_api.tests.turn_pass_support import PASSWORD, make_user, pass_header

pytestmark = pytest.mark.django_db

_PROJECT = ProjectIdentity(id="1", slug="testproj", title="Test Project")


@pytest.fixture(autouse=True)
def _reset_safe_fs(monkeypatch):
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})


class _Thread:
    """Runs the turn inline, so the test sees its end state."""

    def __init__(self, target, daemon=None):
        self._target = target

    def start(self):
        self._target()


class _Adapter(dict):
    def reload(self):
        pass

    def save(self):
        pass


class _Config:
    API_USER = "service"
    API_PASS = "service-pw"


@pytest.fixture
def cc(monkeypatch, tmp_path):
    root = tmp_path / "users"
    root.mkdir()
    monkeypatch.setenv("DMAC_USER_ROOT_MOUNT", str(root))
    monkeypatch.setattr(cc_turn, "threading", SimpleNamespace(Thread=_Thread))
    monkeypatch.setattr(cc_turn, "_select_chat_config", lambda request, req: SimpleNamespace(API_USER="", API_PASS=""))
    monkeypatch.setattr(cc_turn, "_record_ledger_row", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "_decide_route", lambda *a, **k: cc_router.RouteDecision(
        route=cc_router.ROUTE_CC, model_class="opus", model_id="model-x", reasoning="forced", source="forced"))
    monkeypatch.setattr(cc_engine, "cc_runner_available", lambda: (True, "ok"))
    monkeypatch.setattr("NessieAI.cc.cc_provision.resolve_user_project", lambda *a, **k: _PROJECT)
    calls = SimpleNamespace(engine=[])

    def engine(**kwargs):
        calls.engine.append(kwargs)
        kwargs["send_event"]("query_complete", {"reply": "done"})

    monkeypatch.setattr(cc_engine, "run_cc_turn", engine)
    return calls


def _start(user, chat=None, *, api_user="caller", api_pass=PASSWORD):
    chat = chat or ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=chat, user=user, query="q", status="running")
    events = []
    cc_turn.start_task(
        SimpleNamespace(user=user),
        SimpleNamespace(query="q", mode="standard", max_turn_length_s=None, fresh_session=True, use_prod=False),
        force_cc=True, chat_session=chat, query_task=task,
        send_event=lambda event, data: events.append((event, data)),
        adapter=_Adapter(), api_user=api_user, api_pass=api_pass,
        resolved_session_id=str(chat.session_id),
    )
    return task, events


def _assert_revoked(task):
    row = CCTurn.objects.get(task=task)
    assert row.revoked_at is not None
    assert row.login_nonce is None and row.login_ciphertext is None


def test_the_container_gets_the_pass_and_the_row_holds_the_callers_own_login(cc, monkeypatch):
    seen = {}

    def engine(**kwargs):
        seen.update(kwargs)
        seen["held"] = turn_pass.login_for(CCTurn.objects.get(task__task_id=kwargs["run_id"]))
        kwargs["send_event"]("query_complete", {"reply": "done"})

    monkeypatch.setattr(cc_engine, "run_cc_turn", engine)
    prod = _Config()
    monkeypatch.setattr(cc_turn, "_select_chat_config", lambda request, req: prod)
    with override_settings(NEXTSEEK_CHAT_CONFIG_PROD=prod):
        task, _ = _start(make_user("caller"))
    assert len(seen["turn_pass"]) == 43
    assert seen["held"] == ("caller", PASSWORD), "the caller's own login, never the prod swap"
    assert (seen["api_user"], seen["api_pass"]) == ("caller", PASSWORD)
    _assert_revoked(task)


@pytest.mark.parametrize("way_out", ["runner_down", "no_project", "project_changed", "engine_raises"])
def test_every_way_out_of_the_turn_revokes_the_pass(cc, monkeypatch, way_out):
    user = make_user("leaver")
    chat = ChatSession.objects.create(user=user)
    if way_out == "runner_down":
        monkeypatch.setattr(cc_engine, "cc_runner_available", lambda: (False, "down"))
    elif way_out == "no_project":
        def no_project(*args, **kwargs):
            raise ProjectResolutionError("no SEEK person")
        monkeypatch.setattr("NessieAI.cc.cc_provision.resolve_user_project", no_project)
    elif way_out == "project_changed":
        chat.extra_state = {"cc_project_dirname": "some-other-project"}
        chat.save()
    else:
        def engine(**kwargs):
            raise RuntimeError("the container died")
        monkeypatch.setattr(cc_engine, "run_cc_turn", engine)
    task, events = _start(user, chat)
    assert any(event == "query_error" for event, _ in events)
    _assert_revoked(task)


def test_without_a_secret_key_the_turn_stops_with_a_setup_error_and_no_container(cc, monkeypatch):
    probed = []
    monkeypatch.setattr(cc_engine, "cc_runner_available", lambda: (probed.append(1), (True, "ok"))[1])
    with override_settings(SECRET_KEY=""):
        task, events = _start(make_user("keyless"))
    errors = [data for event, data in events if event == "query_error"]
    assert errors and errors[-1]["error"] == cc_turn.TURN_PASS_SETUP_ERROR
    assert not CCTurn.objects.filter(task=task).exists()
    assert cc.engine == [] and probed == []


def test_the_engine_dates_and_revokes_the_pass_through_its_callbacks(cc, monkeypatch):
    seen = {}

    def engine(**kwargs):
        deadline = time.time() + 100
        kwargs["on_deadline"](deadline)
        row = CCTurn.objects.get(task__task_id=kwargs["run_id"])
        seen["grace"] = row.expires_at.timestamp() - deadline
        kwargs["on_turn_end"]()
        seen["revoked"] = CCTurn.objects.get(pk=row.pk).revoked_at is not None
        kwargs["send_event"]("query_complete", {"reply": "done"})

    monkeypatch.setattr(cc_engine, "run_cc_turn", engine)
    task, _ = _start(make_user("dated"))
    assert abs(seen["grace"] - 60) < 0.01
    assert seen["revoked"] is True
    _assert_revoked(task)


def test_the_pass_answers_while_the_turn_runs_and_not_after(cc, monkeypatch):
    monkeypatch.setattr("nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
                        lambda self, request, view: True)
    seen = {}

    def engine(**kwargs):
        seen["pass"] = kwargs["turn_pass"]
        seen["during"] = APIClient().get(f"/nextseek_api/assistant/tasks/{kwargs['run_id']}/progress/",
                                         **pass_header(kwargs["turn_pass"])).status_code
        kwargs["send_event"]("query_complete", {"reply": "done"})

    monkeypatch.setattr(cc_engine, "run_cc_turn", engine)
    task, _ = _start(make_user("runner"))
    after = APIClient().get(f"/nextseek_api/assistant/tasks/{task.task_id}/progress/", **pass_header(seen["pass"]))
    assert seen["during"] == 200
    assert after.status_code == 401


def test_an_ns_turn_issues_no_pass(cc, monkeypatch):
    monkeypatch.setattr(cc_turn, "_decide_route", lambda *a, **k: cc_router.RouteDecision(
        route=cc_router.ROUTE_NS, model_class=None, model_id=None, reasoning="lookup", source="baml"))
    monkeypatch.setattr(cc_turn, "_eval_config", lambda config, user, req: config)
    monkeypatch.setattr(cc_turn, "_emit_ns_run_root", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "run_query", lambda session, config, query, send_event, credentials=None, **kw:
                        send_event("query_complete", {"reply": "ns", "bundle_id": None}))
    task, _ = _start(make_user("ns-user"))
    assert not CCTurn.objects.filter(task=task).exists()


@override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=None)
def test_a_turn_started_by_a_basic_header_holds_that_login_for_its_ops(cc, monkeypatch):
    """Review Focus 1: the nessie_tests harness and API clients start turns with a Basic header and no browser
    session. The turn must hold the header's login, and an op sent with the pass must act as it."""
    make_user("hdr-user", password="hdr pw:ü")
    monkeypatch.setattr("nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
                        lambda self, request, view: True)
    monkeypatch.setattr("nextseek_api.services.assistant.plain_scope", lambda user: None)
    monkeypatch.setattr("nextseek_api.services.cc_assistant.plain_scope", lambda user: None)
    seen = {}

    def fake_run_op(op, args, *, config, **kwargs):
        seen["login"] = (config.API_USER, config.API_PASS)
        return {"source": "catalog"}

    def engine(**kwargs):
        with patch("nextseek_api.services.assistant.run_op", side_effect=fake_run_op):
            seen["status"] = APIClient().post("/nextseek_api/assistant/graph-schema/", {}, format="json",
                                              **pass_header(kwargs["turn_pass"])).status_code
        kwargs["send_event"]("query_complete", {"reply": "done"})

    monkeypatch.setattr(cc_engine, "run_cc_turn", engine)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Basic " + base64.b64encode("hdr-user:hdr pw:ü".encode()).decode())
    resp = client.post("/nextseek_api/cc-assistant/cc/query/async/",
                       {"query": "q", "mode": "standard", "fresh_session": True}, format="json")
    assert resp.status_code == 202, resp.content
    assert seen["status"] == 200
    assert seen["login"] == ("hdr-user", "hdr pw:ü")
    _assert_revoked(QueryTask.objects.get(task_id=resp.json()["task_id"]))

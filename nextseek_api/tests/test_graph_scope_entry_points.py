"""
Every request that can reach the graph agent carries the caller's project scope, resolved on the server.

Spec docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 4.2, 4.3 and 11.4:

- ``plain_scope(user)`` is ``resolve_scope`` as plain data (``{"is_admin", "project_ids"}``): a superuser is admin,
  ``is_staff`` alone is not, and a caller it cannot resolve (no SEEK person, or any database error) gets ``None``,
  which refuses every graph query.
- Only the ViewSets resolve it (NessieAI never imports ``nextseek_api.graph_search``); they hand the plain dict down
  through ``graph_scope=`` to ``run_sse_pipeline``, ``run_async_pipeline``, ``start_task`` and ``run_retry``, which
  pass it to the orchestrator. The granular ops put it on their own per-request copy (``_granular_chat_config``).
- The Django singletons never carry a scope, and the granular view never injects ``neo4j_exec``.

Nothing here reaches a model, Neo4j or MySQL: the membership reads are patched and the pipeline threads are captured
instead of started.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from chat_nextseek.graph_scope import SCOPE_ATTR, GraphScope, scope_of
from nextseek_api.graph_search import scope as scope_module
from nextseek_api.graph_search.scope import Scope, ScopeUnavailable, plain_scope

REPO = Path(__file__).resolve().parents[2]
MEMBER = {"is_admin": False, "project_ids": [2, 13]}


def _user(username="someone", *, is_superuser=False, is_staff=False):
    user = MagicMock()
    user.username = username
    user.is_superuser = is_superuser
    user.is_staff = is_staff
    return user


def _wire(mock_connections, person_row, project_rows=()):
    cursor = MagicMock()
    cursor.fetchone.return_value = person_row
    cursor.fetchall.return_value = list(project_rows)
    mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = cursor
    return cursor


# --------------------------------------------------------------------------- #
# plain_scope
# --------------------------------------------------------------------------- #

@patch("nextseek_api.graph_search.scope.connections")
def test_a_superuser_is_admin_without_a_query(mock_connections):
    assert plain_scope(_user("admin", is_superuser=True)) == {"is_admin": True, "project_ids": []}
    mock_connections.__getitem__.assert_not_called()


@patch("nextseek_api.graph_search.scope.connections")
def test_is_staff_alone_is_a_member_of_its_projects(mock_connections):
    _wire(mock_connections, (144,), [(13,), (2,)])

    assert plain_scope(_user("staff", is_staff=True)) == {"is_admin": False, "project_ids": [2, 13]}


@patch("nextseek_api.graph_search.scope.connections")
def test_a_member_of_no_project_sees_nothing_rather_than_everything(mock_connections):
    _wire(mock_connections, (144,), [])

    assert plain_scope(_user()) == {"is_admin": False, "project_ids": []}


@patch("nextseek_api.graph_search.scope.connections")
def test_an_unresolvable_caller_gets_no_scope(mock_connections, caplog):
    _wire(mock_connections, None)

    assert plain_scope(_user("nobody")) is None
    assert "graph scope" in caplog.text


@patch("nextseek_api.graph_search.scope.connections")
def test_a_database_error_gets_no_scope(mock_connections, caplog):
    mock_connections.__getitem__.side_effect = RuntimeError("seek is down")

    assert plain_scope(_user()) is None
    assert "graph scope" in caplog.text


def test_plain_scope_is_resolve_scope_as_plain_data():
    with patch.object(scope_module, "resolve_scope", return_value=Scope(False, 7, (3, 1))):
        assert plain_scope(_user()) == {"is_admin": False, "project_ids": [3, 1]}
    with patch.object(scope_module, "resolve_scope", side_effect=ScopeUnavailable("x")):
        assert plain_scope(_user()) is None


def test_the_plain_form_round_trips_into_a_graph_scope():
    assert GraphScope.from_plain(MEMBER) == GraphScope(is_admin=False, project_ids=(2, 13))
    assert GraphScope.from_plain({"is_admin": True, "project_ids": []}).is_admin is True


# --------------------------------------------------------------------------- #
# The Django singletons carry no scope
# --------------------------------------------------------------------------- #

def test_the_django_singletons_carry_no_scope():
    assert scope_of(getattr(settings, "NEXTSEEK_CHAT_CONFIG", None)) is None
    assert scope_of(getattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None)) is None


def test_the_settings_template_and_the_config_class_set_no_scope():
    from chat_nextseek.config import ChatConfig

    assert not hasattr(ChatConfig, SCOPE_ATTR)
    for relative in ("startup/templates/local_settings.py.template", "startup/dev/lane_local_settings.py"):
        path = REPO / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for name in (SCOPE_ATTR, "with_scope", "operator_scope_from_env", "CHAT_NEXTSEEK_GRAPH_ADMIN"):
            assert name not in text, f"{relative} names {name}"


def test_no_served_module_reads_the_operator_opt_in():
    """Only single-operator surfaces may opt in to admin; nothing under nextseek_api, NessieAI/ns, NessieAI/cc,
    NessieAI/router or dmac names the variable or the helper."""
    offenders = []
    for root in ("nextseek_api", "dmac", "NessieAI/ns", "NessieAI/cc", "NessieAI/router"):
        for path in (REPO / root).rglob("*.py"):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "CHAT_NEXTSEEK_GRAPH_ADMIN" in text or "operator_scope_from_env" in text:
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


# --------------------------------------------------------------------------- #
# The ViewSets hand plain_scope(request.user) down
# --------------------------------------------------------------------------- #

class _CapturedThread:
    """Stands in for threading.Thread in a ViewSet module: records target and kwargs, never runs."""

    started: list = []

    def __init__(self, target=None, kwargs=None, daemon=None, **_):
        self.target = target
        self.kwargs = dict(kwargs or {})

    def start(self):
        _CapturedThread.started.append(self)


class _Config:
    API_USER = "service"
    API_PASS = "service-pw"


# The hermetic settings module carries no chat config (it comes from the gitignored local settings); these tests
# never run the pipeline, so a plain stand-in is enough.
@override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=None)
class ViewSetHandsTheScopeDown(TestCase):
    databases = {"default"}

    def setUp(self):
        _CapturedThread.started = []
        self.user = User.objects.create_user("member", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        for target, value in (
            ("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", True),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _scope_patch(self, module, value=MEMBER):
        patcher = patch(f"nextseek_api.services.{module}.plain_scope", return_value=value)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def _thread_patch(self, module):
        patcher = patch(f"nextseek_api.services.{module}.threading.Thread", _CapturedThread)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_query_hands_the_scope_to_the_sse_pipeline(self):
        resolved = self._scope_patch("assistant")
        self._thread_patch("assistant")

        resp = self.client.post("/nextseek_api/assistant/query/", {"query": "how many", "mode": "standard"},
                                format="json")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resolved.call_args.args[0], self.user)
        thread = _CapturedThread.started[-1]
        self.assertEqual(thread.target.__name__, "run_sse_pipeline")
        self.assertEqual(thread.kwargs["graph_scope"], MEMBER)

    def test_query_async_hands_the_scope_to_the_async_pipeline(self):
        resolved = self._scope_patch("assistant")
        self._thread_patch("assistant")

        resp = self.client.post("/nextseek_api/assistant/query/async/", {"query": "how many", "mode": "standard"},
                                format="json")

        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resolved.call_args.args[0], self.user)
        thread = _CapturedThread.started[-1]
        self.assertEqual(thread.target.__name__, "run_async_pipeline")
        self.assertEqual(thread.kwargs["graph_scope"], MEMBER)

    def test_the_routed_endpoint_hands_the_scope_to_start_task(self):
        resolved = self._scope_patch("cc_assistant")
        with patch("nextseek_api.services.cc_assistant.cc_turn.start_task") as start_task:
            resp = self.client.post("/nextseek_api/cc-assistant/query/async/",
                                    {"query": "how many", "mode": "standard"}, format="json")

        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resolved.call_args.args[0], self.user)
        self.assertEqual(start_task.call_args.kwargs["graph_scope"], MEMBER)

    def test_the_evaluator_retry_hands_the_scope_to_run_retry(self):
        admin = User.objects.create_user("evaladmin", password="p", is_superuser=True, is_staff=True)
        from nextseek_api.assistant.models_db import ChatSession, QueryTask

        chat = ChatSession.objects.create(user=admin)
        task = QueryTask.objects.create(session=chat, user=admin, query="q", status="completed",
                                        result={"reply": "r", "bundle_id": None})
        resolved = self._scope_patch("evaluator", {"is_admin": True, "project_ids": []})
        self._thread_patch("evaluator")
        self.client.force_authenticate(user=admin)

        resp = self.client.post("/nextseek_api/evaluator/retry/",
                                {"task_id": str(task.task_id), "query": "again", "mode": "standard"},
                                format="json")

        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resolved.call_args.args[0], admin)
        thread = _CapturedThread.started[-1]
        self.assertEqual(thread.target.__name__, "run_retry")
        self.assertEqual(thread.kwargs["graph_scope"], {"is_admin": True, "project_ids": []})

    def test_the_granular_graph_view_never_injects_neo4j_exec(self):
        with patch("nextseek_api.services.assistant._granular_chat_config", return_value=SimpleNamespace()), \
             patch("nextseek_api.services.assistant.run_op", return_value={"plan": {}, "result": {}}) as run_op:
            resp = self.client.post("/nextseek_api/assistant/graph/", {"query": "lineage"}, format="json")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertNotIn("neo4j_exec", run_op.call_args.kwargs)


def test_no_granular_call_site_passes_neo4j_exec():
    tree = ast.parse((REPO / "nextseek_api/services/assistant.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "run_op"]
    assert calls, "the granular view no longer calls run_op"
    assert all("neo4j_exec" not in {k.arg for k in c.keywords} for c in calls)


# --------------------------------------------------------------------------- #
# _granular_chat_config puts the scope on its copy
# --------------------------------------------------------------------------- #

def _granular(value, *, user=None):
    from nextseek_api.services import assistant

    singleton = _Config()
    request = SimpleNamespace(user=user or _user(), session={})
    with patch.object(assistant, "_select_chat_config", return_value=singleton), \
         patch.object(assistant, "resolve_seek_auth", return_value=(("caller", "caller-pw"), {})), \
         patch.object(assistant, "plain_scope", return_value=value) as resolved:
        cfg = assistant._granular_chat_config(request, SimpleNamespace())
    return cfg, singleton, resolved, request


def test_the_granular_copy_carries_the_callers_scope():
    cfg, singleton, resolved, request = _granular(MEMBER)

    assert cfg is not singleton
    assert scope_of(cfg) == GraphScope(is_admin=False, project_ids=(2, 13), source="request")
    assert (cfg.API_USER, cfg.API_PASS) == ("caller", "caller-pw")
    assert resolved.call_args.args[0] is request.user
    assert not hasattr(singleton, SCOPE_ATTR)


@pytest.mark.parametrize("value", [None, {"is_admin": "yes"}, {"is_admin": False, "project_ids": ["2"]}])
def test_no_or_a_malformed_scope_stores_none(value):
    cfg, singleton, _, _ = _granular(value)

    assert hasattr(cfg, SCOPE_ATTR) and getattr(cfg, SCOPE_ATTR) is None
    assert scope_of(cfg) is None
    assert not hasattr(singleton, SCOPE_ATTR)


@patch("nextseek_api.graph_search.scope.connections")
def test_a_superuser_granular_copy_is_admin(mock_connections):
    from nextseek_api.services import assistant

    request = SimpleNamespace(user=_user("admin", is_superuser=True), session={})
    with patch.object(assistant, "_select_chat_config", return_value=_Config()), \
         patch.object(assistant, "resolve_seek_auth", return_value=(("admin", "pw"), {})):
        cfg = assistant._granular_chat_config(request, SimpleNamespace())

    assert scope_of(cfg) == GraphScope(is_admin=True, source="request")


# --------------------------------------------------------------------------- #
# NessieAI passes the plain scope to the orchestrator
# --------------------------------------------------------------------------- #

def _pipeline_kwargs(**extra):
    adapter = MagicMock()
    return dict(adapter=adapter, chat_config=_Config(), send_event=lambda *a, **k: None, api_user="u",
                api_pass="p", chat_session=MagicMock(title="t"), resolved_session_id="s-1", **extra)


@pytest.mark.parametrize("mode, entry", [("standard", "run_query"), ("plan", "run_query_plan")])
def test_the_sse_pipeline_passes_the_scope(mode, entry):
    from NessieAI.ns import turn

    with patch.object(turn, entry) as target:
        turn.run_sse_pipeline(req=SimpleNamespace(mode=mode, query="q"), graph_scope=MEMBER,
                              event_queue=MagicMock(), **_pipeline_kwargs())

    assert target.call_args.kwargs["graph_scope"] == MEMBER


@pytest.mark.parametrize("mode, entry", [("standard", "run_query"), ("plan", "run_query_plan"),
                                         ("pipeline", "run_pipeline_launch")])
def test_the_async_pipeline_passes_the_scope(mode, entry):
    from NessieAI.ns import turn

    with patch.object(turn, entry) as target:
        turn.run_async_pipeline(req=SimpleNamespace(mode=mode, query="q"), graph_scope=MEMBER,
                                **_pipeline_kwargs())

    assert target.call_args.kwargs["graph_scope"] == MEMBER


def test_without_a_scope_the_pipeline_config_carries_none():
    """No scope resolved: the orchestrator gets the request config, whose scope is absent, which refuses."""
    from NessieAI.ns import turn

    kwargs = _pipeline_kwargs()
    with patch.object(turn, "run_query") as target:
        turn.run_async_pipeline(req=SimpleNamespace(mode="standard", query="q"), **kwargs)

    config = target.call_args.args[1]
    assert scope_of(config) is None
    assert target.call_args.kwargs.get("graph_scope") is None


@pytest.mark.parametrize("mode, entry", [("standard", "run_query"), ("plan", "run_query_plan")])
@override_settings(NEXTSEEK_CHAT_CONFIG=_Config())
def test_run_retry_passes_the_scope(mode, entry):
    from NessieAI.ns import retry

    target = MagicMock()
    orchestrator = (target, MagicMock()) if entry == "run_query" else (MagicMock(), target)
    with patch.object(retry, "_get_orchestrator", return_value=orchestrator):
        retry.run_retry(adapter=MagicMock(), req=SimpleNamespace(mode=mode, query="q"), send_event=MagicMock(),
                        api_user="u", api_pass="p", session_id_str="s-1", graph_scope=MEMBER)

    assert target.call_args.kwargs["graph_scope"] == MEMBER


class _Adapter(dict):
    def save(self):
        pass


class _SyncThreading:
    class Thread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()


@pytest.mark.parametrize("mode, entry", [("standard", "run_query"), ("plan", "run_query_plan")])
def test_start_task_passes_the_scope_on_the_ns_route(monkeypatch, mode, entry):
    from NessieAI.cc import turn
    from NessieAI.router import router as cc_router

    seen = {}

    def fake(adapter, config, query, send_event, **kwargs):
        seen.update(kwargs)
        send_event("query_complete", {"reply": "ok"})

    decision = SimpleNamespace(route=cc_router.ROUTE_NS, model_class=None, model_id=None, source="forced",
                               reasoning="test")
    monkeypatch.setattr(turn, "threading", _SyncThreading)
    monkeypatch.setattr(turn, "_select_chat_config", lambda request, r: _Config())
    monkeypatch.setattr(turn, "_decide_route", lambda *a, **k: decision)
    monkeypatch.setattr(turn, "_record_ledger_row", lambda *a, **k: None)
    monkeypatch.setattr(turn, "_auto_title_if_unset", lambda *a, **k: None)
    monkeypatch.setattr(turn, entry, fake)
    monkeypatch.setattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None, raising=False)

    from nextseek_api.assistant.models_api import QueryRequest

    turn.start_task(
        SimpleNamespace(user=_user("member")), QueryRequest(query="how many tissue samples", mode=mode),
        force_cc=False, chat_session=SimpleNamespace(extra_state={}, session_id="s-1", results_history=[]),
        query_task=SimpleNamespace(task_id="t-1"), send_event=lambda ev, data: None, adapter=_Adapter(),
        api_user="u", api_pass="p", resolved_session_id="s-1", graph_scope=MEMBER,
    )

    assert seen["graph_scope"] == MEMBER

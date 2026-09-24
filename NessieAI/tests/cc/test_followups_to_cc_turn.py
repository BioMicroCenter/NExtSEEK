"""The 2026-09-23 follow-up ruling through the real CC turn (POST -> start_task -> engine).

The router is stubbed at ``cc_router.decide`` and the engine at ``cc_engine.run_cc_turn``,
so no model, no container and no network: what runs is the policy, the fallback, the
prior-turn staging and the memory file, in the order the live turn runs them.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession, QueryTask
from NessieAI.cc import cc_engine
from NessieAI.cc.cc_provision import ProjectIdentity
from NessieAI.router import router as cc_router

pytestmark = pytest.mark.django_db(transaction=True)

CC_QUERY_URL = "/nextseek_api/cc-assistant/query/async/"
_PROJECT = ProjectIdentity(id="1", slug="testproj", title="Test Project")
CYPHER = "MATCH (n:T_NHP) RETURN n.uuid AS uuid, n.Species AS Species LIMIT 5000"
ROWS = [{"uuid": "NHP-1", "Species": "Macaca mulatta"},
        {"uuid": "NHP-2", "Species": "Macaca fascicularis"}]


@pytest.fixture(autouse=True)
def _permission():
    with patch("nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
               return_value=True):
        yield


@pytest.fixture(autouse=True)
def _chat_config(monkeypatch):
    cfg = type("Cfg", (), {"API_USER": "", "API_PASS": ""})()
    monkeypatch.setattr("NessieAI.cc.turn._select_chat_config", lambda request, req: cfg)
    monkeypatch.setattr("NessieAI.cc.turn._record_ledger_row", lambda *a, **k: None)
    monkeypatch.setattr(cc_router, "_resolve_cc_model_id", lambda: "model-x")


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("fu-user", password="x")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def cc_root(tmp_path, monkeypatch):
    root = tmp_path / "users"
    root.mkdir()
    monkeypatch.setenv("DMAC_USER_ROOT_MOUNT", str(root))
    monkeypatch.setattr("NessieAI.cc.cc_provision.resolve_user_project", lambda *a, **k: _PROJECT)
    return root


def _wait(task_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = QueryTask.objects.get(task_id=task_id)
        if t.status in ("completed", "error"):
            return t
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} not terminal after {timeout}s")


def _post(client, text, sid):
    resp = client.post(CC_QUERY_URL, {"query": text, "mode": "standard", "session_id": str(sid)},
                       format="json")
    assert resp.status_code == 202, resp.content
    return resp.json()["task_id"]


def _route(task):
    return next(p for p in task.progress if p["event"] == "route_decided")["data"]


def _ns_router(monkeypatch):
    monkeypatch.setattr(cc_router, "decide", lambda q, history=None: cc_router.RouteDecision(
        route=cc_router.ROUTE_NS, model_class=None, model_id=None, reasoning="lookup",
        source="baml"))


def _session_after_one_ns_graph_turn(user, tmp_path, monkeypatch):
    outputs = tmp_path / "outputs" / "run"
    (outputs / "graph_result").mkdir(parents=True)
    rows_file = outputs / "graph_result" / "graph_result_bundle_1.json"
    rows_file.write_text(json.dumps({"cypher": CYPHER, "rows": ROWS}))
    monkeypatch.setenv("NEXTSEEK_OUTPUTS_DIR", str(tmp_path / "outputs"))
    bundle = {"id": 1, "user_query": "Find NHP samples", "mode": "graph_query",
              "parser_plan": {"mode": "graph_query", "intent_summary": "Find NHP samples."},
              "graph_plan": {"cypher": CYPHER, "explanation": "all NHP", "parameters": {}},
              "graph_result": {"ok": True, "count": 2, "total": 2, "truncated": False, "data": ROWS},
              "terminal_reply": "There are 2 NHP samples.",
              "files": [{"key": "graph_result", "label": "Graph query result rows",
                         "path": str(rows_file), "filename": rows_file.name,
                         "kind": "graph_result", "bundle_id": 1}]}
    entry = {"turn_id": 1, "ts": "t", "user_query": "Find NHP samples", "mode": "graph_query",
             "router_choice": "nextseek_query", "status": "completed", "bundle_id": 1,
             "assistant_reply": "There are 2 NHP samples.", "key_entities": {"sampletypes": ["NHP"]}}
    return ChatSession.objects.create(user=user, extra_state={"chat_log": [entry]},
                                      results_history=[bundle])


def test_a_followup_after_an_ns_turn_runs_on_cc_with_the_previous_turn_staged(
        client, user, cc_root, tmp_path, monkeypatch):
    session = _session_after_one_ns_graph_turn(user, tmp_path, monkeypatch)
    _ns_router(monkeypatch)
    seen = {}

    def fake_cc_turn(**kw):
        seen.update(kw)
        seen["memory"] = Path(kw["memory_claude_md"]).read_text() if kw.get("memory_claude_md") else ""
        kw["send_event"]("query_complete", {"reply": "Two species."})

    monkeypatch.setattr(cc_engine, "cc_runner_available", lambda: (True, "ok"))
    monkeypatch.setattr(cc_engine, "run_cc_turn", fake_cc_turn)

    task = _wait(_post(client, "Which species are among those 2?", session.session_id))
    rd = _route(task)
    assert rd["route"] == cc_router.ROUTE_CC and rd["source"] == "followup"
    assert seen["previous_turns"] is True
    assert "/data/previous_turns/MANIFEST.md" in seen["memory"]

    staged = cc_root / "1-testproj" / "fu-user" / "_memory" / str(session.session_id) / "previous_turns"
    details = json.loads((staged / "turn-01" / "search_details.json").read_text())
    assert details["graph"]["cypher"] == CYPHER and details["neo4j"]["count"] == 2
    assert json.loads((staged / "turn-01" / "rows.json").read_text())["rows"] == ROWS
    assert (staged / "turn-01" / "graph_result_bundle_1.json").is_file()


def test_a_self_contained_question_after_a_cc_turn_runs_on_ns(client, user, cc_root, monkeypatch):
    session = ChatSession.objects.create(user=user, extra_state={"chat_log": [
        {"turn_id": 1, "ts": "t", "mode": "cc", "user_query": "plot the species",
         "assistant_reply": "done", "router_choice": "container_cc", "status": "completed"}]})
    _ns_router(monkeypatch)
    ran = {}
    from NessieAI.cc import turn as cc_turn

    def fake_run_query(session, config, query, send_event, credentials=None, **kw):
        ran["ns"] = query
        send_event("query_complete", {"reply": "12 HeLa samples", "bundle_id": None})

    monkeypatch.setattr(cc_turn, "run_query", fake_run_query)
    monkeypatch.setattr(cc_engine, "run_cc_turn", lambda **kw: pytest.fail("ran on CC"))
    task = _wait(_post(client, "How many HeLa samples do we have?", session.session_id))
    assert _route(task)["route"] == cc_router.ROUTE_NS and _route(task)["source"] == "baml"
    assert ran["ns"] == "How many HeLa samples do we have?"


def test_a_followup_falls_back_to_ns_for_one_turn_when_cc_is_down(
        client, user, cc_root, tmp_path, monkeypatch):
    session = _session_after_one_ns_graph_turn(user, tmp_path, monkeypatch)
    _ns_router(monkeypatch)
    ran = {}
    from NessieAI.cc import turn as cc_turn

    def fake_run_query(session, config, query, send_event, credentials=None, **kw):
        ran["ns"] = query
        send_event("query_complete", {"reply": "answered on NS", "bundle_id": None})

    monkeypatch.setattr(cc_turn, "run_query", fake_run_query)
    monkeypatch.setattr(cc_engine, "cc_runner_available", lambda: (False, "no agent image"))
    monkeypatch.setattr(cc_engine, "run_cc_turn", lambda **kw: pytest.fail("ran on CC"))
    task = _wait(_post(client, "which of them are female?", session.session_id))
    rd = _route(task)
    assert rd["route"] == cc_router.ROUTE_NS and rd["source"] == "cc_unavailable"
    assert "no agent image" in rd["reasoning"]
    assert ran["ns"] == "which of them are female?"
    assert task.status == "completed"


def test_the_follow_up_gets_every_property_of_the_previous_turns_samples(
        client, user, cc_root, tmp_path, monkeypatch):
    """CC-RERUN-FINDINGS fix 1 through the real turn: the staging read goes through the Neo4j
    tool (write check and scope prover), on a config carrying this caller's scope."""
    from chat_nextseek import helpers
    from chat_nextseek.graph_scope import GraphScope, scope_of

    session = _session_after_one_ns_graph_turn(user, tmp_path, monkeypatch)
    _ns_router(monkeypatch)
    calls = []

    def fake_tool(config, cypher, parameters=None):
        calls.append((scope_of(config), cypher, parameters))
        return {"ok": True, "data": [
            {"sample": {"uuid": "NHP-1", "id": 1, "type": "NHP", "Species": "Macaca mulatta",
                        "Sex": "female"}},
            {"sample": {"uuid": "NHP-2", "id": 2, "type": "NHP", "Species": "Macaca fascicularis",
                        "Sex": "male"}}]}

    monkeypatch.setattr(helpers, "tool_neo4j_query", fake_tool)
    monkeypatch.setattr("nextseek_api.services.cc_assistant.plain_scope",
                        lambda user: {"is_admin": False, "project_ids": [2]})
    monkeypatch.setattr(cc_engine, "cc_runner_available", lambda: (True, "ok"))
    monkeypatch.setattr(cc_engine, "run_cc_turn",
                        lambda **kw: kw["send_event"]("query_complete", {"reply": "ok"}))

    _wait(_post(client, "Break those down by sex", session.session_id))

    (scope, cypher, parameters), = calls
    assert isinstance(scope, GraphScope)
    assert scope.is_admin is False and scope.project_ids == (2,)
    assert parameters == {"uids": ["NHP-1", "NHP-2"]}
    staged = cc_root / "1-testproj" / "fu-user" / "_memory" / str(session.session_id) / "previous_turns"
    text = (staged / "turn-01" / "samples.csv").read_text()
    assert "Sex" in text.splitlines()[0] and "female" in text and "male" in text

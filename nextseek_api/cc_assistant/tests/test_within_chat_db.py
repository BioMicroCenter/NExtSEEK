"""Lane C behavioral suite (spec-001 T12): the wiring the hermetic tests cannot
see. Runs ONLY in the repo-mounted container lane (within-chat-lane.sh).
Select with -k within_chat_db (module name carries the marker token)."""
from __future__ import annotations

import logging
import time
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant import ns_digest
from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant.cc_provision import ProjectIdentity
from nextseek_api.cc_assistant.cc_turn_complete import TurnCompletePayload

pytestmark = pytest.mark.django_db(transaction=True)

CC_QUERY_URL = "/nextseek_api/cc-assistant/query/async/"
_FAKE_PROJECT = ProjectIdentity(id="1", slug="testproj", title="Test Project")


def _wait_terminal(task_id, timeout=30.0):
    """AR-13: _run executes in a daemon thread; poll to a deadline."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = QueryTask.objects.get(task_id=task_id)
        if t.status in ("completed", "error"):
            return t
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} not terminal after {timeout}s")


@pytest.fixture(autouse=True)
def _assistant_project_permission():
    with patch(
        "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
        return_value=True,
    ):
        yield


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("wc-user", password="x")


@pytest.fixture
def client(user):
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


def _decision(route, reasoning="test-reasoning"):
    return cc_router.RouteDecision(
        route=route,
        model_class=None,
        model_id=None,
        reasoning=reasoning,
        source="baml",
    )


def test_within_chat_db_mixed_sequence_exactly_once(client, monkeypatch):
    """NS-answered → unrelated → error(double query_error, site #10 shape) →
    CC-answered: sequential turn_ids 1..4, one entry each, correct fields."""
    routes = iter([
        _decision(cc_router.ROUTE_NS),
        _decision(cc_router.ROUTE_UNRELATED),
        _decision(cc_router.ROUTE_NS),
        _decision(cc_router.ROUTE_CC),
    ])
    monkeypatch.setattr(cc_router, "_baml_decision", lambda q, h=None: next(routes))

    def fake_run_query(session, config, query, send_event, credentials=None):
        from chat_nextseek.chat_memory import append_turn

        append_turn(
            session,
            user_query=query,
            mode="search",
            assistant_reply="42 mice",
            bundle_id=1,
        )
        send_event("query_complete", {"reply": "42 mice", "bundle_id": 1})

    from nextseek_api.services import cc_assistant as svc

    monkeypatch.setattr(svc, "run_query", fake_run_query)

    def fake_run_query_error(session, config, query, send_event, credentials=None):
        send_event("query_error", {"error": "graph agent exploded", "agent": "graph"})
        raise RuntimeError("boom")

    def fake_cc_turn(**kw):
        kw["send_event"]("query_complete", {"reply": "cc answer"})
        payload_writer = kw.get("on_turn_complete")
        if payload_writer:
            payload_writer(
                TurnCompletePayload(
                    chat_session=kw["chat_session"],
                    user_query=kw["query"],
                    assistant_reply="cc answer",
                    ts="t",
                    artifacts=None,
                    cc_traces=[],
                    turn_id="uuid-1",
                    cc_session_id=None,
                    raw_jsonl=b"",
                )
            )

    _patch_cc_project(monkeypatch)
    monkeypatch.setattr(cc_engine, "cc_runner_available", lambda: (True, "ok"))
    monkeypatch.setattr(cc_engine, "run_cc_turn", lambda **kw: fake_cc_turn(**kw))

    tid, sid = _post_query(client, "find mice")
    _wait_terminal(tid)
    tid, _ = _post_query(client, "who won the game", session_id=sid)
    _wait_terminal(tid)
    monkeypatch.setattr(svc, "run_query", fake_run_query_error)
    tid, _ = _post_query(client, "explode please", session_id=sid)
    assert _wait_terminal(tid).status == "error"
    tid, _ = _post_query(client, "make a chart", session_id=sid)
    _wait_terminal(tid)

    log = ChatSession.objects.get(session_id=sid).extra_state["chat_log"]
    assert [e["turn_id"] for e in log] == [1, 2, 3, 4]
    assert log[0]["router_choice"] == "nextseek_query" and log[0]["status"] == "completed"
    assert log[1]["router_choice"] == "unrelated" and "assistant_reply" not in log[1]
    assert log[2]["status"] == "error"
    assert log[2]["error"] == "graph agent exploded"
    assert "assistant_reply" not in log[2]
    assert log[3]["router_choice"] == "container_cc" and log[3]["assistant_reply"] == "cc answer"


def test_within_chat_db_history_reaches_decide_read_before_append(client, monkeypatch):
    """§12.8 behavioral half: captured history == prior turns only."""
    captured = {}

    def capturing(q, h=None):
        captured.setdefault("histories", []).append(h)
        return _decision(cc_router.ROUTE_UNRELATED)

    monkeypatch.setattr(cc_router, "_baml_decision", capturing)
    tid, sid = _post_query(client, "first")
    _wait_terminal(tid)
    tid, _ = _post_query(client, "second", session_id=sid)
    _wait_terminal(tid)
    h1, h2 = captured["histories"]
    assert h1 == []
    assert [t.position for t in h2] == [1]
    assert h2[0].router_choice == "unrelated"
    assert h2[0].assistant_reply is None


def test_within_chat_db_route_decided_carries_real_reasoning(client, monkeypatch):
    monkeypatch.setattr(
        cc_router,
        "_baml_decision",
        lambda q, h=None: _decision(cc_router.ROUTE_UNRELATED, reasoning="X-marks-the-spot"),
    )
    tid, _ = _post_query(client, "anything")
    task = _wait_terminal(tid)
    rd = next(p for p in task.progress if p["event"] == "route_decided")
    assert rd["data"]["reasoning"] == "X-marks-the-spot"
    assert rd["data"]["source"] == "baml"


def test_within_chat_db_get_session_visibility_and_turn_ids(client, user):
    """PD-6: answered + preview-only stay visible with turn_id; non-answer hidden."""
    s = ChatSession.objects.create(
        user=user,
        extra_state={
            "chat_log": [
                {
                    "turn_id": 1,
                    "ts": "t",
                    "mode": "search",
                    "user_query": "q1",
                    "assistant_reply": "full answer",
                    "bundle_id": None,
                },
                {
                    "turn_id": 2,
                    "ts": "t",
                    "mode": "search",
                    "user_query": "q2",
                    "assistant_reply": "",
                    "assistant_reply_preview": "preview text",
                },
                {
                    "turn_id": 3,
                    "ts": "t",
                    "mode": "unrelated",
                    "user_query": "q3",
                    "router_choice": "unrelated",
                    "status": "completed",
                },
                {
                    "turn_id": 4,
                    "ts": "t",
                    "mode": "cc",
                    "user_query": "q4",
                    "router_choice": "container_cc",
                    "status": "error",
                    "error": "boom",
                },
            ]
        },
    )
    resp = client.get(f"/nextseek_api/assistant/sessions/{s.session_id}/?include=turns")
    turns = resp.json()["turns"]
    assert [(t["turn_id"], t["user_query"]) for t in turns] == [(1, "q1"), (2, "q2")]
    assert turns[1]["reply"] == "preview text"


def test_within_chat_db_digest_composed_fresh_and_not(client, monkeypatch):
    """G-6: digest present with fresh=True (digest-only) AND fresh=False."""
    from nextseek_api.services import cc_assistant as svc

    written = {}

    def fake_run_cc_turn(**kw):
        p = kw.get("memory_claude_md")
        written[kw["query"]] = open(p).read() if p else None
        kw["send_event"]("query_complete", {"reply": "ok"})

    _patch_cc_project(monkeypatch)
    monkeypatch.setattr(cc_engine, "cc_runner_available", lambda: (True, "ok"))
    monkeypatch.setattr(cc_engine, "run_cc_turn", lambda **kw: fake_run_cc_turn(**kw))
    monkeypatch.setattr(
        cc_router,
        "_baml_decision",
        lambda q, h=None: _decision(cc_router.ROUTE_NS),
    )

    def fake_run_query(session, config, query, send_event, credentials=None):
        from chat_nextseek.chat_memory import append_turn

        session["results_history"] = [
            {
                "id": 1,
                "timestamp": "t",
                "mode": "search",
                "user_query": query,
                "terminal_reply": "found",
                "reply": "found",
                "endpoint": "/e",
                "method": "GET",
                "parser_plan": {},
                "api_result_full": {
                    "ok": True,
                    "data": {"total": 2, "rows": [{"uid": "A-1"}, {"uid": "A-2"}]},
                },
            }
        ]
        append_turn(
            session,
            user_query=query,
            mode="search",
            assistant_reply="found",
            bundle_id=1,
        )
        send_event("query_complete", {"reply": "found", "bundle_id": 1})

    monkeypatch.setattr(svc, "run_query", fake_run_query)

    tid, sid = _post_query(client, "seed NS turn")
    _wait_terminal(tid)
    monkeypatch.setattr(
        cc_router,
        "_baml_decision",
        lambda q, h=None: _decision(cc_router.ROUTE_CC),
    )
    tid, _ = _post_query(client, "cc normal", session_id=sid)
    _wait_terminal(tid)
    assert written["cc normal"] is not None
    assert ns_digest.DIGEST_HEADER in written["cc normal"]

    resp = client.post(
        CC_QUERY_URL,
        {
            "query": "cc fresh",
            "mode": "standard",
            "session_id": str(sid),
            "fresh_session": True,
        },
        format="json",
    )
    tid = resp.json()["task_id"]
    _wait_terminal(tid)
    assert written["cc fresh"] is not None
    assert ns_digest.DIGEST_HEADER in written["cc fresh"]


def test_within_chat_db_cc_writer_survives_midturn_op_append(client, monkeypatch, user):
    """PD-5/delta-9: an op-appended NS entry mid-CC-turn is NOT clobbered by
    the CC success writer (refresh_from_db before apply)."""

    def fake_cc_turn(**kw):
        s = ChatSession.objects.get(session_id=kw["chat_session"].session_id)
        from chat_nextseek.chat_memory import append_turn

        cache = dict(s.extra_state or {})

        class _D(dict):
            def get(self, k, d=None):
                return super().get(k, d)

        d = _D(cache)
        append_turn(
            d,
            user_query="op query",
            mode="search",
            assistant_reply="op result",
            bundle_id=9,
        )
        s.extra_state = dict(d)
        s.save(update_fields=["extra_state", "updated_at"])
        kw["send_event"]("query_complete", {"reply": "cc answer"})
        kw["on_turn_complete"](
            TurnCompletePayload(
                chat_session=kw["chat_session"],
                user_query=kw["query"],
                assistant_reply="cc answer",
                ts="t",
                artifacts=None,
                cc_traces=[],
                turn_id="uuid-2",
                cc_session_id=None,
                raw_jsonl=b"",
            )
        )

    monkeypatch.setattr(
        cc_router,
        "_baml_decision",
        lambda q, h=None: _decision(cc_router.ROUTE_CC),
    )
    _patch_cc_project(monkeypatch)
    monkeypatch.setattr(cc_engine, "cc_runner_available", lambda: (True, "ok"))
    monkeypatch.setattr(cc_engine, "run_cc_turn", lambda **kw: fake_cc_turn(**kw))
    tid, sid = _post_query(client, "compound question")
    _wait_terminal(tid)
    log = ChatSession.objects.get(session_id=sid).extra_state["chat_log"]
    queries = [e["user_query"] for e in log]
    assert "op query" in queries and "compound question" in queries


def test_within_chat_db_recall_resolution_path(client, user):
    """§4.C server contract: get_session turn_id → bundle_id → download_bundle."""
    s = ChatSession.objects.create(
        user=user,
        results_history=[
            {
                "id": 2,
                "user_query": "q",
                "api_result_full": {
                    "ok": True,
                    "data": {"total": 1, "rows": [{"uid": "U-1"}]},
                },
            }
        ],
        extra_state={
            "chat_log": [
                {
                    "turn_id": 5,
                    "ts": "t",
                    "mode": "search",
                    "user_query": "q",
                    "assistant_reply": "a",
                    "bundle_id": 2,
                }
            ]
        },
    )
    detail = client.get(
        f"/nextseek_api/assistant/sessions/{s.session_id}/?include=turns"
    ).json()
    turn = next(t for t in detail["turns"] if t["turn_id"] == 5)
    assert turn["bundle_id"] == 2
    bundle = client.get(
        f"/nextseek_api/assistant/sessions/{s.session_id}/bundles/2/"
    ).json()
    assert bundle["api_result_full"]["data"]["rows"] == [{"uid": "U-1"}]


def test_within_chat_db_plan_mode_error_leaves_trace(client, monkeypatch):
    """Site #12: a plan-mode catch-all error now writes an error-labeled entry."""
    from nextseek_api.services import cc_assistant as svc

    monkeypatch.setattr(
        cc_router,
        "_baml_decision",
        lambda q, h=None: _decision(cc_router.ROUTE_NS),
    )

    def fake_plan(session, config, query, send_event, credentials=None):
        from chat_nextseek.chat_memory import append_turn

        reply = "An unexpected error occurred in the planner pipeline: KeyError('x')"
        append_turn(
            session,
            user_query=query,
            mode="error_plan_pipeline",
            assistant_reply=reply,
            status="error",
            error="KeyError('x')",
        )
        send_event("query_complete", {"reply": reply})

    monkeypatch.setattr(svc, "run_query_plan", fake_plan)
    resp = client.post(
        CC_QUERY_URL,
        {"query": "plan me", "mode": "plan"},
        format="json",
    )
    tid, sid = resp.json()["task_id"], resp.json()["session_id"]
    _wait_terminal(tid)
    log = ChatSession.objects.get(session_id=sid).extra_state["chat_log"]
    assert log[-1]["status"] == "error" and log[-1]["mode"] == "error_plan_pipeline"


# --------------------------------------------------------------- ns_run_root
# The hermetic tests in test_ns_run_root_event.py call _emit_ns_run_root
# directly, so they cannot see the CALL SITE. A helper that is perfectly tested
# and never correctly called reads as covered while being dead: the original
# brief named the session `ns_session`, which does not exist in that scope, and
# would have NameError'd on every NS turn with the hermetic suite fully green.
# These drive the real endpoint down the NS branch instead.
#
# Two things these tests deliberately do NOT establish. (a) Directory lifetime:
# the fake run_query below writes a fresh run_root_dir on every call, which the
# real orchestrator does not -- the directory is per chat SESSION and is reused
# by every turn of a multi-turn case, so consumers de-duplicate on run_root. See
# test_ns_run_root_event.py's module docstring. (b) Anything about the CC branch,
# which has no run_root at all.

def _progress_events(task_id, name):
    return [e for e in (QueryTask.objects.get(task_id=task_id).progress or [])
            if e["event"] == name]


def test_within_chat_db_ns_run_root_reaches_the_event_stream(client, monkeypatch):
    """A successful NS turn publishes the run_root the session carries, so a
    collector can join the task_id to its outputs/<ts>_<user>/ directory."""
    from nextseek_api.services import cc_assistant as svc

    monkeypatch.setattr(
        cc_router, "_baml_decision", lambda q, h=None: _decision(cc_router.ROUTE_NS)
    )

    def fake_run_query(session, config, query, send_event, credentials=None):
        # Mirrors orchestrator._ensure_query_log_dir, which writes this key
        # three statements into run_query.
        session["run_root_dir"] = "/app/outputs/260804_101500_wc-user"
        send_event("query_complete", {"reply": "ok", "bundle_id": None})

    monkeypatch.setattr(svc, "run_query", fake_run_query)

    tid, _ = _post_query(client, "how many mice")
    _wait_terminal(tid)

    assert _progress_events(tid, "ns_run_root") == [
        {"event": "ns_run_root",
         "data": {"run_root": "/app/outputs/260804_101500_wc-user"}}
    ]


def test_within_chat_db_ns_run_root_is_emitted_on_the_plan_branch(client, monkeypatch):
    """The NS branch forks on mode, and plan-mode calls run_query_plan. The emit
    sits after that fork on purpose, so BOTH arms reach it -- an emit that drifted
    into the `else:` would drop the join key for every plan-mode turn."""
    from nextseek_api.services import cc_assistant as svc

    monkeypatch.setattr(
        cc_router, "_baml_decision", lambda q, h=None: _decision(cc_router.ROUTE_NS)
    )

    def fake_run_query_plan(session, config, query, send_event, credentials=None):
        session["run_root_dir"] = "/app/outputs/260804_101500_plan"
        send_event("query_complete", {"reply": "a plan", "bundle_id": None})

    monkeypatch.setattr(svc, "run_query_plan", fake_run_query_plan)
    # Fail loudly rather than silently passing via the standard arm.
    monkeypatch.setattr(
        svc, "run_query",
        lambda *a, **k: pytest.fail("plan mode must not reach run_query"),
    )

    tid, _ = _post_query(client, "plan me", mode="plan")
    _wait_terminal(tid)

    assert _progress_events(tid, "ns_run_root") == [
        {"event": "ns_run_root", "data": {"run_root": "/app/outputs/260804_101500_plan"}}
    ]


def test_within_chat_db_ns_run_root_survives_a_raising_orchestrator(
    client, monkeypatch, caplog
):
    """The turn a collector most needs is the one that BLEW UP -- run_root_dir is
    written before anything can fail, so the directory exists and holds a
    console.txt. The emit therefore lives in a `finally`.

    Second half matters as much as the first: instrumentation must not swallow a
    real failure, so the original RuntimeError must still propagate out of the NS
    branch unchanged and be the exception the catch-all logs."""
    from nextseek_api.services import cc_assistant as svc

    monkeypatch.setattr(
        cc_router, "_baml_decision", lambda q, h=None: _decision(cc_router.ROUTE_NS)
    )

    def exploding_run_query(session, config, query, send_event, credentials=None):
        session["run_root_dir"] = "/app/outputs/260804_101500_wc-user"
        raise RuntimeError("graph agent exploded")

    monkeypatch.setattr(svc, "run_query", exploding_run_query)

    with caplog.at_level(logging.ERROR, logger="nextseek_api.services.cc_assistant"):
        tid, _ = _post_query(client, "how many mice")
        _wait_terminal(tid)

    # 1. The join key survived the raise.
    assert _progress_events(tid, "ns_run_root") == [
        {"event": "ns_run_root",
         "data": {"run_root": "/app/outputs/260804_101500_wc-user"}}
    ]

    # 2. The original exception propagated UNCHANGED -- not swallowed by the
    #    finally, and not replaced by one raised from inside it.
    logged = [r for r in caplog.records if r.msg == "cc-assistant pipeline error"]
    assert len(logged) == 1, "the catch-all did not see the exception"
    exc = logged[0].exc_info[1]
    assert type(exc) is RuntimeError and str(exc) == "graph agent exploded"

    # 3. And the turn is still reported as failed to the user.
    assert QueryTask.objects.get(task_id=tid).status == "error"

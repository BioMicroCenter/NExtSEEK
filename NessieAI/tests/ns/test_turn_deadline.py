"""Every op and nested NS turn of a Container-CC turn runs under the turn's own deadline (plan 04, piece 4).

The server's deadline is ``CCTurn.deadline_at``; an ``X-Nextseek-Deadline`` header may only bring it forward. An op's
limit is min(its table limit, deadline - now - 45 s); a model op with under 20 s usable gets TIME_UP (HTTP 408) at once
and no model is called. A nested query/plan/pipeline turn runs under the deadline minus the same 45 s. No model.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from NessieAI.ns import granular, op_limits
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask
from nextseek_api.assistant.turn_pass import issue_pass, set_deadline

START = 1_000_000.0


def _at(epoch):
    return SimpleNamespace(deadline_at=datetime.fromtimestamp(epoch, tz=timezone.utc))


def test_the_header_may_only_bring_the_deadline_forward():
    turn = _at(START + 180)
    assert op_limits.turn_deadline_epoch(turn, None) == START + 180
    assert op_limits.turn_deadline_epoch(turn, str(START + 400)) == START + 180, "a later header is ignored"
    assert op_limits.turn_deadline_epoch(turn, str(START + 100)) == START + 100, "an earlier one is honoured"
    assert op_limits.turn_deadline_epoch(turn, "soon") == START + 180
    assert op_limits.turn_deadline_epoch(turn, "nan") == START + 180
    assert op_limits.turn_deadline_epoch(None, str(START)) is None, "no turn: a header sets nothing"


@pytest.mark.parametrize("into, op, limit", [
    (60.0, "graph", 55.0),
    (100.0, "graph", 35.0),
    (100.0, "generate-submission", 35.0),
    (130.0, "graph", None),          # 5 s usable
    (170.0, "aggregate", None),      # past the reserve
])
def test_op_limits_in_a_180_s_turn(into, op, limit):
    """The view's limit: plan 03's op_limit_s with the turn's deadline. ``None``: under MIN_USABLE_S (op_limit_s may
    clamp a spent turn at 0; either way it is below), which the view answers with TIME_UP for a model op."""
    got = op_limits.op_limit_s(op, op_limits.turn_deadline_epoch(_at(START + 180), None), START + into)
    if limit is None:
        assert got < op_limits.MIN_USABLE_S
    else:
        assert got == pytest.approx(limit)


def test_a_nested_turn_runs_under_the_deadline_less_the_answer_reserve():
    until, time_up = op_limits.nested_deadline(_at(START + 180), None, START + 60)
    assert until == START + 180 - op_limits.ANSWER_RESERVE_S and time_up is False
    assert op_limits.nested_deadline(_at(START + 180), None, START + 130)[1] is True
    assert op_limits.nested_deadline(None, None, START) == (None, False)


@pytest.mark.django_db(transaction=True)
class TestThroughThePass:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.user = get_user_model().objects.create_user(f"dl-{uuid.uuid4().hex[:8]}", password="p")
        self.chat = ChatSession.objects.create(user=self.user)
        self.task = QueryTask.objects.create(session=self.chat, user=self.user, query="q", status="running")
        self.turn, raw = issue_pass(task=self.task, chat=self.chat, user=self.user, login=(self.user.username, "p"))
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"NextseekTurn {raw}")
        with patch("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", return_value=True):
            yield

    def _never_called(self):
        def boom(*a, **k):
            raise AssertionError("no model may be called after TIME_UP")
        return (patch("chat_nextseek.portable.entity_agent", boom), patch("chat_nextseek.portable.parser_agent", boom),
                patch("chat_nextseek.portable.graph_agent", boom))

    def test_time_up_without_a_model_call(self):
        set_deadline(self.turn, time.time() + 60)
        p1, p2, p3 = self._never_called()
        with p1, p2, p3:
            resp = self.client.post("/nextseek_api/assistant/graph/", {"query": "how many mice"}, format="json")
        assert resp.status_code == 408 and resp.json()["code"] == "TIME_UP"
        assert CCTurn.objects.get(pk=self.turn.pk).ops_in_flight == 0

    def test_an_earlier_header_is_honoured(self):
        set_deadline(self.turn, time.time() + 170)
        p1, p2, p3 = self._never_called()
        with p1, p2, p3:
            resp = self.client.post("/nextseek_api/assistant/graph/", {"query": "how many mice"}, format="json",
                                    HTTP_X_NEXTSEEK_DEADLINE=str(int(time.time() + 50)))
        assert resp.status_code == 408 and resp.json()["code"] == "TIME_UP"

    def test_an_op_that_calls_no_model_is_never_time_up(self):
        set_deadline(self.turn, time.time() + 60)
        handler = MagicMock(return_value={"source": "catalog", "schema": "", "vocabulary": ""})
        with patch.dict(granular._HANDLERS, {"graph-schema": handler}):
            resp = self.client.post("/nextseek_api/assistant/graph-schema/", {}, format="json")
        assert resp.status_code == 200, resp.content
        handler.assert_called_once()

    def test_a_no_model_op_with_30_s_left_runs_with_the_10_s_floor(self):
        set_deadline(self.turn, time.time() + 30)
        seen = {}

        def handler(args, config, session, write_gate, neo4j_exec, outputs_dir, **op_ctx):
            seen["limit_s"] = op_ctx["limit_s"]
            return {"saved_files": {}}

        with patch.dict(granular._HANDLERS, {"report": handler}):
            resp = self.client.post("/nextseek_api/assistant/report/", {"mode": "samples", "project": "p"},
                                    format="json")
        assert resp.status_code == 200, resp.content
        assert seen["limit_s"] == op_limits.NO_MODEL_FLOOR_S == 10.0

    def test_a_nested_turn_with_no_time_left_is_refused_before_its_task_exists(self):
        set_deadline(self.turn, time.time() + 60)
        before = QueryTask.objects.count()
        resp = self.client.post("/nextseek_api/assistant/query/async/",
                                {"query": "q", "mode": "standard", "session_id": str(self.chat.session_id)},
                                format="json")
        assert resp.status_code == 408 and resp.json()["code"] == "TIME_UP"
        assert QueryTask.objects.count() == before
        self.turn.refresh_from_db(fields=["ops_in_flight"])
        assert self.turn.ops_in_flight == 0, "a TIME_UP refusal takes no slot (checked before plan 03's take)"

    def test_a_nested_turn_gets_the_deadline_less_the_reserve(self):
        deadline = time.time() + 300
        set_deadline(self.turn, deadline)
        seen = {}
        with patch("nextseek_api.services.assistant.run_async_pipeline", lambda **kw: seen.update(kw)):
            resp = self.client.post("/nextseek_api/assistant/query/async/",
                                    {"query": "q", "mode": "standard", "session_id": str(self.chat.session_id)},
                                    format="json")
            for _ in range(100):
                if seen:
                    break
                time.sleep(0.05)
        assert resp.status_code == 202
        assert seen["deadline_epoch"] == pytest.approx(deadline - op_limits.ANSWER_RESERVE_S, abs=1.0)


@pytest.mark.parametrize("mode, entry", [
    ("standard", "run_query"), ("plan", "run_query_plan"), ("pipeline", "run_pipeline_launch"),
])
def test_the_thread_body_hands_the_deadline_to_the_entry_point_it_runs(monkeypatch, mode, entry):
    """run_async_pipeline forwards deadline_epoch to whichever orchestrator entry point req.mode selects."""
    from NessieAI.ns import turn as ns_turn
    seen: dict = {}
    for name in ("run_query", "run_query_plan", "run_pipeline_launch"):
        monkeypatch.setattr(ns_turn, name, lambda *a, _n=name, **kw: seen.setdefault(_n, kw))
    monkeypatch.setattr(ns_turn, "_save_session_or_report", lambda *a, **k: None)
    ns_turn.run_async_pipeline(adapter={}, chat_config=SimpleNamespace(), req=SimpleNamespace(query="q", mode=mode),
                               send_event=lambda e, d: None, api_user="u", api_pass="p", chat_session=None,
                               resolved_session_id="s", deadline_epoch=START + 30)
    assert list(seen) == [entry]
    assert seen[entry]["deadline_epoch"] == START + 30

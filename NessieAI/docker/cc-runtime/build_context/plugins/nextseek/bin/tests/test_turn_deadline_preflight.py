"""A tool refuses a model op the turn has no usable time left for, and tells the server its deadline (piece 4).

Usable time is the turn's deadline (NEXTSEEK_CC_TURN_DEADLINE_EPOCH) less the 45 s answer reserve. Under 20 s, a
model op exits at once with TIME_UP (exit 11) and the approved text; Django makes the same check and is the control.
Every request the assistant client sends carries X-Nextseek-Deadline, which can only shorten the server's deadline.
"""
import json
import os
import subprocess
import sys
import time

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _assistant_client as ac  # noqa: E402
import _turn_deadline as td  # noqa: E402
import _turn_pass as tp  # noqa: E402

BIN = os.path.join(os.path.dirname(__file__), "..")
ENV = td.TURN_DEADLINE_ENV
TEXT = "Not enough time left in this turn to run nextseek-graph. Answer from what you already have and say what is missing."


def test_preflight_refuses_under_20_s_usable_and_allows_from_20(monkeypatch):
    now = 1_000_000.0
    monkeypatch.setenv(ENV, str(now + 45 + 19))
    assert td.preflight("nextseek-graph", now) == TEXT
    monkeypatch.setenv(ENV, str(now + 45 + 20))
    assert td.preflight("nextseek-graph", now) is None


def test_preflight_allows_when_the_deadline_is_absent_or_unreadable(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert td.preflight("nextseek-graph", 0.0) is None
    monkeypatch.setenv(ENV, "not a number")
    assert td.preflight("nextseek-graph", 0.0) is None


def test_the_deadline_header_is_the_turns_deadline(monkeypatch):
    monkeypatch.setenv(ENV, "1700000123.9")
    assert td.deadline_headers() == {"X-Nextseek-Deadline": "1700000123"}
    monkeypatch.setenv(ENV, "inf")
    assert td.deadline_headers() == {}
    monkeypatch.delenv(ENV)
    assert td.deadline_headers() == {}


def _runner(agent, *extra, deadline_in):
    env = dict(os.environ, NEXTSEEK_DRY_RUN="1", **{ENV: str(time.time() + deadline_in)})
    return subprocess.run([sys.executable, os.path.join(BIN, "_nextseek_runner.py"), "--agent", agent, *extra],
                          env=env, capture_output=True, text=True, timeout=30)


def test_a_model_op_late_in_the_turn_exits_time_up_with_the_text():
    proc = _runner("graph", "--query", "how many mice", deadline_in=50)
    assert proc.returncode == 11
    assert json.loads(proc.stderr.strip().splitlines()[-1]) == {"error": {"code": "TIME_UP", "message": TEXT}}


def test_the_entity_tool_is_named_by_its_own_name():
    proc = _runner("entity", "--query", "mice", deadline_in=50)
    assert proc.returncode == 11 and "run nextseek-entity-extract." in proc.stderr


@pytest.mark.parametrize("agent, extra", [("graph", ("--query", "q")), ("graph-schema", ())])
def test_an_op_with_time_left_or_no_model_is_not_refused(agent, extra):
    deadline_in = 120 if agent == "graph" else 50
    proc = _runner(agent, *extra, deadline_in=deadline_in)
    assert proc.returncode == 0, proc.stderr


def test_the_assistant_client_sends_the_deadline_header(monkeypatch):
    monkeypatch.setenv(ENV, "1700000123")
    seen = {}

    def handler(request):
        seen["header"] = request.headers.get("X-Nextseek-Deadline")
        return httpx.Response(200, json={})
    client = ac.AssistantClient(base_url="http://n", assistant_prefix="nextseek_api/assistant",
                                auth=tp.TurnPassAuth("pass-1"), transport=httpx.MockTransport(handler))
    for timeout in (None, 7.0):   # plan 03's post_op and download_artifact_to pass a timeout
        with client._client(timeout=timeout) as http:
            http.get(client._url("me/"))
        assert seen["header"] == "1700000123"

"""A child NS turn refused with an op code (BUSY when the turn's two slots are taken, TIME_UP, PASS_NOT_ALLOWED) makes
nextseek-query, nextseek-plan and nextseek-pipeline exit with that code, as the 11 op tools do (approach 1)."""
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _nextseek_runner as runner  # noqa: E402
import _assistant_client as ac  # noqa: E402

SID = "11111111-1111-1111-1111-111111111111"
BUSY_MESSAGE = ("Two ops or NS queries of this turn are already running; wait for one of them to answer before "
                "starting another.")
BUSY = {"code": "BUSY", "reason": None, "message": BUSY_MESSAGE, "errors": [{"title": "BUSY", "detail": BUSY_MESSAGE}]}


class _Args:
    query = "how many samples"
    planner = False
    message = "Launch scrnaseq on D.SEQ-1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_URL", "http://testserver")
    monkeypatch.setenv("NEXTSEEK_TURN_PASS", "pass-for-tests")
    monkeypatch.setenv("NEXTSEEK_CHAT_SESSION_ID", SID)
    monkeypatch.delenv("NEXTSEEK_DRY_RUN", raising=False)


def _answer_async_with(monkeypatch, status, body):
    real_init = ac.AssistantClient.__init__

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/query/async/"), request.url.path
        return httpx.Response(status, json=body)

    def patched_init(self, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, **kw)

    monkeypatch.setattr(ac.AssistantClient, "__init__", patched_init)


@pytest.mark.parametrize("dispatch", [runner._dispatch_query, runner._dispatch_plan, runner._dispatch_pipeline])
def test_a_busy_child_turn_exits_10_with_the_servers_message(monkeypatch, capsys, dispatch):
    _answer_async_with(monkeypatch, 429, BUSY)
    with pytest.raises(SystemExit) as exc:
        dispatch(_Args())
    assert exc.value.code == 10
    error = json.loads(capsys.readouterr().err.strip().splitlines()[-1])["error"]
    assert (error["code"], error["message"]) == ("BUSY", BUSY_MESSAGE)


def test_a_refusal_without_an_op_code_keeps_todays_exit(monkeypatch, capsys):
    _answer_async_with(monkeypatch, 500, {"detail": "boom"})
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_query(_Args())
    assert exc.value.code == 4
    assert json.loads(capsys.readouterr().err.strip().splitlines()[-1])["error"]["code"] == "AGENT_FAILED"

import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _nextseek_runner as runner  # noqa: E402
import _assistant_client as ac  # noqa: E402


class _Args:
    def __init__(self, message):
        self.message = message


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_URL", "http://testserver")
    monkeypatch.setenv("API_USER", "u")
    monkeypatch.setenv("API_PASS", "p")
    monkeypatch.delenv("NEXTSEEK_DRY_RUN", raising=False)


def _mock_client(monkeypatch, handler):
    real_init = ac.AssistantClient.__init__

    def patched_init(self, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, **kw)

    monkeypatch.setattr(ac.AssistantClient, "__init__", patched_init)


def test_dispatch_pipeline_posts_message_mode_and_session(monkeypatch):
    # AsyncQueryResponse/TaskProgressResponse require UUID task_id + session_id
    # (extra="forbid"), so the mock payloads use real UUIDs.
    sid = "11111111-1111-1111-1111-111111111111"
    task_uuid = "22222222-2222-2222-2222-222222222222"
    monkeypatch.setenv("NEXTSEEK_CHAT_SESSION_ID", sid)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/query/async/"):
            seen["body"] = json.loads(request.content)
            return httpx.Response(202, json={"task_id": task_uuid, "session_id": sid})
        # progress poll → one terminal query_complete
        return httpx.Response(200, json={
            "task_id": task_uuid, "session_id": sid, "status": "completed",
            "progress": [{"event": "query_complete",
                          "data": {"reply": "Proposing scrnaseq — pick a genome?",
                                   "debug": {}, "bundle_id": None}}],
            "result": None,
        })

    _mock_client(monkeypatch, handler)
    out = runner._dispatch_pipeline(_Args("Launch scrnaseq on D.SEQ-1"))

    assert out["reply"].startswith("Proposing scrnaseq")
    assert seen["body"]["query"] == "Launch scrnaseq on D.SEQ-1"
    assert seen["body"]["mode"] == "pipeline"
    assert seen["body"]["session_id"] == sid


def test_dispatch_pipeline_requires_session_id(monkeypatch):
    monkeypatch.delenv("NEXTSEEK_CHAT_SESSION_ID", raising=False)
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_pipeline(_Args("anything"))
    assert exc.value.code == 2  # CONFIG_MISSING


def test_dispatch_pipeline_requires_message(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_CHAT_SESSION_ID", "sess-42")
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_pipeline(_Args(""))
    assert exc.value.code == 3  # VALIDATION


def test_dispatch_pipeline_dry_run(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_DRY_RUN", "1")
    monkeypatch.setenv("NEXTSEEK_CHAT_SESSION_ID", "sess-42")
    out = runner._dispatch_pipeline(_Args("Launch scrnaseq"))
    assert out == {"reply": "[dry-run]", "debug": {}, "bundle_id": None}

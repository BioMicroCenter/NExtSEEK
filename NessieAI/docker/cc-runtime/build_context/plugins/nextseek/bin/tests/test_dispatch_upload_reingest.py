import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _nextseek_runner as runner  # noqa: E402

_BUILD_ID = "a" * 64


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_USER", "u")
    monkeypatch.setenv("API_PASS", "p")
    monkeypatch.delenv("NEXTSEEK_DRY_RUN", raising=False)


def test_upload_reingest_is_registered_under_its_runner_key():
    assert runner._DISPATCH["upload-reingest"] is runner._dispatch_upload_reingest


def test_upload_reingest_without_confirmed_write_is_write_blocked(capsys):
    args = SimpleNamespace(build_ids=_BUILD_ID, confirmed_write=False)
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_upload_reingest(args)
    assert exc.value.code == 5
    err = json.loads(capsys.readouterr().err)
    assert err["error"]["code"] == "WRITE_BLOCKED"


def test_upload_reingest_without_build_ids_is_a_validation_error(capsys):
    args = SimpleNamespace(build_ids="", confirmed_write=True)
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_upload_reingest(args)
    assert exc.value.code == 3
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "VALIDATION"


def test_upload_reingest_forwards_build_ids_and_the_boolean_confirmation(monkeypatch):
    # The default (direct) road, through the runner's own _make_client and the env Django gives the container: one
    # POST to NExtSEEK's upload-reingest endpoint carrying the turn pass. NExtSEEK is an httpx.MockTransport.
    import httpx

    import _assistant_client as ac

    monkeypatch.setenv("NEXTSEEK_URL", "http://nextseek_nginx")
    monkeypatch.setenv("NEXTSEEK_TURN_PASS", "pass-1")
    monkeypatch.delenv("NEXTSEEK_CC_OPS_ROAD", raising=False)
    seen = []

    def nextseek(request):
        seen.append(request)
        return httpx.Response(200, json={"op": "upload-reingest", "result": {"jobs": [], "reply": "ok"}})

    real_init = ac.AssistantClient.__init__

    def with_mock_transport(self, **kw):
        real_init(self, **{**kw, "transport": httpx.MockTransport(nextseek)})

    monkeypatch.setattr(ac.AssistantClient, "__init__", with_mock_transport)
    args = SimpleNamespace(build_ids=_BUILD_ID, confirmed_write=True)
    out = runner._dispatch_upload_reingest(args)
    assert out == {"jobs": [], "reply": "ok"}
    [request] = seen
    assert (request.method, request.url.path) == ("POST", "/nextseek_api/assistant/upload-reingest/")
    assert request.headers["Authorization"].split()[-1] == "pass-1"
    body = json.loads(request.content)
    assert body == {"build_ids": _BUILD_ID, "confirmed_write": True}
    assert body["confirmed_write"] is True


def test_upload_reingest_on_the_sidecar_road_forwards_the_same_body(monkeypatch):
    # The rollback road (NEXTSEEK_CC_OPS_ROAD=sidecar) sends the same op and body over the sidecar, with the turn pass.
    import _sidecar_client as sc

    monkeypatch.setenv("NEXTSEEK_CC_OPS_ROAD", "sidecar")
    monkeypatch.setenv("NEXTSEEK_TURN_PASS", "pass-1")
    monkeypatch.delenv("NEXTSEEK_USERNAME", raising=False)
    seen = {}

    def fake_call_op(op, body, **kw):
        seen["op"], seen["body"], seen["kw"] = op, body, kw
        return {"jobs": [], "reply": "ok"}

    monkeypatch.setattr(sc, "call_op", fake_call_op)
    monkeypatch.setattr(sc, "sidecar_url_from_env", lambda: "ws://sidecar")
    args = SimpleNamespace(build_ids=_BUILD_ID, confirmed_write=True)
    out = runner._dispatch_upload_reingest(args)
    assert out == {"jobs": [], "reply": "ok"}
    assert seen["op"] == "upload-reingest"
    assert seen["body"] == {"build_ids": _BUILD_ID, "confirmed_write": True}
    assert seen["body"]["confirmed_write"] is True
    assert seen["kw"] == {"ns_turn": ("u", "pass-1"), "sidecar_url": "ws://sidecar"}

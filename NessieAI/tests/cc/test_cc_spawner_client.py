"""The app's spawner client against the real cc-spawner HTTP layer (the Docker SDK faked behind it)."""
from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

import pytest

from NessieAI.cc import spawner_client

_SPAWNER = Path(__file__).resolve().parents[3] / "docker" / "cc-spawner" / "spawner.py"
pytestmark = pytest.mark.skipif(not _SPAWNER.exists(), reason="cc-spawner source not in this tree")

TOKEN = "wire-token"
RUN = "0123abcd-4567-89ab-cdef-0123456789ab"


def _load():
    spec = importlib.util.spec_from_file_location("cc_spawner_wire", _SPAWNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cc_spawner_wire"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Container:
    def __init__(self, name, labels):
        self.name, self.labels = name, labels
        self.attrs = {"Config": {"Labels": labels}}
        self.sent = b""
        self.stopped = False

    def attach_socket(self, params=None):
        outer = self

        class _S:
            def sendall(self, data):
                outer.sent += data

            def shutdown(self, how):
                pass

        class _R:
            _sock = _S()

        return _R()

    def logs(self, **kw):
        return iter([b'{"type":"sys', b'tem"}\n{"type":"result"}\n', b"tail-no-newline"])

    def stop(self, timeout=None):
        self.stopped = True

    def exec_run(self, cmd, **kw):
        self.exec = (cmd, kw)
        return 0, (b'{"ok": true, "user_id": 4}\n', b"")


class _Containers:
    def __init__(self):
        self.by_name = {}

    def run(self, **kw):
        c = _Container(kw["name"], kw["labels"])
        self.by_name[kw["name"]] = c
        self.last_kw = kw
        return c

    def get(self, name):
        from docker.errors import NotFound
        if name not in self.by_name:
            raise NotFound("x")
        return self.by_name[name]


class _Ok:
    def get(self, _name):
        return object()


class _Client:
    def __init__(self):
        self.containers = _Containers()
        self.images = self.networks = _Ok()
        self.containers.by_name["seek"] = _Container("seek", {})

    def ping(self):
        return True


@pytest.fixture
def served(monkeypatch):
    mod = _load()
    client = _Client()
    cfg = mod.Config(token=TOKEN)
    server = mod.make_server(mod.Spawner(client, cfg), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("NEXTSEEK_SPAWNER_URL", f"http://127.0.0.1:{server.server_address[1]}/")
    monkeypatch.setenv("NEXTSEEK_SPAWNER_TOKEN", TOKEN)
    yield client
    server.shutdown()
    server.server_close()


def _body(**over):
    body = {"run_id": RUN, "user_id": "alice", "project_dirname": "proj", "model_id": "m",
            "max_budget_usd": 0.5, "environment": {"API_USER": "alice"}, "stdin_envelope": '{"type":"user"}\n'}
    body.update(over)
    return body


@pytest.mark.parametrize("value", [None, "", "   "])
def test_switch_is_off_when_the_url_is_unset_or_empty(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("NEXTSEEK_SPAWNER_URL", raising=False)
    else:
        monkeypatch.setenv("NEXTSEEK_SPAWNER_URL", value)
    assert spawner_client.enabled() is False


def test_switch_is_on_when_the_url_is_set(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_SPAWNER_URL", "http://cc-spawner:8080")
    assert spawner_client.enabled() is True


def test_health_over_the_wire(served):
    assert spawner_client.health() == (True, "ok")


def test_health_without_a_token_is_not_green(served, monkeypatch):
    # The spawner answers a no-token /health with a plain {ok}; every real call would then get 401.
    monkeypatch.delenv("NEXTSEEK_SPAWNER_TOKEN", raising=False)
    assert spawner_client.health() == (False, "NEXTSEEK_SPAWNER_TOKEN is not set")


def test_health_with_a_wrong_token_is_not_green(served, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_SPAWNER_TOKEN", "wrong")
    ok, detail = spawner_client.health()
    assert ok is False and "403" in detail


def test_health_without_the_authenticated_detail_is_not_green(served, monkeypatch):
    # A 200 that carries no detail is the unauthenticated answer (say a proxy dropped the header).
    monkeypatch.setattr(spawner_client, "_headers", lambda: {})
    ok, detail = spawner_client.health()
    assert ok is False and "token" in detail


def test_health_unreachable(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_SPAWNER_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("NEXTSEEK_SPAWNER_TOKEN", TOKEN)
    ok, detail = spawner_client.health()
    assert ok is False and "spawner" in detail


def test_a_wrong_token_is_refused(served, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_SPAWNER_TOKEN", "wrong")
    with pytest.raises(spawner_client.SpawnerRefused) as caught:
        spawner_client.stop_turn(RUN)
    assert caught.value.status == 403


def test_turn_lines_are_reassembled_across_chunks(served):
    stream = spawner_client.start_turn(_body(), read_timeout=10)
    lines = []
    while (line := stream.read_event_line()) is not None:
        lines.append(line)
    stream.close()
    assert lines == ['{"type":"system"}', '{"type":"result"}', "tail-no-newline"]
    assert served.containers.by_name[f"dmac-cc-agent-{RUN}"].sent == b'{"type":"user"}\n'


def test_a_refused_turn_raises_with_the_status(served):
    with pytest.raises(spawner_client.SpawnerRefused) as caught:
        spawner_client.start_turn(_body(user_id=".."), read_timeout=10)
    assert caught.value.status == 422


def test_stop_turn_reports_gone(served):
    spawner_client.start_turn(_body(), read_timeout=10).close()
    assert spawner_client.stop_turn(RUN) is True
    assert served.containers.by_name[f"dmac-cc-agent-{RUN}"].stopped is True


def test_seek_op_over_the_wire_keeps_the_payload_out_of_argv(served):
    out = spawner_client.seek_op("create", {"password": "pw-1"}, timeout=10)
    assert out["exit_code"] == 0 and out["last_line"] == '{"ok": true, "user_id": 4}'
    cmd, kw = served.containers.by_name["seek"].exec
    assert "pw-1" not in repr(cmd) and "pw-1" in kw["environment"]["NEXTSEEK_PAYLOAD"]


def test_unknown_seek_op_is_refused(served):
    with pytest.raises(spawner_client.SpawnerRefused) as caught:
        spawner_client.seek_op("nope", {}, timeout=10)
    assert caught.value.status == 404

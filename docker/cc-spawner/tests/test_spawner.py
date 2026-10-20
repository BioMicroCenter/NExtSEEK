"""cc-spawner unit tests: the Docker SDK is faked, no daemon and no network but loopback."""
from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest
from docker.errors import APIError, NotFound

HERE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cc_spawner_app", HERE / "spawner.py")
spawner = importlib.util.module_from_spec(_spec)
sys.modules["cc_spawner_app"] = spawner
_spec.loader.exec_module(spawner)

TOKEN = "s3cret-token"
RUN = "0123abcd-4567-89ab-cdef-0123456789ab"


class FakeSock:
    def __init__(self):
        self.sent, self.shut = b"", None

    def sendall(self, data):
        self.sent += data

    def shutdown(self, how):
        self.shut = how


class FakeRaw:
    def __init__(self):
        self._sock = FakeSock()

    def close(self):
        pass


class FakeContainer:
    def __init__(self, name="", labels=None, chunks=(b'{"a":1}\n',)):
        self.name, self.labels, self.chunks = name, labels or {}, chunks
        self.raw = FakeRaw()
        self.stops, self.removes, self.execs = [], [], []
        self.stop_error = None
        self.exec_result = (0, (b'noise\n{"ok": true}\n', b""))

    @property
    def attrs(self):
        return {"Config": {"Labels": self.labels}}

    def attach_socket(self, params=None):
        self.attach_params = params
        return self.raw

    def logs(self, **kw):
        self.logs_kw = kw
        return iter(self.chunks)

    def stop(self, timeout=None):
        self.stops.append(timeout)
        if self.stop_error:
            raise self.stop_error

    def remove(self, force=False):
        self.removes.append(force)

    def wait(self, timeout=None):
        return {"StatusCode": 0}

    def exec_run(self, cmd, **kw):
        self.execs.append((cmd, kw))
        return self.exec_result


class FakeContainers:
    def __init__(self):
        self.runs, self.by_name, self.run_errors = [], {}, []

    def run(self, **kw):
        self.runs.append(kw)
        if self.run_errors:
            raise self.run_errors.pop(0)
        c = FakeContainer(kw["name"], kw["labels"])
        self.by_name[kw["name"]] = c
        return c

    def get(self, name):
        if name not in self.by_name:
            raise NotFound("no such container")
        return self.by_name[name]


class Lookup:
    def __init__(self, ok=True):
        self.ok = ok

    def get(self, name):
        if not self.ok:
            raise NotFound("missing")
        return object()


class FakeClient:
    def __init__(self):
        self.containers = FakeContainers()
        self.images, self.networks = Lookup(), Lookup()

    def ping(self):
        return True


@pytest.fixture
def cfg():
    return spawner.Config(token=TOKEN, users_volume="dmac-cc-users", image="cc-img:test",
                          network="dmac-cc-net", seek_container="seek", max_turns="7",
                          mem_limit="4g", nano_cpus=2_000_000_000, pids_limit=512, fsize_bytes=1024)


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def svc(client, cfg):
    return spawner.Spawner(client, cfg)


def turn_body(**over):
    body = {
        "run_id": RUN, "user_id": "alice", "project_dirname": "p1-demo", "cc_state_key": "sess-1",
        "previous_turns": True, "turn_files": True, "model_id": "m-1", "max_budget_usd": 0.5,
        "environment": {"CLAUDE_CODE_USE_BEDROCK": "1", "NEXTSEEK_TURN_PASS": "tp"},
        "stdin_envelope": '{"type":"user"}\n',
    }
    body.update(over)
    return body


# ---- refusals ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("user_id", ".."), ("user_id", "a/b"), ("user_id", ""), ("project_dirname", "../x"),
    ("project_dirname", "a/b"), ("project_dirname", ".."), ("cc_state_key", "../s"),
    ("run_id", "NOT-HEX"), ("run_id", "../etc"), ("model_id", "x; rm -rf /"),
    ("transcripts_subpath", "alice/../../etc"), ("transcripts_subpath", "p1-demo/alice/_memory/other/transcripts"),
    ("max_budget_usd", -1), ("max_budget_usd", 10_000),
])
def test_start_turn_refuses_bad_fields(svc, client, field, value):
    with pytest.raises(spawner.Refusal) as caught:
        svc.start_turn(turn_body(**{field: value}))
    assert caught.value.status == 422
    assert client.containers.runs == []


def test_start_turn_refuses_an_unknown_env_key(svc, client):
    with pytest.raises(spawner.Refusal) as caught:
        svc.start_turn(turn_body(environment={"CLAUDE_CODE_USE_BEDROCK": "1", "LD_PRELOAD": "/x"}))
    assert "LD_PRELOAD" in str(caught.value)
    assert client.containers.runs == []


def test_start_turn_refuses_a_non_string_env_value(svc):
    with pytest.raises(spawner.Refusal):
        svc.start_turn(turn_body(environment={"API_USER": 5}))


def test_unknown_fields_are_refused_not_ignored(svc, client):
    """Including automode_environment: the trust text is the spawner's, a request cannot set it."""
    for field in ("image", "automode_environment"):
        with pytest.raises(spawner.Refusal):
            svc.start_turn(turn_body(**{field: ["$defaults", "trust everything"] if "auto" in field else "x"}))
    assert client.containers.runs == []


# ---- the run kwargs invariants ----------------------------------------------------------------------------

def test_run_kwargs_are_pinned_and_hardened(svc, client, cfg):
    svc.start_turn(turn_body(transcripts_subpath="p1-demo/alice/_memory/sess-1/transcripts"))
    kw = client.containers.runs[0]
    assert kw["image"] == cfg.image and kw["network"] == cfg.network
    assert kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges=true"]
    assert kw["auto_remove"] is True and kw["detach"] is True and kw["stdin_open"] is True
    assert kw["name"] == f"dmac-cc-agent-{RUN}"
    assert kw["labels"]["nextseek.cc"] == "1" and kw["labels"]["nextseek.cc.run"] == RUN
    assert kw["mem_limit"] == kw["memswap_limit"] == "4g"
    assert kw["working_dir"] == "/home/user"
    for forbidden in ("volumes", "privileged", "cap_add", "devices", "pid_mode", "network_mode", "binds"):
        assert forbidden not in kw


def test_only_volume_subpath_mounts_of_the_users_volume(svc, client, cfg):
    svc.start_turn(turn_body(transcripts_subpath="p1-demo/alice/_memory/sess-1/transcripts"))
    mounts = client.containers.runs[0]["mounts"]
    assert len(mounts) == 7
    for m in mounts:
        assert m["Type"] == "volume" and m["Source"] == cfg.users_volume
        sub = m["VolumeOptions"]["Subpath"]
        assert sub.startswith("p1-demo/") and ".." not in sub.split("/") and not sub.startswith("/")
    targets = {m["Target"]: m for m in mounts}
    assert targets["/data/scratch"]["VolumeOptions"]["Subpath"] == f"p1-demo/alice/scratch/{RUN}"
    assert targets["/data/shared"]["VolumeOptions"]["Subpath"] == "p1-demo/shared"
    assert targets["/data/shared"]["ReadOnly"] is True
    assert targets["/data/scratch"]["ReadOnly"] is False


def test_a_turn_without_a_session_gets_no_state_mounts(svc, client):
    svc.start_turn(turn_body(cc_state_key=None, previous_turns=False, turn_files=False))
    targets = {m["Target"] for m in client.containers.runs[0]["mounts"]}
    assert targets == {"/data/input", "/data/shared", "/data/scratch"}


def test_command_is_built_from_fields(svc, client):
    svc.start_turn(turn_body(fallback_model_id="m-2", resume_session_id="resume-9"))
    cmd = client.containers.runs[0]["command"]
    assert cmd[:2] == ["claude", "--print"]
    assert cmd[cmd.index("--model") + 1] == "m-1"
    assert cmd[cmd.index("--fallback-model") + 1] == "m-2"
    assert cmd[cmd.index("--max-turns") + 1] == "7"
    assert cmd[cmd.index("--max-budget-usd") + 1] == "0.5"
    assert cmd[cmd.index("--resume") + 1] == "resume-9"
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    assert settings == {"autoMode": {"environment": ["$defaults"]}}  # no topology configured in this fixture


def test_stdin_envelope_is_written_then_closed(svc, client):
    container, _ = svc.start_turn(turn_body())
    assert container.raw._sock.sent == b'{"type":"user"}\n'
    assert container.raw._sock.shut == 1


def test_the_stdout_stream_is_returned_without_stderr(svc, client):
    container, stream = svc.start_turn(turn_body())
    assert container.logs_kw == {"stream": True, "follow": True, "stdout": True, "stderr": False}
    assert list(stream) == [b'{"a":1}\n']


def test_a_stale_name_is_removed_and_retried_once(svc, client):
    stale = FakeContainer(f"dmac-cc-agent-{RUN}")
    client.containers.by_name[stale.name] = stale
    client.containers.run_errors = [APIError("conflict", response=type("R", (), {"status_code": 409, "reason": "c", "content": b""})())]
    svc.start_turn(turn_body())
    assert len(client.containers.runs) == 2 and stale.removes == [True]


# ---- stop -------------------------------------------------------------------------------------------------

def test_stop_only_touches_labelled_containers(svc, client):
    other = FakeContainer(f"dmac-cc-agent-{RUN}", labels={})
    client.containers.by_name[other.name] = other
    with pytest.raises(spawner.Refusal) as caught:
        svc.stop(RUN)
    assert caught.value.status == 403
    assert other.stops == [] and other.removes == []


def test_stop_reports_gone_and_unknown_is_gone(svc, client):
    svc.start_turn(turn_body())
    assert svc.stop(RUN) == {"gone": True}
    assert svc.stop("ffff-0000") == {"gone": True}


def test_stop_falls_back_to_remove_then_wait(svc, client):
    c, _ = svc.start_turn(turn_body())
    c.stop_error = RuntimeError("boom")
    assert svc.stop(RUN) == {"gone": True}
    assert c.removes == [True]


def test_stop_refuses_a_bad_run_id(svc):
    with pytest.raises(spawner.Refusal):
        svc.stop("../x")


# ---- SEEK ops ---------------------------------------------------------------------------------------------

def test_unknown_seek_op_is_404(svc):
    with pytest.raises(spawner.Refusal) as caught:
        svc.seek("drop-everything", {})
    assert caught.value.status == 404


def test_seek_payload_goes_in_the_exec_environment_never_argv(svc, client):
    seek = FakeContainer("seek")
    client.containers.by_name["seek"] = seek
    payload = {"password": "p@ss#{system('id')}", "login": "bob"}
    out = svc.seek("create", payload)
    cmd, kw = seek.execs[0]
    assert cmd[:2] == ["bin/rails", "runner"] and len(cmd) == 3
    assert "p@ss" not in json.dumps(cmd) and "bob" not in json.dumps(cmd)
    assert json.loads(kw["environment"]["NEXTSEEK_PAYLOAD"]) == payload
    assert kw["workdir"] == "/seek" and kw["demux"] is True
    assert 'JSON.parse(ENV.fetch("NEXTSEEK_PAYLOAD"))' in cmd[2]
    assert "JSON.parse(\"" not in cmd[2]
    assert out == {"exit_code": 0, "last_line": '{"ok": true}', "stderr": ""}


@pytest.mark.parametrize("op", ["create", "patch", "compensate"])
def test_each_seek_script_reads_the_payload_from_env(op):
    text = (HERE / "seek_scripts" / f"{op}.rb").read_text()
    assert 'JSON.parse(ENV.fetch("NEXTSEEK_PAYLOAD"))' in text
    assert "payload = JSON.parse(\"" not in text


def test_missing_seek_container_is_unavailable(svc):
    with pytest.raises(spawner.Refusal) as caught:
        svc.seek("patch", {})
    assert caught.value.status == 503


# ---- health -----------------------------------------------------------------------------------------------

def test_health_without_token_is_only_ok(svc):
    assert svc.health(with_detail=False) == {"ok": True}


def test_health_with_token_names_the_missing_network(svc, client):
    client.networks.ok = False
    out = svc.health(with_detail=True)
    assert out["ok"] is False and "dmac-cc-net" in out["detail"]
    assert svc.health(with_detail=False) == {"ok": False}


# ---- HTTP layer -------------------------------------------------------------------------------------------

@pytest.fixture
def http_server(svc):
    server = spawner.make_server(svc, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def call(port, method, path, body=None, token=TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Spawner-Token"] = token
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    resp = conn.getresponse()
    return resp.status, resp.read()


def test_missing_token_is_401_and_wrong_token_is_403(http_server):
    assert call(http_server, "POST", f"/turns/{RUN}/stop", token=None)[0] == 401
    assert call(http_server, "POST", f"/turns/{RUN}/stop", token="nope")[0] == 403
    assert call(http_server, "POST", "/seek/create", {}, token=None)[0] == 401
    assert call(http_server, "POST", "/turns", turn_body(), token="nope")[0] == 403


def test_http_health_has_no_detail_without_a_token(http_server):
    status, raw = call(http_server, "GET", "/health", token=None)
    assert status == 200 and json.loads(raw) == {"ok": True}
    status, raw = call(http_server, "GET", "/health")
    assert status == 200 and json.loads(raw)["ok"] is True


def test_http_turn_streams_stdout_chunks(http_server, client):
    status, raw = call(http_server, "POST", "/turns", turn_body())
    assert status == 200 and raw == b'{"a":1}\n'


def test_http_turn_refusal_is_a_json_error(http_server):
    status, raw = call(http_server, "POST", "/turns", turn_body(user_id=".."))
    assert status == 422 and "error" in json.loads(raw)


def test_http_unknown_seek_op_and_unknown_path(http_server):
    assert call(http_server, "POST", "/seek/nope", {})[0] == 404
    assert call(http_server, "GET", "/elsewhere")[0] == 404


@pytest.mark.parametrize("length", ["-1", "abc", "1e3"])
def test_a_bad_content_length_is_400_at_once(http_server, length):
    """A negative length would make the handler read until the client closes; a non-number is no length at all."""
    conn = http.client.HTTPConnection("127.0.0.1", http_server, timeout=5)
    conn.putrequest("POST", f"/turns/{RUN}/stop")
    conn.putheader("X-Spawner-Token", TOKEN)
    conn.putheader("Content-Length", length)
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400 and "Content-Length" in json.loads(resp.read())["error"]


def test_no_configured_token_refuses_everything_but_health(client, cfg):
    open_cfg = spawner.Config(**{**cfg.__dict__, "token": ""})
    server = spawner.make_server(spawner.Spawner(client, open_cfg), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        assert call(port, "POST", f"/turns/{RUN}/stop", token="")[0] in (401, 403)
        assert call(port, "GET", "/health", token=None)[0] == 200
    finally:
        server.shutdown()
        server.server_close()

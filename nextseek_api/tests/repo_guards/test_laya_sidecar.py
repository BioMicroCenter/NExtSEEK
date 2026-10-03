"""The laya sidecar wrapper keeps the wire contract in scripts/laya/CONTRACT.md,
checks the key, and never logs a request body (JevLevROUTING, SPEC s10, s11).

A fake agent stands in for the model: no weights, no torch, no network.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LAYA_DIR = REPO_ROOT / "docker" / "laya"
KEY = "test-key-123"
SECRET_TEXT = "my secret patient sentence 8675309"


def _load():
    spec = importlib.util.spec_from_file_location("serve_wrapper", LAYA_DIR / "serve_wrapper.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeAgent:
    """Same shape as laya.Agent.system_one's reply for one choice question."""

    def __init__(self, truncated=False, boom=False):
        self.truncated, self.boom, self.calls = truncated, boom, []

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        if self.boom:
            raise RuntimeError("weights path /models/x leaked " + state)
        (qid, q), = questions.items()
        keys = list(q["criteria"])
        return {
            "model": "fake",
            "answers": {qid: {"type": "choice", "choice": keys[0],
                              "probabilities": {k: 1.0 / len(keys) for k in keys},
                              "answer_confidence": 0.9}},
            "usage": {"state_tokens": 42, "truncated": self.truncated},
        }


@pytest.fixture()
def serve(request):
    mod = _load()
    agent = FakeAgent(**getattr(request, "param", {}))
    server = mod.make_server(agent, revision="20261003-abcdef012345", api_key=KEY, host="127.0.0.1", port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", agent
    server.shutdown()
    server.server_close()


def _call(url, path, body=None, key=KEY, method=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


BODY = {"state": SECRET_TEXT, "question_id": "route", "prompt": "Which engine?",
        "options": {"nextseek_query": "data", "container_cc": "code", "unrelated": "other"}}


def test_health_reports_the_revision_without_a_key(serve):
    url, _ = serve
    assert _call(url, "/health", key=None) == (200, {"revision": "20261003-abcdef012345"})


def test_route_returns_the_contract_reply(serve):
    url, agent = serve
    code, out = _call(url, "/route", BODY)
    assert code == 200
    assert out == {"revision": "20261003-abcdef012345",
                   "probabilities": {"nextseek_query": 1 / 3, "container_cc": 1 / 3, "unrelated": 1 / 3},
                   "answer_confidence": 0.9, "state_tokens": 42, "truncated": False}
    state, questions = agent.calls[0]
    assert state == SECRET_TEXT
    assert questions == {"route": {"type": "choice", "instructions": "Which engine?",
                                   "criteria": BODY["options"]}}


@pytest.mark.parametrize("serve", [{"truncated": True}], indirect=True)
def test_route_passes_truncation_through(serve):
    url, _ = serve
    assert _call(url, "/route", BODY)[1]["truncated"] is True


@pytest.mark.parametrize("key", [None, "wrong"])
def test_route_needs_the_bearer_key(serve, key):
    url, agent = serve
    assert _call(url, "/route", BODY, key=key)[0] == 401
    assert agent.calls == []


@pytest.mark.parametrize("bad", [
    {}, {**BODY, "state": 5}, {**BODY, "options": {}}, {**BODY, "options": {"a": 1}},
    {**BODY, "prompt": ""}, {**BODY, "question_id": "other"}, [1, 2],
])
def test_route_refuses_a_malformed_body(serve, bad):
    url, agent = serve
    assert _call(url, "/route", bad)[0] == 400
    assert agent.calls == []


def test_unknown_path_is_404(serve):
    assert _call(serve[0], "/v1/systemone", BODY)[0] == 404


@pytest.mark.parametrize("serve", [{"boom": True}], indirect=True)
def test_a_model_failure_is_a_bare_500_and_never_echoes_or_logs_the_text(serve, caplog, capfd):
    url, _ = serve
    with caplog.at_level(logging.DEBUG):
        code, out = _call(url, "/route", BODY)
    assert code == 500 and out == {"error": "inference failed"}
    assert SECRET_TEXT not in json.dumps(out)
    assert SECRET_TEXT not in caplog.text
    assert "leaked" not in caplog.text


def test_a_good_request_logs_nothing_with_the_body(serve, caplog, capfd):
    url, _ = serve
    with caplog.at_level(logging.DEBUG):
        _call(url, "/route", BODY)
    err = capfd.readouterr()
    assert SECRET_TEXT not in caplog.text + err.out + err.err
    assert "Which engine" not in caplog.text + err.out + err.err


def test_wrapper_refuses_to_start_without_a_key():
    with pytest.raises(SystemExit):
        _load().make_server(FakeAgent(), revision="r", api_key="", host="127.0.0.1", port=0)


def test_wrapper_imports_no_torch_until_the_model_loads():
    src = (LAYA_DIR / "serve_wrapper.py").read_text()
    top = src.split("def main", 1)[0]
    assert "import torch" not in top and "import laya" not in top


def _text(name):
    return (LAYA_DIR / name).read_text()


def test_dockerfile_is_pinned_hash_checked_cpu_only_and_has_no_weights():
    df = _text("Dockerfile")
    first = next(l for l in df.splitlines() if l.startswith("FROM "))
    assert "python:3.12." in first and ":latest" not in first
    assert "--require-hashes" in df and "requirements.lock" in df
    assert "download.pytorch.org/whl/cpu" in df
    assert "HF_HUB_OFFLINE=1" in df
    assert "USER " in df and "USER root" not in df
    copies = [l for l in df.splitlines() if l.startswith(("COPY", "ADD"))]
    assert copies and not any("model" in l for l in copies)
    assert "serve_wrapper.py" in df
    assert not any(l.startswith("EXPOSE") for l in df.splitlines())  # the port rule: nothing published, ever


def test_build_context_never_ships_the_checkpoint():
    assert "models" in _text(".dockerignore").split()


def test_lock_pins_laya_with_hashes_and_every_line_is_hashed():
    lock = [l for l in _text("requirements.lock").splitlines() if l and not l.startswith("#")]
    assert any(l.startswith("laya==0.3.25") for l in lock)
    assert any(l.startswith("torch==") and "+cpu" in l for l in lock)
    pins = [l for l in lock if "==" in l]
    assert all(l.rstrip().endswith("\\") for l in pins)
    assert all(l.strip().startswith(("--hash=sha256:", "#")) or "==" in l for l in lock)
    assert not any(l.startswith(("fastapi", "uvicorn")) for l in lock)


def test_compose_passes_the_revision_to_the_wrapper():
    import yaml
    env = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]["laya-router"]["environment"]
    assert env["LAYA_REVISION"] == "${LAYA_REVISION:-unset}"


def test_laya_env_example_holds_only_a_placeholder_key():
    lines = [l for l in (REPO_ROOT / "docker" / "laya.env.example").read_text().splitlines()
             if l and not l.startswith("#")]
    assert lines == ['LAYA_API_KEY="SET_IN_LOCAL_ENV"']

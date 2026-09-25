"""run_cc_turn's side of the turn-record contract.

The translator records which models answered and what fell back
(``test_translate_model_fallback.py``); these tests check the engine hands it the
``--model`` id, carries the record into the persisted trace, and passes a
model-unavailable ``query_error`` through as the translator built it. No docker, no
network, no database.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import docker as docker_mod

from NessieAI.cc import cc_engine
from NessieAI.cc.cc_config import CCPaths
from NessieAI.cc.translate import MODEL_UNAVAILABLE_TRIED

RUN_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
MAIN = "us.anthropic.claude-opus-4-8"
FALLBACK = "us.anthropic.claude-opus-4-7"
FALLBACK_FRAME = {"type": "system", "subtype": "model_fallback", "trigger": "server_error",
                  "original_model": MAIN, "fallback_model": FALLBACK, "session_id": "s-1"}
RECORD = [{"agent": "container_cc", "from": MAIN, "to": FALLBACK, "reason": "server_error"}]


class _Container:
    def __init__(self):
        self.stopped = False

    def attach_socket(self, params=None):
        return object()

    def logs(self, **kwargs):
        return iter(())

    def stop(self, timeout=None):
        self.stopped = True

    def remove(self, force=False):
        self.stopped = True


class _Sock:
    def __init__(self, container, lines, idle):
        self._container = container
        self._lines = [json.dumps(line) for line in lines]
        self._idle = idle

    def send_stdin(self, _data):
        return None

    def close_stdin(self):
        return None

    def read_event_line(self):
        if self._lines:
            return self._lines.pop(0)
        if not self._idle:
            return None
        time.sleep(0.02)
        return None if self._container.stopped else ""


def _drive(tmp_path, monkeypatch, lines, *, idle=False, persist=False, **kwargs):
    container = _Container()

    class _Containers:
        def run(self, **_kw):
            return container

    class _Client:
        containers = _Containers()

    monkeypatch.setattr(docker_mod, "from_env", lambda: _Client())
    monkeypatch.setattr(cc_engine, "BridgeAttachSocket",
                        lambda raw, stdout_stream=None: _Sock(container, lines, idle))
    metas: list[dict] = []
    payloads: list = []
    if persist:
        from NessieAI.cc import cc_provision

        real_build = cc_provision.build_user_dirs

        def with_transcript(*a, **k):
            dirs = real_build(*a, **k)
            root = Path(dirs.cc_state_mnt) / "projects"
            root.mkdir(parents=True, exist_ok=True)
            (root / "turn.jsonl").write_bytes(
                b'{"type":"user","message":{"role":"user","content":"q"}}\n')
            return dirs

        monkeypatch.setattr(cc_provision, "build_user_dirs", with_transcript)
        from NessieAI.cc import cc_trace

        real_extract = cc_trace.extract_trace

        def spy(*a, **k):
            metas.append(dict(k.get("result_meta") or {}))
            return real_extract(*a, **k)

        monkeypatch.setattr(cc_trace, "extract_trace", spy)
        kwargs.update(chat_session=object(), user_query="q", cc_state_key="abc-123",
                      on_turn_complete=payloads.append)
    events: list[tuple[str, dict]] = []
    kwargs.setdefault("model_id", MAIN)
    cc_engine.run_cc_turn(
        query="q", api_user=None, api_pass=None,
        send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj", run_id=RUN_ID,
        paths=CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path)),
        **kwargs,
    )
    terminals = [(e, d) for e, d in events if e in ("query_complete", "query_error")]
    assert len(terminals) == 1
    return terminals[0], metas, payloads


def _ok(model_usage=None):
    frame = {"type": "result", "subtype": "success", "is_error": False, "result": "done",
             "session_id": "s-1", "total_cost_usd": 0.01, "num_turns": 1, "duration_ms": 5}
    if model_usage is not None:
        frame["modelUsage"] = model_usage
    return frame


def test_the_engine_hands_the_translator_its_model_flag(tmp_path, monkeypatch):
    (event, data), _, _ = _drive(tmp_path, monkeypatch, [_ok()])
    assert event == "query_complete"
    assert data["models_used"] == [MAIN]
    assert data["model_fallback"] == []


def test_a_fallback_reaches_the_reply_event_and_the_persisted_trace(tmp_path, monkeypatch):
    (event, data), metas, payloads = _drive(
        tmp_path, monkeypatch, [FALLBACK_FRAME, _ok({FALLBACK: {"costUSD": 0.01}})],
        persist=True)
    assert event == "query_complete"
    assert data["models_used"] == [FALLBACK]
    assert data["model_fallback"] == RECORD
    assert metas and metas[0]["models_used"] == [FALLBACK]
    assert metas[0]["model_fallback"] == RECORD
    [trace] = data["cc_traces"]
    assert trace["models_used"] == [FALLBACK] and trace["model_fallback"] == RECORD
    assert payloads[0].cc_traces[0]["model_fallback"] == RECORD


def test_a_model_unavailable_turn_ends_in_the_approved_error(tmp_path, monkeypatch):
    text = "API Error: 503 Service Unavailable. This is a server-side issue."
    error_frame = {"type": "result", "subtype": "success", "is_error": True, "result": text,
                   "api_error_status": 503, "session_id": "s-1", "modelUsage": {}}
    (event, data), _, _ = _drive(tmp_path, monkeypatch, [FALLBACK_FRAME, error_frame])
    assert event == "query_error"
    assert data["error"] == MODEL_UNAVAILABLE_TRIED
    assert data["reason"] == "model_unavailable"
    assert data["detail"] == text
    assert data["model_fallback"] == RECORD

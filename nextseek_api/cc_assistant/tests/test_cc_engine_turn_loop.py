"""Drive run_cc_turn's stream/persist/timeout/finally with a fake Docker client."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import docker as docker_mod
from docker.errors import APIError, NotFound

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths(tmp_path: Path) -> CCPaths:
    return CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path))


def _run_id() -> str:
    return "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


class _FakeSock:
    def __init__(self, lines: list[str | None]):
        self._lines = list(lines)

    def send_stdin(self, _data: bytes) -> None:
        return None

    def close_stdin(self) -> None:
        return None

    def read_event_line(self) -> str | None:
        if not self._lines:
            return None
        return self._lines.pop(0)


class _FakeContainer:
    def __init__(self):
        self.stopped = False
        self.removed = False
        self.stop_raises = False
        self.remove_raises = False

    def attach_socket(self, params=None):
        return object()

    def logs(self, **kwargs):
        return iter(())

    def stop(self, timeout=None):
        self.stopped = True
        if self.stop_raises:
            raise RuntimeError("stop failed")

    def remove(self, force=False):
        self.removed = True
        if self.remove_raises:
            raise RuntimeError("remove failed")


def _install_client(monkeypatch, container):
    class _Containers:
        def run(self, **kwargs):
            return container

    class _Client:
        def __init__(self):
            self.containers = _Containers()

    monkeypatch.setattr(docker_mod, "from_env", lambda: _Client())
    return container


def test_run_cc_turn_streams_result_and_persists(tmp_path, monkeypatch):
    container = _FakeContainer()
    _install_client(monkeypatch, container)
    events: list[tuple[str, dict]] = []
    payloads: list = []

    lines = [
        "not-json",
        "",
        json.dumps({"type": "system", "subtype": "init", "session_id": "sid-1", "model": "opus"}),
        json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello from cc"}]},
        }),
        json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "hello from cc",
            "total_cost_usd": 0.02,
            "session_id": "sid-1",
            "num_turns": 1,
            "duration_ms": 12,
        }),
    ]
    monkeypatch.setattr(
        cc_engine, "BridgeAttachSocket",
        lambda raw, stdout_stream=None: _FakeSock(lines),
    )
    monkeypatch.setattr(cc_engine.os, "chmod", lambda *a, **k: (_ for _ in ()).throw(OSError("chmod")))
    monkeypatch.setattr(
        cc_engine.cc_session, "store_has_transcripts", lambda _p: False,
    )

    mem = tmp_path / "CLAUDE.md"
    mem.write_text("memory")

    def boom_copy(*a, **k):
        raise OSError("copy failed")

    monkeypatch.setattr(cc_engine.shutil, "copyfile", boom_copy)

    def boom_sweep(**kw):
        raise RuntimeError("sweep boom")

    monkeypatch.setattr(
        "nextseek_api.cc_assistant.cc_staging.sweep_user_staging", boom_sweep,
    )

    real_build = None
    from nextseek_api.cc_assistant import cc_provision

    real_build = cc_provision.build_user_dirs

    def wrap_dirs(*a, **k):
        dirs = real_build(*a, **k)
        root = Path(dirs.cc_state_mnt) / "projects"
        root.mkdir(parents=True, exist_ok=True)
        (root / "turn.jsonl").write_bytes(
            b'{"type":"user","message":{"role":"user","content":"q"}}\n'
        )
        return dirs

    monkeypatch.setattr(cc_provision, "build_user_dirs", wrap_dirs)

    class _Scrub:
        skipped = 1
        rewritten = 0

    monkeypatch.setattr(
        cc_engine, "scrub_transcript_store",
        lambda *a, **k: _Scrub(),
    )

    class _Trace:
        def model_dump(self):
            return {"cc": True}

    monkeypatch.setattr(
        "nextseek_api.cc_assistant.cc_trace.extract_trace",
        lambda *a, **k: _Trace(),
    )

    cc_engine.run_cc_turn(
        query="q", model_id="m", api_user="u", api_pass="p",
        send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj",
        run_id=_run_id(),
        paths=_paths(tmp_path),
        cc_state_key="abc-123",
        session_id="resume-me",
        memory_claude_md=str(mem),
        chat_session=object(),
        user_query="q",
        on_turn_complete=lambda payload: payloads.append(payload),
        chat_session_id="abc-123",
    )
    kinds = [e for e, _ in events]
    assert "agent_started" in kinds
    assert "query_complete" in kinds
    assert container.stopped is True


def test_run_cc_turn_timeout_and_generic_error(tmp_path, monkeypatch):
    container = _FakeContainer()
    _install_client(monkeypatch, container)
    events: list[tuple[str, dict]] = []

    class _BlockingSock:
        def send_stdin(self, _data):
            return None

        def close_stdin(self):
            return None

        def read_event_line(self):
            time.sleep(0.02)
            if container.stopped:
                return None
            return ""

    monkeypatch.setattr(
        cc_engine, "BridgeAttachSocket",
        lambda raw, stdout_stream=None: _BlockingSock(),
    )
    cc_engine.run_cc_turn(
        query="q", model_id="m", api_user="u", api_pass="p",
        send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj",
        run_id=_run_id(),
        paths=_paths(tmp_path),
        turn_timeout=0.05,
    )
    assert any(e == "query_error" and d.get("reason") == "exec_timeout" for e, d in events)


def test_run_cc_turn_attach_apierror_and_notfound(tmp_path, monkeypatch):
    container = _FakeContainer()
    container.stop_raises = True
    container.remove_raises = True
    _install_client(monkeypatch, container)
    events: list[tuple[str, dict]] = []

    def boom_attach(params=None):
        raise APIError("attach failed")

    container.attach_socket = boom_attach
    monkeypatch.setattr(
        cc_engine, "scrub_transcript_store",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("scrub")),
    )
    cc_engine.run_cc_turn(
        query="q", model_id="m", send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj",
        run_id=_run_id(), paths=_paths(tmp_path),
    )
    assert any(e == "query_error" for e, _ in events)

    events.clear()
    container.attach_socket = lambda params=None: (_ for _ in ()).throw(NotFound("gone"))
    cc_engine.run_cc_turn(
        query="q", model_id="m", send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj",
        run_id=_run_id(), paths=_paths(tmp_path),
    )
    assert any(e == "query_error" for e, _ in events)


def test_run_cc_turn_finalize_without_result(tmp_path, monkeypatch):
    container = _FakeContainer()
    _install_client(monkeypatch, container)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        cc_engine, "BridgeAttachSocket",
        lambda raw, stdout_stream=None: _FakeSock(["not-json", None]),
    )
    cc_engine.run_cc_turn(
        query="q", model_id="m", send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj",
        run_id=_run_id(), paths=_paths(tmp_path),
    )
    assert any(e == "query_complete" for e, _ in events)


def test_newest_jsonl_and_snapshot_edges(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cc_engine._newest_jsonl_under(empty) is None
    assert cc_engine._snapshot_tree(tmp_path / "missing") == {}
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.txt").write_text("x")
    (root / "link").symlink_to(root / "a.txt")
    snap = cc_engine._snapshot_tree(root)
    assert "a.txt" in snap
    assert "link" not in snap
    old = tmp_path / "old.jsonl"
    old.write_text("{}\n")
    assert cc_engine._newest_jsonl_under(tmp_path, min_mtime=time.time() + 100) is None
    assert cc_engine._newest_jsonl_under(tmp_path, min_mtime=0) == old
    assert cc_engine._safe_relpath("") is False
    assert cc_engine._safe_relpath("/abs") is False
    assert cc_engine._safe_relpath("ok/file.txt") is True

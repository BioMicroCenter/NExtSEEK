"""The turn's vocabulary reaches the container as /data/turn/vocabulary.json, read-only (plan 04, piece 3).

Django writes it into the turn's own folder (``<project>/<user>/_turn/<run id>``), which no agent mount covers, and
mounts that folder read-only; it is not world-writable, and it is removed once the container has stopped. A chat's
first Container-CC turn has no previous-turns mount and still gets it (review focus 1). The Docker client is faked.
"""
from __future__ import annotations

import json
import stat
from pathlib import Path

import docker as docker_mod
import pytest

from NessieAI.cc import cc_engine, safe_fs
from NessieAI.cc.cc_config import CCPaths
from NessieAI.cc.cc_provision import build_user_dirs


@pytest.fixture(autouse=True)
def _fresh_agent_roots(monkeypatch):
    """build_user_dirs registers roots in safe_fs's global registry (plan 01); every test starts with none."""
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})

RUN_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
VOCAB = {"sampletypes": [{"code": "MUS"}], "assays": [], "keywords": ["mice"], "projects": []}
RESULT = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
                     "total_cost_usd": 0.02, "session_id": "sid", "num_turns": 1, "duration_ms": 5})


class _Sock:
    def __init__(self):
        self._lines = [RESULT]

    def send_stdin(self, _d):
        pass

    def close_stdin(self):
        pass

    def read_event_line(self):
        return self._lines.pop(0) if self._lines else None


class _Container:
    def attach_socket(self, params=None):
        return object()

    def logs(self, **kw):
        return iter(())

    def stop(self, timeout=None):
        pass

    def remove(self, force=False):
        pass


def _run(tmp_path, monkeypatch, *, vocabulary, previous_turns=False):
    seen: dict = {}

    class _Containers:
        def run(self, **kwargs):
            seen["mounts"] = kwargs["mounts"]
            for m in kwargs["mounts"] or []:
                if m["Target"] == cc_engine._CONTAINER_TURN:
                    backing = tmp_path / m["VolumeOptions"]["Subpath"]
                    seen["file"] = json.loads((backing / cc_engine.VOCABULARY_FILE).read_text())
                    seen["dir_mode"] = stat.S_IMODE(backing.stat().st_mode)
            return _Container()

    monkeypatch.setattr(docker_mod, "from_env", lambda: type("C", (), {"containers": _Containers()})())
    monkeypatch.setattr(cc_engine, "BridgeAttachSocket", lambda raw, stdout_stream=None: _Sock())
    events: list = []
    cc_engine.run_cc_turn(
        query="how many mice", model_id="opus", send_event=lambda e, d: events.append((e, d)), user_id="alice",
        project_dirname="1-testproj", run_id=RUN_ID,
        paths=CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path)),
        previous_turns=previous_turns, vocabulary=vocabulary)
    return seen, events


def test_the_turn_folder_is_djangos_own_and_per_turn():
    dirs = build_user_dirs(CCPaths(users_volume="v", user_root_mount="/r"), "1-p", "alice", run_id="R1")
    assert dirs.turn_subpath == "1-p/alice/_turn/R1"
    assert dirs.turn_mnt == "/r/1-p/alice/_turn/R1"
    assert build_user_dirs(CCPaths(users_volume="v", user_root_mount="/r"), "1-p", "alice").turn_subpath is None


def test_a_first_cc_turn_mounts_the_vocabulary_read_only_without_previous_turns(tmp_path, monkeypatch):
    """Review focus 1 (server half)."""
    seen, events = _run(tmp_path, monkeypatch, vocabulary=VOCAB)

    by_target = {m["Target"]: m for m in seen["mounts"]}
    assert "/data/previous_turns" not in by_target
    turn = by_target["/data/turn"]
    assert turn["ReadOnly"] is True
    assert turn["VolumeOptions"]["Subpath"] == f"1-testproj/alice/_turn/{RUN_ID}"
    assert seen["file"] == VOCAB
    assert seen["dir_mode"] & 0o022 == 0, "the turn folder is never group or world writable"
    assert not (tmp_path / f"1-testproj/alice/_turn/{RUN_ID}").exists(), "removed after the container stopped"
    assert events[-1][0] == "query_complete"


def test_no_vocabulary_no_turn_mount(tmp_path, monkeypatch):
    seen, _ = _run(tmp_path, monkeypatch, vocabulary=None)
    assert all(m["Target"] != "/data/turn" for m in seen["mounts"])

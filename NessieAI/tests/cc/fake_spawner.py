"""Stand-in for cc-spawner in the turn tests: run_cc_turn talks to the spawner client, and this answers it.

A test keeps faking the Docker client as before (``docker.from_env`` patched to a fake whose containers.run, attach
and stop it checks). This module turns the engine's ``POST /turns`` body into the run kwargs with the REAL cc-spawner
code (``Spawner._validate_turn`` and ``_run_kwargs``), hands them to that fake client and returns its stream. So a
test still sees the container config a turn really ends up with (mounts, env, limits, command), and the app side
never touches Docker.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import docker  # tests only: the app side no longer imports the SDK

from NessieAI.cc import cc_engine, spawner_client
from NessieAI.cc.attach import BridgeAttachSocket  # noqa: F401 - tests patch this name on this module

_SPAWNER = Path(__file__).resolve().parents[3] / "docker" / "cc-spawner" / "spawner.py"
_mod = None


def load_spawner():
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location("cc_spawner_for_tests", _SPAWNER)
        _mod = importlib.util.module_from_spec(spec)
        sys.modules["cc_spawner_for_tests"] = _mod
        spec.loader.exec_module(_mod)
    return _mod


def _service(client):
    sp = load_spawner()
    cfg = sp.Config(token="t", ns_url="http://127.0.0.1:8000", neo4j_uri="bolt://neo4j:7687", automode_gcp=True,
                    image=cc_engine.DEFAULT_IMAGE, network=cc_engine.DEFAULT_NETWORK,
                    max_turns=cc_engine._DEFAULT_MAX_TURNS, mem_limit=cc_engine._DEFAULT_MEM_LIMIT,
                    nano_cpus=cc_engine._DEFAULT_NANO_CPUS, pids_limit=cc_engine._DEFAULT_PIDS_LIMIT,
                    fsize_bytes=cc_engine._DEFAULT_FSIZE_BYTES)
    return sp.Spawner(client, cfg)


def install(monkeypatch) -> dict:
    """Point the spawner client at the fake Docker client. Returns the live containers by run id."""
    live: dict = {}
    monkeypatch.setenv("NEXTSEEK_SPAWNER_URL", "http://cc-spawner:8080")

    def start_turn(body, *, read_timeout):
        client = docker.from_env()
        svc = _service(client)
        kwargs = svc._run_kwargs(svc._validate_turn(body))
        container = svc._run_with_stale_name_retry(kwargs)
        live[body["run_id"]] = container
        raw = container.attach_socket(params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1})
        stream = container.logs(stream=True, follow=True, stdout=True, stderr=False)
        sock = BridgeAttachSocket(raw, stdout_stream=stream)
        sock.send_stdin(body["stdin_envelope"].encode("utf-8"))
        sock.close_stdin()
        return sock

    def stop_turn(run_id):
        container = live.get(run_id)
        return True if container is None else load_spawner().Spawner._stop_and_confirm_exit(container)

    monkeypatch.setattr(spawner_client, "start_turn", start_turn)
    monkeypatch.setattr(spawner_client, "stop_turn", stop_turn)
    return live

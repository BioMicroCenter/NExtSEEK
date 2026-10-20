"""run_cc_turn and cc_runner_available behind NEXTSEEK_SPAWNER_URL: the spawner client is faked, Docker is never touched."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import docker as docker_mod
import pytest

from NessieAI.cc import cc_engine, cc_provision, spawner_client
from NessieAI.cc.cc_config import CCPaths

RUN = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_SPAWNER = Path(__file__).resolve().parents[3] / "docker" / "cc-spawner" / "spawner.py"


def _load_spawner():
    spec = importlib.util.spec_from_file_location("cc_spawner_parity", _SPAWNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cc_spawner_parity"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Stream:
    def __init__(self, lines, stopped=None):
        self.lines, self.stopped = list(lines), stopped

    def read_event_line(self):
        if self.stopped is not None:
            while not self.stopped.is_set():
                time.sleep(0.01)
            return None
        return self.lines.pop(0) if self.lines else None

    def close(self):
        pass


@pytest.fixture
def spawner_on(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_SPAWNER_URL", "http://cc-spawner:8080")
    monkeypatch.setattr(docker_mod, "from_env", lambda: (_ for _ in ()).throw(AssertionError("direct Docker used")))


def _run(tmp_path, events, **over):
    cc_engine.run_cc_turn(
        query="q", model_id="m-1", api_user="alice", api_pass="pw", turn_pass="tp-1",
        send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj", run_id=RUN,
        paths=CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path)),
        cc_state_key="abc-123", session_id="resume-me", **over)


def test_runner_available_follows_the_spawner_when_the_switch_is_on(spawner_on, monkeypatch):
    monkeypatch.setattr(spawner_client, "health", lambda: (False, "network missing"))
    assert cc_engine.cc_runner_available() == (False, "network missing")
    monkeypatch.setattr(spawner_client, "health", lambda: (True, "ok"))
    assert cc_engine.cc_runner_available() == (True, "ok")


def test_runner_available_uses_docker_when_the_switch_is_off(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_SPAWNER_URL", "")
    monkeypatch.setattr(spawner_client, "health", lambda: (_ for _ in ()).throw(AssertionError("spawner used")))

    class _Ok:
        def get(self, _n):
            return object()

    class _Client:
        images = networks = _Ok()

        def ping(self):
            return True

    monkeypatch.setattr(docker_mod, "from_env", lambda: _Client())
    assert cc_engine.cc_runner_available() == (True, "ok")


def test_a_turn_goes_through_the_spawner_and_is_stopped(spawner_on, tmp_path, monkeypatch):
    seen = {"stops": []}
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "sid-1", "model": "m"}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "hi",
                    "total_cost_usd": 0.01, "session_id": "sid-1", "num_turns": 1, "duration_ms": 5}),
    ]

    def fake_start(body, *, read_timeout):
        seen["body"], seen["read_timeout"] = body, read_timeout
        return _Stream(lines)

    monkeypatch.setattr(spawner_client, "start_turn", fake_start)
    monkeypatch.setattr(spawner_client, "stop_turn", lambda run_id: seen["stops"].append(run_id) or True)
    events: list = []
    _run(tmp_path, events)

    kinds = [e for e, _ in events]
    assert "agent_started" in kinds and "query_complete" in kinds
    assert seen["stops"] == [RUN]  # one confirmed stop; the clean-up calls after it send nothing
    body = seen["body"]
    assert body["run_id"] == RUN and body["user_id"] == "alice" and body["project_dirname"] == "proj"
    assert body["cc_state_key"] == "abc-123" and body["resume_session_id"] is None  # empty store: starts fresh
    assert body["model_id"] == "m-1" and body["environment"]["NEXTSEEK_TURN_PASS"] == "tp-1"
    assert json.loads(body["stdin_envelope"]) == {"type": "user", "message": {"role": "user", "content": "q"}}
    assert body["stdin_envelope"].endswith("\n")
    for forbidden in ("image", "network", "mounts", "volumes", "command", "labels", "cap_drop"):
        assert forbidden not in body
    assert "automode_environment" not in body
    assert seen["read_timeout"] > cc_engine._DEFAULT_TURN_TIMEOUT


def test_the_body_environment_stays_inside_the_spawners_allowlist(spawner_on, tmp_path, monkeypatch):
    allowed = _load_spawner().ALLOWED_ENV_KEYS
    seen = {}
    monkeypatch.setattr(spawner_client, "start_turn", lambda body, *, read_timeout: seen.update(body=body) or _Stream([]))
    monkeypatch.setattr(spawner_client, "stop_turn", lambda run_id: True)
    monkeypatch.setenv("NEXTSEEK_BASE_URL", "http://127.0.0.1:8000")
    _run(tmp_path, [], chat_session_id="abc-123")
    assert set(seen["body"]["environment"]) <= allowed


def test_the_watchdog_stops_a_silent_turn_through_the_spawner(spawner_on, tmp_path, monkeypatch):
    import threading

    stopped = threading.Event()
    calls = []

    def stop(run_id):
        calls.append(run_id)
        stopped.set()
        return True

    monkeypatch.setattr(spawner_client, "start_turn", lambda body, *, read_timeout: _Stream([], stopped))
    monkeypatch.setattr(spawner_client, "stop_turn", stop)
    events: list = []
    _run(tmp_path, events, turn_timeout=0.05)
    assert calls and any(e in ("query_complete", "query_error") for e, _ in events)


def test_a_refused_start_is_a_query_error_and_the_stop_is_still_tried(spawner_on, tmp_path, monkeypatch):
    stops = []

    def refuse(body, *, read_timeout):
        raise spawner_client.SpawnerRefused(422, "invalid user_id")

    monkeypatch.setattr(spawner_client, "start_turn", refuse)
    monkeypatch.setattr(spawner_client, "stop_turn", lambda run_id: stops.append(run_id) or True)
    events: list = []
    _run(tmp_path, events)
    assert [e for e, _ in events if e == "query_error"]
    assert stops == [RUN]


# ---- the spawner builds what cc_engine builds on the direct path ----------------------------------------------

def test_spawner_mounts_match_the_direct_path(tmp_path):
    sp = _load_spawner()
    svc = sp.Spawner(object(), sp.Config(token="t"))
    paths = CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path))
    transcripts = "proj/alice/_memory/abc-123/transcripts"
    direct = cc_engine._build_volumes(
        paths=paths, project_dirname="proj", user_id="alice", cc_state_key="abc-123", run_id=RUN,
        transcripts_subpath=transcripts, previous_turns=True, turn_files=True)
    v = svc._validate_turn({
        "run_id": RUN, "user_id": "alice", "project_dirname": "proj", "cc_state_key": "abc-123",
        "transcripts_subpath": transcripts, "previous_turns": True, "turn_files": True, "model_id": "m",
        "environment": {}, "stdin_envelope": "x\n"})
    assert [dict(m) for m in svc._mounts(v)] == [dict(m) for m in direct]


def test_spawner_subpaths_match_build_user_dirs(tmp_path):
    sp = _load_spawner()
    dirs = cc_provision.build_user_dirs(
        CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path)), "proj", "alice",
        session_id="abc-123", run_id=RUN)
    subs = sp._subpaths("proj", "alice", "abc-123", RUN)
    assert subs == {
        "input": dirs.input_subpath, "shared": dirs.shared_subpath, "scratch": dirs.run_scratch_subpath,
        "cc_state": dirs.cc_state_subpath, "transcripts": dirs.transcripts_subpath,
        "previous_turns": dirs.previous_turns_subpath, "turn": dirs.turn_subpath,
    }


def test_spawner_run_kwargs_match_the_direct_path(monkeypatch):
    sp = _load_spawner()
    cfg = sp.Config(token="t", ns_url="http://127.0.0.1:8000", neo4j_uri="bolt://neo4j:7687", automode_gcp=True,
                    image=cc_engine.DEFAULT_IMAGE, network=cc_engine.DEFAULT_NETWORK,
                    max_turns=cc_engine._DEFAULT_MAX_TURNS, mem_limit=cc_engine._DEFAULT_MEM_LIMIT,
                    nano_cpus=cc_engine._DEFAULT_NANO_CPUS, pids_limit=cc_engine._DEFAULT_PIDS_LIMIT,
                    fsize_bytes=cc_engine._DEFAULT_FSIZE_BYTES)
    svc = sp.Spawner(object(), cfg)
    body = {"run_id": RUN, "user_id": "alice", "project_dirname": "proj", "cc_state_key": "abc-123",
            "model_id": "m-1", "fallback_model_id": "m-2", "max_budget_usd": 0.5, "resume_session_id": "r-1",
            "environment": {"API_USER": "alice"}, "stdin_envelope": "x\n",
            }
    theirs = svc._run_kwargs(svc._validate_turn(body))
    command = cc_engine._build_command(
        model_id="m-1", session_id="r-1", max_budget_usd=0.5, fallback_model_id="m-2",
        source={"NEXTSEEK_INTERNAL_BASE_URL": "http://127.0.0.1:8000", "NEO4J_URI": "bolt://neo4j:7687",
                "GCP_API_KEY": "x"})
    ours = cc_engine._run_kwargs(image=cfg.image, command=command, environment={"API_USER": "alice"},
                                 mounts=theirs["mounts"], run_id=RUN, user_id="alice")
    assert {k: v for k, v in theirs.items() if k != "ulimits"} == {k: v for k, v in ours.items() if k != "ulimits"}
    assert [dict(u) for u in theirs["ulimits"]] == [dict(u) for u in ours["ulimits"]]


def test_id_rules_are_one_rule_on_both_sides():
    sp = _load_spawner()
    assert sp._SESSION_RE.pattern == cc_engine._SESSION_RE.pattern
    assert sp._MODEL_RE.pattern == cc_engine._MODEL_RE.pattern
    assert sp._RUN_ID_RE.pattern == cc_engine._CONTAINER_NAME_SAFE_RE.pattern
    for rx in (sp._SESSION_RE, sp._MODEL_RE, sp._RUN_ID_RE):
        assert not rx.fullmatch("-abc") and not rx.fullmatch("--x")


@pytest.mark.parametrize("over", [{"session_id": "--dangerously-skip-permissions"}, {"session_id": "-r"},
                                  {"model_id": "--help"}])
def test_the_direct_path_refuses_a_dash_led_session_or_model_id(tmp_path, monkeypatch, over):
    monkeypatch.setenv("NEXTSEEK_SPAWNER_URL", "")
    monkeypatch.setattr(docker_mod, "from_env", lambda: (_ for _ in ()).throw(AssertionError("Docker reached")))
    kwargs = dict(query="q", model_id="m-1", api_user="alice", api_pass="pw", turn_pass="tp-1",
                  send_event=lambda e, d: None, user_id="alice", project_dirname="proj", run_id=RUN,
                  paths=CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path)),
                  cc_state_key="abc-123", session_id="resume-me")
    kwargs.update(over)
    with pytest.raises(ValueError, match="invalid (session_id|model_id)"):
        cc_engine.run_cc_turn(**kwargs)


def test_spawner_allowlist_covers_every_key_the_builder_can_emit(monkeypatch):
    sp = _load_spawner()
    src = {
        "AWS_REGION": "us-east-1", "NEXTSEEK_INTERNAL_BASE_URL": "http://127.0.0.1:8000",
        "NEXTSEEK_SIDECAR_HOST": "h", "NEXTSEEK_SIDECAR_PORT": "1", "DMAC_BEDROCK_PROXY_URL": "http://p",
        cc_engine._CC_SONNET_MODEL_ENV: "global.anthropic.claude-sonnet-5-5",
    }
    env = cc_engine.build_agent_environment(
        source=src, api_user="u", turn_pass="t", path_mappings={}, chat_session_id="s", turn_deadline=1.0,
        ops_road="sidecar")
    assert set(env) <= sp.ALLOWED_ENV_KEYS


@pytest.mark.parametrize("ns,neo,gcp", [("", "", False), ("http://127.0.0.1:8000", "", False),
                                         ("http://nginx:80", "bolt://n:7687", True)])
def test_automode_text_is_identical_in_the_spawner_and_the_engine(ns, neo, gcp):
    sp = _load_spawner()
    src = {"NEXTSEEK_INTERNAL_BASE_URL": ns, "NEO4J_URI": neo, "GCP_API_KEY": "k" if gcp else ""}
    assert sp.Config(token="t", ns_url=ns, neo4j_uri=neo, automode_gcp=gcp).automode_environment() == \
        cc_engine._automode_environment(src)


def test_spawner_config_reads_the_automode_env_by_name():
    sp = _load_spawner()
    cfg = sp.Config.from_env({"NEXTSEEK_INTERNAL_BASE_URL": "http://a", "NEO4J_URI": "bolt://b",
                              "NEXTSEEK_CC_AUTOMODE_GCP": "1", "GCP_API_KEY": "must-not-be-read"})
    assert (cfg.ns_url, cfg.neo4j_uri, cfg.automode_gcp) == ("http://a", "bolt://b", True)
    assert "must-not-be-read" not in repr(cfg)

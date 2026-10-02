"""Spec piece 1 in the engine: the agent's env carries the one-turn pass and never a password; the turn driver is
told the deadline before the spawn; the pass is revoked after the container stops and before the scrub, on every
way out of the turn. Fake Docker client, no Django."""
from __future__ import annotations

import inspect
import re
import time

import docker as docker_mod
from docker.errors import APIError

from NessieAI import paths
from NessieAI.cc import cc_engine
from NessieAI.tests.cc.test_cc_engine_turn_loop import _FakeContainer, _FakeSock, _install_client, _paths, _run_id

PASS = "T" * 43
PASSWORD_KEYS = ("NEXTSEEK_PASSWORD", "API_PASS", "SEEK_PASSWORD")


def test_the_agent_env_has_the_pass_and_no_password_even_from_a_hostile_source():
    hostile = {key: "leaked-password" for key in PASSWORD_KEYS}
    env = cc_engine.build_agent_environment(source=hostile, api_user="demo", turn_pass=PASS, path_mappings={})
    assert env["NEXTSEEK_TURN_PASS"] == PASS
    assert env["NEXTSEEK_USERNAME"] == env["API_USER"] == "demo"
    assert not set(PASSWORD_KEYS) & set(env)
    assert all("leaked-password" not in value for value in env.values())


def test_no_pass_leaves_the_key_out():
    env = cc_engine.build_agent_environment(source={}, api_user="demo", turn_pass=None, path_mappings={})
    assert "NEXTSEEK_TURN_PASS" not in env


def _spy_spawn(monkeypatch):
    seen = {}

    class _Containers:
        def run(self, **kwargs):
            seen["environment"] = kwargs["environment"]
            raise APIError("spawn intercepted by the test")

    class _Client:
        containers = _Containers()

    monkeypatch.setattr(docker_mod, "from_env", lambda: _Client())
    return seen


def test_the_container_gets_the_pass_the_driver_gets_the_deadline_and_a_failed_spawn_still_revokes(
        tmp_path, monkeypatch):
    seen = _spy_spawn(monkeypatch)
    order, deadlines = [], []
    before = time.time()
    cc_engine.run_cc_turn(
        query="q", model_id="m", api_user="demo", api_pass="the-password", turn_pass=PASS,
        send_event=lambda event, data: None, user_id="alice", project_dirname="proj", run_id=_run_id(),
        paths=_paths(tmp_path), turn_timeout=120,
        on_deadline=lambda epoch: (order.append("deadline"), deadlines.append(epoch)),
        on_turn_end=lambda: order.append("revoke"),
    )
    env = seen["environment"]
    assert env["NEXTSEEK_TURN_PASS"] == PASS
    assert not set(PASSWORD_KEYS) & set(env)
    assert "the-password" not in "".join(env.values())
    (deadline,) = deadlines
    assert before + 120 <= deadline <= time.time() + 120
    assert env[cc_engine._TURN_DEADLINE_ENV] == str(int(deadline))
    assert order == ["deadline", "revoke"]


def _recording_scrub(monkeypatch, order, scrubbed):
    def scrub(cc_state_dir, environment):
        order.append("scrub")
        scrubbed.update(environment)
        return cc_engine.ScrubReport(0, 0)

    monkeypatch.setattr(cc_engine, "scrub_transcript_store", scrub)
    monkeypatch.setattr(cc_engine, "scrub_sibling_transcript_stores",
                        lambda *args, **kwargs: cc_engine.ScrubReport(0, 0))


def _run(tmp_path, **kwargs):
    cc_engine.run_cc_turn(
        query="q", model_id="m", api_user="demo", api_pass="the-password", turn_pass=PASS,
        send_event=kwargs.pop("send_event", lambda event, data: None), user_id="alice", project_dirname="proj",
        run_id=_run_id(), paths=_paths(tmp_path), cc_state_key="abc-123", chat_session_id="abc-123", **kwargs,
    )


class _ExitingContainer:
    """Records the moment the engine makes sure the agent is gone: plan 01's _stop_and_confirm_exit stops the
    container first (a successful stop means it is gone, no wait); only after a failed stop plus a force-remove does a bounded wait run; the finally stops it again."""

    def __init__(self, order):
        self.order = order
        self.stopped = False

    def attach_socket(self, params=None):
        return object()

    def logs(self, **kwargs):
        return iter(())

    def wait(self, timeout=None):
        self.order.append("exit")
        return {"StatusCode": 0}

    def stop(self, timeout=None):
        self.order.append("exit")
        self.stopped = True

    def remove(self, force=False):
        return None


def test_the_pass_is_revoked_once_right_after_the_container_stops(tmp_path, monkeypatch):
    """Spec piece 1: revoked right after the container stops, before the sweep, the publish and the scrub (which
    take their secrets from the login already in memory), and only once although the finally asks again."""
    order, scrubbed = [], {}
    _install_client(monkeypatch, _ExitingContainer(order))
    monkeypatch.setattr(cc_engine, "BridgeAttachSocket", lambda raw, stdout_stream=None: _FakeSock(["not-json", None]))
    real_publish = cc_engine._publish_artifacts

    def publish(*args, **kwargs):
        order.append("publish")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(cc_engine, "_publish_artifacts", publish)
    _recording_scrub(monkeypatch, order, scrubbed)
    _run(tmp_path, on_turn_end=lambda: order.append("revoke"))
    assert order.count("revoke") == 1, order
    assert order.index("exit") < order.index("revoke") < order.index("publish") < order.index("scrub"), order
    assert scrubbed["NEXTSEEK_PASSWORD"] == "the-password"
    assert scrubbed["NEXTSEEK_TURN_PASS"] == PASS


def test_a_failed_revocation_does_not_skip_the_scrub(tmp_path, monkeypatch):
    order, scrubbed = [], {}
    _install_client(monkeypatch, _FakeContainer())
    monkeypatch.setattr(cc_engine, "BridgeAttachSocket", lambda raw, stdout_stream=None: _FakeSock(["not-json", None]))
    _recording_scrub(monkeypatch, order, scrubbed)

    def revoke():
        raise RuntimeError("the database went away")

    _run(tmp_path, on_turn_end=revoke)
    assert order == ["scrub"]


def test_a_timed_out_turn_revokes_its_pass(tmp_path, monkeypatch):
    container = _FakeContainer()
    _install_client(monkeypatch, container)

    class _Stuck:
        def send_stdin(self, _data):
            return None

        def close_stdin(self):
            return None

        def read_event_line(self):
            time.sleep(0.02)
            return None if container.stopped else ""

    monkeypatch.setattr(cc_engine, "BridgeAttachSocket", lambda raw, stdout_stream=None: _Stuck())
    revoked, events = [], []
    _run(tmp_path, turn_timeout=0.05, on_turn_end=lambda: revoked.append(True),
         send_event=lambda event, data: events.append((event, data)))
    assert any(event == "query_error" and data.get("reason") == "exec_timeout" for event, data in events)
    assert revoked == [True]


def test_the_deploy_probe_calls_the_env_builder_with_its_current_arguments():
    text = (paths.REPO_ROOT / "startup" / "steps" / "deploy_checks.py").read_text()
    arguments = re.search(r"build_agent_environment\(([^)]*)\)", text).group(1)
    keywords = {part.split("=")[0].strip() for part in arguments.split(",") if "=" in part}
    assert keywords <= set(inspect.signature(cc_engine.build_agent_environment).parameters)
    assert "api_pass" not in keywords

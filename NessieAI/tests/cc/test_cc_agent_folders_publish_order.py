"""A turn's files are published only after its container has exited (step 1, requirement 3).

Publishing reads the agent's scratch; while the agent still runs it can change a file between Django's check and
its copy. The stop now comes before the staging sweep and the publish, a turn that ended normally is first given
a few seconds to exit by itself, and a turn whose container cannot be confirmed gone publishes nothing.
"""
from __future__ import annotations

import docker as docker_mod
import pytest

from NessieAI.cc import cc_engine, safe_fs
from NessieAI.cc.cc_config import CCPaths
from NessieAI.tests.cc.agent_folder_canary import FakeAgent, FakeContainer

RUN_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})


def _turn(tmp_path, monkeypatch, container, calls):
    scratch = tmp_path / "proj" / "alice" / "scratch" / RUN_ID

    def work():
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "report.csv").write_bytes(b"a,b\n")

    class _Client:
        class containers:
            @staticmethod
            def run(**_kwargs):
                return container

    monkeypatch.setattr(docker_mod, "from_env", lambda: _Client())
    monkeypatch.setattr(cc_engine, "BridgeAttachSocket", lambda raw, stdout_stream=None: FakeAgent(work))
    real_publish = cc_engine._publish_artifacts

    def spy_publish(*args, **kwargs):
        calls.append("publish")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(cc_engine, "_publish_artifacts", spy_publish)
    monkeypatch.setattr("NessieAI.cc.cc_staging.sweep_user_staging", lambda **_kw: calls.append("sweep"))
    events: list[tuple[str, dict]] = []
    cc_engine.run_cc_turn(
        query="q", model_id="m", api_user="alice-login", api_pass="pw",
        send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj", run_id=RUN_ID,
        paths=CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path)),
        turn_timeout=30,
    )
    return events


def _terminals(events):
    return [(e, d) for e, d in events if e in ("query_complete", "query_error")]


def test_the_sweep_and_the_publish_wait_for_the_agent_to_exit(tmp_path, monkeypatch):
    calls: list[str] = []
    events = _turn(tmp_path, monkeypatch, FakeContainer(calls), calls)
    assert "publish" in calls and "sweep" in calls, calls
    exited = min(calls.index(name) for name in ("wait", "stop") if name in calls)
    assert exited < calls.index("sweep") < calls.index("publish"), calls
    [(event, data)] = _terminals(events)
    assert event == "query_complete" and data["artifacts"], data


def test_a_turn_that_ended_is_given_time_to_exit_before_it_is_stopped(tmp_path, monkeypatch):
    calls: list[str] = []
    _turn(tmp_path, monkeypatch, FakeContainer(calls), calls)
    before_publish = calls[: calls.index("publish")]
    assert before_publish[0] == "wait" and "stop" not in before_publish, calls


def test_nothing_is_published_when_the_agent_cannot_be_confirmed_gone(tmp_path, monkeypatch):
    calls: list[str] = []
    container = FakeContainer(calls, wait_ok=False, stop_ok=False, remove_ok=False)
    events = _turn(tmp_path, monkeypatch, container, calls)
    assert "publish" not in calls and "sweep" not in calls, calls
    assert not (tmp_path / "proj" / "alice" / "output" / "artifacts").exists()
    [(event, data)] = _terminals(events)
    assert event == "query_complete" and data["artifacts"] is None

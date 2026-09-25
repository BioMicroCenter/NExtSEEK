"""The operator-approved message a Container-CC turn gets when it hits its time limit.

The old text ("Container-CC turn exceeded the 180s limit and was stopped. ...") named
the engine and the limit in seconds. The approved one speaks to the researcher, renders
the limit from the turn's own ``turn_timeout`` (whole minutes as "N-minute", anything
else as "N-second"), and keeps ``reason: "exec_timeout"`` and every other field.
"""
from __future__ import annotations

import pytest

from NessieAI.cc import cc_engine


@pytest.mark.parametrize("seconds, phrase", [
    (180, "3-minute"), (300, "5-minute"), (60, "1-minute"), (60.0, "1-minute"),
    (45, "45-second"), (90, "90-second"), (0.3, "0.3-second"), (30, "30-second"),
])
def test_the_limit_is_rendered_in_whole_minutes_else_seconds(seconds, phrase):
    assert cc_engine._time_limit_phrase(seconds) == phrase


def test_a_three_minute_limit_reads_as_the_approved_sentence():
    assert cc_engine._time_limit_message(180) == (
        "This took longer than the 3-minute limit, so I stopped. "
        "Say continue and I will carry on from where I got to.")


def test_a_turn_stopped_at_its_limit_keeps_its_fields_and_says_what_fell_back(tmp_path, monkeypatch):
    """A fallback model that then hangs ends the turn at its limit: the record still says
    a second model was used."""
    import json
    import time

    import docker as docker_mod

    from NessieAI.cc.cc_config import CCPaths

    frames = [json.dumps({"type": "system", "subtype": "model_fallback",
                          "trigger": "server_error", "original_model": "us.anthropic.a",
                          "fallback_model": "us.anthropic.b", "session_id": "s-1"})]

    class _Container:
        stopped = False

        def attach_socket(self, params=None):
            return object()

        def logs(self, **kwargs):
            return iter(())

        def stop(self, timeout=None):
            _Container.stopped = True

        def remove(self, force=False):
            _Container.stopped = True

    class _Sock:
        def send_stdin(self, _data):
            return None

        def close_stdin(self):
            return None

        def read_event_line(self):
            if frames:
                return frames.pop(0)
            time.sleep(0.02)
            return None if _Container.stopped else ""

    class _Client:
        class containers:
            @staticmethod
            def run(**_kw):
                return _Container()

    monkeypatch.setattr(docker_mod, "from_env", lambda: _Client())
    monkeypatch.setattr(cc_engine, "BridgeAttachSocket", lambda raw, stdout_stream=None: _Sock())
    events = []
    cc_engine.run_cc_turn(
        query="q", model_id="us.anthropic.a", api_user=None, api_pass=None,
        send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj", run_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        paths=CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path)),
        turn_timeout=0.3,
    )
    [(event, data)] = [(e, d) for e, d in events if e in ("query_complete", "query_error")]
    assert event == "query_error"
    assert data["error"] == cc_engine._time_limit_message(0.3)
    assert data["reason"] == "exec_timeout"
    assert data["agent"] == "container_cc"
    assert data["cc_session_id"] == "s-1"
    assert {"partial_reply", "artifacts", "cc_raw_files"} <= set(data)
    assert data["model_fallback"] == [{"agent": "container_cc", "from": "us.anthropic.a",
                                       "to": "us.anthropic.b", "reason": "server_error"}]

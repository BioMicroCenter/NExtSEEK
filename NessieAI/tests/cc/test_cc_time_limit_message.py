"""The operator-approved message a Container-CC turn gets when it hits its time limit.

The old text ("Container-CC turn exceeded the 180s limit and was stopped. ...") named
the engine and the limit in seconds. The approved one speaks to the researcher, renders
the limit from the turn's own ``turn_timeout`` (whole minutes as "N-minute", anything
else as "N-second"), and keeps ``reason: "exec_timeout"`` and every other field.

One exception (review M2): a turn stopped while Claude Code was retrying a model call.
With the approved retry bound (3) and request timeout (60 s) a hung upstream needs about
244 s to give up, so the 180 s watchdog always fires first, and "say continue" would
only stall again. When the last frame before the stop was a ``system/api_retry``, the
user gets the approved unavailability text instead, with ``reason: "model_unavailable"``
and the time-limit fact in ``detail``. The ``api_retry`` frames below are the shapes
Claude Code 2.1.282 printed against a fake Bedrock endpoint on 2026-09-25.
"""
from __future__ import annotations

import json
import time

import docker as docker_mod
import pytest

from NessieAI.cc import cc_engine
from NessieAI.cc.cc_config import CCPaths
from NessieAI.cc.translate import MODEL_UNAVAILABLE, MODEL_UNAVAILABLE_TRIED

INIT = {"type": "system", "subtype": "init", "session_id": "s-1", "model": "us.anthropic.a"}
FALLBACK = {"type": "system", "subtype": "model_fallback", "trigger": "server_error",
            "original_model": "us.anthropic.a", "fallback_model": "us.anthropic.b",
            "session_id": "s-1"}
FALLBACK_RECORD = [{"agent": "container_cc", "from": "us.anthropic.a", "to": "us.anthropic.b",
                    "reason": "server_error"}]
# A hung upstream: no status, error "unknown" (runs j_hang_* / z_hang_*).
RETRY_HANG = {"type": "system", "subtype": "api_retry", "attempt": 3, "max_retries": 3,
              "retry_delay_ms": 2219, "error_status": None, "error": "unknown",
              "session_id": "s-1", "uuid": "u-1"}
# A 503 (runs a503_* / g503_*).
RETRY_503 = {"type": "system", "subtype": "api_retry", "attempt": 2, "max_retries": 3,
             "retry_delay_ms": 1068, "error_status": 503, "error": "server_error",
             "session_id": "s-1", "uuid": "u-2"}
SAID = {"type": "assistant", "message": {"content": [{"type": "text", "text": "Found 58 samples."}]},
        "session_id": "s-1"}


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


def _stopped_turn(tmp_path, monkeypatch, frames, *, write=None, clock=None):
    """Feed ``frames``, then idle until the watchdog stops the turn; return its terminal.

    ``clock``, when given, stands in for the engine's monotonic clock: the retry frame's
    arrival and the watchdog's stop are the only two readings it takes.
    """
    if clock is not None:
        monkeypatch.setattr(cc_engine, "_monotonic", clock)
    lines = [json.dumps(frame) for frame in frames]
    scratch = tmp_path / "proj" / "alice" / "scratch" / "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

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
            if write:
                for name, body in write.items():
                    (scratch / name).write_bytes(body)
                write.clear()
            if lines:
                return lines.pop(0)
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
    return data


def test_a_turn_stopped_at_its_limit_keeps_its_fields_and_says_what_fell_back(tmp_path, monkeypatch):
    """A fallback model that then works slowly ends the turn at its limit: the record still
    says a second model was used, and the message is the time-limit one."""
    data = _stopped_turn(tmp_path, monkeypatch, [INIT, FALLBACK])
    assert data["error"] == cc_engine._time_limit_message(0.3)
    assert data["reason"] == "exec_timeout"
    assert data["agent"] == "container_cc"
    assert data["cc_session_id"] == "s-1"
    assert {"partial_reply", "artifacts", "cc_raw_files"} <= set(data)
    assert data["model_fallback"] == FALLBACK_RECORD


def test_a_turn_stopped_while_retrying_a_hung_model_says_the_model_was_unavailable(
        tmp_path, monkeypatch):
    data = _stopped_turn(tmp_path, monkeypatch, [INIT, SAID, RETRY_HANG],
                         write={"part.csv": b"a,b\n1,2\n"})
    assert data["error"] == MODEL_UNAVAILABLE
    assert data["reason"] == "model_unavailable"
    assert data["detail"] == ("stopped at the 0.3 s limit while retrying the model: "
                              "unknown, attempt 3 of 3")
    # Everything the time-limit error carries is kept.
    assert data["agent"] == "container_cc"
    assert data["cc_session_id"] == "s-1"
    assert data["partial_reply"] == "Found 58 samples."
    assert [a["label"] for a in data["artifacts"]] == ["part.csv"]
    assert "cc_raw_files" in data
    assert data["model_fallback"] == []


def test_a_turn_stopped_while_retrying_after_a_fallback_says_a_second_model_was_tried(
        tmp_path, monkeypatch):
    data = _stopped_turn(tmp_path, monkeypatch, [INIT, FALLBACK, RETRY_503])
    assert data["error"] == MODEL_UNAVAILABLE_TRIED
    assert data["reason"] == "model_unavailable"
    assert data["detail"] == ("stopped at the 0.3 s limit while retrying the model: "
                              "server_error (503), attempt 2 of 3")
    assert data["model_fallback"] == FALLBACK_RECORD


def test_a_turn_whose_retry_succeeded_before_the_stop_keeps_the_time_limit_text(
        tmp_path, monkeypatch):
    """The model answered after the retry, so the turn was busy, not waiting on a model."""
    data = _stopped_turn(tmp_path, monkeypatch, [INIT, RETRY_503, SAID])
    assert data["error"] == cc_engine._time_limit_message(0.3)
    assert data["reason"] == "exec_timeout"
    assert "detail" not in data


def test_a_turn_with_no_retry_keeps_the_time_limit_text(tmp_path, monkeypatch):
    data = _stopped_turn(tmp_path, monkeypatch, [INIT, SAID])
    assert data["error"] == cc_engine._time_limit_message(0.3)
    assert data["reason"] == "exec_timeout"


# --- review L: a stop long after the retry is a slow answer, not a missing model ------
# Claude Code prints no frame while a response streams, so after a retry that worked the
# last frame stays the api_retry until the answer is complete. API_TIMEOUT_MS bounds only
# the wait for response headers, so past the retry's delay plus that timeout plus 5 s,
# the retried request must be streaming: the turn simply ran out of time.

class _Clock:
    """The retry frame arrives at 0 s; the watchdog stops the turn at ``stop`` s."""

    def __init__(self, stop: float):
        self._readings = [0.0, stop]

    def __call__(self) -> float:
        return self._readings.pop(0) if len(self._readings) > 1 else self._readings[0]


RETRY_1_503 = dict(RETRY_503, attempt=1, retry_delay_ms=591)


def test_a_stop_inside_the_retry_window_says_the_model_was_unavailable(tmp_path, monkeypatch):
    # 0.591 s delay + 60 s default request timeout + 5 s = 65.591 s.
    data = _stopped_turn(tmp_path, monkeypatch, [INIT, RETRY_1_503], clock=_Clock(65.5))
    assert data["reason"] == "model_unavailable"
    assert data["error"] == MODEL_UNAVAILABLE


def test_a_stop_after_the_retry_window_is_a_slow_answer_and_keeps_the_time_limit_text(
        tmp_path, monkeypatch):
    """The reviewer's reproduction: one transient 503, then a healthy but slow stream."""
    data = _stopped_turn(tmp_path, monkeypatch, [INIT, RETRY_1_503], clock=_Clock(65.7))
    assert data["reason"] == "exec_timeout"
    assert data["error"] == cc_engine._time_limit_message(0.3)
    assert "detail" not in data


@pytest.mark.parametrize("stop, reason", [(7.5, "model_unavailable"), (7.7, "exec_timeout")])
def test_the_window_uses_the_request_timeout_the_agent_was_given(
        tmp_path, monkeypatch, stop, reason):
    """NEXTSEEK_CC_API_TIMEOUT_MS=2000: 0.591 + 2 + 5 = 7.591 s."""
    monkeypatch.setenv("NEXTSEEK_CC_API_TIMEOUT_MS", "2000")
    data = _stopped_turn(tmp_path, monkeypatch, [INIT, RETRY_1_503], clock=_Clock(stop))
    assert data["reason"] == reason


@pytest.mark.parametrize("retry, retry_at, stopped_at, api_timeout_ms, expected", [
    ({"retry_delay_ms": 591}, 10.0, 75.5, "60000", True),
    ({"retry_delay_ms": 591}, 10.0, 75.7, "60000", False),
    ({"retry_delay_ms": 0}, 0.0, 6.0, "1000", True),
    ({"retry_delay_ms": 0}, 0.0, 6.1, "1000", False),
    ({}, 0.0, 64.9, "60000", True),                        # no delay recorded: 0
    ({"retry_delay_ms": "junk"}, 0.0, 65.1, "60000", False),
    ({"retry_delay_ms": 591}, 0.0, 65.0, "not a number", True),  # falls back to 60000
    ({"retry_delay_ms": 591}, None, 1.0, "60000", False),  # arrival never recorded
])
def test_the_retry_window(retry, retry_at, stopped_at, api_timeout_ms, expected):
    assert cc_engine._stopped_waiting_on_retry(
        retry, retry_at=retry_at, stopped_at=stopped_at,
        api_timeout_ms=api_timeout_ms) is expected

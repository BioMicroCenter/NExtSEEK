"""13b.1: a Container-CC turn that overruns its wall clock still publishes what it wrote.

The watchdog path used to ``return`` right after reporting ``exec_timeout``,
before the staging sweep and before ``_publish_artifacts``. The agent writes its
deliverables into a PER-TURN scratch subtree that no later turn mounts, so a
turn that did real work and then overran lost that work for good, and anything
the sidecar staged for it was left behind as a stray.

The turn must still end in exactly one ``query_error`` with reason
``exec_timeout`` and the same error text: the user is told it timed out, and a
timed-out turn must not be recorded as completed (``on_turn_complete`` writes a
``completed`` chat_log entry, and chat_log is what the sticky-CC rule reads).

The fake container's "agent" writes files into its scratch on its first read
and then never emits a terminal frame, so the watchdog has to end the turn. No
docker, no network, no database.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import docker as docker_mod

from NessieAI.cc import cc_engine
from NessieAI.cc.cc_config import CCPaths

RUN_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
USER = "alice"
API_USER = "alice-login"
PROJECT = "proj"
TIMEOUT_S = 0.3
# The operator-approved message (2026-09-25), with the limit rendered from turn_timeout.
TIMEOUT_TEXT = (
    f"This took longer than the {TIMEOUT_S}-second limit, so I stopped. "
    "Say continue and I will carry on from where I got to."
)


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


class _OverrunningAgent:
    """Runs ``work`` once on the first read, then idles until the watchdog stops it."""

    def __init__(self, container: _Container, work, reply_parts=()):
        self._container = container
        self._work = work
        self._worked = False
        # Real assistant frames, so the real translator accumulates them: F20 reads
        # what it accumulated, and a test that patched the translator would prove nothing.
        self._pending = [
            json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "text", "text": text}]}})
            for text in reply_parts
        ]

    def send_stdin(self, _data):
        return None

    def close_stdin(self):
        return None

    def read_event_line(self):
        if not self._worked:
            self._worked = True
            self._work()
        if self._pending:
            return self._pending.pop(0)
        time.sleep(0.02)
        if self._container.stopped:
            return None
        return ""


def _scratch(root: Path) -> Path:
    return root / PROJECT / USER / "scratch" / RUN_ID


def _output(root: Path) -> Path:
    return root / PROJECT / USER / "output"


def _writes(root: Path, files: dict[str, bytes]):
    def work():
        for rel, body in files.items():
            dest = _scratch(root) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
    return work


def _run(tmp_path, monkeypatch, work, reply_parts=(), **kwargs):
    container = _Container()

    class _Containers:
        def run(self, **_kw):
            return container

    class _Client:
        containers = _Containers()

    monkeypatch.setattr(docker_mod, "from_env", lambda: _Client())
    monkeypatch.setattr(
        cc_engine, "BridgeAttachSocket",
        lambda raw, stdout_stream=None: _OverrunningAgent(container, work, reply_parts),
    )
    events: list[tuple[str, dict]] = []
    cc_engine.run_cc_turn(
        query="q", model_id="m", api_user=API_USER, api_pass="pw",
        send_event=lambda e, d: events.append((e, dict(d))),
        user_id=USER, project_dirname=PROJECT, run_id=RUN_ID,
        paths=CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path)),
        turn_timeout=TIMEOUT_S,
        **kwargs,
    )
    return events


def _terminals(events):
    return [(e, d) for e, d in events if e in ("query_complete", "query_error")]


def test_an_overrun_turn_publishes_the_file_it_wrote(tmp_path, monkeypatch):
    events = _run(tmp_path, monkeypatch, _writes(tmp_path, {"report.csv": b"a,b\n1,2\n"}))

    [(event, data)] = _terminals(events)
    assert event == "query_error"
    assert data["reason"] == "exec_timeout"
    assert data["error"] == TIMEOUT_TEXT
    published = _output(tmp_path) / "artifacts" / RUN_ID / "report.csv"
    assert published.read_bytes() == b"a,b\n1,2\n", (
        "the file the agent wrote before the watchdog fired must be published "
        "into output/, because its per-turn scratch is never mounted again"
    )
    assert data["artifacts"] == [{
        "artifact_type": "file", "key": f"{RUN_ID}/report.csv",
        "label": "report.csv", "file_format": "csv",
    }]


def test_an_overrun_turn_publishes_no_raw_files(tmp_path, monkeypatch):
    """raw/ is ONE directory shared by every turn of the user (unlike
    artifacts/<turn_id>/), and an earlier completed turn's chat_log entry still
    names its path. A stopped turn's raw file may be half-written, so publishing
    it would overwrite that turn's good copy with a truncated one."""
    earlier = _output(tmp_path) / "raw" / "rows.json"
    earlier.parent.mkdir(parents=True)
    earlier.write_bytes(b"[1, 2, 3]")

    events = _run(tmp_path, monkeypatch, _writes(tmp_path, {
        "raw/rows.json": b"[1, 2",
        "report.csv": b"a,b\n",
    }))

    [(event, data)] = _terminals(events)
    assert event == "query_error" and data["reason"] == "exec_timeout"
    assert earlier.read_bytes() == b"[1, 2, 3]", (
        "a timed-out turn must not overwrite an earlier turn's raw file"
    )
    assert data["cc_raw_files"] == []
    # The deliverables still go out: they land in this turn's own directory.
    assert (_output(tmp_path) / "artifacts" / RUN_ID / "report.csv").read_bytes() == b"a,b\n"
    assert data["artifacts"][0]["key"] == f"{RUN_ID}/report.csv"


def test_an_overrun_turn_sweeps_what_the_sidecar_staged_for_it(tmp_path, monkeypatch):
    """The in-turn sweep only takes markers stamped during THIS turn, so a turn
    that skips it leaves its own staged download behind for the recovery path,
    which files it under whichever later turn runs it."""
    req = "0f0e0d0c-0b0a-4908-8706-050403020100"
    staged = tmp_path / "_staging" / hashlib.sha256(API_USER.encode()).hexdigest()

    def stage():
        (staged / req).mkdir(parents=True)
        (staged / req / "submission.xlsx").write_bytes(b"xlsx-bytes")
        (staged / f"{req}.complete").write_bytes(b"")

    events = _run(tmp_path, monkeypatch, stage)

    [(event, data)] = _terminals(events)
    assert event == "query_error" and data["reason"] == "exec_timeout"
    published = (_output(tmp_path) / "artifacts" / RUN_ID
                 / "nextseek-artifacts" / "submission.xlsx")
    assert published.read_bytes() == b"xlsx-bytes"
    assert data["artifacts"][0]["key"] == f"{RUN_ID}/nextseek-artifacts/submission.xlsx"


def test_an_overrun_turn_is_still_not_recorded_as_completed(tmp_path, monkeypatch):
    completed = []
    events = _run(
        tmp_path, monkeypatch, _writes(tmp_path, {"report.csv": b"x"}),
        chat_session=object(), user_query="q",
        on_turn_complete=lambda payload: completed.append(payload),
    )

    assert [e for e, _ in _terminals(events)] == ["query_error"]
    assert completed == [], (
        "on_turn_complete appends a chat_log entry with status 'completed', which "
        "would make the chat sticky to CC after a turn that failed"
    )


def test_an_overrun_turn_that_wrote_nothing_reports_exactly_as_before(tmp_path, monkeypatch):
    events = _run(tmp_path, monkeypatch, lambda: None)

    [(event, data)] = _terminals(events)
    assert event == "query_error"
    assert data["error"] == TIMEOUT_TEXT
    assert data["reason"] == "exec_timeout"
    assert data["agent"] == "container_cc"
    assert data["artifacts"] is None
    assert data["cc_raw_files"] == []


def test_a_publish_failure_after_an_overrun_still_reports_the_timeout(tmp_path, monkeypatch):
    def broken_publish(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(cc_engine, "_publish_artifacts", broken_publish)
    events = _run(tmp_path, monkeypatch, _writes(tmp_path, {"report.csv": b"x"}))

    [(event, data)] = _terminals(events)
    assert event == "query_error"
    assert data["reason"] == "exec_timeout", (
        "a failure while salvaging the turn's files must not replace the timeout "
        "the user is owed with a generic 'turn failed: OSError'"
    )
    assert data["error"] == TIMEOUT_TEXT



# --------------------------------------------------------------------------
# F20: the turn hands back what it had, not only its files.
# --------------------------------------------------------------------------


def test_an_overrun_turn_carries_the_text_the_agent_had_written(tmp_path, monkeypatch):
    """The files were already salvaged; the user still got no words at all.

    A researcher asked for a summary file, the turn overran, and the reply was the bare
    limit message. Whatever the agent had worked out by then existed only in the
    transcript row.
    """
    events = _run(tmp_path, monkeypatch, lambda: None,
                  reply_parts=["Found 731 matching samples.", "Building the table now."])

    [(event, data)] = _terminals(events)
    assert event == "query_error"
    assert data["reason"] == "exec_timeout"
    assert data["partial_reply"] == "Found 731 matching samples.\n\nBuilding the table now."


def test_an_overrun_turn_with_nothing_said_carries_no_partial(tmp_path, monkeypatch):
    events = _run(tmp_path, monkeypatch, lambda: None)

    [(_event, data)] = _terminals(events)
    assert data["partial_reply"] is None


def test_the_limit_message_says_how_to_carry_on():
    """D5 keeps the limit where it is, so the message has to do the work."""
    assert "Say continue and I will carry on from where I got to." in TIMEOUT_TEXT

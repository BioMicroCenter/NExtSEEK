"""#68 — a CC turn that does NOT succeed must still leave a durable transcript.

``run_cc_turn`` used to write its ``CCSessionTranscript`` row (and the
``output/raw/transcript-<run_id>.jsonl`` copy) only from inside the
``query_complete`` branch. Every other way a turn can end left NOTHING behind:

* branch 2 — a ``result`` frame with ``is_error`` / a non-``success`` subtype,
  which ``translate._handle_result`` turns into ``query_error``;
* branch 4 — the watchdog timeout, which ``send_event``s ``query_error`` and
  then ``return``s *before* the gate (this one is not even enumerated in the
  issue body);
* branch 5 — the two exception handlers.

So exactly the turns worth triaging were the only turns with no durable record.
The fix is ONE capture+persist fallback in the ``finally`` (the ``finally`` runs
on all five paths, and is the only point where the container is guaranteed
stopped), not five patched branches.

Three properties of the fix are load-bearing and each has its own test here:

* it is ordered AFTER ``container.stop()`` and BEFORE ``scrub_transcript_store``
  — capture must not move after the scrub, which rewrites every jsonl via
  ``os.replace`` and so restamps them all with a fresh mtime, while
  ``_newest_jsonl_under`` picks by mtime;
* it carries its own ``try/except``, so a failure inside it can never skip the
  #72/#76 scrub that follows;
* it does NOT call ``on_turn_complete`` (that is ``_append_cc_turn_complete``,
  which also appends a ``chat_log`` entry with ``status: "completed"`` — the
  service layer already appends a ``status: "error"`` entry for the same turn,
  and ``chat_log`` is what the sticky-CC rule reads).

Harness: a fake docker client whose container replays a scripted stdout stream
through the REAL ``BridgeAttachSocket``/``CCStreamTranslator``, and whose
"agent" appends to a real session jsonl in a tmp cc-state store. No docker, no
network; the DB is real (``django_db``) because the row IS the subject.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from pathlib import Path

import pytest

from nextseek_api.assistant.models_db import CCSessionTranscript, ChatSession
from nextseek_api.cc_assistant import cc_engine, cc_transcript_store
from nextseek_api.cc_assistant.cc_config import CCPaths

pytestmark = pytest.mark.django_db

USER = "erin"
PROJECT = "proj"
CC_STATE_KEY = "chat-1"
CC_SESSION = "cc-sess-1"

# ``_container_name_for_run`` is fail-closed on ``^[0-9a-f-]{1,64}$`` (real run_ids
# are Celery task UUIDs), so these have to be hex-safe.
RUN1 = "aa11"
RUN2 = "bb22"

PW = "hunter2-s3cr3t"
BASIC = base64.b64encode(f"{USER}:{PW}".encode()).decode()

INIT = {"type": "system", "subtype": "init", "session_id": CC_SESSION}
ERROR_RESULT = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "session_id": CC_SESSION,
    "result": "the agent exploded",
}
SUCCESS_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "session_id": CC_SESSION,
    "result": "here is your answer",
    "total_cost_usd": 0.01,
    "num_turns": 1,
    "duration_ms": 12,
}


def _jsonl(*texts: str) -> bytes:
    return b"".join(
        (json.dumps({"type": "user", "message": {"content": t}}) + "\n").encode()
        for t in texts
    )


# ---------------------------------------------------------------------------
# Fake docker plumbing — the real attach socket + translator run on top of it
# ---------------------------------------------------------------------------


class _FakeSocket:
    """docker-py's hijacked attach socket, reduced to what BridgeAttachSocket
    touches on the write side (reads come from the ``logs`` stream)."""

    def __init__(self):
        self.sent = bytearray()
        self.shutdowns: list[int] = []

    def sendall(self, data):
        self.sent.extend(data)

    def shutdown(self, how):
        self.shutdowns.append(how)

    def close(self):
        pass


class _FakeContainer:
    def __init__(self, script):
        self._script = script
        self.stopped = threading.Event()
        self.stop_calls = 0
        self.remove_calls = 0
        self.socket = _FakeSocket()

    def attach_socket(self, params=None):
        return self.socket

    def logs(self, **kwargs):
        return self._script(self)

    def stop(self, timeout=None):
        self.stop_calls += 1
        self.stopped.set()

    def remove(self, force=False):
        self.remove_calls += 1
        self.stopped.set()


class _FakeContainers:
    def __init__(self, script, spawn_error=None, on_spawn=None):
        self._script = script
        self._spawn_error = spawn_error
        self._on_spawn = on_spawn
        self.run_kwargs = None
        self.container = None

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        if self._on_spawn is not None:
            self._on_spawn()
        if self._spawn_error is not None:
            raise self._spawn_error
        self.container = _FakeContainer(self._script)
        return self.container


class _FakeClient:
    def __init__(self, **kw):
        self.containers = _FakeContainers(**kw)


def _frames_script(frames, *, append_to=None, appended=b""):
    """Replay ``frames`` as stdout lines; optionally the agent writes first."""

    def script(container):
        if appended:
            _append(append_to, appended)
        for frame in frames:
            yield (json.dumps(frame) + "\n").encode()

    return script


def _never_terminating_script(*, append_to=None, appended=b""):
    """A stream that never emits a terminal frame — the watchdog must end it."""

    def script(container):
        if appended:
            _append(append_to, appended)
        yield (json.dumps(INIT) + "\n").encode()
        while not container.stopped.is_set():
            time.sleep(0.02)
            yield b"\n"          # blank line: `if not line: continue`

    return script


def _append(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as fh:
        fh.write(body)


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------


class _Harness:
    def __init__(self, tmp_path, monkeypatch, chat_session):
        self.tmp_path = tmp_path
        self._monkeypatch = monkeypatch
        self.chat_session = chat_session
        self.state = tmp_path / PROJECT / USER / "cc-state" / CC_STATE_KEY
        self.session_path = self.state / "projects" / "-home-user" / "sess.jsonl"
        self.output = tmp_path / PROJECT / USER / "output"
        self.events: list[tuple[str, dict]] = []
        self.client = None

    def plant(self, body: bytes) -> None:
        """Seed the store with an EARLIER turn's records (as --resume leaves it)."""
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_bytes(body)

    def raw_copy(self, run_id: str) -> Path:
        return self.output / "raw" / f"transcript-{run_id}.jsonl"

    def run(self, *, script=None, spawn_error=None, on_spawn=None, run_id=RUN1,
            turn_timeout=180, on_turn_complete=None, chat_session=...):
        import docker as docker_mod

        self.client = _FakeClient(
            script=script or _frames_script([INIT, ERROR_RESULT]),
            spawn_error=spawn_error, on_spawn=on_spawn,
        )
        self._monkeypatch.setattr(docker_mod, "from_env", lambda: self.client)
        cc_engine.run_cc_turn(
            query="how many mice",
            model_id=None,
            send_event=lambda e, d: self.events.append((e, d)),
            user_id=USER,
            project_dirname=PROJECT,
            run_id=run_id,
            paths=CCPaths(users_volume="dmac-cc-users",
                          user_root_mount=str(self.tmp_path)),
            cc_state_key=CC_STATE_KEY,
            api_user=USER,
            api_pass=PW,
            chat_session=(self.chat_session if chat_session is ... else chat_session),
            user_query="how many mice",
            on_turn_complete=on_turn_complete,
            turn_timeout=turn_timeout,
        )
        return self.events

    @property
    def terminal(self) -> str:
        return self.events[-1][0]

    def rows(self, run_id: str | None = None):
        qs = CCSessionTranscript.objects.filter(chat_session=self.chat_session)
        if run_id is not None:
            qs = qs.filter(turn_id=run_id)
        return list(qs)

    def blob(self, run_id: str) -> bytes:
        rows = self.rows(run_id)
        assert len(rows) == 1, f"expected exactly one row for {run_id}, got {len(rows)}"
        return cc_transcript_store.decompress(bytes(rows[0].blob))


@pytest.fixture
def harness(tmp_path, monkeypatch, django_user_model):
    user = django_user_model.objects.create_user("erin-68", password="x")
    return _Harness(tmp_path, monkeypatch,
                    ChatSession.objects.create(user=user))


# ---------------------------------------------------------------------------
# Branch 2 — a query_error result frame
# ---------------------------------------------------------------------------


def test_a_query_error_turn_persists_a_transcript_row(harness):
    """The headline. ``translate._handle_result`` maps a non-success subtype to
    ``query_error``, which falls straight past the ``query_complete`` gate."""
    body = _jsonl("agent started", "agent died here")
    harness.run(script=_frames_script(
        [INIT, ERROR_RESULT], append_to=harness.session_path, appended=body))

    assert harness.terminal == "query_error"
    blob = harness.blob(RUN1)
    assert blob, "the row must not hold an empty blob"
    assert b"agent died here" in blob


def test_a_query_error_turn_writes_the_raw_copy_too(harness):
    """The ``raw/`` copy is the on-disk half of the same record, and it is named
    for the run, so it must hold that run's records."""
    body = _jsonl("only this turn")
    harness.run(script=_frames_script(
        [INIT, ERROR_RESULT], append_to=harness.session_path, appended=body))

    copy = harness.raw_copy(RUN1)
    assert copy.is_file(), "an errored turn must still leave output/raw/transcript-*"
    assert copy.read_bytes() == harness.blob(RUN1)


# ---------------------------------------------------------------------------
# Branch 4 — the watchdog timeout (NOT enumerated in the issue)
# ---------------------------------------------------------------------------


def test_a_watchdog_timeout_turn_persists_a_transcript_row(harness):
    """The timeout ``return``s before ``_publish_artifacts`` and before the gate,
    so a fix that only widened the gate would still lose this turn entirely."""
    body = _jsonl("started a long thing", "never finished it")
    harness.run(
        script=_never_terminating_script(
            append_to=harness.session_path, appended=body),
        turn_timeout=1,
    )

    assert harness.terminal == "query_error"
    assert harness.events[-1][1]["reason"] == "exec_timeout"
    blob = harness.blob(RUN1)
    assert b"never finished it" in blob


# ---------------------------------------------------------------------------
# Branch 5 — the exception handlers
# ---------------------------------------------------------------------------


def test_an_exception_turn_persists_a_transcript_row(harness, caplog):
    """Spawn raises ``APIError`` after the agent has written. ``terminal`` is
    still ``None`` on this path, which is exactly why the fallback's log line
    names ``terminal``, not ``event``: ``event`` is bound only on the normal
    completion path, so reading it from the ``finally`` would raise
    ``UnboundLocalError`` on precisely the branches this issue is about.
    """
    from docker.errors import APIError

    body = _jsonl("wrote something before the SDK blew up")
    harness.plant(b"")   # the store exists; this turn's bytes arrive at spawn

    with caplog.at_level(logging.WARNING,
                         logger="nextseek_api.cc_assistant.cc_engine"):
        harness.run(
            spawn_error=APIError("spawn intercepted by test"),
            on_spawn=lambda: _append(harness.session_path, body),
        )

    assert harness.terminal == "query_error"
    assert b"wrote something before the SDK blew up" in harness.blob(RUN1)

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert RUN1 in logged
    assert "<no terminal event>" in logged, (
        "the fallback must name the terminal event CLASS from `terminal`, which "
        "is bound on every path, and say so plainly when there was none"
    )


# ---------------------------------------------------------------------------
# #72 x #68 — what lands in the row, and what is left on disk
# ---------------------------------------------------------------------------


def test_the_persisted_blob_carries_no_plaintext_password(harness):
    """The failed-turn row goes through the SAME in-process scrub the success
    path uses. ``store_transcript`` does no redaction of its own, so a fallback
    that skipped the scrub would put the user's SEEK password in the DB."""
    leaky = (
        '{"type":"user","message":{"content":"NEXTSEEK_PASSWORD=%s"}}\n'
        '{"type":"user","message":{"content":"> Authorization: Basic %s"}}\n'
        % (PW, BASIC)
    ).encode()
    harness.run(script=_frames_script(
        [INIT, ERROR_RESULT], append_to=harness.session_path, appended=leaky))

    blob = harness.blob(RUN1)
    assert PW.encode() not in blob
    assert BASIC.encode() not in blob
    assert b"<REDACTED>" in blob
    assert PW.encode() not in harness.raw_copy(RUN1).read_bytes()


def test_the_on_disk_source_is_still_scrubbed_and_watermarked(harness):
    """The dispatcher's constraint, asserted directly. ``cc_sweep`` is
    fail-closed on the watermark, so a capture inserted ahead of the scrub must
    leave both the scrub and its manifest entry exactly as they were."""
    leaky = (
        '{"type":"user","message":{"content":"NEXTSEEK_PASSWORD=%s"}}\n' % PW
    ).encode()
    harness.run(script=_frames_script(
        [INIT, ERROR_RESULT], append_to=harness.session_path, appended=leaky))

    body = harness.session_path.read_bytes()
    assert PW.encode() not in body
    assert cc_engine.transcript_is_verified_scrubbed(harness.session_path, body) is True


def test_the_capture_runs_before_the_scrub_restamps_every_jsonl(harness):
    """Position, asserted behaviourally rather than structurally.

    ``scrub_transcript_store`` rewrites each dirty jsonl via ``os.replace``,
    which stamps it with a FRESH mtime, and ``_newest_jsonl_under`` picks by
    mtime. A stale session file in the same store — far too old to be a
    candidate while the turn was running — becomes the newest file in that store
    the instant the scrub touches it. A capture moved after the scrub would
    therefore persist ANOTHER session's transcript under this run's turn_id.
    """
    stale = harness.state / "projects" / "-home-user" / "sess-old.jsonl"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(
        ('{"type":"user","message":{"content":"OTHER SESSION pw=%s"}}\n' % PW).encode())
    os.utime(stale, (time.time() - 600, time.time() - 600))

    mine = _jsonl("this session, this turn")
    harness.run(script=_frames_script(
        [INIT, ERROR_RESULT], append_to=harness.session_path, appended=mine))

    assert stale.stat().st_mtime > harness.session_path.stat().st_mtime, (
        "fixture is only meaningful if the scrub really did make the stale file "
        "the newest jsonl in this store"
    )
    assert harness.blob(RUN1) == mine
    assert b"OTHER SESSION" not in harness.blob(RUN1)


def test_a_failing_store_transcript_does_not_skip_the_scrub(harness, monkeypatch):
    """The fallback carries its own ``try/except`` for exactly this: an ORM
    failure inside it must not take the #72/#76 security control down with it.

    ``store_transcript`` deliberately lets ORM exceptions propagate, so this is
    the realistic shape of that failure and not a contrived one.
    """
    calls: list[dict] = []

    def boom(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(cc_transcript_store, "store_transcript", boom)

    leaky = (
        '{"type":"user","message":{"content":"NEXTSEEK_PASSWORD=%s"}}\n' % PW
    ).encode()
    harness.run(script=_frames_script(
        [INIT, ERROR_RESULT], append_to=harness.session_path, appended=leaky))

    assert len(calls) == 1, "the fallback must have attempted the persist"
    body = harness.session_path.read_bytes()
    assert PW.encode() not in body, "the scrub must still have run"
    assert cc_engine.transcript_is_verified_scrubbed(harness.session_path, body) is True
    assert harness.rows() == [], "nothing was persisted, and that is not fatal"


# ---------------------------------------------------------------------------
# The success path must be untouched
# ---------------------------------------------------------------------------


def test_a_successful_turn_writes_exactly_one_row_and_not_from_the_finally(
    harness, monkeypatch
):
    """``_append_cc_turn_complete`` upserts the same unique key from inside the
    ``try``. Without the ``transcript_persisted`` guard the fallback would run
    again in the ``finally`` and re-write the row — asserted on CONTENT and
    COUNT, since an upsert makes a double write invisible to ``exists()``.
    """
    from nextseek_api.services.cc_assistant import _append_cc_turn_complete

    calls: list[dict] = []
    real = cc_transcript_store.store_transcript
    monkeypatch.setattr(
        cc_transcript_store, "store_transcript",
        lambda **kw: (calls.append(kw), real(**kw))[1],
    )

    prior = _jsonl("turn 1 a", "turn 1 b")
    harness.plant(prior)
    mine = _jsonl("turn 2, the successful one")
    harness.run(
        script=_frames_script([INIT, SUCCESS_RESULT],
                              append_to=harness.session_path, appended=mine),
        on_turn_complete=_append_cc_turn_complete,
    )

    assert harness.terminal == "query_complete"
    assert calls == [], "the finally must not re-persist a turn that succeeded"
    assert len(harness.rows()) == 1
    assert harness.blob(RUN1) == mine, "the row holds the SLICE, not the session"


# ---------------------------------------------------------------------------
# The Part-2 slice, across two failed turns of one --resume session
# ---------------------------------------------------------------------------


def test_two_errored_turns_store_disjoint_slices_that_reconstruct_the_file(harness):
    """``--resume`` appends both turns to ONE jsonl. Row N must hold turn N, not
    turns 1..N, or the stored bytes grow quadratically in turn count."""
    first = _jsonl("turn 1 said this", "and this")
    harness.run(
        script=_frames_script([INIT, ERROR_RESULT],
                              append_to=harness.session_path, appended=first),
        run_id=RUN1,
    )
    second = _jsonl("turn 2 said something else")
    harness.run(
        script=_frames_script([INIT, ERROR_RESULT],
                              append_to=harness.session_path, appended=second),
        run_id=RUN2,
    )

    a, b = harness.blob(RUN1), harness.blob(RUN2)
    assert len(harness.rows()) == 2
    assert a == first
    assert b == second
    assert a + b == harness.session_path.read_bytes(), (
        "the two slices must partition the cumulative session file"
    )

"""D7 (2026-09-25 dev run): a Container-CC reply names container paths.

6 of 12 CC replies named ``/data/scratch/...``, which the agent is told never to do,
and one (task 1249) printed the documented template literally:
``/dmac/users/<project>/<user>/scratch/<run id>/nhp58_by_project.csv``. The engine
now rewrites both into this turn's real paths, from the same ``path_mappings`` it
hands the agent, before the reply is persisted or sent.

The pure function first, then the two places ``run_cc_turn`` applies it: the
completed reply (the event and the ``on_turn_complete`` payload) and the partial
reply a timed-out turn hands back. No docker, no network, no database.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import docker as docker_mod
import pytest

from NessieAI.cc import cc_engine
from NessieAI.cc.cc_config import CCPaths

SCRATCH = "/dmac/users/proj/alice/scratch/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
OUTPUT = "/dmac/users/proj/alice/output"
FULL = cc_engine.path_mappings_for(output_mnt=OUTPUT, run_scratch_mnt=SCRATCH)
NO_SCRATCH = cc_engine.path_mappings_for(output_mnt=OUTPUT, run_scratch_mnt=None)


def _rw(text, mappings=FULL):
    return cc_engine.rewrite_container_paths(text, mappings)


# --- the pure function ---------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Saved to /data/scratch/chart.png.", f"Saved to {SCRATCH}/chart.png."),
    ("`/data/scratch/sub/dir/a.csv`", f"`{SCRATCH}/sub/dir/a.csv`"),
    ("Everything is in /data/scratch", f"Everything is in {SCRATCH}"),
    ("Everything is in /data/scratch.", f"Everything is in {SCRATCH}."),
    ("in /data/scratch/, as usual", f"in {SCRATCH}/, as usual"),
    ("(/data/scratch/a.csv)", f"({SCRATCH}/a.csv)"),
    ("/data/output/report.xlsx", f"{OUTPUT}/report.xlsx"),
    ("see /data/output", f"see {OUTPUT}"),
])
def test_container_roots_become_the_turns_real_roots(text, expected):
    assert _rw(text) == expected


def test_every_occurrence_is_rewritten():
    text = "/data/scratch/a.csv and /data/scratch/b.csv and /data/output/c.xlsx"
    assert _rw(text) == f"{SCRATCH}/a.csv and {SCRATCH}/b.csv and {OUTPUT}/c.xlsx"


@pytest.mark.parametrize("text", [
    "/data/scratchpad/a.csv",           # a different directory, not the scratch root
    "/data/scratch_old/a.csv",
    "/data/scratch-2/a.csv",
    "/data/scratch.bak",
    "/data/outputs/a.csv",
    "/mnt/data/scratch/a.csv",          # another root that merely contains the string
    "~/data/scratch/a.csv",
    "/data/input/a.csv",                # other /data/ roots are never touched
    "/data/shared/a.csv",
    "/data/previous_turns/turn-01/rows.csv",
])
def test_paths_that_are_not_a_mapped_root_are_left_alone(text):
    assert _rw(text) == text


def test_the_documented_scratch_template_becomes_the_real_scratch_root():
    """Exactly what task 1249 printed."""
    text = ("The file is at "
            "`/dmac/users/<project>/<user>/scratch/<run id>/nhp58_by_project.csv`.")
    assert _rw(text) == f"The file is at `{SCRATCH}/nhp58_by_project.csv`."


def test_the_documented_output_template_becomes_the_real_output_root():
    text = "/dmac/users/<project>/<user>/output/report.xlsx"
    assert _rw(text) == f"{OUTPUT}/report.xlsx"


@pytest.mark.parametrize("run_id_token", ["<run id>", "<run_id>", "<run-id>"])
def test_the_run_id_placeholder_is_matched_however_it_is_spelled(run_id_token):
    text = f"/dmac/users/<project>/<user>/scratch/{run_id_token}/a.csv"
    assert _rw(text) == f"{SCRATCH}/a.csv"


def test_a_root_with_no_mapping_is_left_alone():
    """A turn with no run id has no scratch entry: nothing to translate to."""
    text = ("/data/scratch/a.csv and /dmac/users/<project>/<user>/scratch/<run id>/a.csv "
            "and /data/output/b.xlsx")
    assert _rw(text, NO_SCRATCH) == (
        "/data/scratch/a.csv and /dmac/users/<project>/<user>/scratch/<run id>/a.csv "
        f"and {OUTPUT}/b.xlsx")


@pytest.mark.parametrize("mappings", [
    {},
    None,
    {"scratch": {"container_root": "/data/scratch", "logical_root": None}},
    {"scratch": {"container_root": "/data/scratch", "logical_root": ""}},
    {"scratch": "not a dict"},
])
def test_an_unusable_mapping_changes_nothing(mappings):
    text = "/data/scratch/a.csv"
    assert _rw(text, mappings) == text


def test_a_replaced_path_is_never_rewritten_again():
    """A logical root that itself contains a container root must not be rescanned."""
    odd = cc_engine.path_mappings_for(output_mnt="/x/output",
                                      run_scratch_mnt="/data/output/run-1")
    assert _rw("/data/scratch/a.csv", odd) == "/data/output/run-1/a.csv"


@pytest.mark.parametrize("value", [None, "", 3])
def test_non_text_and_empty_text_pass_through(value):
    assert _rw(value) == value


def test_a_reply_with_no_container_path_is_unchanged():
    text = "There are 58 samples: 47 Macaca fascicularis and 11 Macaca mulatta."
    assert _rw(text) == text


# --- where run_cc_turn applies it ------------------------------------------------

RUN_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


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


class _Sock:
    """Feeds ``lines``; then either ends the stream or idles until the watchdog stops it."""

    def __init__(self, container, lines, idle=False):
        self._container = container
        self._lines = list(lines)
        self._idle = idle

    def send_stdin(self, _data):
        return None

    def close_stdin(self):
        return None

    def read_event_line(self):
        if self._lines:
            return self._lines.pop(0)
        if not self._idle:
            return None
        time.sleep(0.02)
        return None if self._container.stopped else ""


def _drive(tmp_path, monkeypatch, lines, *, idle=False, persist=False, **kwargs):
    container = _Container()

    class _Containers:
        def run(self, **_kw):
            return container

    class _Client:
        containers = _Containers()

    monkeypatch.setattr(docker_mod, "from_env", lambda: _Client())
    monkeypatch.setattr(cc_engine, "BridgeAttachSocket",
                        lambda raw, stdout_stream=None: _Sock(container, lines, idle))
    payloads: list = []
    if persist:
        from NessieAI.cc import cc_provision

        real_build = cc_provision.build_user_dirs

        def with_transcript(*a, **k):
            dirs = real_build(*a, **k)
            root = Path(dirs.cc_state_mnt) / "projects"
            root.mkdir(parents=True, exist_ok=True)
            (root / "turn.jsonl").write_bytes(
                b'{"type":"user","message":{"role":"user","content":"q"}}\n')
            return dirs

        monkeypatch.setattr(cc_provision, "build_user_dirs", with_transcript)

        class _Trace:
            def model_dump(self):
                return {"cc": True}

        monkeypatch.setattr("NessieAI.cc.cc_trace.extract_trace", lambda *a, **k: _Trace())
        kwargs.update(chat_session=object(), user_query="q", cc_state_key="abc-123",
                      on_turn_complete=payloads.append)
    events: list[tuple[str, dict]] = []
    cc_engine.run_cc_turn(
        query="q", model_id="m", api_user=None, api_pass=None,
        send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj", run_id=RUN_ID,
        paths=CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path)),
        **kwargs,
    )
    return events, payloads


def _real_roots(tmp_path):
    return (f"{tmp_path}/proj/alice/scratch/{RUN_ID}", f"{tmp_path}/proj/alice/output")


def test_the_completed_reply_is_rewritten_before_it_is_persisted_and_sent(tmp_path, monkeypatch):
    reply = ("Wrote /data/scratch/nhp58.csv, also at "
             "/dmac/users/<project>/<user>/scratch/<run id>/nhp58.csv")
    lines = [json.dumps({"type": "result", "subtype": "success", "is_error": False,
                         "result": reply, "session_id": "sid-1"})]
    events, payloads = _drive(tmp_path, monkeypatch, lines, persist=True)
    scratch, _ = _real_roots(tmp_path)
    expected = f"Wrote {scratch}/nhp58.csv, also at {scratch}/nhp58.csv"

    complete = [d for e, d in events if e == "query_complete"]
    assert len(complete) == 1
    assert complete[0]["reply"] == expected
    assert len(payloads) == 1
    assert payloads[0].assistant_reply == expected


def test_the_completed_reply_is_rewritten_without_a_chat_session(tmp_path, monkeypatch):
    lines = [json.dumps({"type": "result", "subtype": "success", "is_error": False,
                         "result": "See /data/output/r.xlsx", "session_id": "sid-1"})]
    events, _ = _drive(tmp_path, monkeypatch, lines)
    _, output = _real_roots(tmp_path)
    [complete] = [d for e, d in events if e == "query_complete"]
    assert complete["reply"] == f"See {output}/r.xlsx"


def test_a_timed_out_turns_partial_reply_is_rewritten(tmp_path, monkeypatch):
    lines = [json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "So far I wrote /data/scratch/part.csv"}]}})]
    events, _ = _drive(tmp_path, monkeypatch, lines, idle=True, turn_timeout=0.3)
    scratch, _ = _real_roots(tmp_path)
    [error] = [d for e, d in events if e == "query_error"]
    assert error["reason"] == "exec_timeout"
    assert error["partial_reply"] == f"So far I wrote {scratch}/part.csv"

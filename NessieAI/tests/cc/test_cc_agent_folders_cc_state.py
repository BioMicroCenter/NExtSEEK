"""The turn's transcript store is never read, written, renamed or chmodded through a link (step 1).

``<cc-state>/<session>/`` is the agent's ~/.claude: writable by the agent and kept across the chat's turns. Each
test plants links where Django lists, reads or rewrites it, runs the real engine function, and checks the canary
in this test's own temp folder. The canary holds MARK, and these tests hand the scrub MARK as the password, so a
scrub that followed a link would rewrite the canary.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from NessieAI.cc import cc_engine, safe_fs
from NessieAI.cc.cc_config import CCPaths
from NessieAI.cc.cc_provision import build_user_dirs
from NessieAI.tests.cc.agent_folder_canary import MARK, Canary

ENV_MARK = {"NEXTSEEK_USERNAME": "alice", "NEXTSEEK_PASSWORD": MARK.decode(), "API_PASS": MARK.decode()}


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    # The registry of agent roots is process-wide; each test starts with none.
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})


@pytest.fixture
def canary(tmp_path):
    return Canary(tmp_path)


def _cc_state(tmp_path) -> Path:
    folder = tmp_path / "users" / "proj" / "alice" / "cc-state" / "sess"
    folder.mkdir(parents=True)
    return folder


def test_the_scrub_never_follows_a_linked_store(tmp_path, canary):
    cc_state = _cc_state(tmp_path)
    canary.link_dir(cc_state / "projects")
    report = cc_engine.scrub_transcript_store(cc_state, ENV_MARK)
    assert report.rewritten == 0
    canary.assert_untouched()
    assert not (cc_state.parent / ".sess.scrub.json").exists(), "nothing is watermarked for a store that is a link"


def test_the_scrub_never_writes_its_temporary_file_through_a_link(tmp_path, canary):
    store = _cc_state(tmp_path) / "projects" / "-home-user"
    store.mkdir(parents=True)
    transcript = store / "sess.jsonl"
    transcript.write_bytes(b'{"type":"user","message":{"content":"echo ' + MARK + b'"}}\n')
    canary.link_file(store / "sess.jsonl.scrub-tmp")
    report = cc_engine.scrub_transcript_store(store.parents[1], ENV_MARK)
    assert (report.rewritten, report.skipped) == (1, 0)
    assert MARK not in transcript.read_bytes()
    assert stat.S_IMODE(transcript.stat().st_mode) == 0o666, "the agent appends to it on the next turn"
    canary.assert_untouched()


def test_the_scrub_never_follows_a_linked_session_folder_or_file(tmp_path, canary):
    projects = _cc_state(tmp_path) / "projects"
    projects.mkdir()
    canary.link_dir(projects / "-home-user")
    canary.link_file(projects / "leak.jsonl")
    report = cc_engine.scrub_transcript_store(projects.parent, ENV_MARK)
    assert (report.rewritten, report.skipped) == (0, 0)
    canary.assert_untouched()


def test_the_sibling_scrub_never_follows_a_linked_store(tmp_path, canary):
    current = _cc_state(tmp_path)
    sibling = current.parent / "sess-b"
    sibling.mkdir()
    canary.link_dir(sibling / "projects")
    report = cc_engine.scrub_sibling_transcript_stores(current.parent, ENV_MARK, exclude=current)
    assert report.rewritten == 0
    canary.assert_untouched()


def test_the_line_count_snapshot_never_reads_through_a_linked_store(tmp_path, canary):
    cc_state = _cc_state(tmp_path)
    canary.link_dir(cc_state / "projects")
    assert cc_engine._transcript_line_counts(cc_state, ("projects",)) == {}
    canary.assert_untouched()


def test_the_newest_transcript_is_never_a_link(tmp_path, canary):
    store = _cc_state(tmp_path) / "projects" / "-home-user"
    store.mkdir(parents=True)
    real = store / "real.jsonl"
    real.write_bytes(b"{}\n")
    os.utime(real, (1.0, 1.0))
    canary.link_file(store / "newer.jsonl")
    assert cc_engine._newest_jsonl_under(store.parents[1], ("projects",)) == real


def test_a_folder_inside_a_registered_session_is_never_a_root(tmp_path, canary, monkeypatch):
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})
    cc_state = _cc_state(tmp_path)
    store = cc_state / "projects" / "-home-user"
    store.mkdir(parents=True)
    (store / "t.jsonl").write_bytes(b"{}\n")
    dirs = build_user_dirs(CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path / "users")),
                           "proj", "alice", session_id="sess")
    assert dirs.cc_state_mnt == str(cc_state)
    with pytest.raises(safe_fs.UnsafePath):
        cc_engine._newest_jsonl_under(cc_state / "projects")
    assert cc_engine._transcript_line_counts(cc_state / "projects") == {}
    # From the registered session folder, with projects in rel_parts, the same store is read.
    assert cc_engine._newest_jsonl_under(cc_state, ("projects",)) == store / "t.jsonl"
    assert cc_engine._transcript_line_counts(cc_state, ("projects",)) == {str(store / "t.jsonl"): 1}
    canary.assert_untouched()


def test_the_turn_capture_never_reads_through_a_linked_store(tmp_path, canary):
    cc_state = _cc_state(tmp_path)
    canary.link_dir(cc_state / "projects")
    got = cc_engine._read_turn_transcript(str(cc_state), turn_start=0.0, prior_lines={},
                                          environment={}, attempts=1)
    assert got == cc_engine.CapturedTranscript(b"", b"")
    canary.assert_unread(got)


def test_the_turn_capture_never_picks_a_linked_transcript(tmp_path, canary):
    store = _cc_state(tmp_path) / "projects" / "-home-user"
    store.mkdir(parents=True)
    canary.link_file(store / "leak.jsonl")
    got = cc_engine._read_turn_transcript(str(store.parents[1]), turn_start=0.0, prior_lines={},
                                          environment={}, attempts=1)
    assert got == cc_engine.CapturedTranscript(b"", b"")
    canary.assert_unread(got)

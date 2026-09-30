"""The turn's scratch and the sidecar's _staging folder are never listed, read or copied through a link (step 1).

Scratch is the agent's per-turn folder; _staging is written by the sidecar. Django snapshots scratch, copies what
changed into output/ (publish), and sweeps staged downloads into scratch. Each test plants a link where Django
works and checks the canary in this test's own temp folder.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from NessieAI.cc import cc_engine, cc_staging, safe_fs
from NessieAI.tests.cc.agent_folder_canary import MARK, Canary

API_USER = "alice@mit.edu"
REQ = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    # The registry of agent roots is process-wide; each test starts with none.
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})


@pytest.fixture
def canary(tmp_path):
    return Canary(tmp_path)


def _scratch(tmp_path) -> Path:
    folder = tmp_path / "users" / "proj" / "alice" / "scratch" / "run1"
    folder.mkdir(parents=True)
    return folder


def _output(tmp_path) -> Path:
    return tmp_path / "users" / "proj" / "alice" / "output"


def _staging(tmp_path) -> Path:
    base = tmp_path / "users" / "_staging" / hashlib.sha256(API_USER.encode()).hexdigest()
    base.mkdir(parents=True)
    return base


def _sweep(tmp_path, scratch: Path):
    return cc_staging.sweep_user_staging(
        user_root_mount=str(tmp_path / "users"), scratch_dir=str(scratch), api_user=API_USER,
        user_id="alice", project_dirname="proj", since_ts=None)


def test_the_snapshot_lists_only_plain_files_and_never_through_a_link(tmp_path, canary):
    scratch = _scratch(tmp_path)
    (scratch / "real.csv").write_bytes(b"a\n")
    canary.link_file(scratch / "leak.csv")
    canary.link_dir(scratch / "sub")
    os.mkfifo(scratch / "pipe.csv")
    assert set(cc_engine._snapshot_tree(scratch)) == {"real.csv"}
    canary.assert_untouched()


def test_publish_copies_real_files_and_nothing_through_a_link(tmp_path, canary):
    scratch = _scratch(tmp_path)
    output = _output(tmp_path)
    before = cc_engine.snapshot_before(scratch, "alice")
    (scratch / "report.csv").write_bytes(b"a,b\n")
    (scratch / "raw").mkdir()
    (scratch / "raw" / "debug.log").write_bytes(b"dbg\n")
    canary.link_file(scratch / "leak.csv")
    canary.link_file(scratch / "raw" / "leak.log")
    canary.link_dir(scratch / "linked")

    result = cc_engine._publish_artifacts(scratch, output, turn_id="run1",
                                          output_logical_root="/dmac/users/proj/alice/output", before=before)

    assert result["files_created"] == ["raw/debug.log", "report.csv"]
    assert (output / "artifacts" / "run1" / "report.csv").read_bytes() == b"a,b\n"
    canary.assert_untouched()
    canary.assert_unread(output, result)


def test_publish_never_copies_through_a_folder_swapped_for_a_link(tmp_path, canary, monkeypatch):
    """The agent's folder can change between the listing and the copy; the copy re-walks every step."""
    scratch = _scratch(tmp_path)
    output = _output(tmp_path)
    before = cc_engine.snapshot_before(scratch, "alice")
    (scratch / "sub").mkdir()
    (scratch / "sub" / "secret.jsonl").write_bytes(b"agent output\n")
    real_snapshot = cc_engine._snapshot_tree

    def snapshot_then_swap(root):
        listing = real_snapshot(root)
        shutil.rmtree(scratch / "sub")
        canary.link_dir(scratch / "sub")
        return listing

    monkeypatch.setattr(cc_engine, "_snapshot_tree", snapshot_then_swap)
    cc_engine._publish_artifacts(scratch, output, turn_id="run1",
                                 output_logical_root="/dmac/users/proj/alice/output", before=before)
    canary.assert_untouched()
    canary.assert_unread(output)


def test_the_sweep_never_follows_a_linked_staging_folder(tmp_path):
    canary = Canary(tmp_path, extra={f"{REQ}/report.csv": b"row," + MARK + b"\n", f"{REQ}.complete": b""})
    scratch = _scratch(tmp_path)
    (tmp_path / "users" / "_staging").mkdir(parents=True)
    canary.link_dir(tmp_path / "users" / "_staging" / hashlib.sha256(API_USER.encode()).hexdigest())

    result = _sweep(tmp_path, scratch)

    assert result.delivered == []
    canary.assert_untouched()
    canary.assert_unread(scratch)


def test_the_sweep_walks_the_users_folder_from_the_staging_root(tmp_path):
    """A real request in the user's folder is delivered: the sweep roots at _staging with the hash in rel, so a
    sweep that passed _staging/<hash> as a root would be refused and deliver nothing."""
    scratch = _scratch(tmp_path)
    base = _staging(tmp_path)
    (base / REQ).mkdir()
    (base / REQ / "a.csv").write_bytes(b"a\n")
    (base / f"{REQ}.complete").write_text("")

    result = _sweep(tmp_path, scratch)

    assert result.delivered == ["nextseek-artifacts/a.csv"]
    assert safe_fs.agent_root_of(base) == tmp_path / "users" / "_staging"
    with pytest.raises(safe_fs.UnsafePath):
        list(safe_fs.iter_files(base))  # the <hash> folder is never a root


def test_a_staged_file_over_the_size_cap_is_left_in_place(tmp_path, monkeypatch):
    """Over the cap is delivered by no path (the in-turn sweep and cc_sweep_staging share it): it stays where
    the sidecar put it, with its marker."""
    monkeypatch.setattr(cc_staging, "_MAX_STAGED_BYTES", 4)
    scratch = _scratch(tmp_path)
    base = _staging(tmp_path)
    (base / REQ).mkdir()
    (base / REQ / "big.csv").write_bytes(b"0123456789")
    (base / f"{REQ}.complete").write_text("")

    result = _sweep(tmp_path, scratch)

    assert result.delivered == []
    assert (base / f"{REQ}.complete").exists() and (base / REQ / "big.csv").exists()


def test_the_sweep_still_delivers_and_cleans_up(tmp_path):
    scratch = _scratch(tmp_path)
    base = _staging(tmp_path)
    (base / REQ / "sub").mkdir(parents=True)
    (base / REQ / "a.csv").write_bytes(b"a\n")
    (base / REQ / "sub" / "b.csv").write_bytes(b"b\n")
    (base / f"{REQ}.complete").write_text("")

    result = _sweep(tmp_path, scratch)

    assert result.delivered == ["nextseek-artifacts/a.csv", "nextseek-artifacts/sub/b.csv"]
    assert (scratch / "nextseek-artifacts" / "sub" / "b.csv").read_bytes() == b"b\n"
    assert not (base / REQ).exists() and not (base / f"{REQ}.complete").exists()

"""Tests for checkpoint.py — append-only checkpoint/resume."""
from __future__ import annotations

import os

import pytest

from nextseek_api.batch_upload.checkpoint import (
    determine_resume_uid,
    get_last_checkpoint_uid,
    write_checkpoint,
)


class TestWriteCheckpoint:
    def test_creates_dir_and_file(self, tmp_path):
        cp_dir = str(tmp_path / "sub" / "deep")
        write_checkpoint(cp_dir, "cp.txt", "UID-001")
        path = os.path.join(cp_dir, "cp.txt")
        assert os.path.isfile(path)
        with open(path) as f:
            assert f.read().strip() == "UID-001"

    def test_appends_multiple_uids(self, tmp_path):
        cp_dir = str(tmp_path)
        write_checkpoint(cp_dir, "cp.txt", "UID-001")
        write_checkpoint(cp_dir, "cp.txt", "UID-002")
        write_checkpoint(cp_dir, "cp.txt", "UID-003")
        path = os.path.join(cp_dir, "cp.txt")
        with open(path) as f:
            lines = f.read().strip().splitlines()
        assert lines == ["UID-001", "UID-002", "UID-003"]

    def test_empty_checkpoint_dir_is_noop(self, tmp_path):
        # Should not raise or create any files
        write_checkpoint("", "cp.txt", "UID-001")

    def test_noop_when_no_dir(self):
        write_checkpoint("", "cp.txt", "UID-001")
        # No exception should be raised


class TestGetLastCheckpointUid:
    def test_returns_last_uid(self, tmp_path):
        cp_dir = str(tmp_path)
        path = os.path.join(cp_dir, "cp.txt")
        with open(path, "w") as f:
            f.write("UID-001\nUID-002\nUID-003\n")
        assert get_last_checkpoint_uid(cp_dir, "cp.txt") == "UID-003"

    def test_returns_none_when_file_missing(self, tmp_path):
        assert get_last_checkpoint_uid(str(tmp_path), "missing.txt") is None

    def test_returns_none_when_empty_dir(self):
        assert get_last_checkpoint_uid("", "cp.txt") is None

    def test_returns_none_for_empty_file(self, tmp_path):
        cp_dir = str(tmp_path)
        path = os.path.join(cp_dir, "cp.txt")
        with open(path, "w") as f:
            f.write("")
        assert get_last_checkpoint_uid(cp_dir, "cp.txt") is None

    def test_handles_trailing_whitespace(self, tmp_path):
        cp_dir = str(tmp_path)
        path = os.path.join(cp_dir, "cp.txt")
        with open(path, "w") as f:
            f.write("UID-001\n  UID-002  \n")
        assert get_last_checkpoint_uid(cp_dir, "cp.txt") == "UID-002"


class TestDetermineResumeUid:
    def test_returns_none_when_no_sources(self, tmp_path):
        assert determine_resume_uid(None, str(tmp_path), "cp.txt") is None

    def test_cli_flag_only(self, tmp_path):
        result = determine_resume_uid("UID-005", str(tmp_path), "cp.txt")
        assert result == "UID-005"

    def test_checkpoint_only(self, tmp_path):
        cp_dir = str(tmp_path)
        with open(os.path.join(cp_dir, "cp.txt"), "w") as f:
            f.write("UID-003\n")
        result = determine_resume_uid(None, cp_dir, "cp.txt")
        assert result == "UID-003"

    def test_takes_lexicographic_max(self, tmp_path):
        cp_dir = str(tmp_path)
        with open(os.path.join(cp_dir, "cp.txt"), "w") as f:
            f.write("UID-010\n")
        # CLI flag is earlier alphabetically
        result = determine_resume_uid("UID-005", cp_dir, "cp.txt")
        assert result == "UID-010"

    def test_cli_wins_when_later(self, tmp_path):
        cp_dir = str(tmp_path)
        with open(os.path.join(cp_dir, "cp.txt"), "w") as f:
            f.write("UID-003\n")
        result = determine_resume_uid("UID-050", cp_dir, "cp.txt")
        assert result == "UID-050"

"""Hermetic tests for the CC upload Celery body (no live broker)."""
from __future__ import annotations

from pathlib import Path

import pytest

from nextseek_api.cc_assistant import cc_upload_tasks as tasks
from nextseek_api.cc_assistant.cc_upload_validate import validate_upload_filename


class _Self:
    def __init__(self):
        self.states = []

    def update_state(self, **kw):
        self.states.append(kw)


def test_run_cc_upload_moves_files_and_unlinks_tmp(tmp_path: Path, monkeypatch):
    dest = tmp_path / "input"
    a = tmp_path / "a.csv"
    b = tmp_path / "b.txt"
    a.write_text("A")
    b.write_text("B")
    states = []

    def update_state(self, **kw):
        states.append(kw)

    monkeypatch.setattr(type(tasks.run_cc_upload_task), "update_state", update_state, raising=False)
    result = tasks.run_cc_upload_task.run(
        input_mnt=str(dest),
        files=[{"name": "a.csv", "tmp_path": str(a)}, {"name": "b.txt", "tmp_path": str(b)}],
    )
    assert result == {"saved": ["a.csv", "b.txt"], "count": 2}
    assert (dest / "a.csv").read_text() == "A"
    assert (dest / "b.txt").read_text() == "B"
    assert not a.exists() and not b.exists()
    assert states[-1]["state"] == "PROGRESS"
    assert states[-1]["meta"]["progress_pct"] == 100


def test_run_cc_upload_empty_files_still_succeeds(tmp_path: Path):
    dest = tmp_path / "input"
    result = tasks.run_cc_upload_task.run(input_mnt=str(dest), files=[])
    assert result == {"saved": [], "count": 0}
    assert dest.is_dir()


def test_run_cc_upload_rejects_traversal_name_and_cleans_tmp(tmp_path: Path):
    dest = tmp_path / "input"
    staged = tmp_path / "staged.bin"
    staged.write_text("x")
    with pytest.raises(ValueError):
        validate_upload_filename("../x")
    with pytest.raises(Exception):
        tasks.run_cc_upload_task.run(
            input_mnt=str(dest),
            files=[{"name": "../x", "tmp_path": str(staged)}],
        )
    assert not staged.exists()

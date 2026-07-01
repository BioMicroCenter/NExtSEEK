"""Newest-jsonl selection on REAL stat data. Py3.12-safe: no monkeypatching of
pathlib.Path.stat (PosixPath uses __slots__ — `monkeypatch.setattr(<Path>, "stat", …)`
raises `AttributeError: 'PosixPath' object attribute 'stat' is read-only`). Instead
create real files and set distinct mtimes with os.utime."""
import os
from pathlib import Path

_NSAPI = Path(__file__).resolve().parents[2]


def test_newest_jsonl_respects_min_mtime(tmp_path):
    from nextseek_api.cc_assistant.cc_engine import _newest_jsonl_under
    old = tmp_path / "old.jsonl"; old.write_text("x"); os.utime(old, (1.0, 1.0))
    new = tmp_path / "new.jsonl"; new.write_text("y"); os.utime(new, (10.0, 10.0))
    assert _newest_jsonl_under(tmp_path, min_mtime=5.0) == new
    assert _newest_jsonl_under(tmp_path, min_mtime=20.0) is None


def test_run_cc_turn_sets_turn_start_ts_before_container():
    src = (_NSAPI / "cc_assistant" / "cc_engine.py").read_text()
    idx_ts = src.index("translator._turn_start_ts")
    idx_run = src.index("client.containers.run")
    assert idx_ts < idx_run

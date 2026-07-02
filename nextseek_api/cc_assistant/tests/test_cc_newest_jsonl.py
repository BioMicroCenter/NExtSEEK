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
    """`translator._turn_start_ts` must be set before the CC sibling is
    spawned. Step 2b (G7-11) extracted the actual `client.containers.run`
    call into `_spawn_with_stale_name_retry` (defined ahead of `run_cc_turn`
    in the file for the stale-name-conflict retry), so this now checks
    ordering WITHIN `run_cc_turn`'s own body against the spawn call site
    (`_spawn_with_stale_name_retry(client`), not the raw
    `client.containers.run` substring (which is now that helper's internal
    implementation detail and would appear earlier in the file regardless of
    `run_cc_turn`'s internal ordering)."""
    src = (_NSAPI / "cc_assistant" / "cc_engine.py").read_text()
    body = src[src.index("def run_cc_turn("):]
    idx_ts = body.index("translator._turn_start_ts")
    idx_run = body.index("_spawn_with_stale_name_retry(client")
    assert idx_ts < idx_run


def test_cc_engine_actually_invokes_on_turn_complete():
    """RED if run_cc_turn's persist block never CALLS the callback (Task 11 Step 2)."""
    src = (_NSAPI / "cc_assistant" / "cc_engine.py").read_text()
    assert "on_turn_complete(TurnCompletePayload(" in src


def test_services_wires_append_cc_turn_complete_into_run_cc_turn():
    """RED if services/cc_assistant.py stops passing the real writer (Task 11 Step 3)."""
    src = (_NSAPI / "services" / "cc_assistant.py").read_text()
    assert "on_turn_complete=_append_cc_turn_complete" in src

"""Newest-jsonl selection on REAL stat data. Py3.12-safe: no monkeypatching of
pathlib.Path.stat (PosixPath uses __slots__ — `monkeypatch.setattr(<Path>, "stat", …)`
raises `AttributeError: 'PosixPath' object attribute 'stat' is read-only`). Instead
create real files and set distinct mtimes with os.utime."""
import os

from NessieAI import paths

_CC_ENGINE = paths.CC_DIR / "cc_engine.py"
# The CC turn body, which hands run_cc_turn its on_turn_complete writer, moved
# out of nextseek_api/services/cc_assistant.py into NessieAI/cc/turn.py (Phase B).
_TURN = paths.CC_DIR / "turn.py"


def test_newest_jsonl_respects_min_mtime(tmp_path):
    from NessieAI.cc.cc_engine import _newest_jsonl_under
    old = tmp_path / "old.jsonl"; old.write_text("x"); os.utime(old, (1.0, 1.0))
    new = tmp_path / "new.jsonl"; new.write_text("y"); os.utime(new, (10.0, 10.0))
    assert _newest_jsonl_under(tmp_path, min_mtime=5.0) == new
    assert _newest_jsonl_under(tmp_path, min_mtime=20.0) is None


def test_run_cc_turn_sets_turn_start_ts_before_container():
    """`translator._turn_start_ts` must be set before the CC sibling is
    spawned: ordering WITHIN `run_cc_turn`'s own body, against the call that
    asks the spawner to start the container."""
    src = _CC_ENGINE.read_text()
    body = src[src.index("def run_cc_turn("):]
    idx_ts = body.index("translator._turn_start_ts")
    idx_run = body.index("spawner_client.start_turn(")
    assert idx_ts < idx_run


def test_cc_engine_actually_invokes_on_turn_complete():
    """RED if run_cc_turn's persist block never CALLS the callback (Task 11 Step 2)."""
    src = _CC_ENGINE.read_text()
    assert "on_turn_complete(TurnCompletePayload(" in src


def test_services_wires_append_cc_turn_complete_into_run_cc_turn():
    """RED if the CC turn (NessieAI/cc/turn.py) stops passing the real writer (Task 11 Step 3)."""
    src = _TURN.read_text()
    assert "on_turn_complete=_append_cc_turn_complete" in src

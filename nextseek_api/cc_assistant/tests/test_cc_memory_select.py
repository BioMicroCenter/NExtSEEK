"""Hermetic: window + sync-target selection. No Django."""
from nextseek_api.cc_assistant import cc_memory


def _m(sid, ts, changed=False, summary=None):
    return cc_memory.SessionMeta(session_id=sid, updated_at=ts, fingerprint=None,
                                 summary=summary, transcript_path=f"/{sid}.jsonl",
                                 changed=changed)


def test_window_excludes_current_and_is_most_recent_first():
    sessions = [_m("A", 10), _m("B", 30), _m("C", 20), _m("Y", 99)]
    win = cc_memory.select_window(sessions, current_id="Y", window_size=10)
    assert [s.session_id for s in win] == ["B", "C", "A"]


def test_window_caps_at_size():
    sessions = [_m(str(i), i) for i in range(20)]
    win = cc_memory.select_window(sessions, current_id="nope", window_size=10)
    assert len(win) == 10
    assert [s.session_id for s in win][0] == "19"


def test_sync_target_is_most_recent_changed_non_current():
    sessions = [_m("A", 10, changed=True), _m("B", 30, changed=False),
                _m("C", 20, changed=True), _m("Y", 99, changed=True)]
    tgt = cc_memory.select_sync_target(sessions, current_id="Y")
    assert tgt.session_id == "C"


def test_sync_target_none_when_nothing_changed():
    sessions = [_m("A", 10, changed=False), _m("Y", 99, changed=True)]
    assert cc_memory.select_sync_target(sessions, current_id="Y") is None

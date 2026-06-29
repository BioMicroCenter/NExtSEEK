"""Hermetic: sweep target selection (idle + changed). No Django/Celery import."""
from nextseek_api.cc_assistant import cc_sweep, cc_memory


def _m(sid, updated_ts, changed):
    return cc_memory.SessionMeta(session_id=sid, updated_at=updated_ts, fingerprint=None,
                                 summary=None, transcript_path=f"/{sid}.jsonl", changed=changed)


def test_selects_idle_and_changed_only():
    now = 1000.0
    metas = [
        _m("idle_changed", now - 1000, True),
        _m("idle_unchanged", now - 1000, False),
        _m("fresh_changed", now - 10, True),
    ]
    picked = {m.session_id for m in cc_sweep.select_sweep_targets(metas, now, idle_seconds=900)}
    assert picked == {"idle_changed"}


def test_empty_when_none_qualify():
    assert cc_sweep.select_sweep_targets([], 0.0, idle_seconds=900) == []

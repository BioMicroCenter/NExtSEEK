"""F §12.4: deterministic HistoryTurn builder — caps, legacy derivation, four turn kinds."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import router_context as rc


def _ns(tid, **kw):
    e = {"turn_id": tid, "ts": "t", "mode": "search", "user_query": f"q{tid}",
         "assistant_reply": f"a{tid}", "router_choice": "nextseek_query",
         "status": "completed",
         "result_summary": {"count": 139, "first_uids": ["D.SEQ-1", "D.SEQ-2",
                                                         "D.SEQ-3", "D.SEQ-4", "D.SEQ-5"]}}
    e.update(kw); return e


def test_truncate_utf8_never_splits_codepoint():
    s = "é" * 300                       # 2 bytes each
    out = rc.truncate_utf8(s, 511)      # odd cap lands mid-codepoint
    assert out.endswith("…")
    out.encode("utf-8")                 # must not raise
    assert len(out[:-1].encode("utf-8")) <= 511


def test_truncate_noop_under_cap():
    assert rc.truncate_utf8("short", 512) == "short"
    assert rc.truncate_utf8("hello", 0) == "…"


def test_build_history_four_kinds():
    log = [
        _ns(1),
        {"turn_id": 2, "ts": "t", "mode": "cc", "user_query": "q2",
         "assistant_reply": "cc answer", "router_choice": "container_cc",
         "status": "completed", "cc_run_id": "uuid"},
        {"turn_id": 3, "ts": "t", "mode": "unrelated", "user_query": "q3",
         "router_choice": "unrelated", "status": "completed"},
        {"turn_id": 4, "ts": "t", "mode": "container_cc", "user_query": "q4",
         "router_choice": "container_cc", "status": "error", "error": "timeout"},
    ]
    h = rc.build_history(log)
    assert [t.position for t in h] == [1, 2, 3, 4]        # oldest→newest
    assert h[0].result_count == 139 and h[0].sample_uids == ["D.SEQ-1", "D.SEQ-2", "D.SEQ-3"]
    assert h[1].result_count is None and h[1].sample_uids == []
    assert h[2].assistant_reply is None and h[2].router_choice == "unrelated"
    assert h[3].status == "error" and h[3].error == "timeout" and h[3].assistant_reply is None


def test_build_history_legacy_derivation():
    log = [{"turn_id": 1, "ts": "t", "mode": "cc", "user_query": "q",
            "assistant_reply": "a"},                       # legacy CC
           {"turn_id": 2, "ts": "t", "mode": "search", "user_query": "q",
            "assistant_reply": ""}]                        # legacy NS, ""-reply
    h = rc.build_history(log)
    assert h[0].router_choice == "container_cc" and h[0].status == "completed"
    assert h[1].router_choice == "nextseek_query"
    assert h[1].assistant_reply is None                    # "" mapped to None


def test_build_history_last_five_only():
    log = [_ns(i) for i in range(1, 9)]
    h = rc.build_history(log)
    assert [t.position for t in h] == [4, 5, 6, 7, 8]


def test_build_history_skips_malformed_never_raises():
    log = [_ns(1), "not-a-dict", {"no_turn_id": True}, None, _ns(2),
           {"turn_id": 3, "assistant_reply": 123}]
    h = rc.build_history(log)
    assert [t.position for t in h] == [1, 2]


def test_history_turn_rejects_empty_reply():
    with pytest.raises(Exception):
        rc.HistoryTurn(position=1, user_message="q", assistant_reply="")


def test_caps_applied_per_field():
    e = _ns(1, user_query="u" * 600, assistant_reply="a" * 2000,
            status="error", error="e" * 500)
    t = rc.build_history([e])[0]
    assert len(t.user_message.encode()) <= 515             # 512 + "…"
    assert len(t.assistant_reply.encode()) <= 1027
    assert len(t.error.encode()) <= 259


def test_build_history_preserves_log_order_with_odd_positions():
    """Positions come from turn_ids as stored — out-of-order/duplicate ids must
    not crash, reorder, or dedupe (the log's order IS the conversation order)."""
    log = [_ns(5), _ns(2), _ns(2)]
    h = rc.build_history(log)
    assert [t.position for t in h] == [5, 2, 2]


def test_build_history_coerces_weird_uids_and_caps_them():
    e = _ns(1)
    e["result_summary"]["first_uids"] = [123, None, "ok", {"x": 1}, "y", "z"]
    t = rc.build_history([e])[0]
    assert len(t.sample_uids) == 3
    assert all(isinstance(u, str) for u in t.sample_uids)


def test_build_history_limit_zero_and_negative():
    assert rc.build_history([_ns(1)], limit=0) == []
    assert rc.build_history([_ns(1)], limit=-1) == []
    assert rc.build_history("not-a-list") == []
    assert rc.build_history(None) == []

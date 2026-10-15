"""A Container-CC turn after a crash sees the crashed question plus the fixed marker."""
from NessieAI.cc.cc_turn_context import build_cc_contexts


def test_crashed_turn_is_in_the_cc_context():
    log = [{"turn_id": 1, "mode": "cc", "status": "completed", "user_query": "a", "assistant_reply": "ok"},
           {"turn_id": 2, "mode": "nextseek_query", "status": "error", "user_query": "b", "error": "ValueError",
            "intent_summary": "Unhandled error in the pipeline."}]
    ctxs = build_cc_contexts(log)
    assert [c.turn_id for c in ctxs] == [1, 2]
    assert ctxs[1].user_query == "b" and ctxs[1].status == "error"
    assert "failed with an internal error (ValueError)" in ctxs[1].reply


def test_crashed_turn_text_beyond_type_is_hidden():
    log = [{"turn_id": 2, "mode": "cc", "status": "error", "user_query": "b", "error": "boom: secret detail",
            "intent_summary": "Unhandled error in the pipeline."}]
    assert "secret" not in build_cc_contexts(log)[0].reply


def test_cc_non_answer_entries_are_not_shown_as_crashes():
    from NessieAI.cc import cc_turn_complete
    from chat_nextseek.chat_memory import recent_turns
    for err in ("Container-CC route is not available: x", "TimeLimit", None):
        e = cc_turn_complete.serialize_non_answer_entry(
            user_query="q", router_choice="container_cc", status="error", error=err, ts="t", turn_id=5)
        assert build_cc_contexts([e]) == []
        assert recent_turns({"chat_log": [e]}) == []


def test_digest_labels_a_crash_by_its_real_route():
    from NessieAI.cc.ns_digest import _render_cc_turn
    log = [{"turn_id": 2, "mode": "nextseek_query", "status": "error", "user_query": "b", "error": "ValueError",
            "intent_summary": "Unhandled error in the pipeline."}]
    assert _render_cc_turn(build_cc_contexts(log)[0])[0].startswith("- turn 2 (nextseek_query)")

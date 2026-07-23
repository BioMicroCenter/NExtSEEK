from nextseek_api.cc_assistant.ns_turn_context import build_contexts
from nextseek_api.cc_assistant.cc_turn_context import build_cc_contexts
from nextseek_api.cc_assistant.ns_digest import render_within_chat_digest
from nextseek_api.cc_assistant.cc_turn_complete import serialize_cc_chat_log_entry, TurnCompletePayload


# Mixed conversation: turn 1 NS (has a bundle), turn 2 CC (answered).
CHAT_LOG = [
    {"turn_id": 1, "mode": "nextseek_query", "user_query": "find NHP sequencing",
     "status": "completed", "bundle_id": 10},
    {"turn_id": 2, "mode": "cc", "user_query": "count sex of those",
     "assistant_reply": "3 male, 2 female", "status": "completed"},
]
RESULTS_HISTORY = [{"id": 10, "user_query": "find NHP sequencing", "endpoint": "/advanced_search",
                    "method": "POST", "api_result_full": {"ok": True, "data": {"total": 139,
                    "rows": [{"uid": "D.SEQ-1"}]}}, "terminal_reply": "139 records"}]


def test_cc_agent_digest_contains_both_ns_and_cc_prior_turns():
    md = render_within_chat_digest(
        build_contexts(CHAT_LOG, RESULTS_HISTORY, session_id="s"),
        build_cc_contexts(CHAT_LOG))
    assert "turn 1 (bundle 10): find NHP sequencing" in md   # prior NS turn visible
    assert "nextseek-recall --turn 1" in md                   # with its recall affordance
    assert "turn 2 (CC): count sex of those" in md            # prior CC turn NOW visible (the fix)
    assert "3 male, 2 female" in md


def test_cc_turn_writeback_carries_query_and_reply():
    payload = TurnCompletePayload(
        chat_session=None, user_query="count sex of those", assistant_reply="3 male, 2 female",
        ts="t", artifacts=None, cc_traces=[], turn_id="run-uuid", cc_session_id="s", raw_jsonl=b"")
    entry = serialize_cc_chat_log_entry(payload, turn_id=2)
    assert entry["user_query"] == "count sex of those"
    assert entry["assistant_reply"] == "3 male, 2 female"
    assert entry["mode"] == "cc" and entry["status"] == "completed"

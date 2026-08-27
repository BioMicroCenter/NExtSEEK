from nextseek_api.cc_assistant.cc_turn_context import build_cc_contexts, CCTurnContext


def test_build_cc_contexts_projects_answered_cc_turns():
    chat_log = [
        {"turn_id": 1, "mode": "nextseek_query", "user_query": "find NHP", "status": "completed"},
        {"turn_id": 2, "mode": "cc", "user_query": "count those", "assistant_reply": "42 samples",
         "status": "completed", "ts": "t"},
    ]
    ctxs = build_cc_contexts(chat_log)
    assert len(ctxs) == 1
    assert ctxs[0].turn_id == 2 and ctxs[0].route == "cc"
    assert ctxs[0].user_query == "count those" and ctxs[0].reply == "42 samples"


def test_build_cc_contexts_skips_ns_unanswered_and_malformed():
    chat_log = [
        {"turn_id": 3, "mode": "cc", "user_query": "x", "status": "error"},      # not answered
        {"turn_id": "u", "mode": "cc", "user_query": "y", "status": "completed"}, # non-int turn_id
        {"mode": "cc", "user_query": "z", "status": "completed"},                 # no turn_id
        "not a dict",
    ]
    assert build_cc_contexts(chat_log) == []


def test_build_cc_contexts_truncates_long_reply():
    chat_log = [{"turn_id": 1, "mode": "cc", "user_query": "q",
                 "assistant_reply": "x" * 5000, "status": "completed"}]
    ctx = build_cc_contexts(chat_log)[0]
    assert ctx.reply_truncated is True and len(ctx.reply) == 2000

from chat_nextseek.chat_memory import recent_turns, format_for_prompt


def test_ns_history_includes_answered_cc_turns():
    session = {"chat_log": [
        {"turn_id": 1, "mode": "nextseek_query", "user_query": "find NHP", "status": "completed",
         "ts": "t"},
        {"turn_id": 2, "mode": "cc", "user_query": "count those", "assistant_reply": "42",
         "status": "completed", "ts": "t"},
    ]}
    turns = recent_turns(session, n=5)
    assert any(t.get("mode") == "cc" for t in turns)          # CC turn survives the answered filter
    block = format_for_prompt(turns)
    assert "count those" in block                              # and renders into the NS parser context

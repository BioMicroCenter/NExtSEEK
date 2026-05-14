from chat_nextseek.nfcore_wizard import _wizard_builder_history_messages


def _turn(turn_id, mode, user_query, assistant_reply, wizard_step=None):
    t = {
        "turn_id": turn_id,
        "mode": mode,
        "user_query": user_query,
        "assistant_reply_preview": assistant_reply,
    }
    if wizard_step is not None:
        t["wizard_state"] = {"step": wizard_step}
    return t


def test_returns_empty_when_no_chat_log():
    session = {}
    assert _wizard_builder_history_messages(session) == []


def test_returns_empty_when_chat_log_empty():
    session = {"chat_log": []}
    assert _wizard_builder_history_messages(session) == []


def test_filters_to_wizard_mode_turns_only():
    session = {"chat_log": [
        _turn(1, "new_search", "Find NDMA mice", "Found 195 mice."),
        _turn(2, "nfcore_wizard", "Lets build nfcore", "Pick a pipeline.", "pipeline"),
        _turn(3, "nfcore_wizard", "rnaseq", "Great, you picked rnaseq.", "builder"),
    ]}
    out = _wizard_builder_history_messages(session)
    assert out == [
        {"role": "user", "content": "Lets build nfcore"},
        {"role": "assistant", "content": "Pick a pipeline."},
        {"role": "user", "content": "rnaseq"},
        {"role": "assistant", "content": "Great, you picked rnaseq."},
    ]


def test_skips_turns_missing_user_query_or_reply():
    session = {"chat_log": [
        _turn(1, "nfcore_wizard", "", "stray reply", "builder"),
        _turn(2, "nfcore_wizard", "stray user", "", "builder"),
        _turn(3, "nfcore_wizard", "real user", "real reply", "builder"),
    ]}
    out = _wizard_builder_history_messages(session)
    assert out == [
        {"role": "user", "content": "real user"},
        {"role": "assistant", "content": "real reply"},
    ]


def test_caps_at_last_10_wizard_turns():
    turns = [_turn(i, "nfcore_wizard", f"q{i}", f"r{i}", "builder") for i in range(1, 16)]
    session = {"chat_log": turns}
    out = _wizard_builder_history_messages(session)
    assert len(out) == 20
    assert out[0] == {"role": "user", "content": "q6"}
    assert out[-1] == {"role": "assistant", "content": "r15"}


def test_handles_non_list_chat_log_defensively():
    session = {"chat_log": "not-a-list"}
    assert _wizard_builder_history_messages(session) == []

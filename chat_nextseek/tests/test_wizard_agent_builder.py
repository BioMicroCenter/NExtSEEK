from unittest.mock import MagicMock, patch

import pytest

from chat_nextseek.agents import (
    MAX_TOOL_ITERATIONS,
    WizardToolLoopError,
    _wizard_agent_builder,
)


def _resp(stop_reason, blocks):
    return {"stop_reason": stop_reason, "content": blocks}


def _config_with_mock_client(client):
    config = MagicMock()
    config.WIZARD_AGENT_SYSTEM_PROMPT = "(builder system prompt)"
    config.get_agent_model = MagicMock(return_value=(client, "us.anthropic.claude-opus-4-6-v1", None))
    return config


def test_single_tool_call_then_finalize_returns_wizard_agent_output():
    """LLM calls memory_query then finalize_turn — output reflects the
    finalize_turn payload."""
    session = {
        "results_history": [{"bundle_id": 7}],
        "nfcore_wizard": {
            "active": True, "step": "builder",
            "pipeline": "rnaseq",
            "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []},
            "pinned_context": {"bundle_id": 7},
        },
    }
    client = MagicMock()
    config = _config_with_mock_client(client)
    client.chat_with_tools = MagicMock(side_effect=[
        _resp("tool_use", [
            {"type": "tool_use", "id": "t1", "name": "memory_query",
             "input": {"question": "list every UID, JSON array, no other text"}},
        ]),
        _resp("tool_use", [
            {"type": "tool_use", "id": "t2", "name": "finalize_turn",
             "input": {"action": "stay",
                       "selection_updates": {"uids": ["U1", "U2"]},
                       "reply": "Loaded 2 samples."}},
        ]),
    ])
    with patch("chat_nextseek.builder_tools.dispatch_tool_call",
               return_value="[\"U1\", \"U2\"]") as disp:
        out = _wizard_agent_builder(
            config=config, session=session, user_text="use my last search",
        )

    assert out.action == "stay"
    assert out.selection_updates == {"uids": ["U1", "U2"]}
    assert out.reply == "Loaded 2 samples."
    assert disp.call_count == 1
    # First dispatch must have been memory_query, NOT finalize_turn.
    dispatched_name = disp.call_args.kwargs["tool_name"]
    assert dispatched_name == "memory_query"


def test_finalize_only_in_first_response_returns_immediately():
    """If the LLM calls finalize_turn as its first/only tool, loop exits without
    calling any other tools."""
    session = {"nfcore_wizard": {"active": True, "step": "builder",
                                 "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []}}}
    client = MagicMock()
    config = _config_with_mock_client(client)
    client.chat_with_tools = MagicMock(return_value=_resp("tool_use", [
        {"type": "tool_use", "id": "tF", "name": "finalize_turn",
         "input": {"action": "stay", "selection_updates": {}, "reply": "ack"}},
    ]))

    with patch("chat_nextseek.builder_tools.dispatch_tool_call") as disp:
        out = _wizard_agent_builder(
            config=config, session=session, user_text="hi",
        )
    assert out.action == "stay"
    assert out.reply == "ack"
    disp.assert_not_called()


def test_loop_cap_raises_wizard_tool_loop_error():
    """If LLM hits MAX_TOOL_ITERATIONS without finalize_turn, raise."""
    session = {"nfcore_wizard": {"active": True, "step": "builder",
                                 "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []}}}
    client = MagicMock()
    config = _config_with_mock_client(client)
    client.chat_with_tools = MagicMock(return_value=_resp("tool_use", [
        {"type": "tool_use", "id": "tX", "name": "memory_query",
         "input": {"question": "anything"}},
    ]))

    with patch("chat_nextseek.builder_tools.dispatch_tool_call", return_value="some result"):
        with pytest.raises(WizardToolLoopError):
            _wizard_agent_builder(
                config=config, session=session, user_text="hi",
            )


def test_advance_action_propagated():
    """Build-intent finalize_turn returns action='advance' and the summary reply."""
    session = {"nfcore_wizard": {"active": True, "step": "builder",
                                 "selection": {"uids": ["U1"], "cohort_criteria": [], "enrichment_fields": []}}}
    client = MagicMock()
    config = _config_with_mock_client(client)
    client.chat_with_tools = MagicMock(return_value=_resp("tool_use", [
        {"type": "tool_use", "id": "t1", "name": "finalize_turn",
         "input": {"action": "advance", "selection_updates": {},
                   "reply": "Building: rnaseq, 1 UID. Confirm?"}},
    ]))

    out = _wizard_agent_builder(
        config=config, session=session, user_text="build it",
    )
    assert out.action == "advance"
    assert "Confirm" in out.reply


def test_max_tool_iterations_is_five():
    assert MAX_TOOL_ITERATIONS == 5


def test_no_chat_with_tools_method_raises_helpful_error():
    """If the resolved client lacks chat_with_tools, _wizard_agent_builder
    must raise a clear error rather than failing with AttributeError."""
    session = {"nfcore_wizard": {"active": True, "step": "builder",
                                 "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []}}}
    client = MagicMock(spec=[])   # no chat_with_tools attribute
    config = _config_with_mock_client(client)

    with pytest.raises(WizardToolLoopError, match="(?i)chat_with_tools"):
        _wizard_agent_builder(
            config=config, session=session, user_text="hi",
        )


def test_history_messages_are_prepended_before_current_user_text():
    """When history_messages is passed, _wizard_agent_builder prepends them
    in order, then appends the current turn's user message."""
    session = {"nfcore_wizard": {"active": True, "step": "builder",
                                 "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []}}}
    client = MagicMock()
    config = _config_with_mock_client(client)
    captured = {}

    def fake_chat_with_tools(*, messages, tools, system, model):
        captured["messages"] = list(messages)
        return _resp("tool_use", [
            {"type": "tool_use", "id": "tF", "name": "finalize_turn",
             "input": {"action": "stay", "selection_updates": {}, "reply": "ack"}},
        ])

    client.chat_with_tools = MagicMock(side_effect=fake_chat_with_tools)

    history = [
        {"role": "user", "content": "rnaseq"},
        {"role": "assistant", "content": "Great, you picked rnaseq."},
        {"role": "user", "content": "what fields exist?"},
        {"role": "assistant", "content": "Cohort, Sex, Genotype, ..."},
    ]

    with patch("chat_nextseek.builder_tools.dispatch_tool_call"):
        out = _wizard_agent_builder(
            config=config, session=session,
            user_text="filter by Cohort",
            history_messages=history,
        )

    assert out.action == "stay"
    sent = captured["messages"]
    assert len(sent) == 5
    assert sent[0] == {"role": "user", "content": "rnaseq"}
    assert sent[3] == {"role": "assistant", "content": "Cohort, Sex, Genotype, ..."}
    assert sent[4] == {"role": "user", "content": "filter by Cohort"}


def test_history_messages_default_none_preserves_current_behavior():
    """When history_messages is omitted, behavior is identical to before:
    messages starts with just the current user_text."""
    session = {"nfcore_wizard": {"active": True, "step": "builder",
                                 "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []}}}
    client = MagicMock()
    config = _config_with_mock_client(client)
    captured = {}

    def fake_chat_with_tools(*, messages, tools, system, model):
        captured["messages"] = list(messages)
        return _resp("tool_use", [
            {"type": "tool_use", "id": "tF", "name": "finalize_turn",
             "input": {"action": "stay", "selection_updates": {}, "reply": "ack"}},
        ])

    client.chat_with_tools = MagicMock(side_effect=fake_chat_with_tools)
    with patch("chat_nextseek.builder_tools.dispatch_tool_call"):
        _wizard_agent_builder(
            config=config, session=session, user_text="hi",
        )

    assert captured["messages"] == [{"role": "user", "content": "hi"}]


def test_empty_history_messages_list_is_equivalent_to_none():
    """history_messages=[] should behave the same as omitting it."""
    session = {"nfcore_wizard": {"active": True, "step": "builder",
                                 "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []}}}
    client = MagicMock()
    config = _config_with_mock_client(client)
    captured = {}

    def fake_chat_with_tools(*, messages, tools, system, model):
        captured["messages"] = list(messages)
        return _resp("tool_use", [
            {"type": "tool_use", "id": "tF", "name": "finalize_turn",
             "input": {"action": "stay", "selection_updates": {}, "reply": "ack"}},
        ])

    client.chat_with_tools = MagicMock(side_effect=fake_chat_with_tools)
    with patch("chat_nextseek.builder_tools.dispatch_tool_call"):
        _wizard_agent_builder(
            config=config, session=session, user_text="hi",
            history_messages=[],
        )

    assert captured["messages"] == [{"role": "user", "content": "hi"}]

def test_chat_with_tools_attribute_not_callable_raises():
    """A client whose chat_with_tools attribute is not callable should
    raise WizardToolLoopError, not crash later."""
    session = {"nfcore_wizard": {"active": True, "step": "builder",
                                 "selection": {"uids": [], "cohort_criteria": [],
                                               "enrichment_fields": []}}}
    client = MagicMock(spec=[])
    client.chat_with_tools = "not a function"   # non-callable attribute

    config = MagicMock()
    config.WIZARD_AGENT_SYSTEM_PROMPT = "(prompt)"
    config.get_agent_model = MagicMock(return_value=(client, "model", None))

    with pytest.raises(WizardToolLoopError, match="(?i)chat_with_tools"):
        _wizard_agent_builder(
            config=config, session=session, user_text="hi",
        )


def test_end_turn_with_text_synthesizes_finalize_turn_stay():
    """When the LLM stops with stop_reason='end_turn' and produces a text
    block (instead of calling finalize_turn), treat it as an implicit
    finalize_turn(action='stay', reply=<text>). The chat_log only stores
    reply previews, so replayed history naturally trains the LLM to skip
    the finalize_turn wrapper — we accept that."""
    session = {"nfcore_wizard": {"active": True, "step": "builder",
                                 "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []}}}
    client = MagicMock()
    config = _config_with_mock_client(client)
    client.chat_with_tools = MagicMock(side_effect=[
        _resp("tool_use", [
            {"type": "tool_use", "id": "t1", "name": "memory_query",
             "input": {"question": "what fields exist?"}},
        ]),
        _resp("end_turn", [
            {"type": "text", "text": "Here are the 29 fields: ..."},
        ]),
    ])
    with patch("chat_nextseek.builder_tools.dispatch_tool_call",
               return_value="(field summary)"):
        out = _wizard_agent_builder(
            config=config, session=session, user_text="what fields exist?",
        )

    assert out.action == "stay"
    assert out.selection_updates == {}
    assert out.reply == "Here are the 29 fields: ..."


def test_end_turn_without_text_still_raises():
    """end_turn with no text blocks should still raise — nothing to use as reply."""
    session = {"nfcore_wizard": {"active": True, "step": "builder",
                                 "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []}}}
    client = MagicMock()
    config = _config_with_mock_client(client)
    client.chat_with_tools = MagicMock(return_value=_resp("end_turn", []))

    with pytest.raises(WizardToolLoopError, match="finalize_turn"):
        _wizard_agent_builder(
            config=config, session=session, user_text="hi",
        )


def test_stop_reason_max_tokens_still_raises():
    """A non-end_turn stop_reason (max_tokens, stop_sequence) still raises
    — we only treat end_turn as implicit finalize_turn."""
    session = {"nfcore_wizard": {"active": True, "step": "builder",
                                 "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []}}}
    client = MagicMock()
    config = _config_with_mock_client(client)
    client.chat_with_tools = MagicMock(return_value=_resp(
        "max_tokens", [{"type": "text", "text": "truncated reply"}],
    ))

    with pytest.raises(WizardToolLoopError, match="max_tokens"):
        _wizard_agent_builder(
            config=config, session=session, user_text="hi",
        )

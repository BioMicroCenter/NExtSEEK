from unittest.mock import MagicMock

from chat_nextseek.llm_clients import BedrockClient


def _stub_converse_response(stop_reason: str, content_blocks: list) -> dict:
    """Mimic a Bedrock Converse API response."""
    return {
        "stopReason": stop_reason,
        "output": {"message": {"role": "assistant", "content": content_blocks}},
        "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
    }


def _bedrock_client_with_mock_converse(converse_return_value):
    client = BedrockClient.__new__(BedrockClient)
    client.client = MagicMock()
    client.max_output_tokens = 4096
    client.client.converse = MagicMock(return_value=converse_return_value)
    return client


def test_chat_with_tools_returns_normalized_tool_use_block():
    """A tool_use response from Converse API is normalized to anthropic-style."""
    converse_resp = _stub_converse_response(
        "tool_use",
        [
            {"text": "Let me check."},
            {"toolUse": {"toolUseId": "t1", "name": "memory_query",
                         "input": {"question": "how many?"}}},
        ],
    )
    client = _bedrock_client_with_mock_converse(converse_resp)

    result = client.chat_with_tools(
        model="us.anthropic.claude-opus-4-7",
        messages=[{"role": "user", "content": "how many samples?"}],
        tools=[{"name": "memory_query", "description": "ask memory",
                "input_schema": {"type": "object",
                                 "properties": {"question": {"type": "string"}},
                                 "required": ["question"]}}],
        system="you are a wizard",
    )

    assert result["stop_reason"] == "tool_use"
    # The two content blocks are normalized to anthropic-style:
    assert result["content"][0] == {"type": "text", "text": "Let me check."}
    assert result["content"][1] == {
        "type": "tool_use", "id": "t1", "name": "memory_query",
        "input": {"question": "how many?"},
    }


def test_chat_with_tools_returns_normalized_text_block_when_done():
    """An end_turn response is normalized to text-only content."""
    converse_resp = _stub_converse_response(
        "end_turn",
        [{"text": "Here is the answer."}],
    )
    client = _bedrock_client_with_mock_converse(converse_resp)

    result = client.chat_with_tools(
        model="us.anthropic.claude-opus-4-7",
        messages=[{"role": "user", "content": "go"}],
        tools=[],
        system="sys",
    )

    assert result["stop_reason"] == "end_turn"
    assert result["content"] == [{"type": "text", "text": "Here is the answer."}]


def test_chat_with_tools_passes_tools_to_converse_in_toolconfig_shape():
    """Verify tools are forwarded as toolConfig.tools in Bedrock shape."""
    converse_resp = _stub_converse_response("end_turn", [{"text": "ok"}])
    client = _bedrock_client_with_mock_converse(converse_resp)

    client.chat_with_tools(
        model="us.anthropic.claude-opus-4-7",
        messages=[{"role": "user", "content": "go"}],
        tools=[
            {"name": "tool_a", "description": "A",
             "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}}},
        ],
        system="sys",
    )

    call_kwargs = client.client.converse.call_args.kwargs
    assert "toolConfig" in call_kwargs
    tool_specs = call_kwargs["toolConfig"]["tools"]
    assert len(tool_specs) == 1
    assert tool_specs[0]["toolSpec"]["name"] == "tool_a"
    assert tool_specs[0]["toolSpec"]["description"] == "A"
    # input_schema is wrapped in {"json": ...} for Bedrock:
    assert tool_specs[0]["toolSpec"]["inputSchema"]["json"]["type"] == "object"


def test_chat_with_tools_forwards_messages_in_converse_shape():
    """User text messages get wrapped to Converse content-block shape."""
    converse_resp = _stub_converse_response("end_turn", [{"text": "ok"}])
    client = _bedrock_client_with_mock_converse(converse_resp)

    client.chat_with_tools(
        model="us.anthropic.claude-opus-4-7",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        system="sys",
    )

    call_kwargs = client.client.converse.call_args.kwargs
    assert call_kwargs["messages"] == [
        {"role": "user", "content": [{"text": "hello"}]},
    ]
    assert call_kwargs["system"] == [{"text": "sys"}]


def test_chat_with_tools_handles_tool_use_only_response_no_text_block():
    """Some turns return only tool_use blocks with no preceding text."""
    converse_resp = _stub_converse_response(
        "tool_use",
        [{"toolUse": {"toolUseId": "t9", "name": "refine_last_search",
                      "input": {"filters": {"sampletype_code": "TIS"}}}}],
    )
    client = _bedrock_client_with_mock_converse(converse_resp)

    result = client.chat_with_tools(
        model="us.anthropic.claude-opus-4-7",
        messages=[{"role": "user", "content": "only tumor"}],
        tools=[],
        system="sys",
    )

    assert result["stop_reason"] == "tool_use"
    assert result["content"] == [
        {"type": "tool_use", "id": "t9", "name": "refine_last_search",
         "input": {"filters": {"sampletype_code": "TIS"}}}
    ]


def test_chat_with_tools_handles_multiple_tool_use_blocks_in_one_response():
    """Anthropic models can emit multiple tool_use blocks in a single response."""
    converse_resp = _stub_converse_response(
        "tool_use",
        [
            {"toolUse": {"toolUseId": "t1", "name": "memory_query",
                         "input": {"question": "count?"}}},
            {"toolUse": {"toolUseId": "t2", "name": "refine_last_search",
                         "input": {"filters": {}}}},
        ],
    )
    client = _bedrock_client_with_mock_converse(converse_resp)

    result = client.chat_with_tools(
        model="us.anthropic.claude-opus-4-7",
        messages=[{"role": "user", "content": "do two things"}],
        tools=[],
        system="sys",
    )

    assert result["stop_reason"] == "tool_use"
    assert len(result["content"]) == 2
    assert result["content"][0]["id"] == "t1"
    assert result["content"][1]["id"] == "t2"


def test_chat_with_tools_omits_toolconfig_when_tools_empty():
    """When `tools=[]`, the Converse call must NOT include a toolConfig key."""
    converse_resp = _stub_converse_response("end_turn", [{"text": "ok"}])
    client = _bedrock_client_with_mock_converse(converse_resp)

    client.chat_with_tools(
        model="us.anthropic.claude-opus-4-7",
        messages=[{"role": "user", "content": "go"}],
        tools=[],
        system="sys",
    )

    call_kwargs = client.client.converse.call_args.kwargs
    assert "toolConfig" not in call_kwargs


def test_chat_with_tools_translates_anthropic_tool_use_block_to_converse_shape():
    """Assistant message with anthropic-native tool_use block must be
    re-wrapped to Bedrock Converse's `toolUse` shape on the way IN."""
    converse_resp = _stub_converse_response("end_turn", [{"text": "ok"}])
    client = _bedrock_client_with_mock_converse(converse_resp)

    client.chat_with_tools(
        model="us.anthropic.claude-opus-4-7",
        messages=[
            {"role": "user", "content": "use memory"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "memory_query",
                 "input": {"question": "how many?"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "There are 195 mice."},
            ]},
        ],
        tools=[],
        system="sys",
    )

    sent = client.client.converse.call_args.kwargs["messages"]
    # First message: plain user text → wrapped to [{"text": ...}]
    assert sent[0] == {"role": "user", "content": [{"text": "use memory"}]}
    # Second message: anthropic tool_use → Bedrock toolUse
    assert sent[1] == {
        "role": "assistant",
        "content": [
            {"toolUse": {"toolUseId": "t1", "name": "memory_query",
                         "input": {"question": "how many?"}}},
        ],
    }
    # Third message: anthropic tool_result → Bedrock toolResult
    assert sent[2] == {
        "role": "user",
        "content": [
            {"toolResult": {"toolUseId": "t1",
                            "content": [{"text": "There are 195 mice."}]}},
        ],
    }


def test_chat_with_tools_translates_mixed_text_and_tool_use_in_assistant_message():
    """Assistant content can mix text + tool_use blocks. Both must translate."""
    converse_resp = _stub_converse_response("end_turn", [{"text": "ok"}])
    client = _bedrock_client_with_mock_converse(converse_resp)

    client.chat_with_tools(
        model="us.anthropic.claude-opus-4-7",
        messages=[
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "t2", "name": "tool_a", "input": {"x": 1}},
            ]},
        ],
        tools=[],
        system="sys",
    )

    sent = client.client.converse.call_args.kwargs["messages"]
    assert sent[1]["content"] == [
        {"text": "Let me check."},
        {"toolUse": {"toolUseId": "t2", "name": "tool_a", "input": {"x": 1}}},
    ]


def test_chat_with_tools_passes_through_already_bedrock_shaped_blocks():
    """If a caller passes blocks already in Bedrock shape (toolUse/toolResult
    without the anthropic 'type' wrapper), pass through unchanged."""
    converse_resp = _stub_converse_response("end_turn", [{"text": "ok"}])
    client = _bedrock_client_with_mock_converse(converse_resp)

    client.chat_with_tools(
        model="us.anthropic.claude-opus-4-7",
        messages=[
            {"role": "user", "content": [
                {"text": "already bedrock-shaped text block"},
            ]},
            {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "t3", "name": "tool_b", "input": {}}},
            ]},
        ],
        tools=[],
        system="sys",
    )

    sent = client.client.converse.call_args.kwargs["messages"]
    assert sent[0]["content"] == [{"text": "already bedrock-shaped text block"}]
    assert sent[1]["content"] == [
        {"toolUse": {"toolUseId": "t3", "name": "tool_b", "input": {}}},
    ]

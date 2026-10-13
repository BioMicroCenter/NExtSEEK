"""What the NS Bedrock client sends Opus 4.7 is pinned byte for byte (model switch for run 2, 2026-09-28).

Run 1 runs on Opus 4.7. The switch teaches ``BedrockClient`` the Opus 5.5 family (always thinking, no sampling
parameters, no forced tool), and none of that may change an Opus 4.7 request: run 1 and run 2 must differ only in
the model. Each case below is a request shape run 1 really sends: the parser's forced tool call at high thinking,
the plain structured retry, a report writer's plain call, a no-thinking Opus call (planner), and a tool-loop step
with a replayed history and cache points (follow-up, pipeline agent), first try and moved call.

The expected bodies were captured from the client BEFORE the switch (795deeb3) and are compared as serialised JSON,
key order included: botocore serialises the Converse body in the order the dict holds.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from chat_nextseek.llm_clients import BedrockClient

OPUS47 = "us.anthropic.claude-opus-4-7"

SCHEMA = {"type": "object", "properties": {"mode": {"type": "string"}}, "required": ["mode"],
          "additionalProperties": False}
TOOL = {"name": "run_new_query", "description": "Run a query.",
        "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}}}
HISTORY = [
    {"role": "user", "content": "The user's follow-up question: which have RNA?"},
    {"role": "assistant", "content": [
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "t1", "name": "run_new_query", "input": {"question": "which have RNA"}},
    ]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{\"ok\": true}"}]},
]


def _client():
    client = BedrockClient.__new__(BedrockClient)
    client.client = MagicMock()
    client.max_output_tokens = 4096
    client.client.converse = MagicMock(return_value={
        "stopReason": "end_turn",
        "output": {"message": {"role": "assistant", "content": [{"text": "{\"mode\": \"graph_query\"}"}]}},
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
    })
    return client


def _sent(client) -> str:
    (call,) = client.client.converse.call_args_list
    return json.dumps(call.kwargs)


def _parser_forced_tool(client):
    client.chat_structured(messages=[{"role": "user", "content": "Q"}], system="SYS", model=OPUS47,
                           schema=SCHEMA, schema_name="emit_parser_plan", temperature=0, thinking_budget=16000)


def _plain_high(client):
    client.chat(messages=[{"role": "system", "content": "SYS"}, {"role": "user", "content": "Q"}],
                model=OPUS47, temperature=0, response_format={"type": "json_object"}, thinking_budget=16000)


def _plain_medium(client):
    client.chat(messages=[{"role": "system", "content": "SYS"}, {"role": "user", "content": "Q"}],
                model=OPUS47, temperature=0, thinking_budget=8000)


def _plain_low(client):
    client.chat(messages=[{"role": "user", "content": "Q"}], model=OPUS47, temperature=0, thinking_budget=4000)


def _plain_no_thinking(client):
    client.chat(messages=[{"role": "system", "content": "SYS"}, {"role": "user", "content": "Q"}],
                model=OPUS47, temperature=0)


def _loop_step_cached(client):
    client.chat_with_tools(messages=HISTORY, tools=[TOOL], system="SYS", model=OPUS47, temperature=0.0,
                           cache_prompt=True, cache_ttl="1h")


def _loop_step_moved(client):
    client.chat_with_tools(messages=HISTORY, tools=[TOOL], system="SYS", model=OPUS47, temperature=0.0,
                           cache_prompt=False)


_MSGS = [{"role": "user", "content": [{"text": "Q"}]}]
_LOOP_MSGS = [
    {"role": "user", "content": [{"text": "The user's follow-up question: which have RNA?"}]},
    {"role": "assistant", "content": [
        {"text": "Checking."},
        {"toolUse": {"toolUseId": "t1", "name": "run_new_query", "input": {"question": "which have RNA"}}},
    ]},
    {"role": "user", "content": [{"toolResult": {"toolUseId": "t1", "content": [{"text": "{\"ok\": true}"}]}}]},
]
_TOOL_SPEC = {"toolSpec": {"name": "run_new_query", "description": "Run a query.",
                           "inputSchema": {"json": TOOL["input_schema"]}}}
_CACHE = {"cachePoint": {"type": "default", "ttl": "1h"}}

EXPECTED = {
    "parser_forced_tool": {
        "modelId": OPUS47, "messages": _MSGS, "system": [{"text": "SYS"}],
        "inferenceConfig": {"maxTokens": 20096},
        "additionalModelRequestFields": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}},
        "toolConfig": {"tools": [{"toolSpec": {"name": "emit_parser_plan",
                                               "description": "Return the result as emit_parser_plan.",
                                               "inputSchema": {"json": SCHEMA}}}],
                       "toolChoice": {"tool": {"name": "emit_parser_plan"}}},
    },
    "plain_high": {
        "modelId": OPUS47, "messages": _MSGS, "inferenceConfig": {"maxTokens": 20096},
        "system": [{"text": "SYS"}],
        "additionalModelRequestFields": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}},
    },
    "plain_medium": {
        "modelId": OPUS47, "messages": _MSGS, "inferenceConfig": {"maxTokens": 12096},
        "system": [{"text": "SYS"}],
        "additionalModelRequestFields": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}},
    },
    "plain_low": {
        "modelId": OPUS47, "messages": _MSGS, "inferenceConfig": {"maxTokens": 8096},
        "additionalModelRequestFields": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}},
    },
    "plain_no_thinking": {
        "modelId": OPUS47, "messages": _MSGS, "inferenceConfig": {"maxTokens": 4096},
        "system": [{"text": "SYS"}],
    },
    "loop_step_cached": {
        "modelId": OPUS47, "messages": _LOOP_MSGS, "system": [{"text": "SYS"}, _CACHE],
        "inferenceConfig": {"maxTokens": 4096},
        "toolConfig": {"tools": [_TOOL_SPEC, _CACHE]},
    },
    "loop_step_moved": {
        "modelId": OPUS47, "messages": _LOOP_MSGS, "system": [{"text": "SYS"}],
        "inferenceConfig": {"maxTokens": 4096},
        "toolConfig": {"tools": [_TOOL_SPEC]},
    },
}

CASES = {
    "parser_forced_tool": _parser_forced_tool,
    "plain_high": _plain_high,
    "plain_medium": _plain_medium,
    "plain_low": _plain_low,
    "plain_no_thinking": _plain_no_thinking,
    "loop_step_cached": _loop_step_cached,
    "loop_step_moved": _loop_step_moved,
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_an_opus_4_7_request_is_byte_identical_to_run_1(case):
    client = _client()
    CASES[case](client)
    assert _sent(client) == json.dumps(EXPECTED[case])


def test_the_history_the_caller_holds_is_not_changed_by_a_call():
    before = json.dumps(HISTORY)
    _loop_step_cached(_client())
    assert json.dumps(HISTORY) == before

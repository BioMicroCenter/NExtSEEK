"""The NS Bedrock client and the always-thinking Claude family (model switch for run 2, 2026-09-28).

Run 2 moves the NS Opus agents to Claude Opus 5.5 on Bedrock. Per the claude-api reference ("Thinking & Effort",
"Migrating to Claude Opus 5.5"), Opus 5.5 refuses with a 400: ``temperature``/``top_p``/``top_k``, thinking with
``budget_tokens``, thinking ``disabled``, and a forced ``tool_choice`` (``any`` or a named tool). Its thinking is
always on; effort is the only control, and its default is ``medium``. Pinned here:

* the capability helper names the families (``model_traits``);
* every Opus 5.5 request carries adaptive thinking and an explicit effort, the catalog level mapped straight to it
  (no level means ``low``), no sampling parameter, and a ``maxTokens`` with room for the thinking;
* a structured call on Opus 5.5 never sends a forced tool: it goes straight to the plain JSON path, and the ledger
  says so (``structured_via`` ``prompt``), while ``reasoning_present`` still reads the response;
* a forced tool asked of a model that refuses one fails before any request is sent.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from chat_nextseek.llm_clients import BedrockClient, LLMStructuredUnsupportedError, model_traits

OPUS55 = "us.anthropic.claude-opus-5-5"


# ---------------------------------------------------------------------------- the families


@pytest.mark.parametrize("model, adaptive_only, always_thinks, forced_tool_ok", [
    # Opus 4.7 and 4.8: adaptive is the only thinking mode, no sampling, but no thinking unless asked.
    ("us.anthropic.claude-opus-4-7", True, False, True),
    ("us.anthropic.claude-opus-4-8", True, False, True),
    ("claude-opus-4-7", True, False, True),
    # Opus 5 thinks when thinking is left out; Opus 5.5 cannot stop, and refuses a forced tool.
    ("anthropic.claude-opus-5", True, True, True),
    (OPUS55, True, True, False),
    ("anthropic.claude-opus-5-5", True, True, False),
    ("claude-opus-5-5", True, True, False),
    # Fable and Mythos always think; their 5.1 releases refuse a forced tool.
    ("claude-fable-5", True, True, True),
    ("claude-fable-5-1", True, True, False),
    ("claude-mythos-5-1", True, True, False),
    ("claude-mythos-preview", True, True, True),
    # Sonnet 5 is adaptive and thinks by default; Sonnet 4.6 and older take sampling and budgets.
    ("claude-sonnet-5", True, True, True),
    ("us.anthropic.claude-sonnet-4-6", False, False, True),
    ("anthropic.claude-sonnet-4-5-20250929-v1:0", False, False, True),
    ("anthropic.claude-opus-4-5-20251101-v1:0", False, False, True),
    ("us.anthropic.claude-haiku-4-5-20251001-v1:0", False, False, True),
    # Not Claude.
    ("deepseek.v3.2", False, False, True),
    ("gemini-3.8-flash", False, False, True),
    ("", False, False, True),
    (None, False, False, True),
])
def test_the_families_by_what_a_request_may_carry(model, adaptive_only, always_thinks, forced_tool_ok):
    traits = model_traits(model)
    assert (traits.adaptive_only, traits.always_thinks, traits.forced_tool_ok) == \
        (adaptive_only, always_thinks, forced_tool_ok)


# ---------------------------------------------------------------------------- request bodies


def _client(content=None):
    client = BedrockClient.__new__(BedrockClient)
    client.client = MagicMock()
    client.max_output_tokens = 4096
    client.client.converse = MagicMock(return_value={
        "stopReason": "end_turn",
        "output": {"message": {"role": "assistant", "content": content or [
            {"reasoningContent": {"reasoningText": {"text": "", "signature": "sig-1"}}},
            {"text": "{\"mode\": \"graph_query\"}"},
        ]}},
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
    })
    return client


def _body(client) -> dict:
    (call,) = client.client.converse.call_args_list
    return call.kwargs


# The catalog's level, as the budget ChatConfig hands the client, and the effort and ceiling it must become.
LEVELS = [
    (None, "low", 8192),
    (4000, "low", 8192),
    (8000, "medium", 16384),
    (16000, "high", 32768),
]


@pytest.mark.parametrize("budget, effort, max_tokens", LEVELS)
def test_a_plain_opus_5_5_call_carries_adaptive_thinking_an_effort_and_no_sampling(budget, effort, max_tokens):
    client = _client()
    client.chat(messages=[{"role": "system", "content": "S"}, {"role": "user", "content": "Q"}], model=OPUS55,
                temperature=0, response_format={"type": "json_object"}, thinking_budget=budget)
    body = _body(client)
    assert body["inferenceConfig"] == {"maxTokens": max_tokens}
    assert body["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"},
                                                   "output_config": {"effort": effort}}
    assert "toolConfig" not in body


@pytest.mark.parametrize("budget, effort, max_tokens", LEVELS)
def test_an_opus_5_5_tool_loop_step_carries_the_same(budget, effort, max_tokens):
    client = _client([{"text": "done"}])
    client.chat_with_tools(messages=[{"role": "user", "content": "Q"}], model=OPUS55, system="S", temperature=0.0,
                           tools=[{"name": "answer", "description": "", "input_schema": {"type": "object"}}],
                           thinking_budget=budget)
    body = _body(client)
    assert body["inferenceConfig"] == {"maxTokens": max_tokens}
    assert body["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"},
                                                   "output_config": {"effort": effort}}
    assert "toolChoice" not in body["toolConfig"]


def test_a_caller_ceiling_above_the_thinking_room_is_kept():
    client = _client([{"text": "done"}])
    client.chat_with_tools(messages=[{"role": "user", "content": "Q"}], model=OPUS55, system="S", tools=[],
                           max_tokens=50000, thinking_budget=4000)
    assert _body(client)["inferenceConfig"] == {"maxTokens": 50000}


def test_tool_choice_auto_is_still_sent_to_opus_5_5():
    client = _client([{"text": "done"}])
    client.chat_with_tools(messages=[{"role": "user", "content": "Q"}], model=OPUS55, system="S",
                           tools=[{"name": "answer", "description": "", "input_schema": {"type": "object"}}],
                           tool_choice="auto")
    assert _body(client)["toolConfig"]["toolChoice"] == {"auto": {}}


@pytest.mark.parametrize("choice", ["any", "answer", {"tool": {"name": "answer"}}, {"any": {}}])
def test_a_forced_tool_is_refused_for_opus_5_5_before_any_request(choice):
    client = _client()
    with pytest.raises(LLMStructuredUnsupportedError):
        client.chat_with_tools(messages=[{"role": "user", "content": "Q"}], model=OPUS55, system="S",
                               tools=[{"name": "answer", "description": "", "input_schema": {"type": "object"}}],
                               tool_choice=choice)
    client.client.converse.assert_not_called()


def test_chat_structured_on_opus_5_5_sends_nothing_and_says_why():
    client = _client()
    with pytest.raises(LLMStructuredUnsupportedError, match="forced tool"):
        client.chat_structured(messages=[{"role": "user", "content": "Q"}], system="S", model=OPUS55,
                               schema={"type": "object"}, schema_name="emit_x", thinking_budget=8000)
    client.client.converse.assert_not_called()


def test_sonnet_4_6_still_gets_its_temperature_and_no_thinking_field():
    client = _client([{"text": "{}"}])
    client.chat(messages=[{"role": "user", "content": "Q"}], model="us.anthropic.claude-sonnet-4-6", temperature=0)
    body = _body(client)
    assert body["inferenceConfig"] == {"maxTokens": 4096, "temperature": 0}
    assert "additionalModelRequestFields" not in body


# ---------------------------------------------------------------------------- the structured path


class _Plan(BaseModel):
    mode: str = "unsupported"


class _Cfg:
    LOG_DIR = None
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None, "low": 4000, "medium": 8000, "high": 16000}
    AGENT_MODEL_CATALOG: dict = {}

    def __init__(self, client, log_dir=None):
        self.LOG_DIR = log_dir
        self.LLM_CLIENT = client
        self.LLM_MODEL = OPUS55
        self.LLM_CLIENTS = {"anth": client}


def test_a_structured_opus_5_5_call_goes_straight_to_plain_json_and_the_ledger_says_so(tmp_path):
    import json

    from chat_nextseek.schemas.schema_helper import call_llm_structured

    client = _client()
    plan = call_llm_structured(_Cfg(client, log_dir=str(tmp_path)), "Q", _Plan, system="S", client=client,
                               model_name=OPUS55, agent_label="parser", thinking_budget=8000)

    assert plan.mode == "graph_query"
    body = _body(client)  # one request, and it is the plain one
    assert "toolConfig" not in body
    assert body["additionalModelRequestFields"]["output_config"] == {"effort": "medium"}
    (record,) = [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]
    assert record["model"] == OPUS55 and record["outcome"] == "ok"
    assert record["structured_via"] == "prompt"
    assert record["reasoning_present"] is True


def test_a_structured_opus_4_7_call_still_forces_its_tool(tmp_path):
    from chat_nextseek.schemas.schema_helper import call_llm_structured

    client = _client([{"toolUse": {"toolUseId": "t", "name": "emit_plan", "input": {"mode": "graph_query"}}}])
    call_llm_structured(_Cfg(client, log_dir=str(tmp_path)), "Q", _Plan, system="S", client=client,
                        model_name="us.anthropic.claude-opus-4-7", agent_label="parser", thinking_budget=16000)
    assert "toolChoice" in _body(client)["toolConfig"]

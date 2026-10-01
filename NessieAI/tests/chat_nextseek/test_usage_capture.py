"""The clients record every token field a price needs, and the ledger keeps them.

Pricing a call (``chat_nextseek.model_prices``) needs more than the three counts the
clients used to keep:

* Gemini: ``thoughts_token_count`` (thinking, billed as output and NOT inside
  ``candidates_token_count``) and ``cached_content_token_count`` (the part of the prompt
  read from the cache, billed at the cache rate). Both were dropped.
* Bedrock: the cache TTL a call asked for, so a write is priced at the 5-minute or the
  1-hour rate, and ``cacheDetails`` (the writes per TTL) when Bedrock reports it.

The ledger (``llm_calls.jsonl``) records the new fields beside the old ones.

No live calls: the genai and boto3 clients are faked.
"""
from __future__ import annotations

import time
import types

from chat_nextseek.llm_clients import BedrockClient, GeminiClient, LLMResponse, _converse_usage
from chat_nextseek.schemas.schema_helper import _ledger_entry


# ---------------------------------------------------------------------------- Gemini

class _GeminiResponse:
    def __init__(self, **usage):
        self.text = '{"ok": true}'
        self.candidates = [types.SimpleNamespace(finish_reason="STOP")]
        self.usage_metadata = types.SimpleNamespace(**usage)


def _gemini(response) -> GeminiClient:
    client = GeminiClient.__new__(GeminiClient)
    client.client = types.SimpleNamespace(models=types.SimpleNamespace(
        generate_content=lambda **_kw: response))
    return client


def test_gemini_records_thinking_and_cached_tokens():
    resp = _gemini(_GeminiResponse(
        prompt_token_count=1200, candidates_token_count=30, total_token_count=1680,
        thoughts_token_count=450, cached_content_token_count=1000,
    )).chat(messages=[{"role": "user", "content": "hi"}], model="gemini-3.5-flash")
    assert resp.usage == {
        "prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1680,
        "thoughts_tokens": 450, "cached_tokens": 1000,
    }


def test_gemini_without_thinking_or_cache_records_them_as_absent_not_zero():
    """A response that does not report a count leaves it None: an unknown is not a zero."""
    resp = _gemini(_GeminiResponse(
        prompt_token_count=11, candidates_token_count=22, total_token_count=33,
    )).chat(messages=[{"role": "user", "content": "hi"}], model="gemini-3.5-flash")
    assert resp.usage["thoughts_tokens"] is None and resp.usage["cached_tokens"] is None
    assert resp.usage["prompt_tokens"] == 11


# ---------------------------------------------------------------------------- Bedrock

def test_converse_usage_splits_cache_writes_by_ttl_when_bedrock_reports_them():
    usage = _converse_usage({"usage": {
        "inputTokens": 10, "outputTokens": 5, "totalTokens": 3015,
        "cacheReadInputTokens": 0, "cacheWriteInputTokens": 3000,
        "cacheDetails": [{"ttl": "5m", "inputTokens": 1000}, {"ttl": "1h", "inputTokens": 2000}],
    }})
    assert usage["cache_write_tokens"] == 3000
    assert usage["cache_write_5m_tokens"] == 1000
    assert usage["cache_write_1h_tokens"] == 2000


def test_converse_usage_records_the_ttl_the_call_asked_for():
    body = {"usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15, "cacheWriteInputTokens": 900}}
    assert _converse_usage(body, cache_ttl="1h")["cache_ttl"] == "1h"
    assert "cache_ttl" not in _converse_usage(body)


class _Converse:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def converse(self, **kwargs):
        self.requests.append(kwargs)
        return self.response


def _bedrock(stub) -> BedrockClient:
    client = BedrockClient(region="us-east-1")
    client.client = stub
    return client


def _tool_response(usage):
    return {
        "stopReason": "tool_use",
        "output": {"message": {"content": [{"toolUse": {"toolUseId": "t", "name": "f", "input": {}}}]}},
        "usage": usage,
        "ResponseMetadata": {"RequestId": "r", "HTTPStatusCode": 200, "RetryAttempts": 0},
    }


def _call_tools(client, **kw):
    return client.chat_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "f", "description": "", "input_schema": {"type": "object"}}],
        system="s", model="us.anthropic.claude-opus-4-7", **kw,
    )


def test_a_cached_tool_call_says_which_ttl_its_writes_were_asked_for():
    usage = {"inputTokens": 10, "outputTokens": 5, "totalTokens": 915, "cacheWriteInputTokens": 900}
    result = _call_tools(_bedrock(_Converse(_tool_response(usage))), cache_prompt=True)
    assert result["usage"]["cache_ttl"] == "1h", "the tool loops ask for the 1-hour TTL"
    result = _call_tools(_bedrock(_Converse(_tool_response(usage))), cache_prompt=True, cache_ttl="5m")
    assert result["usage"]["cache_ttl"] == "5m"


def test_an_uncached_tool_call_names_no_ttl():
    usage = {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}
    result = _call_tools(_bedrock(_Converse(_tool_response(usage))))
    assert "cache_ttl" not in result["usage"]


# ---------------------------------------------------------------------------- the ledger

def test_the_ledger_keeps_the_new_token_fields():
    resp = LLMResponse(content="x", raw=None, model="m", provider="bedrock", metadata={}, usage={
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        "cache_read_tokens": 700, "cache_write_tokens": 300, "cache_write_5m_tokens": 100,
        "cache_write_1h_tokens": 200, "cache_ttl": "1h",
    })
    entry = _ledger_entry("followup", "m", types.SimpleNamespace(provider="bedrock"), 0, "ok",
                          time.perf_counter(), resp=resp)
    assert entry["prompt_tokens"] == 10 and entry["completion_tokens"] == 5
    assert entry["cache_read_tokens"] == 700 and entry["cache_write_tokens"] == 300
    assert entry["cache_write_5m_tokens"] == 100 and entry["cache_write_1h_tokens"] == 200
    assert entry["cache_ttl"] == "1h"


def test_the_ledger_keeps_gemini_thinking_and_cached_tokens():
    resp = LLMResponse(content="x", raw=None, model="g", provider="gcp", metadata={}, usage={
        "prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1680,
        "thoughts_tokens": 450, "cached_tokens": 1000,
    })
    entry = _ledger_entry("graph_agent", "g", types.SimpleNamespace(provider="gcp"), 0, "ok",
                          time.perf_counter(), resp=resp)
    assert entry["thoughts_tokens"] == 450 and entry["cached_tokens"] == 1000


def test_a_ledger_record_without_the_new_fields_does_not_invent_them():
    resp = LLMResponse(content="x", raw=None, model="g", provider="gcp", metadata={},
                       usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})
    entry = _ledger_entry("chatter", "g", types.SimpleNamespace(provider="gcp"), 0, "ok",
                          time.perf_counter(), resp=resp)
    for key in ("thoughts_tokens", "cached_tokens", "cache_read_tokens", "cache_write_tokens", "cache_ttl"):
        assert key not in entry

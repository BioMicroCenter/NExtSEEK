"""T6 (round 7): a 1h Bedrock cache point after the parser's static head.

The parser's head (system prompt, endpoint catalog, graph schema) is the same for every question; the
history, recent context, entity result and question follow it. The marker is a ``cache_point`` key on one
system message: ``BedrockClient._convert_messages`` turns it into head block / cache point / tail block, and
``_call_with_recovery`` keeps it only on an unmoved Bedrock call. No network: every SDK call is a stub.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from chat_nextseek import model_prices
from chat_nextseek.agents import parser as parser_mod
from chat_nextseek.llm_clients import BedrockClient, LLMResponse, LLMServiceUnavailableError
from chat_nextseek.schemas.schema_helper import call_llm_structured
from pydantic import BaseModel

OPUS55 = "us.anthropic.claude-opus-5-5"
GEMINI = "gemini-3.1-pro-preview"
CP = {"cachePoint": {"type": "default", "ttl": "1h"}}
OK = {
    "stopReason": "end_turn",
    "output": {"message": {"role": "assistant", "content": [{"text": "{\"mode\": \"graph_query\"}"}]}},
    "usage": {"inputTokens": 200, "outputTokens": 5, "totalTokens": 205},
}


def _bedrock(converse) -> BedrockClient:
    client = BedrockClient.__new__(BedrockClient)
    client.max_output_tokens = 4096
    client.client = SimpleNamespace(converse=converse)
    client.reset_connections = lambda: True
    return client


def _marked(tail="TAIL"):
    return [
        {"role": "system", "content": "PROMPT"},
        {"role": "system", "content": "SCHEMA", "cache_point": "1h"},
        {"role": "system", "content": tail},
        {"role": "user", "content": "Q"},
    ]


# ---------------------------------------------------------------- 1. parser_agent's message order

def _cfg(index=None):
    return SimpleNamespace(
        PARSER_SYSTEM_PROMPT="PROMPT", MIN_API_ENDPOINTS=[{"path": "/a"}], MIN_GRAPH_SCHEMA={"g": 1},
        ENDPOINT_INDEX=index, FORCE_PARSER_MODE=None,
        get_agent_model=lambda name: (object(), "gemini-test", None),
    )


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def _fake(**kw):
        calls.append(kw["messages"])
        return parser_mod.ParserPlan(mode="new_search", target_endpoint="/nextseek_api/samples/retrieve/",
                                     intent_summary="x")

    monkeypatch.setattr(parser_mod, "call_llm_structured", _fake)
    monkeypatch.setattr(parser_mod, "build_recent_results_summary", lambda session: "RECENT")
    return calls


def _run(history, question, entity, config, monkeypatch):
    import chat_nextseek.chat_memory as chat_memory
    monkeypatch.setattr(chat_memory, "history_block", lambda session: history)
    try:
        parser_mod.parser_agent({"results_history": []}, config, question, entity)
    except Exception:  # the stub's plan may trip a guardrail; only the request matters here
        pass


def test_the_cache_point_follows_prompt_endpoints_and_schema(sent, monkeypatch):
    _run("HISTORY", "first question", {"keywords": ["a"]}, _cfg(), monkeypatch)
    msgs = sent[0]
    at = [i for i, m in enumerate(msgs) if m.get("cache_point")]
    assert at == [2] and msgs[2]["content"].startswith("GRAPH_SCHEMA")
    assert [m["content"].split(":")[0].split(" (")[0] for m in msgs[:3]] == ["PROMPT", "API ENDPOINT CATALOG", "GRAPH_SCHEMA"]
    assert msgs[3]["content"] == "HISTORY" and msgs[-1] == {"role": "user", "content": "first question"}


def test_a_ready_endpoint_index_moves_the_endpoints_after_the_cache_point(sent, monkeypatch):
    idx = SimpleNamespace(ready=True, query=lambda text, top_n: [({"path": "/b"}, 1.0, None)])
    _run("", "q", {}, _cfg(idx), monkeypatch)
    msgs = sent[0]
    assert msgs[1]["cache_point"] == "1h" and msgs[1]["content"].startswith("GRAPH_SCHEMA")
    assert msgs[2]["content"].startswith("API ENDPOINT CATALOG")


def test_the_head_is_byte_equal_across_questions(sent, monkeypatch):
    _run("H1", "one", {"keywords": ["a"]}, _cfg(), monkeypatch)
    _run("", "two", {"keywords": ["b"]}, _cfg(), monkeypatch)
    head = lambda msgs: json.dumps(msgs[:3])  # noqa: E731
    assert head(sent[0]) == head(sent[1])


# ---------------------------------------------------------------- 2. the Converse request

def test_a_marked_list_sends_head_cache_point_tail():
    seen = []
    client = _bedrock(lambda **kw: seen.append(kw) or OK)
    resp = client.chat(messages=_marked(), model=OPUS55)
    assert seen[0]["system"] == [{"text": "PROMPT\n\nSCHEMA"}, CP, {"text": "TAIL"}]
    assert resp.usage["cache_ttl"] == "1h"


def test_an_unmarked_list_sends_one_block():
    seen = []
    client = _bedrock(lambda **kw: seen.append(kw) or OK)
    client.chat(messages=[{"role": "system", "content": "A"}, {"role": "system", "content": "B"},
                          {"role": "user", "content": "Q"}], model=OPUS55)
    assert seen[0]["system"] == [{"text": "A\n\nB"}]


# ---------------------------------------------------------------- 3. usage and price

def test_cache_read_and_write_usage_and_price():
    read = dict(OK, usage={"inputTokens": 200, "outputTokens": 5, "totalTokens": 17205,
                           "cacheReadInputTokens": 17000, "cacheWriteInputTokens": 0})
    write = dict(OK, usage={"inputTokens": 200, "outputTokens": 5, "totalTokens": 17205,
                            "cacheReadInputTokens": 0, "cacheWriteInputTokens": 17000,
                            "cacheDetails": [{"inputTokens": 17000, "ttl": "1h"}]})
    r = _bedrock(lambda **kw: read).chat(messages=_marked(), model=OPUS55).usage
    w = _bedrock(lambda **kw: write).chat(messages=_marked(), model=OPUS55).usage
    assert r["cache_read_tokens"] == 17000 and r["cache_ttl"] == "1h"
    assert w["cache_write_1h_tokens"] == 17000 and w["cache_ttl"] == "1h"
    only = {"prompt_tokens": 0, "completion_tokens": 0}
    assert model_prices.call_cost(OPUS55, {**r, **only}).cost_usd == pytest.approx(0.0037, abs=1e-4)  # 17,000 x $0.22/M
    assert model_prices.call_cost(OPUS55, {**w, **only}).cost_usd == pytest.approx(0.1496, abs=1e-4)  # 17,000 x $8.80/M


# ---------------------------------------------------------------- 4. the ladder strips the marker

class _Plan(BaseModel):
    mode: str


class _Gcp:
    provider = "gcp"

    def __init__(self):
        self.messages = []

    def reset_connections(self):
        return True

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.messages.append(messages)
        return LLMResponse(content='{"mode": "graph_query"}', raw=None, usage={"prompt_tokens": 1, "completion_tokens": 1},
                           model=model, provider="gcp", metadata={})


def _config(bedrock, gcp):
    return SimpleNamespace(
        LOG_DIR=None, LLM_CLIENT=bedrock, LLM_MODEL="unused", _CATALOG_KEY="default",
        _THINKING_BUDGET_MAP={None: None, "high": 16000}, LLM_CLIENTS={"anth": bedrock, "gcp": gcp},
        AGENT_MODEL_CATALOG={"gcp:current": {"parser": {"provider": "gcp", "model": GEMINI, "thinking_level": None}}},
    )


def test_a_moved_call_and_a_non_bedrock_primary_never_see_the_marker():
    def down(**kw):
        raise ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "down"}}, "Converse")

    bedrock, gcp = _bedrock(down), _Gcp()
    plan = call_llm_structured(_config(bedrock, gcp), "q", _Plan, system="s", messages=_marked(), client=bedrock,
                               model_name=OPUS55, agent_label="parser")
    assert plan.mode == "graph_query"
    assert gcp.messages and all("cache_point" not in m for m in gcp.messages[0])
    # a non-Bedrock primary
    gcp2 = _Gcp()
    call_llm_structured(_config(bedrock, gcp2), "q", _Plan, system="s", messages=_marked(), client=gcp2,
                        model_name=GEMINI, agent_label="parser")
    assert all("cache_point" not in m for m in gcp2.messages[0])


def test_an_unmoved_bedrock_call_keeps_the_marker():
    seen = []
    bedrock = _bedrock(lambda **kw: seen.append(kw) or OK)
    call_llm_structured(_config(bedrock, _Gcp()), "q", _Plan, system="s", messages=_marked(), client=bedrock,
                        model_name=OPUS55, agent_label="parser")
    assert CP in seen[0]["system"]


# ---------------------------------------------------------------- 5. a 400 that names the cache point

def _validation(msg):
    return ClientError({"Error": {"Code": "ValidationException", "Message": msg}}, "Converse")


def test_a_cache_point_400_is_retried_once_without_it():
    seen = []

    def converse(**kw):
        seen.append(kw)
        if len(seen) == 1:
            raise _validation("The model returned the following errors: Invalid CachePoint placement")
        return OK

    resp = _bedrock(converse).chat(messages=_marked(), model=OPUS55)
    assert len(seen) == 2
    assert CP in seen[0]["system"]
    assert seen[1]["system"] == [{"text": "PROMPT\n\nSCHEMA\n\nTAIL"}]
    assert "cache_ttl" not in resp.usage and resp.content


def test_other_400s_and_a_second_failure_are_not_retried():
    seen = []

    def other(**kw):
        seen.append(kw)
        raise _validation("A conversation must start with a user message.")

    with pytest.raises(Exception):
        _bedrock(other).chat(messages=_marked(), model=OPUS55)
    assert len(seen) == 1

    seen2 = []

    def twice(**kw):
        seen2.append(kw)
        raise _validation("cache point not allowed")

    with pytest.raises(Exception):
        _bedrock(twice).chat(messages=_marked(), model=OPUS55)
    assert len(seen2) == 2

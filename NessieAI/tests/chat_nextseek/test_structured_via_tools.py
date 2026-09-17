"""Structured output by forced tool call, instead of by asking nicely in the prompt.

`BedrockClient.chat` accepts `response_format` and never puts it in the request, so
every Bedrock structured call in this codebase was unconstrained text that Pydantic
tried to parse afterwards. A forced `toolChoice` makes the model answer by filling the
schema, and a forced tool call cannot come back as an empty text block, which is how
production turn 406 failed three times in a row.

Forcing a specific tool is supported for Claude on Bedrock; AWS removed it only on
Claude Fable 5.1 / Mythos 5.1. Any model that rejects the schema-shaped request raises
LLMStructuredUnsupportedError and the call is retried plain, so the feature can only
help.
"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from chat_nextseek.llm_clients import (
    BedrockClient,
    LLMStructuredUnsupportedError,
    _is_schema_rejection,
    _normalize_tool_choice,
    pydantic_to_tool_schema,
)


class _Filters(BaseModel):
    keywords: list[str] = Field(default_factory=list)


class _Plan(BaseModel):
    mode: str = "unsupported"
    filters: _Filters = Field(default_factory=_Filters)
    metadata: dict = Field(default_factory=dict)


class _StubConverse:
    """Captures the request and replays a canned Converse response."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests: list[dict] = []

    def converse(self, **kwargs):
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _client(stub) -> BedrockClient:
    client = BedrockClient(region="us-east-1")
    client.client = stub
    return client


def _tool_use_response(name, payload, usage=None):
    return {
        "stopReason": "tool_use",
        "output": {"message": {"content": [
            {"toolUse": {"toolUseId": "tu_1", "name": name, "input": payload}},
        ]}},
        "usage": usage or {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
        "metrics": {"latencyMs": 900},
        "ResponseMetadata": {"RequestId": "req-1", "HTTPStatusCode": 200, "RetryAttempts": 0},
    }


# --------------------------------------------------------------------------
# toolChoice
# --------------------------------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    ("auto", {"auto": {}}),
    ("any", {"any": {}}),
    ("emit_parser_plan", {"tool": {"name": "emit_parser_plan"}}),
    ({"tool": {"name": "x"}}, {"tool": {"name": "x"}}),
])
def test_tool_choice_normalisation(given, expected):
    assert _normalize_tool_choice(given) == expected


def test_chat_with_tools_sends_tool_choice_when_asked():
    stub = _StubConverse(_tool_use_response("f", {}))
    _client(stub).chat_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "f", "description": "", "input_schema": {"type": "object"}}],
        system="s", model="us.anthropic.claude-opus-4-7", tool_choice="f",
    )
    assert stub.requests[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "f"}}


def test_chat_with_tools_omits_tool_choice_by_default():
    """The pipeline agent must keep choosing freely, including choosing to reply."""
    stub = _StubConverse(_tool_use_response("f", {}))
    _client(stub).chat_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "f", "description": "", "input_schema": {"type": "object"}}],
        system="s", model="us.anthropic.claude-opus-4-7",
    )
    assert "toolChoice" not in stub.requests[0]["toolConfig"]


# --------------------------------------------------------------------------
# chat_structured
# --------------------------------------------------------------------------

def test_chat_structured_forces_the_named_tool_and_returns_its_input_as_json():
    payload = {"mode": "graph_query", "filters": {"keywords": ["CC"]}, "metadata": {}}
    stub = _StubConverse(_tool_use_response("emit_plan", payload))
    resp = _client(stub).chat_structured(
        messages=[{"role": "user", "content": "q"}], system="sys",
        model="us.anthropic.claude-opus-4-7",
        schema=pydantic_to_tool_schema(_Plan), schema_name="emit_plan",
    )

    req = stub.requests[0]
    assert req["toolConfig"]["toolChoice"] == {"tool": {"name": "emit_plan"}}
    assert req["toolConfig"]["tools"][0]["toolSpec"]["name"] == "emit_plan"
    assert json.loads(resp.content) == payload
    assert resp.metadata["structured_via"] == "tool_use"
    assert resp.usage["prompt_tokens"] == 100


def test_chat_structured_falls_back_to_text_when_the_model_answers_in_prose():
    """A forced tool should always produce a tool call, but if a model replies in text
    anyway the ordinary parse path still gets its chance rather than failing here."""
    stub = _StubConverse({
        "stopReason": "end_turn",
        "output": {"message": {"content": [{"text": '{"mode": "graph_query"}'}]}},
        "usage": {"inputTokens": 5, "outputTokens": 5, "totalTokens": 10},
        "ResponseMetadata": {},
    })
    resp = _client(stub).chat_structured(
        messages=[{"role": "user", "content": "q"}], system=None,
        model="us.anthropic.claude-opus-4-7",
        schema=pydantic_to_tool_schema(_Plan), schema_name="emit_plan",
    )
    assert json.loads(resp.content)["mode"] == "graph_query"


def test_strict_is_off_unless_asked():
    """Opus 4.7 does not support structured outputs on Bedrock, so `strict: true`
    would 400 there. It stays opt-in until a call is pinned to a model that takes it."""
    stub = _StubConverse(_tool_use_response("emit_plan", {}))
    _client(stub).chat_structured(
        messages=[{"role": "user", "content": "q"}], system=None,
        model="us.anthropic.claude-opus-4-7",
        schema=pydantic_to_tool_schema(_Plan), schema_name="emit_plan",
    )
    assert "strict" not in stub.requests[0]["toolConfig"]["tools"][0]["toolSpec"]

    stub2 = _StubConverse(_tool_use_response("emit_plan", {}))
    _client(stub2).chat_structured(
        messages=[{"role": "user", "content": "q"}], system=None,
        model="us.anthropic.claude-sonnet-4-6",
        schema=pydantic_to_tool_schema(_Plan), schema_name="emit_plan", strict=True,
    )
    assert stub2.requests[0]["toolConfig"]["tools"][0]["toolSpec"]["strict"] is True


# --------------------------------------------------------------------------
# Rejection handling: a schema refusal must not look like an outage.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("message,expected", [
    ("ValidationException: toolChoice is not supported for this model", True),
    ("The strict field is not supported", True),
    ("output_config: Extra inputs are not permitted", True),
    ("Input is too long for requested model", False),
    ("messages.0.content: field required", False),
])
def test_schema_rejection_detection(message, expected):
    assert _is_schema_rejection(message) is expected


def test_schema_rejection_raises_its_own_error_not_a_503():
    """Failing over to another provider would be the wrong move: the same model will
    take the same request without the schema."""
    from botocore.exceptions import ClientError

    err = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "toolChoice is not supported"}},
        "Converse",
    )
    stub = _StubConverse(error=err)
    with pytest.raises(LLMStructuredUnsupportedError):
        _client(stub).chat_with_tools(
            messages=[{"role": "user", "content": "q"}],
            tools=[{"name": "f", "description": "", "input_schema": {"type": "object"}}],
            system="s", model="us.anthropic.claude-opus-4-7", tool_choice="f",
        )


def test_unrelated_validation_exception_still_propagates():
    from botocore.exceptions import ClientError

    err = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "Input is too long"}},
        "Converse",
    )
    stub = _StubConverse(error=err)
    with pytest.raises(ClientError):
        _client(stub).chat_with_tools(
            messages=[{"role": "user", "content": "q"}],
            tools=[{"name": "f", "description": "", "input_schema": {"type": "object"}}],
            system="s", model="us.anthropic.claude-opus-4-7",
        )


# --------------------------------------------------------------------------
# Schema conversion
# --------------------------------------------------------------------------

def test_objects_with_properties_are_closed():
    schema = pydantic_to_tool_schema(_Plan)
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["_Filters"]["additionalProperties"] is False


def test_free_form_dict_fields_stay_open():
    """ParserPlan.metadata exists to carry arbitrary keys; closing it would forbid the
    content it is for."""
    schema = pydantic_to_tool_schema(_Plan)
    assert schema["properties"]["metadata"].get("additionalProperties") is not False


def test_tool_name_is_derived_from_the_model_and_is_stable():
    from chat_nextseek.schemas.schema_helper import _schema_tool_name

    class ParserPlan(BaseModel):
        pass

    assert _schema_tool_name(ParserPlan) == "emit_parser_plan"
    assert _schema_tool_name(ParserPlan) == _schema_tool_name(ParserPlan)


# --------------------------------------------------------------------------
# Prompt caching
# --------------------------------------------------------------------------

def test_cache_points_go_after_tools_and_after_system():
    """Checkpoints are processed tools -> system -> messages, so the stable head is
    what a growing tool loop wants cached: every iteration re-sends it."""
    stub = _StubConverse(_tool_use_response("f", {}))
    _client(stub).chat_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "f", "description": "", "input_schema": {"type": "object"}}],
        system="a long stable system prompt", model="us.anthropic.claude-opus-4-7",
        cache_prompt=True,
    )
    req = stub.requests[0]
    assert req["toolConfig"]["tools"][-1] == {"cachePoint": {"type": "default", "ttl": "1h"}}
    assert req["system"][-1] == {"cachePoint": {"type": "default", "ttl": "1h"}}


def test_no_cache_points_by_default():
    stub = _StubConverse(_tool_use_response("f", {}))
    _client(stub).chat_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "f", "description": "", "input_schema": {"type": "object"}}],
        system="s", model="us.anthropic.claude-opus-4-7",
    )
    req = stub.requests[0]
    assert all("cachePoint" not in b for b in req["toolConfig"]["tools"])
    assert all("cachePoint" not in b for b in req["system"])


def test_cache_token_counts_are_recorded():
    """inputTokens counts only the UNcached part, so a caller that ignores these two
    fields under-reports the prompt by exactly the cached portion."""
    stub = _StubConverse(_tool_use_response("f", {}, usage={
        "inputTokens": 200, "outputTokens": 20, "totalTokens": 220,
        "cacheReadInputTokens": 9000, "cacheWriteInputTokens": 0,
    }))
    result = _client(stub).chat_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "f", "description": "", "input_schema": {"type": "object"}}],
        system="s", model="us.anthropic.claude-opus-4-7",
    )
    assert result["usage"]["cache_read_tokens"] == 9000
    assert result["usage"]["cache_write_tokens"] == 0
    assert result["metadata"]["stop_reason"] == "tool_use"
    assert result["metadata"]["request_id"] == "req-1"


# --------------------------------------------------------------------------
# call_llm_structured picks the schema path, and degrades cleanly.
# --------------------------------------------------------------------------

class _SchemaAwareClient:
    """A client that supports chat_structured, like BedrockClient does."""

    provider = "bedrock"

    def __init__(self, structured=None, plain=None, reject=False):
        self.structured_calls: list[dict] = []
        self.plain_calls: list[dict] = []
        self._structured = structured
        self._plain = plain
        self._reject = reject

    def chat_structured(self, *, messages, system, model, schema, schema_name,
                        temperature=0.0, thinking_budget=None, **kw):
        self.structured_calls.append({"schema": schema, "schema_name": schema_name, "system": system})
        if self._reject:
            raise LLMStructuredUnsupportedError("toolChoice is not supported for this model")
        from chat_nextseek.llm_clients import LLMResponse

        return LLMResponse(content=self._structured, raw=None, usage=None, model=model,
                           provider=self.provider, metadata={"structured_via": "tool_use"})

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.plain_calls.append({"messages": messages})
        from chat_nextseek.llm_clients import LLMResponse

        return LLMResponse(content=self._plain, raw=None, usage=None, model=model,
                           provider=self.provider, metadata=None)


class _PlainOnlyClient:
    """A client with no chat_structured, like GeminiClient."""

    provider = "gcp"

    def __init__(self, text):
        self.text = text
        self.calls: list[dict] = []

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.calls.append({"messages": messages, "response_format": response_format})
        from chat_nextseek.llm_clients import LLMResponse

        return LLMResponse(content=self.text, raw=None, usage=None, model=model,
                           provider=self.provider, metadata=None)


class _Config:
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None}
    LOG_DIR = "/tmp"
    AGENT_MODEL_CATALOG: dict = {}

    def __init__(self, client):
        self.LLM_CLIENT = client
        self.LLM_MODEL = "m"
        self.LLM_CLIENTS = {"anth": client}


def test_call_llm_structured_uses_the_schema_path_on_a_capable_client():
    from chat_nextseek.schemas.schema_helper import call_llm_structured

    client = _SchemaAwareClient(structured='{"mode": "graph_query"}')
    plan = call_llm_structured(
        _Config(client), "q", _Plan,
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="parser",
    )
    assert plan.mode == "graph_query"
    assert len(client.structured_calls) == 1
    assert client.structured_calls[0]["schema_name"] == "emit_plan"
    assert client.structured_calls[0]["system"] == "sys"
    assert client.plain_calls == []


def test_system_messages_are_lifted_out_of_the_message_list_for_the_schema_path():
    """Converse takes `system` as its own field; leaving a system role in `messages`
    would make Bedrock reject the request."""
    from chat_nextseek.schemas.schema_helper import call_llm_structured

    captured = {}

    class _Recorder(_SchemaAwareClient):
        def chat_structured(self, *, messages, system, **kw):
            captured["messages"] = messages
            captured["system"] = system
            return super().chat_structured(messages=messages, system=system, **kw)

    client = _Recorder(structured='{"mode": "new_search"}')
    call_llm_structured(
        _Config(client), "q", _Plan,
        messages=[
            {"role": "system", "content": "one"},
            {"role": "system", "content": "two"},
            {"role": "user", "content": "q"},
        ],
        client=client, model_name="m", agent_label="parser",
    )
    assert captured["system"] == "one\n\ntwo"
    assert [m["role"] for m in captured["messages"]] == ["user"]


def test_a_model_that_rejects_the_schema_is_retried_plain_on_the_same_model():
    """Not a 503: failing over to another provider would be wrong, because this model
    will take the same request without the schema."""
    from chat_nextseek.schemas.schema_helper import call_llm_structured

    client = _SchemaAwareClient(reject=True, plain='{"mode": "new_search"}')
    plan = call_llm_structured(
        _Config(client), "q", _Plan,
        messages=[{"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="parser",
    )
    assert plan.mode == "new_search"
    assert len(client.structured_calls) == 1
    assert len(client.plain_calls) == 1


def test_a_client_without_chat_structured_is_untouched():
    from chat_nextseek.schemas.schema_helper import call_llm_structured

    client = _PlainOnlyClient('{"mode": "reporter"}')
    plan = call_llm_structured(
        _Config(client), "q", _Plan,
        messages=[{"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="parser",
    )
    assert plan.mode == "reporter"
    assert client.calls[0]["response_format"] == {"type": "json_object"}


def test_structured_via_tools_can_be_turned_off_per_call():
    from chat_nextseek.schemas.schema_helper import call_llm_structured

    client = _SchemaAwareClient(structured="never used", plain='{"mode": "new_search"}')
    call_llm_structured(
        _Config(client), "q", _Plan,
        messages=[{"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="parser",
        structured_via_tools=False,
    )
    assert client.structured_calls == []
    assert len(client.plain_calls) == 1


def test_parser_mode_enum_reaches_the_schema_but_does_not_constrain_the_type():
    """An unrecognised mode must reach the orchestrator's "unexpected mode" branch and
    get a civil reply, not fail validation and burn the repair loop."""
    from chat_nextseek.schemas.router import PARSER_MODES, ParserPlan

    schema = pydantic_to_tool_schema(ParserPlan)
    assert schema["properties"]["mode"]["enum"] == list(PARSER_MODES)
    assert ParserPlan(mode="something_new").mode == "something_new"

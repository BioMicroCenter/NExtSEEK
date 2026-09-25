"""The clients name a transport failure and a 429 so the ladder can move on them.

Operator ruling 2026-09-25 (fix 5):

* A Bedrock read or connect timeout, and a connection error, used to leave the client
  as a raw botocore exception. ``BedrockClient.chat`` wrapped it into a bare
  ``LLMError``, which the ladder treats as an unrecoverable 400 and ends the turn;
  ``chat_with_tools`` (and so ``chat_structured``, the parser's forced tool call) let it
  escape as a botocore class the ladder never sees, so the parser recorded a parse
  error. Neither moved to another provider. A timeout is now ``LLMTimeoutError`` and a
  connection failure ``LLMAPIConnectionError``, both of which move.
* A Gemini 429 that survived the SDK's own retries became a bare ``LLMError`` and ended
  the turn at once. It is now ``LLMRateLimitError``, which moves like a 503.

No network: every SDK call is a stub that raises.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from chat_nextseek.llm_clients import (
    BedrockClient,
    GeminiClient,
    LLMAPIConnectionError,
    LLMError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)

URL = "https://bedrock-runtime.us-east-1.amazonaws.com/model/x/converse"


def _bedrock(exc: BaseException) -> BedrockClient:
    client = BedrockClient.__new__(BedrockClient)
    client.max_output_tokens = 4096

    def converse(**kwargs):
        raise exc

    client.client = SimpleNamespace(converse=converse)
    return client


def _chat(client):
    return client.chat(messages=[{"role": "user", "content": "hi"}], model="us.anthropic.claude-opus-4-7")


def _chat_with_tools(client):
    return client.chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[], system="s",
                                  model="us.anthropic.claude-opus-4-7")


def _chat_structured(client):
    return client.chat_structured(messages=[{"role": "user", "content": "hi"}], system="s",
                                  model="us.anthropic.claude-opus-4-7",
                                  schema={"type": "object", "properties": {}})


CALLS = [_chat, _chat_with_tools, _chat_structured]


@pytest.mark.parametrize("call", CALLS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("exc", [ReadTimeoutError(endpoint_url=URL), ConnectTimeoutError(endpoint_url=URL)],
                         ids=["read", "connect"])
def test_a_bedrock_timeout_is_a_timeout(call, exc):
    with pytest.raises(LLMTimeoutError) as excinfo:
        call(_bedrock(exc))
    assert excinfo.value.__cause__ is exc


@pytest.mark.parametrize("call", CALLS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("exc", [EndpointConnectionError(endpoint_url=URL),
                                 ConnectionClosedError(endpoint_url=URL)], ids=["endpoint", "closed"])
def test_a_bedrock_connection_failure_is_a_connection_error(call, exc):
    with pytest.raises(LLMAPIConnectionError) as excinfo:
        call(_bedrock(exc))
    assert excinfo.value.__cause__ is exc


@pytest.mark.parametrize("call", CALLS, ids=lambda f: f.__name__)
def test_a_bedrock_throttle_is_still_a_rate_limit(call):
    exc = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse")
    with pytest.raises(LLMRateLimitError):
        call(_bedrock(exc))


def test_any_other_bedrock_failure_in_chat_is_unchanged():
    """Not a transport failure: chat's catch-all still makes it a bare LLMError."""
    with pytest.raises(LLMError) as excinfo:
        _chat(_bedrock(ValueError("something else")))
    assert type(excinfo.value) is LLMError


# ---------------------------------------------------------------------------- Gemini

class _GenaiError(Exception):
    """The shape of google.genai.errors.APIError: a numeric ``code`` and a status in the text."""

    def __init__(self, code, status, message):
        super().__init__(f"{code} {status}. {{'error': {{'code': {code}, 'message': '{message}', "
                         f"'status': '{status}'}}}}")
        self.code = code
        self.status = status


def _gemini(exc: BaseException) -> GeminiClient:
    client = GeminiClient.__new__(GeminiClient)

    def generate_content(**kwargs):
        raise exc

    client.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return client


def _gemini_chat(client):
    return client.chat(messages=[{"role": "user", "content": "hi"}], model="gemini-3.5-flash")


def test_a_gemini_429_is_a_rate_limit():
    with pytest.raises(LLMRateLimitError):
        _gemini_chat(_gemini(_GenaiError(429, "RESOURCE_EXHAUSTED", "Resource has been exhausted")))


def test_a_gemini_429_known_only_by_its_text_is_a_rate_limit():
    with pytest.raises(LLMRateLimitError):
        _gemini_chat(_gemini(RuntimeError("429 RESOURCE_EXHAUSTED. quota exceeded")))


def test_a_gemini_429_whose_text_holds_a_5xx_number_is_still_a_rate_limit():
    """A quota message can quote a limit of 500; the code says what it is."""
    with pytest.raises(LLMRateLimitError):
        _gemini_chat(_gemini(_GenaiError(429, "RESOURCE_EXHAUSTED", "limit: 500 requests per minute")))


def test_a_gemini_503_is_still_unavailable():
    with pytest.raises(LLMServiceUnavailableError):
        _gemini_chat(_gemini(_GenaiError(503, "UNAVAILABLE", "The model is overloaded")))


def test_a_gemini_400_is_still_a_bare_error():
    with pytest.raises(LLMError) as excinfo:
        _gemini_chat(_gemini(_GenaiError(400, "INVALID_ARGUMENT", "bad request")))
    assert type(excinfo.value) is LLMError


# ---------------------------------------------------------------------------- through the ladder

class _Gcp:
    provider = "gcp"

    def __init__(self):
        self.calls = []

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        from chat_nextseek.llm_clients import LLMResponse

        self.calls.append(model)
        return LLMResponse(content='{"mode": "new_search"}', raw=None, usage=None, model=model,
                           provider="gcp", metadata={})


@pytest.mark.parametrize("exc", [ReadTimeoutError(endpoint_url=URL), EndpointConnectionError(endpoint_url=URL)],
                         ids=["read-timeout", "connection"])
def test_the_parsers_forced_tool_call_moves_on_a_bedrock_transport_failure(exc):
    """The parser's Opus call goes out as chat_structured; a stalled or refused Bedrock
    used to surface as a parse error with no move."""
    from pydantic import BaseModel

    from chat_nextseek.schemas.schema_helper import call_llm_structured

    class _Plan(BaseModel):
        mode: str

    bedrock = _bedrock(exc)
    bedrock.reset_connections = lambda: True
    gcp = _Gcp()
    config = SimpleNamespace(
        LOG_DIR=None, LLM_CLIENT=bedrock, LLM_MODEL="unused", _CATALOG_KEY="default",
        _THINKING_BUDGET_MAP={None: None, "high": 16000},
        LLM_CLIENTS={"anth": bedrock, "gcp": gcp},
        AGENT_MODEL_CATALOG={"gcp:current": {"parser": {"provider": "gcp", "model": "gemini-3.1-pro-preview",
                                                        "thinking_level": "high"}}},
    )

    plan = call_llm_structured(config, "q", _Plan, system="s", client=bedrock,
                               model_name="us.anthropic.claude-opus-4-7", agent_label="parser")

    assert plan.mode == "new_search"
    assert gcp.calls == ["gemini-3.1-pro-preview"]


# ---------------------------------------------------------------------------- Gemini transport

import httpx  # noqa: E402

_REQ = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent")


@pytest.mark.parametrize("exc", [
    httpx.ReadTimeout("The read operation timed out", request=_REQ),
    httpx.ConnectTimeout("timed out", request=_REQ),
    httpx.WriteTimeout("timed out", request=_REQ),
    httpx.PoolTimeout("timed out", request=_REQ),
], ids=["read", "connect", "write", "pool"])
def test_a_gemini_httpx_timeout_is_a_timeout(exc):
    """google-genai re-raises httpx transport errors (tenacity reraise=True); they used to
    fall through to a bare LLMError, which ends the turn without a move."""
    with pytest.raises(LLMTimeoutError) as excinfo:
        _gemini_chat(_gemini(exc))
    assert excinfo.value.__cause__ is exc


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("[Errno -3] Temporary failure in name resolution", request=_REQ),
    httpx.ReadError("connection reset by peer", request=_REQ),
    httpx.RemoteProtocolError("Server disconnected without sending a response.", request=_REQ),
], ids=["connect", "read", "server-disconnected"])
def test_a_gemini_network_failure_is_a_connection_error(exc):
    with pytest.raises(LLMAPIConnectionError) as excinfo:
        _gemini_chat(_gemini(exc))
    assert excinfo.value.__cause__ is exc


def test_a_gemini_timeout_whose_text_holds_a_5xx_number_is_still_a_timeout():
    """Checked before the status-code text scan: a URL or port can carry 500-504."""
    with pytest.raises(LLMTimeoutError):
        _gemini_chat(_gemini(httpx.ReadTimeout("timed out after 504 ms", request=_REQ)))


def test_a_real_gemini_4xx_is_never_taken_for_a_transport_failure():
    from google.genai import errors

    with pytest.raises(LLMError) as excinfo:
        _gemini_chat(_gemini(errors.ClientError(400, {"error": {"code": 400, "message": "bad request",
                                                                  "status": "INVALID_ARGUMENT"}})))
    assert type(excinfo.value) is LLMError
    with pytest.raises(LLMRateLimitError):
        _gemini_chat(_gemini(errors.ClientError(429, {"error": {"code": 429, "message": "slow down",
                                                                  "status": "RESOURCE_EXHAUSTED"}})))

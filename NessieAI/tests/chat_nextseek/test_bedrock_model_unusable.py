"""A Bedrock "wrong model id / no access" error moves to the next model (F1, operator ruling 2026-09-25).

``BedrockClient.chat_with_tools`` (the forced tool call every structured Bedrock call goes out
as, and the tool loops' only surface) typed throttling, transport failures, the 5xx codes and
the schema-rejection ``ValidationException``; any other ``ClientError`` left it raw. So a model
id Bedrock does not know (``ValidationException: The provided model identifier is invalid``), a
model this account may not use (``AccessDeniedException``) or a retired one
(``ResourceNotFoundException``) was never moved on, and the ladder did not ledger it. The plain
``chat`` wrapped the same errors into a bare ``LLMError``: fatal, and not "unavailable".

The rule pinned here:

* those three are ``LLMModelUnusableError``, a kind of ``LLMServiceUnavailableError``, from
  ``chat``, ``chat_with_tools`` and ``chat_structured``;
* the ladder (``_call_with_recovery``) and the tool loop move on it once, like a 503, with the
  reason ``model_unusable`` in ``model_fallback`` and in the ledger;
* when the model it moved to fails too, the fatal is ``unavailable``, so the user gets the
  approved "The AI models we use were unavailable..." text;
* the other two ``ValidationException`` kinds do not change: a schema rejection is still
  ``LLMStructuredUnsupportedError`` (retry plain, same model), and a genuine bad request still
  does not move.

No network: every SDK call is a stub.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from pydantic import BaseModel

from chat_nextseek import failure_replies, tool_loop, turn_spend
from chat_nextseek.llm_clients import (
    BedrockClient,
    LLMError,
    LLMFatalError,
    LLMModelUnusableError,
    LLMResponse,
    LLMServiceUnavailableError,
    LLMStructuredUnsupportedError,
    _is_model_id_rejection,
)
from chat_nextseek.schemas import schema_helper
from chat_nextseek.schemas.schema_helper import call_llm_structured, call_llm_text

OPUS = "us.anthropic.claude-opus-4-7"
SONNET = "us.anthropic.claude-sonnet-4-6"
GEMINI = "gemini-3.1-pro-preview"


def _client_error(code: str, message: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


# Real Bedrock wording for each kind (the curly apostrophe is how AWS sends it).
MODEL_ID_INVALID = _client_error("ValidationException", "The provided model identifier is invalid.")
ON_DEMAND = _client_error(
    "ValidationException",
    "Invocation of model ID anthropic.claude-opus-5-5 with on-demand throughput isn’t supported. "
    "Retry your request with the ID or ARN of an inference profile that contains this model.",
)
NO_ACCESS = _client_error("AccessDeniedException", "You don't have access to the model with the specified model ID.")
RETIRED = _client_error(
    "ResourceNotFoundException",
    "This model version has reached the end of its life. Please refer to the AWS documentation for more details.",
)
UNUSABLE = [MODEL_ID_INVALID, ON_DEMAND, NO_ACCESS, RETIRED]
UNUSABLE_IDS = ["model-id-invalid", "on-demand", "access-denied", "resource-not-found"]

SCHEMA_REJECTION = _client_error(
    "ValidationException", "The model returned the following errors: toolChoice.any is not supported for this model"
)
BAD_REQUEST = _client_error(
    "ValidationException",
    "A conversation must start with a user message. Try again with a conversation that starts with a user message.",
)


def _bedrock(converse) -> BedrockClient:
    """A BedrockClient whose SDK call is ``converse`` (an exception is raised every time)."""
    client = BedrockClient.__new__(BedrockClient)
    client.max_output_tokens = 4096
    if isinstance(converse, BaseException):
        exc = converse

        def converse(**kwargs):
            raise exc

    client.client = SimpleNamespace(converse=converse)
    client.reset_connections = lambda: True
    return client


def _chat(client):
    return client.chat(messages=[{"role": "user", "content": "hi"}], model=OPUS)


def _chat_with_tools(client):
    return client.chat_with_tools(messages=[{"role": "user", "content": "hi"}], tools=[], system="s", model=OPUS)


def _chat_structured(client):
    return client.chat_structured(messages=[{"role": "user", "content": "hi"}], system="s", model=OPUS,
                                  schema={"type": "object", "properties": {}})


CALLS = [_chat, _chat_with_tools, _chat_structured]


# ---------------------------------------------------------------------------- the client types it

@pytest.mark.parametrize("call", CALLS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("exc", UNUSABLE, ids=UNUSABLE_IDS)
def test_a_model_bedrock_will_not_run_is_model_unusable(call, exc):
    with pytest.raises(LLMModelUnusableError) as excinfo:
        call(_bedrock(exc))
    assert isinstance(excinfo.value, LLMServiceUnavailableError), "the ladder moves on it like a 503"
    assert excinfo.value.__cause__ is exc


@pytest.mark.parametrize("call", [_chat_with_tools, _chat_structured], ids=lambda f: f.__name__)
def test_a_schema_rejection_is_still_structured_unsupported(call):
    with pytest.raises(LLMStructuredUnsupportedError) as excinfo:
        call(_bedrock(SCHEMA_REJECTION))
    assert not isinstance(excinfo.value, LLMServiceUnavailableError)


@pytest.mark.parametrize("call", [_chat_with_tools, _chat_structured], ids=lambda f: f.__name__)
def test_a_genuine_bad_request_still_leaves_the_tool_surface_raw(call):
    with pytest.raises(ClientError):
        call(_bedrock(BAD_REQUEST))


def test_a_genuine_bad_request_is_still_a_bare_error_in_chat():
    with pytest.raises(LLMError) as excinfo:
        _chat(_bedrock(BAD_REQUEST))
    assert type(excinfo.value) is LLMError


@pytest.mark.parametrize("message", [
    "An error occurred (ValidationException) when calling the Converse operation: "
    "The provided model identifier is invalid.",
    "Invocation of model ID us.anthropic.claude-opus-5-5-v1:0 with on-demand throughput isn’t supported.",
    "Invocation of model ID anthropic.claude-x with on-demand throughput isn't supported.",
    "This action doesn't support the model that you provided. Try again with a supported text or chat model.",
    "The model ID us.anthropic.claude-opus-5-5-v1:0 is not enabled for this account.",
    "The model ID us.anthropic.claude-opus-5-5-v1:0 is not supported in this Region.",
])
def test_a_model_id_validation_message_is_recognised(message):
    assert _is_model_id_rejection(message) is True


@pytest.mark.parametrize("message", [
    "A conversation must start with a user message. Try again with a conversation that starts with a user message.",
    "Input is too long for requested model.",
    "Malformed input request: #/messages/0: extraneous key [foo] is not permitted, please reformat your input.",
    "The model returned the following errors: messages.0.content: text content blocks must be non-empty",
    "The model returned the following errors: the model id field is invalid",
    "The model returned the following errors: `temperature` may only be set to 1 when thinking is enabled.",
    "",
])
def test_any_other_validation_message_is_not(message):
    assert _is_model_id_rejection(message) is False


# ---------------------------------------------------------------------------- the ladder moves on it

class _Gcp:
    provider = "gcp"

    def __init__(self, outcomes=None):
        self.calls: list[str] = []
        self.outcomes = list(outcomes or [])

    def reset_connections(self):
        return True

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.calls.append(model)
        outcome = self.outcomes.pop(0) if self.outcomes else '{"mode": "graph_query"}'
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResponse(content=outcome, raw=None, usage={"prompt_tokens": 10, "completion_tokens": 5},
                           model=model, provider="gcp", metadata={})


def _config(bedrock, gcp, agent, log_dir=None):
    return SimpleNamespace(
        LOG_DIR=log_dir, LLM_CLIENT=bedrock, LLM_MODEL="unused", _CATALOG_KEY="default",
        _THINKING_BUDGET_MAP={None: None, "high": 16000},
        LLM_CLIENTS={"anth": bedrock, "gcp": gcp},
        AGENT_MODEL_CATALOG={"gcp:current": {agent: {"provider": "gcp", "model": GEMINI, "thinking_level": None}}},
    )


class _Plan(BaseModel):
    mode: str


def _ledger(tmp_path):
    return [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]


def _counting(exc):
    """A converse stub that raises ``exc`` and counts its calls."""
    calls = []

    def converse(**kwargs):
        calls.append(kwargs["modelId"])
        raise exc

    return converse, calls


@pytest.mark.parametrize("exc", UNUSABLE, ids=UNUSABLE_IDS)
def test_the_structured_ladder_moves_once_and_names_the_reason(tmp_path, exc):
    converse, sent = _counting(exc)
    bedrock = _bedrock(converse)
    gcp = _Gcp()
    config = _config(bedrock, gcp, "parser", log_dir=str(tmp_path))

    plan = call_llm_structured(config, "q", _Plan, system="s", client=bedrock, model_name=OPUS,
                               agent_label="parser")

    assert plan.mode == "graph_query"
    assert sent == [OPUS], "no plain retry on the same model: it is the model that is refused"
    assert gcp.calls == [GEMINI]
    entries = _ledger(tmp_path)
    assert [(e["model"], e["outcome"]) for e in entries] == [(OPUS, "model_unusable"), (GEMINI, "ok")]
    assert "LLMModelUnusableError" in entries[0]["error"]
    assert entries[1]["fallback_from"] == OPUS and entries[1]["fallback_reason"] == "model_unusable"


def test_the_plain_text_ladder_moves_too():
    converse, sent = _counting(NO_ACCESS)
    bedrock = _bedrock(converse)
    gcp = _Gcp(["the reply"])
    text = call_llm_text(_config(bedrock, gcp, "chatter"), messages=[{"role": "user", "content": "hi"}],
                         client=bedrock, model_name=OPUS, agent_label="chatter")
    assert text == "the reply"
    assert sent == [OPUS] and gcp.calls == [GEMINI]


def test_when_both_models_are_unusable_the_fatal_is_unavailable_and_the_reply_is_the_approved_one():
    bedrock = _bedrock(MODEL_ID_INVALID)
    gcp = _Gcp([LLMModelUnusableError("404 NOT_FOUND"), '{"mode": "never"}'])
    with pytest.raises(LLMFatalError) as excinfo:
        call_llm_structured(_config(bedrock, gcp, "parser"), "q", _Plan, system="s", client=bedrock,
                            model_name=OPUS, agent_label="parser")
    fatal = excinfo.value
    assert fatal.unavailable is True
    assert fatal.model_fallback == [{"agent": "parser", "from": OPUS, "to": GEMINI, "reason": "model_unusable"}]
    assert gcp.calls == [GEMINI], "one move, not a walk of the chain"
    assert failure_replies.model_unavailable_reply(fatal) == failure_replies.MODELS_UNAVAILABLE_TRIED_TWO_REPLY


def test_the_reason_is_a_known_fallback_reason():
    assert "model_unusable" in schema_helper.FALLBACK_REASONS


def test_a_refused_model_is_not_billed_and_the_move_reaches_the_turn_record():
    bedrock = _bedrock(RETIRED)
    gcp = _Gcp()
    with turn_spend.collecting():
        call_llm_structured(_config(bedrock, gcp, "parser"), "q", _Plan, system="s", client=bedrock,
                            model_name=OPUS, agent_label="parser")
        record = turn_spend.turn_record()
    assert record["model_fallback"] == [{"agent": "parser", "from": OPUS, "to": GEMINI, "reason": "model_unusable"}]
    assert record["models_used"] == [GEMINI]
    assert record["cost"]["unobserved_calls"] == [], "Bedrock refused the request: nothing ran"


def test_a_genuine_bad_request_does_not_move_on_the_text_ladder():
    bedrock = _bedrock(BAD_REQUEST)
    gcp = _Gcp(["never"])
    with pytest.raises(LLMFatalError) as excinfo:
        call_llm_text(_config(bedrock, gcp, "chatter"), messages=[{"role": "user", "content": "hi"}],
                      client=bedrock, model_name=OPUS, agent_label="chatter")
    assert excinfo.value.unavailable is False
    assert gcp.calls == []


# ---------------------------------------------------------------------------- the tool loop moves on it

def _tool_converse(fail_for: str, exc):
    """Bedrock refusing one model id and answering the other."""
    sent = []

    def converse(**kwargs):
        sent.append(kwargs["modelId"])
        if kwargs["modelId"] == fail_for:
            raise exc
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": "ok"}]}},
                "usage": {"inputTokens": 10, "outputTokens": 2}}

    return converse, sent


def _loop_config(bedrock, agent="followup", log_dir=None):
    return SimpleNamespace(
        LOG_DIR=log_dir, LLM_CLIENT=bedrock, LLM_MODEL="unused", _CATALOG_KEY="default",
        _THINKING_BUDGET_MAP={None: None}, LLM_CLIENTS={"anth": bedrock},
        AGENT_MODEL_CATALOG={"_fallback": {agent: {"provider": "anth", "model": SONNET, "thinking_level": None}}},
    )


@pytest.mark.parametrize("exc", UNUSABLE, ids=UNUSABLE_IDS)
def test_the_tool_loop_moves_to_its_fallback_and_names_the_reason(tmp_path, exc):
    converse, sent = _tool_converse(OPUS, exc)
    bedrock = _bedrock(converse)
    result = tool_loop.call_tools(_loop_config(bedrock, log_dir=str(tmp_path)), messages=[], tools=[],
                                  system="s", model_name=OPUS, client=bedrock, agent_label="followup")
    assert result["content"][0]["text"] == "ok"
    assert sent == [OPUS, SONNET]
    entries = _ledger(tmp_path)
    assert [(e["model"], e["outcome"]) for e in entries] == [(OPUS, "model_unusable"), (SONNET, "ok")]
    assert entries[1]["fallback_reason"] == "model_unusable"


def test_the_tool_loop_is_unavailable_when_both_models_are_unusable():
    bedrock = _bedrock(NO_ACCESS)
    with pytest.raises(LLMFatalError) as excinfo:
        tool_loop.call_tools(_loop_config(bedrock), messages=[], tools=[], system="s", model_name=OPUS,
                             client=bedrock, agent_label="followup")
    assert excinfo.value.unavailable is True
    assert excinfo.value.model_fallback == [
        {"agent": "followup", "from": OPUS, "to": SONNET, "reason": "model_unusable"},
    ]


@pytest.mark.parametrize("message", [
    # A request problem about something else, reported after a colon or in a later sentence:
    # the model id worked, so the call must not move (review of F1).
    "Model ID us.anthropic.claude-opus-4-7: temperature is not supported when thinking is enabled",
    "Invocation of model ID us.anthropic.claude-opus-4-7 failed: image format webp is not supported",
    "Model ID us.anthropic.claude-opus-4-7: cachePoint is not supported",
    "Invocation of model ID us.anthropic.claude-opus-4-7 was accepted. The field top_k is not supported.",
])
def test_a_request_problem_named_after_the_model_id_is_not_a_model_rejection(message):
    assert _is_model_id_rejection(message) is False


@pytest.mark.parametrize("message", [
    "The model ID anthropic.claude-sonnet-4-5-20250929-v1:0 is not supported in this region",
    "Model ID us.anthropic.claude-opus-5-5 is not enabled",
    "The provided model identifier is invalid.",
    "Invocation of model ID us.anthropic.claude-opus-5-5 with on-demand throughput isn't supported.",
])
def test_a_model_id_rejection_still_matches_including_ids_with_a_version_colon(message):
    assert _is_model_id_rejection(message) is True

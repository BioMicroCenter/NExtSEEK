"""The LLM ledger says how a structured output was obtained and whether the model reasoned.

``llm_calls.jsonl`` recorded latency, tokens, stop reason and outcome, but not whether
the output came from a forced tool call, the provider's JSON mode, JSON asked for only in
the prompt, or a repair turn, nor whether the response carried a reasoning block. A plan
that validated to nothing on its first attempt, with stop_reason tool_use, could not be
told apart from one that came back as prose, or from one the model reasoned about first.

Three keys are added to every record that has a response; the existing keys, which
``nessie_tests/engine_compare.py`` prices from, are unchanged.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from pydantic import BaseModel

from chat_nextseek.llm_clients import (
    BedrockClient,
    LLMResponse,
    LLMStructuredUnsupportedError,
    pydantic_to_tool_schema,
)
from chat_nextseek.schemas.schema_helper import _ledger_entry, call_llm_structured, call_llm_text


class _Plan(BaseModel):
    mode: str = "unsupported"
    intent_summary: str = ""


GOOD = '{"mode": "new_search", "intent_summary": "find mice"}'


def _tool_raw(payload: str) -> dict:
    """What chat_structured keeps as `raw`: chat_with_tools' normalised response."""
    return {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "t1", "name": "emit_plan", "input": json.loads(payload)},
    ]}


class _ToolClient:
    """Bedrock-shaped: answers on the forced-tool path, one scripted output per call."""

    provider = "bedrock"

    def __init__(self, outputs, *, prose=False, reasoning_blocks=0, reject=False, plain=None):
        self._outputs = list(outputs)
        self._prose = prose
        self._reasoning_blocks = reasoning_blocks
        self._reject = reject
        self._plain = plain

    def chat_structured(self, *, messages, system, model, schema, schema_name, **kw):
        if self._reject:
            raise LLMStructuredUnsupportedError("toolChoice is not supported for this model")
        content = self._outputs.pop(0) if len(self._outputs) > 1 else self._outputs[0]
        raw = ({"stop_reason": "end_turn", "content": [{"type": "text", "text": content}]}
               if self._prose else _tool_raw(content))
        return LLMResponse(
            content=content, raw=raw, usage={"prompt_tokens": 10, "completion_tokens": 2},
            model=model, provider=self.provider,
            metadata={"stop_reason": raw["stop_reason"], "structured_via": "tool_use",
                      "reasoning_blocks": self._reasoning_blocks},
        )

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        return LLMResponse(content=self._plain, raw=None, usage=None, model=model,
                           provider=self.provider, metadata={"reasoning_blocks": 0})


class _GeminiLike:
    provider = "gcp"

    def __init__(self, text):
        self.text = text

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        return LLMResponse(content=self.text, raw=None, usage=None, model=model,
                           provider=self.provider, metadata=None)


class _Config:
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP: dict = {}
    AGENT_MODEL_CATALOG: dict = {}

    def __init__(self, client, log_dir):
        self.LLM_CLIENT = client
        self.LLM_MODEL = "m"
        self.LLM_CLIENTS = {"anth": client}
        self.LOG_DIR = str(log_dir)


def _ledger(tmp_path) -> list[dict]:
    path = tmp_path / "llm_calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _structured(client, tmp_path, **kw):
    return call_llm_structured(
        _Config(client, tmp_path), "q", _Plan,
        messages=[{"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="parser", **kw,
    )


def test_a_forced_tool_answer_is_recorded_as_tool_use(tmp_path):
    _structured(_ToolClient([GOOD]), tmp_path)

    [row] = _ledger(tmp_path)
    assert row["outcome"] == "ok"
    assert row["structured_via"] == "tool_use"
    assert row["repair_turn"] is False
    assert row["reasoning_present"] is False


def test_a_forced_tool_answered_in_prose_is_not_recorded_as_tool_use(tmp_path):
    """chat_structured hands the text back when no tool block came, and still stamps
    its metadata tool_use; the ledger must not repeat that."""
    _structured(_ToolClient([GOOD], prose=True), tmp_path)

    assert _ledger(tmp_path)[0]["structured_via"] == "tool_use_prose"


def test_the_attempt_after_a_rejected_output_is_marked_as_a_repair_turn(tmp_path):
    _structured(
        _ToolClient(["{}", GOOD]), tmp_path,
        result_check=lambda plan: None if plan.intent_summary else "no plan",
    )

    rows = _ledger(tmp_path)
    assert [(r["attempt"], r["repair_turn"]) for r in rows] == [(1, False), (2, True)]
    assert {r["structured_via"] for r in rows} == {"tool_use"}


def test_a_reasoning_block_in_the_response_is_recorded(tmp_path):
    _structured(_ToolClient([GOOD], reasoning_blocks=2), tmp_path)

    assert _ledger(tmp_path)[0]["reasoning_present"] is True


def test_a_schema_the_model_refused_falls_back_to_json_in_the_prompt(tmp_path):
    """BedrockClient.chat takes response_format and never sends it, so the plain retry
    is JSON by prompt alone, not JSON mode."""
    _structured(_ToolClient([GOOD], reject=True, plain=GOOD), tmp_path)

    assert _ledger(tmp_path)[0]["structured_via"] == "prompt"


def test_a_provider_that_sends_json_mode_is_recorded_as_json_mode(tmp_path):
    _structured(_GeminiLike(GOOD), tmp_path)

    row = _ledger(tmp_path)[0]
    assert row["structured_via"] == "json_mode"
    assert row["reasoning_present"] is None  # nothing in this response says either way


def test_a_free_text_call_asks_for_no_structure(tmp_path):
    client = _GeminiLike("Twelve mice.")
    call_llm_text(
        _Config(client, tmp_path), messages=[{"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="chatter",
    )

    row = _ledger(tmp_path)[0]
    assert row["structured_via"] is None
    assert row["repair_turn"] is False


def test_the_keys_the_scorer_prices_from_are_unchanged(tmp_path):
    _structured(_ToolClient([GOOD]), tmp_path)

    row = _ledger(tmp_path)[0]
    for key in ("ts", "agent", "provider", "model", "attempt", "outcome", "elapsed_ms",
                "prompt_tokens", "completion_tokens", "stop_reason"):
        assert key in row, key
    assert (row["prompt_tokens"], row["completion_tokens"]) == (10, 2)


def test_a_record_without_a_response_gets_no_new_keys():
    import time

    entry = _ledger_entry("parser", "m", SimpleNamespace(provider="bedrock"), 0, "timeout",
                          time.perf_counter(), err=TimeoutError("slow"))
    assert "structured_via" not in entry
    assert "reasoning_present" not in entry


def test_an_anthropic_thinking_block_is_seen_in_the_raw_response():
    import time

    raw = SimpleNamespace(content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text")])
    resp = LLMResponse(content=GOOD, raw=raw, usage=None, model="m", provider="anthropic")
    entry = _ledger_entry("parser", "m", SimpleNamespace(provider="anthropic"), 0, "ok",
                          time.perf_counter(), resp=resp)
    assert entry["reasoning_present"] is True


# --------------------------------------------------------------------------
# BedrockClient: the forced-tool path used to drop reasoning blocks outright.
# --------------------------------------------------------------------------

class _StubConverse:
    def __init__(self, response):
        self.response = response

    def converse(self, **kwargs):
        return self.response


def _converse_with_reasoning():
    return {
        "stopReason": "tool_use",
        "output": {"message": {"content": [
            {"reasoningContent": {"reasoningText": {"text": "The user wants mice.", "signature": "s"}}},
            {"toolUse": {"toolUseId": "tu_1", "name": "emit_plan", "input": json.loads(GOOD)}},
        ]}},
        "usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
        "ResponseMetadata": {"RequestId": "req-1", "HTTPStatusCode": 200, "RetryAttempts": 0},
    }


def test_chat_structured_counts_the_reasoning_blocks_it_does_not_return():
    client = BedrockClient(region="us-east-1")
    client.client = _StubConverse(_converse_with_reasoning())

    resp = client.chat_structured(
        messages=[{"role": "user", "content": "q"}], system=None,
        model="us.anthropic.claude-opus-4-7",
        schema=pydantic_to_tool_schema(_Plan), schema_name="emit_plan",
    )

    assert json.loads(resp.content)["mode"] == "new_search"
    assert resp.metadata["reasoning_blocks"] == 1
    assert resp.metadata["structured_via"] == "tool_use"


def test_chat_counts_reasoning_blocks_too():
    client = BedrockClient(region="us-east-1")
    response = _converse_with_reasoning()
    response["output"]["message"]["content"][1] = {"text": GOOD}
    client.client = _StubConverse(response)

    resp = client.chat(messages=[{"role": "user", "content": "q"}], model="m")

    assert resp.metadata["reasoning_blocks"] == 1

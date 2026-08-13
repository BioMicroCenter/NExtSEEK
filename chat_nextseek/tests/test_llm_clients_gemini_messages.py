"""
Regression lock for T0.1: GeminiClient must send the whole conversation.

`_convert_messages` used to build `{"text": [...]}` (no role) and `chat()` passed
`contents[0]['text']`, so only the first message ever reached the model. That made
`schema_helper`'s structured-output repair loop a no-op for every gcp-profile agent:
the correction turn was built, appended, and silently discarded, so attempt 2 re-sent
an identical prompt and got identical output.

Observed in the 2026-07-27 run: Gemini prompt tokens across three repair attempts were
2862 -> 2862 -> 2862, while Bedrock's loop on the same suite grew 17283 -> 25240 -> 25881.

No live calls — the genai client is faked and injected.
"""
from __future__ import annotations

import types

import pytest
from pydantic import BaseModel

from chat_nextseek.llm_clients import GeminiClient, LLMError
from chat_nextseek.schemas.schema_helper import call_llm_structured


# --------------------------------------------------------------------------- fakes


class _FakeCandidate:
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, text, finish_reason="STOP"):
        self.text = text
        self.candidates = [_FakeCandidate(finish_reason)]
        self.usage_metadata = types.SimpleNamespace(
            prompt_token_count=11, candidates_token_count=22, total_token_count=33
        )


class _Recorder:
    """Stands in for `client.models.generate_content` and records every call."""

    def __init__(self, texts):
        self._texts = list(texts)
        self.calls: list[dict] = []

    def __call__(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        text = self._texts.pop(0) if self._texts else "{}"
        return _FakeResponse(text)


def _client(recorder) -> GeminiClient:
    """Build a GeminiClient without importing google.genai or touching the network."""
    client = GeminiClient.__new__(GeminiClient)
    client.client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=recorder))
    return client


CONVERSATION = [
    {"role": "system", "content": "You are a router."},
    {"role": "user", "content": "Find me mice treated with NDMA"},
    {"role": "assistant", "content": '{"mode": "bogus"}'},
    {"role": "user", "content": "Your previous output did not validate for schema QueryPlan."},
]


# --------------------------------------------------------------- _convert_messages


def test_convert_messages_keeps_every_turn_with_roles():
    contents, system_instruction = _client(_Recorder([]))._convert_messages(CONVERSATION)

    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert system_instruction == "You are a router."


def test_convert_messages_uses_the_parts_text_wire_shape():
    contents, _ = _client(_Recorder([]))._convert_messages(CONVERSATION)

    # google-genai expects parts to be part dicts, not bare strings.
    assert contents[0] == {"role": "user", "parts": [{"text": "Find me mice treated with NDMA"}]}
    assert contents[1]["parts"] == [{"text": '{"mode": "bogus"}'}]


def test_convert_messages_joins_multiple_system_messages():
    contents, system_instruction = _client(_Recorder([]))._convert_messages(
        [
            {"role": "system", "content": "First."},
            {"role": "system", "content": "Second."},
            {"role": "user", "content": "Go."},
        ]
    )

    assert system_instruction == "First.\n\nSecond."
    assert len(contents) == 1


# ------------------------------------------------------------------------- chat()


def test_chat_sends_the_whole_conversation_not_just_the_first_message():
    recorder = _Recorder(['{"ok": true}'])

    _client(recorder).chat(messages=CONVERSATION, model="gemini-2.5-pro")

    sent = recorder.calls[0]["contents"]
    assert isinstance(sent, list), "contents must be the full list, not contents[0]['text']"
    assert len(sent) == 3
    flat = str(sent)
    assert "Find me mice treated with NDMA" in flat
    assert "did not validate for schema" in flat


def test_chat_records_finish_reason_in_metadata():
    recorder = _Recorder(['{"ok": true}'])

    resp = _client(recorder).chat(messages=CONVERSATION, model="gemini-2.5-pro")

    assert resp.metadata is not None
    assert resp.metadata.get("finish_reason") == "STOP"


def test_chat_raises_a_typed_error_when_there_is_nothing_to_send():
    recorder = _Recorder([])

    with pytest.raises(LLMError):
        _client(recorder).chat(
            messages=[{"role": "system", "content": "only a system prompt"}],
            model="gemini-2.5-pro",
        )

    assert recorder.calls == [], "must not call the provider with empty contents"


# --------------------------------------------------------- the repair loop end-to-end


class _Plan(BaseModel):
    mode: str


def test_repair_loop_reaches_the_model_on_the_second_attempt():
    """The regression lock: attempt 2 must actually carry the correction turn."""
    recorder = _Recorder(['{"wrong_field": 1}', '{"mode": "new_search"}'])
    config = types.SimpleNamespace(LOG_DIR=None, LLM_CLIENT=None, LLM_MODEL="gemini-2.5-pro")

    plan = call_llm_structured(
        config,
        "Find me mice treated with NDMA",
        _Plan,
        system="You are a router.",
        client=_client(recorder),
        model_name="gemini-2.5-pro",
        retries=2,
    )

    assert plan.mode == "new_search"
    assert len(recorder.calls) == 2

    first = str(recorder.calls[0]["contents"])
    second = str(recorder.calls[1]["contents"])
    assert "did not validate for schema" not in first
    assert "did not validate for schema" in second, (
        "the repair turn was discarded — attempt 2 re-sent an identical prompt"
    )
    assert len(recorder.calls[1]["contents"]) > len(recorder.calls[0]["contents"])

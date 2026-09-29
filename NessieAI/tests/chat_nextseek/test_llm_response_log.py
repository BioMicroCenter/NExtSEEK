"""The raw response of every model call lands in a file under LOG_DIR.

``call_llm_structured`` and ``call_llm_text`` handed ``config.LOG_DIR``, a directory,
to ``log_prompt``, which opens its argument for append. The open raised
IsADirectoryError, ``log_prompt`` swallowed it, and no raw response was ever written.
That is why a parser output that validated to an empty plan could not be diagnosed: the
ledger (``llm_calls.jsonl``) says a call happened and how it ended, and nothing said
what the model actually returned.

The response log keeps what came back, not what was sent: the prompts carry whole
catalogs, several calls run per turn, and the file is shared by every turn the process
serves, so it records the size of the prompt and caps both a line and the file.
"""
from __future__ import annotations

import json
import logging

import pytest
from pydantic import BaseModel

from chat_nextseek.helpers import prompts as prompts_mod
from chat_nextseek.llm_clients import LLMResponse
from chat_nextseek.schemas import schema_helper
from chat_nextseek.schemas.schema_helper import call_llm_structured, call_llm_text


class _Plan(BaseModel):
    mode: str = "unsupported"
    intent_summary: str = ""


class _Scripted:
    """A Bedrock-shaped client that replays one output per call on the forced-tool path."""

    provider = "bedrock"

    def __init__(self, outputs, request_id="req-1"):
        self._outputs = list(outputs)
        self.calls = 0
        self._request_id = request_id

    def _next(self):
        self.calls += 1
        return self._outputs.pop(0) if len(self._outputs) > 1 else self._outputs[0]

    def chat_structured(self, *, messages, system, model, schema, schema_name, **kw):
        return LLMResponse(
            content=self._next(), raw=None, usage=None, model=model, provider=self.provider,
            metadata={"structured_via": "tool_use", "request_id": self._request_id},
        )

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        return LLMResponse(
            content=self._next(), raw=None, usage=None, model=model, provider=self.provider,
            metadata={"request_id": self._request_id},
        )


class _Config:
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP: dict = {}
    AGENT_MODEL_CATALOG: dict = {}

    def __init__(self, client, log_dir):
        self.LLM_CLIENT = client
        self.LLM_MODEL = "m"
        self.LLM_CLIENTS = {"anth": client}
        self.LOG_DIR = str(log_dir)


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(autouse=True)
def _fresh_warning_state(monkeypatch):
    monkeypatch.setattr(prompts_mod, "_failure_reported_stages", set(), raising=False)


def test_a_structured_call_writes_its_raw_response_under_log_dir(tmp_path):
    client = _Scripted(['{"mode": "new_search", "intent_summary": "find mice"}'])

    call_llm_structured(
        _Config(client, tmp_path), "q", _Plan,
        messages=[{"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="parser",
        log_label="parser", log_payload_extra={"user_query": "q"},
    )

    lines = _lines(tmp_path / "llm_responses.jsonl")
    assert len(lines) == 1
    assert lines[0]["stage"] == "parser"
    assert lines[0]["response"] == '{"mode": "new_search", "intent_summary": "find mice"}'
    assert lines[0]["attempt"] == 0
    assert lines[0]["user_query"] == "q"
    assert lines[0]["request_id"] == "req-1"


def test_every_attempt_is_kept_including_the_one_the_repair_loop_rejected(tmp_path):
    """The rejected output is the evidence the empty-plan diagnosis was missing."""
    client = _Scripted(["{}", '{"mode": "new_search", "intent_summary": "x"}'])

    call_llm_structured(
        _Config(client, tmp_path), "q", _Plan,
        messages=[{"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="parser", log_label="parser",
        result_check=lambda plan: None if plan.intent_summary else "no plan",
    )

    lines = _lines(tmp_path / "llm_responses.jsonl")
    assert [(l["attempt"], l["response"]) for l in lines] == [
        (0, "{}"),
        (1, '{"mode": "new_search", "intent_summary": "x"}'),
    ]


def test_a_free_text_call_writes_its_reply_too(tmp_path):
    client = _Scripted(["Here are your 12 mice."])

    call_llm_text(
        _Config(client, tmp_path),
        messages=[{"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="chatter", log_label="chatter",
    )

    lines = _lines(tmp_path / "llm_responses.jsonl")
    assert [(l["stage"], l["response"]) for l in lines] == [("chatter", "Here are your 12 mice.")]


def test_the_log_keeps_the_response_and_only_the_size_of_the_prompt(tmp_path):
    client = _Scripted(['{"mode": "new_search", "intent_summary": "x"}'])
    catalog = "x" * 50_000

    call_llm_structured(
        _Config(client, tmp_path), "q", _Plan,
        messages=[{"role": "system", "content": catalog}, {"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="parser", log_label="parser",
    )

    line = _lines(tmp_path / "llm_responses.jsonl")[0]
    assert "messages" not in line
    assert line["messages_count"] == 2
    assert line["messages_chars"] >= 50_000
    assert catalog not in (tmp_path / "llm_responses.jsonl").read_text(encoding="utf-8")


def test_a_very_long_response_is_cut_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_helper, "RESPONSE_LOG_MAX_CHARS", 10, raising=False)
    client = _Scripted(["y" * 25])

    call_llm_text(
        _Config(client, tmp_path),
        messages=[{"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="chatter", log_label="chatter",
    )

    line = _lines(tmp_path / "llm_responses.jsonl")[0]
    assert line["response"] == "y" * 10
    assert line["response_chars"] == 25
    assert line["response_truncated"] is True


def test_the_file_rolls_over_instead_of_growing_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_helper, "RESPONSE_LOG_MAX_BYTES", 200, raising=False)
    client = _Scripted(["z" * 150])
    config = _Config(client, tmp_path)

    for _ in range(3):
        call_llm_text(
            config, messages=[{"role": "user", "content": "q"}],
            client=client, model_name="m", agent_label="chatter", log_label="chatter",
        )

    current = tmp_path / "llm_responses.jsonl"
    rolled = tmp_path / "llm_responses.jsonl.1"
    assert rolled.exists()
    assert len(_lines(current)) == 1
    assert current.stat().st_size + rolled.stat().st_size < 3 * 400


def test_no_label_means_no_line(tmp_path):
    client = _Scripted(['{"mode": "new_search", "intent_summary": "x"}'])

    call_llm_structured(
        _Config(client, tmp_path), "q", _Plan,
        messages=[{"role": "user", "content": "q"}],
        client=client, model_name="m", agent_label="parser",
    )

    assert not (tmp_path / "llm_responses.jsonl").exists()


# --------------------------------------------------------------------------
# log_prompt: a write that fails is reported once, not swallowed.
# --------------------------------------------------------------------------

def test_log_prompt_writes_to_the_file_it_is_given(tmp_path):
    target = tmp_path / "prompts.jsonl"

    prompts_mod.log_prompt(str(target), "stage", {"response": "r"})

    assert _lines(target)[0]["response"] == "r"


def test_a_failed_write_is_logged_at_warning_once(tmp_path, caplog):
    """A directory where a file belongs is the defect this warning would have named."""
    with caplog.at_level(logging.WARNING, logger=prompts_mod.__name__):
        prompts_mod.log_prompt(str(tmp_path), "parser", {"response": "r"})
        prompts_mod.log_prompt(str(tmp_path), "parser", {"response": "r"})

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(tmp_path) in warnings[0].getMessage()
    assert "parser" in warnings[0].getMessage()


def test_one_failing_stage_does_not_silence_another(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=prompts_mod.__name__):
        prompts_mod.log_prompt(str(tmp_path), "chatter", {"response": "r"})
        prompts_mod.log_prompt(str(tmp_path), "parser", {"response": "r"})

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


def test_a_failed_write_never_reaches_the_caller(tmp_path):
    prompts_mod.log_prompt(str(tmp_path / "missing-dir" / "x.jsonl"), "stage", {"response": "r"})

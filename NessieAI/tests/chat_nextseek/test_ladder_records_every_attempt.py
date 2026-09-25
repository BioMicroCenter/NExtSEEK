"""Every model attempt the ladder makes gets one ledger record, whatever ended it (F1).

``_call_with_recovery`` and ``tool_loop.call_tools`` wrote a ledger record (and told the turn's
cost collector) only for the errors they know: an ``LLM*`` class. Anything else, for example a
raw botocore ``ClientError`` from ``chat_with_tools`` or a bug in a client, left the ladder with
no ledger line, so the attempt was invisible to the ledger and to the turn's cost.

Pinned here:

* such an attempt is recorded once, with outcome ``error`` and the exception named;
* the exception still propagates unchanged (no move, no wrapping);
* the attempts the ladder already recorded are not recorded twice;
* ``turn_spend`` is told about it once, as an unobserved call: whether it was billed is not
  known, so the turn's cost is partial.

No network: every client is a fake.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from pydantic import BaseModel

from chat_nextseek import tool_loop, turn_spend
from chat_nextseek.llm_clients import LLMError, LLMFatalError, LLMResponse, LLMServiceUnavailableError
from chat_nextseek.schemas.schema_helper import call_llm_structured, call_llm_text

PRIMARY = "primary-model"
FALLBACK = "fallback-1"

RAW_ERRORS = [
    ClientError({"Error": {"Code": "SomethingNewException", "Message": "a code nobody typed"}}, "Converse"),
    KeyError("output"),
    RuntimeError("a bug in a client"),
]
RAW_IDS = ["unknown-client-error", "key-error", "runtime-error"]


class _Client:
    """Replays outcomes: a string is the completion, an exception is raised."""

    def __init__(self, provider, outcomes):
        self.provider = provider
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def reset_connections(self):
        return True

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.calls.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResponse(content=outcome, raw=None, usage={"prompt_tokens": 10, "completion_tokens": 5},
                           model=model, provider=self.provider, metadata={})

    def chat_with_tools(self, *, model, **kw):
        self.calls.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": outcome}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "metadata": {}}


def _config(primary, fallback, agent, log_dir):
    return SimpleNamespace(
        LOG_DIR=log_dir, LLM_CLIENT=primary, LLM_MODEL=PRIMARY, _CATALOG_KEY="default",
        _THINKING_BUDGET_MAP={None: None}, LLM_CLIENTS={"anth": primary, "gcp": fallback},
        AGENT_MODEL_CATALOG={"gcp:current": {agent: {"provider": "gcp", "model": FALLBACK, "thinking_level": None}},
                             "_fallback": {agent: {"provider": "gcp", "model": FALLBACK, "thinking_level": None}}},
    )


class _Plan(BaseModel):
    mode: str


def _ledger(tmp_path):
    path = tmp_path / "llm_calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _structured(config, primary):
    return call_llm_structured(config, "q", _Plan, system="s", client=primary, model_name=PRIMARY,
                               agent_label="parser")


def _text(config, primary):
    return call_llm_text(config, messages=[{"role": "user", "content": "hi"}], client=primary,
                         model_name=PRIMARY, agent_label="chatter")


def _tools(config, primary):
    return tool_loop.call_tools(config, messages=[], tools=[], system="s", model_name=PRIMARY, client=primary,
                                agent_label="followup")


LADDERS = [(_structured, "parser"), (_text, "chatter"), (_tools, "followup")]
LADDER_IDS = ["structured", "text", "tool-loop"]


@pytest.mark.parametrize("call, agent", LADDERS, ids=LADDER_IDS)
@pytest.mark.parametrize("raw", RAW_ERRORS, ids=RAW_IDS)
def test_an_untyped_error_is_recorded_once_and_still_propagates_unchanged(tmp_path, call, agent, raw):
    primary = _Client("bedrock", [raw])
    fallback = _Client("gcp", ['{"mode": "graph_query"}'])
    with turn_spend.collecting():
        with pytest.raises(type(raw)) as excinfo:
            call(_config(primary, fallback, agent, str(tmp_path)), primary)
        record = turn_spend.turn_record()

    assert excinfo.value is raw, "the ladder does not wrap it"
    assert fallback.calls == [], "and does not move on it"
    (entry,) = _ledger(tmp_path)
    assert entry["model"] == PRIMARY and entry["agent"] == agent and entry["outcome"] == "error"
    assert entry["error"].startswith(f"{type(raw).__name__}: ")
    assert record["cost_partial"] is True
    (unobserved,) = record["cost"]["unobserved_calls"]
    assert unobserved["model"] == PRIMARY and unobserved["agent"] == agent


@pytest.mark.parametrize("call, agent", LADDERS, ids=LADDER_IDS)
def test_an_untyped_error_after_a_move_is_recorded_on_the_model_it_moved_to(tmp_path, call, agent):
    raw = RuntimeError("a bug in the fallback client")
    primary = _Client("bedrock", [LLMServiceUnavailableError("503")])
    fallback = _Client("gcp", [raw])
    with pytest.raises(RuntimeError):
        call(_config(primary, fallback, agent, str(tmp_path)), primary)
    entries = _ledger(tmp_path)
    assert [(e["model"], e["outcome"]) for e in entries] == [(PRIMARY, "service_unavailable"), (FALLBACK, "error")]
    assert entries[1]["fallback_reason"] == "unavailable", "the move is still named on the attempt after it"


@pytest.mark.parametrize("call, agent", LADDERS, ids=LADDER_IDS)
def test_the_attempts_already_recorded_are_not_recorded_twice(tmp_path, call, agent):
    primary = _Client("bedrock", [LLMServiceUnavailableError("503")])
    fallback = _Client("gcp", [LLMServiceUnavailableError("503 again")])
    with turn_spend.collecting():
        with pytest.raises(LLMFatalError):
            call(_config(primary, fallback, agent, str(tmp_path)), primary)
        record = turn_spend.turn_record()
    assert [(e["model"], e["outcome"]) for e in _ledger(tmp_path)] == [
        (PRIMARY, "service_unavailable"), (FALLBACK, "service_unavailable"),
    ]
    assert record["cost_partial"] is False


def test_a_bare_llm_error_is_recorded_once_as_before(tmp_path):
    primary = _Client("bedrock", [LLMError("400 bad request")])
    fallback = _Client("gcp", ["never"])
    with turn_spend.collecting():
        with pytest.raises(LLMError):
            _tools(_config(primary, fallback, "followup", str(tmp_path)), primary)
        record = turn_spend.turn_record()
    assert [(e["model"], e["outcome"]) for e in _ledger(tmp_path)] == [(PRIMARY, "error")]
    assert record["cost_partial"] is False, "a 400 is not billed"


def test_the_collector_counts_an_untyped_error_as_unobserved():
    with turn_spend.collecting():
        turn_spend.record_call({"agent": "parser", "provider": "bedrock", "model": PRIMARY, "attempt": 1,
                                "outcome": "error"}, err=RuntimeError("boom"))
        record = turn_spend.turn_record()
    assert record["cost_partial"] is True
    assert record["total_cost_usd"] is None, "spend that was never seen is never reported as zero"
    assert len(record["cost"]["unobserved_calls"]) == 1

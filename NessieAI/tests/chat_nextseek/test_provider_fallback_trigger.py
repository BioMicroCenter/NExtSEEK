"""One fallback trigger: a timeout, an empty body and a 503 all move to the next provider.

Production task 621 (2026-09-23, build b2352f55): a paper-by-title question, the parser
timed out at 35 s, retried the SAME provider on a 60 s window, timed out again, and the
turn ended with ``parser_plan.metadata.failure = transport_timeout``. The provider chain
existed and was never asked, because only a 503 (and an empty ``""`` completion,
re-raised as one) reached it.

The rule pinned here, in ``_call_with_recovery`` and ``tool_loop.call_tools``:

* a timeout, an empty body and a 503 are fallback-eligible;
* on any of them the call moves to the next provider, ONCE;
* when that provider fails too the failure is final (``LLMFatalError`` for a 503 or an
  empty body, the ``LLMTimeoutError`` itself for a timeout, which the parser maps to
  ``transport_timeout``);
* the move adds no wait: a timeout's retry runs on ``timeout_retry_seconds`` whether it
  goes to the same provider or the next, so the parser's worst case stays 35 s + 60 s;
* a non-eligible error (a bare ``LLMError`` such as a 400) never moves.
"""
from __future__ import annotations

import inspect
import time

import pytest
from pydantic import BaseModel

from chat_nextseek.llm_clients import (
    LLMError,
    LLMFatalError,
    LLMResponse,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from chat_nextseek.schemas import schema_helper
from chat_nextseek.schemas.schema_helper import (
    MAX_PROVIDER_SWITCHES,
    call_llm_structured,
    call_llm_text,
    empty_output_problem,
)


class _Plan(BaseModel):
    mode: str = "unsupported"


class _Required(BaseModel):
    mode: str


class _Client:
    """A provider stand-in that replays scripted outcomes and records every call.

    An outcome is a string (the completion), an exception (raised), or a float (sleep
    that many seconds, then answer ``late``) to drive the real wall-clock wrapper.
    """

    def __init__(self, provider: str, outcomes: list, late: str = '{"mode": "late"}'):
        self.provider = provider
        self.outcomes = list(outcomes)
        self.late = late
        self.calls: list[str] = []
        self.recycled = 0

    def reset_connections(self):
        self.recycled += 1
        return True

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.calls.append(model)
        outcome = self.outcomes.pop(0) if self.outcomes else ""
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, float):
            time.sleep(outcome)
            outcome = self.late
        return LLMResponse(
            content=outcome, raw=None, usage=None, model=model,
            provider=self.provider, metadata={"stop_reason": "end_turn"},
        )


class _Config:
    """What the ladder reads. ``_FALLBACK_CHAINS[("default", "anth")]`` is
    ["gcp:current", "anth:lite", "gcp:lite"], so a Bedrock primary falls to gcp:current
    first and gcp:lite is a second fallback the one-move rule must never reach."""

    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None, "low": 4000, "medium": 8000, "high": 16000}
    LOG_DIR = "/tmp"

    def __init__(self, primary, fallback=None, agent="parser"):
        self.LLM_CLIENT = primary
        self.LLM_MODEL = "primary-model"
        self.LLM_CLIENTS = {"anth": primary}
        self.AGENT_MODEL_CATALOG = {}
        if fallback is not None:
            self.LLM_CLIENTS["gcp"] = fallback
            self.AGENT_MODEL_CATALOG = {
                "gcp:current": {agent: {"provider": "gcp", "model": "fallback-1", "thinking_level": None}},
                "gcp:lite": {agent: {"provider": "gcp", "model": "fallback-2", "thinking_level": None}},
            }


def _structured(config, primary, **kw):
    kw.setdefault("agent_label", "parser")
    return call_llm_structured(
        config, "q", kw.pop("model", _Plan), system="s", client=primary,
        model_name="primary-model", **kw,
    )


def _text(config, primary, **kw):
    kw.setdefault("agent_label", "chatter")
    return call_llm_text(
        config, messages=[{"role": "user", "content": "hi"}],
        client=primary, model_name="primary-model", **kw,
    )


# --------------------------------------------------------------------------
# The three eligible failures each move to the next provider.
# --------------------------------------------------------------------------

def test_a_timeout_moves_to_the_next_provider_and_succeeds():
    """Task 621: the primary times out, the retry goes to the fallback provider and
    answers, instead of asking the same provider again."""
    primary = _Client("bedrock", [LLMTimeoutError("LLM call timed out after 35 seconds")])
    fallback = _Client("gcp", ['{"mode": "graph_query"}'])
    plan = _structured(_Config(primary, fallback), primary, timeout_seconds=35, timeout_retry_seconds=60)
    assert plan.mode == "graph_query"
    assert primary.calls == ["primary-model"]
    assert fallback.calls == ["fallback-1"]
    assert primary.recycled == 1, "the timed-out client's pool is still dropped"


def test_a_real_wall_clock_timeout_moves_and_the_retry_gets_the_retry_window(monkeypatch):
    windows: list[tuple[str, float]] = []
    real = schema_helper._call_llm_with_timeout

    def spy(**kw):
        windows.append((kw["client"].provider, kw["timeout_seconds"]))
        return real(**kw)

    monkeypatch.setattr(schema_helper, "_call_llm_with_timeout", spy)
    primary = _Client("bedrock", [2.0])
    fallback = _Client("gcp", ['{"mode": "graph_query"}'])
    t0 = time.perf_counter()
    plan = _structured(_Config(primary, fallback), primary, timeout_seconds=0.2, timeout_retry_seconds=0.5)
    elapsed = time.perf_counter() - t0
    assert plan.mode == "graph_query"
    assert windows == [("bedrock", 0.2), ("gcp", 0.5)]
    assert elapsed < 1.5, "the move must not wait out the stalled primary"


def test_an_empty_text_body_moves_to_the_next_provider():
    primary = _Client("bedrock", ["  \n"])
    fallback = _Client("gcp", ["Here are your 129 samples."])
    config = _Config(primary, fallback, agent="chatter")
    assert _text(config, primary) == "Here are your 129 samples."
    assert primary.calls == ["primary-model"]


def test_a_fence_with_nothing_in_it_is_an_empty_body():
    """A structured call expected content; a code fence around nothing is none."""
    primary = _Client("bedrock", ["```json\n```"])
    fallback = _Client("gcp", ['{"mode": "graph_query"}'])
    plan = _structured(_Config(primary, fallback), primary, model=_Required)
    assert plan.mode == "graph_query"
    assert primary.calls == ["primary-model"]


def test_an_empty_object_is_parseable_content_and_keeps_its_repair_turn():
    """"{}" parses, so it is not an empty body: a result_check that rejects it sends it
    through the repair turn on the same model (the empty-plan guards pin that), and a
    schema that accepts it takes it as the answer."""
    primary = _Client("bedrock", ["{}", '{"mode": "graph_query"}'])
    fallback = _Client("gcp", [])
    plan = _structured(_Config(primary, fallback), primary, result_check=empty_output_problem)
    assert plan.mode == "graph_query"
    assert primary.calls == ["primary-model", "primary-model"]
    assert fallback.calls == []

    primary = _Client("bedrock", ["{}"])
    fallback = _Client("gcp", ['{"mode": "graph_query"}'])
    assert _structured(_Config(primary, fallback), primary).mode == "unsupported"
    assert fallback.calls == []


def test_non_empty_invalid_json_still_uses_the_repair_loop_not_the_chain():
    primary = _Client("bedrock", ["not json", '{"mode": "graph_query"}'])
    fallback = _Client("gcp", [])
    plan = _structured(_Config(primary, fallback), primary)
    assert plan.mode == "graph_query"
    assert primary.calls == ["primary-model", "primary-model"]
    assert fallback.calls == []


def test_a_503_still_moves():
    primary = _Client("bedrock", [LLMServiceUnavailableError("503 UNAVAILABLE")])
    fallback = _Client("gcp", ['{"mode": "graph_query"}'])
    assert _structured(_Config(primary, fallback), primary).mode == "graph_query"
    assert fallback.calls == ["fallback-1"]


def test_the_move_gets_an_attempt_of_its_own():
    """With retries=0 a 503 used to spend the only attempt on the switch and end the
    call as a parse error. The move must be followed by a call to the new provider."""
    primary = _Client("bedrock", [LLMServiceUnavailableError("503")])
    fallback = _Client("gcp", ['{"mode": "graph_query"}'])
    assert _structured(_Config(primary, fallback), primary, retries=0).mode == "graph_query"


@pytest.mark.parametrize("first", [
    LLMTimeoutError("timed out"),
    LLMServiceUnavailableError("503"),
    "",
])
def test_the_chatter_moves_on_every_eligible_failure(first):
    primary = _Client("gcp", [first, first])  # the same failure again if re-asked
    fallback = _Client("bedrock", ["Here are your samples."])
    config = _Config(primary, agent="chatter")
    config.LLM_CLIENTS = {"gcp": primary, "anth": fallback}
    config.AGENT_MODEL_CATALOG = {
        "anth:current": {"chatter": {"provider": "anth", "model": "fallback-1", "thinking_level": None}},
    }
    assert _text(config, primary) == "Here are your samples."
    assert fallback.calls == ["fallback-1"]


# --------------------------------------------------------------------------
# Bounded: one move, then the honest failure.
# --------------------------------------------------------------------------

def test_the_move_limit_is_one():
    assert MAX_PROVIDER_SWITCHES == 1


def test_503_then_503_is_fatal_and_the_second_fallback_is_never_asked():
    primary = _Client("bedrock", [LLMServiceUnavailableError("503")])
    fallback = _Client("gcp", [LLMServiceUnavailableError("503"), '{"mode": "graph_query"}'])
    with pytest.raises(LLMFatalError):
        _structured(_Config(primary, fallback), primary)
    assert fallback.calls == ["fallback-1"], "one move, not a walk of the chain"


def test_empty_then_empty_is_fatal():
    primary = _Client("bedrock", [""])
    fallback = _Client("gcp", ["", '{"mode": "graph_query"}'])
    with pytest.raises(LLMFatalError) as excinfo:
        _structured(_Config(primary, fallback), primary)
    assert "empty completion" in str(excinfo.value)
    assert fallback.calls == ["fallback-1"]


def test_timeout_then_timeout_raises_the_timeout_after_two_calls():
    """The parser's honest failure depends on seeing LLMTimeoutError, not a fatal."""
    primary = _Client("bedrock", [LLMTimeoutError("t1")])
    fallback = _Client("gcp", [LLMTimeoutError("t2"), '{"mode": "graph_query"}'])
    with pytest.raises(LLMTimeoutError):
        _structured(_Config(primary, fallback), primary, timeout_seconds=35, timeout_retry_seconds=60)
    assert primary.calls == ["primary-model"]
    assert fallback.calls == ["fallback-1"]


def test_503_then_a_timeout_on_the_fallback_is_final():
    primary = _Client("bedrock", [LLMServiceUnavailableError("503")])
    fallback = _Client("gcp", [LLMTimeoutError("t"), '{"mode": "graph_query"}'])
    with pytest.raises(LLMTimeoutError):
        _structured(_Config(primary, fallback), primary)
    assert fallback.calls == ["fallback-1"]
    assert primary.calls == ["primary-model"]


def test_timeout_then_503_on_the_fallback_is_fatal():
    primary = _Client("bedrock", [LLMTimeoutError("t")])
    fallback = _Client("gcp", [LLMServiceUnavailableError("503"), '{"mode": "graph_query"}'])
    with pytest.raises(LLMFatalError):
        _structured(_Config(primary, fallback), primary)
    assert fallback.calls == ["fallback-1"]


def test_a_timeout_with_no_chain_keeps_the_same_provider_retry():
    """Nowhere to move: the recycled-socket retry on the same provider stands, once."""
    primary = _Client("bedrock", [LLMTimeoutError("t1"), '{"mode": "graph_query"}'])
    assert _structured(_Config(primary), primary).mode == "graph_query"
    assert primary.calls == ["primary-model", "primary-model"]
    assert primary.recycled == 1

    primary = _Client("bedrock", [LLMTimeoutError("t1"), LLMTimeoutError("t2"), '{"mode": "x"}'])
    with pytest.raises(LLMTimeoutError):
        _structured(_Config(primary), primary)
    assert len(primary.calls) == 2


def test_timeout_retries_zero_still_means_no_retry():
    """entity.py's own retry passes timeout_retries=0; it must not grow a move."""
    primary = _Client("bedrock", [LLMTimeoutError("t")])
    fallback = _Client("gcp", ['{"mode": "graph_query"}'])
    with pytest.raises(LLMTimeoutError):
        _structured(_Config(primary, fallback), primary, timeout_retries=0)
    assert fallback.calls == []


# --------------------------------------------------------------------------
# Not eligible.
# --------------------------------------------------------------------------

def test_a_400_does_not_move():
    primary = _Client("bedrock", [LLMError("ValidationException: 400 malformed request")])
    fallback = _Client("gcp", ['{"mode": "graph_query"}'])
    with pytest.raises(LLMFatalError):
        _structured(_Config(primary, fallback), primary)
    assert fallback.calls == []


# --------------------------------------------------------------------------
# The two turn steps the operator named.
# --------------------------------------------------------------------------

def _parser_config(primary, fallback):
    config = _Config(primary, fallback)
    config.PARSER_SYSTEM_PROMPT = "plan"
    config.MIN_GRAPH_SCHEMA = {}
    config.FORCE_PARSER_MODE = None
    config.get_agent_model = lambda label: (primary, "primary-model", None)
    return config


@pytest.fixture
def parser_mod(monkeypatch):
    from chat_nextseek import chat_memory
    from chat_nextseek.agents import parser

    monkeypatch.setattr(parser, "build_recent_results_summary", lambda session: "")
    monkeypatch.setattr(parser, "_endpoints_for_prompt", lambda config, q: "[]")
    monkeypatch.setattr(chat_memory, "history_block", lambda session: "")
    monkeypatch.setattr(parser, "_apply_parser_guardrails", lambda q, plan, **kw: plan)
    return parser


def test_the_parser_step_moves_on_a_timeout(parser_mod):
    # The primary would time out again: before the fix that second timeout ended the step.
    primary = _Client("bedrock", [LLMTimeoutError("35 s"), LLMTimeoutError("60 s")])
    fallback = _Client("gcp", ['{"mode": "new_search", "intent_summary": "papers"}'])
    plan = parser_mod.parser_agent(object(), _parser_config(primary, fallback), "q", {})
    assert plan.mode == "new_search"
    assert (plan.metadata or {}).get("failure") is None


def test_the_parser_step_keeps_its_honest_failure_when_both_time_out(parser_mod):
    primary = _Client("bedrock", [LLMTimeoutError("t1")])
    fallback = _Client("gcp", [LLMTimeoutError("LLM call timed out after 60 seconds")])
    plan = parser_mod.parser_agent(object(), _parser_config(primary, fallback), "q", {})
    assert plan.mode == "unsupported"
    assert plan.metadata["failure"] == "transport_timeout"
    assert "temporary system fault" in plan.notes
    assert len(primary.calls) + len(fallback.calls) == 2


def test_the_chatter_step_turns_a_final_timeout_into_its_busy_reply():
    """call_llm_text now raises LLMTimeoutError after the move; the chatter must catch
    it with LLMFatalError rather than let it escape as an internal error."""
    from chat_nextseek.agents import chatter

    src = inspect.getsource(chatter)
    assert "except (LLMFatalError, LLMTimeoutError)" in src

    primary = _Client("gcp", [LLMTimeoutError("t1")])
    fallback = _Client("bedrock", [LLMTimeoutError("t2")])
    config = _Config(primary, agent="chatter")
    config.LLM_CLIENTS = {"gcp": primary, "anth": fallback}
    config.AGENT_MODEL_CATALOG = {
        "anth:current": {"chatter": {"provider": "anth", "model": "fallback-1", "thinking_level": None}},
    }
    with pytest.raises(LLMTimeoutError):
        _text(config, primary)


# --------------------------------------------------------------------------
# The tool loop takes the same trigger.
# --------------------------------------------------------------------------

class _ToolClient:
    def __init__(self, provider, outcomes):
        self.provider = provider
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def reset_connections(self):
        return True

    def chat_with_tools(self, *, model, **kw):
        self.calls.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": outcome}],
                "usage": {}, "metadata": {}}


def _tool_config(primary, fallback):
    config = _Config(primary, agent="pipeline_agent")
    config.LLM_CLIENTS = {"anth": primary, "gcp": fallback}
    config.AGENT_MODEL_CATALOG = {
        "gcp:current": {"pipeline_agent": {"provider": "gcp", "model": "fallback-1", "thinking_level": None}},
        "gcp:lite": {"pipeline_agent": {"provider": "gcp", "model": "fallback-2", "thinking_level": None}},
    }
    return config


def _call_tools(config, primary):
    from chat_nextseek.tool_loop import call_tools

    return call_tools(
        config, messages=[], tools=[], system="s", model_name="primary-model",
        client=primary, agent_label="pipeline_agent",
    )


def test_the_tool_loop_moves_on_a_timeout():
    primary = _ToolClient("bedrock", [LLMTimeoutError("t")])
    fallback = _ToolClient("gcp", ["ok"])
    result = _call_tools(_tool_config(primary, fallback), primary)
    assert result["content"][0]["text"] == "ok"
    assert fallback.calls == ["fallback-1"]


def test_the_tool_loop_moves_once():
    primary = _ToolClient("bedrock", [LLMServiceUnavailableError("503")])
    fallback = _ToolClient("gcp", [LLMServiceUnavailableError("503"), "ok"])
    with pytest.raises(LLMFatalError):
        _call_tools(_tool_config(primary, fallback), primary)
    assert fallback.calls == ["fallback-1"]

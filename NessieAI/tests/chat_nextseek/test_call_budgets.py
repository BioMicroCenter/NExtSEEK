"""One wall-clock table for every NS model call (operator ruling 2026-09-28, F3 and F5.1).

``schemas/call_budgets.py`` holds two budgets per agent: the primary's first try, and the one call that moves to the
fallback model. The rules pinned here:

* the values are the ruled ones, and every first try clears the largest genuine success measured for that agent;
* a call that names no budget gets its agent's row, looked up by the catalog key its provider chain uses;
* the moved call gets the agent's moved budget whatever made the primary fail (F5.1: it used to get the retry window
  after a timeout and the first try's budget after anything else, so the parser's Gemini fallback had 35 s after a
  503 and 60 s after a timeout);
* a caller that passes its own budget keeps it;
* the ledger records the window each attempt actually had.
"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from chat_nextseek.llm_clients import (
    LLMAPIConnectionError,
    LLMModelUnusableError,
    LLMRateLimitError,
    LLMResponse,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from chat_nextseek.schemas import call_budgets, schema_helper
from chat_nextseek.schemas.call_budgets import CALL_BUDGETS, CallBudget, budget_for
from chat_nextseek.schemas.schema_helper import call_llm_structured, call_llm_text


# --------------------------------------------------------------------------
# The table.
# --------------------------------------------------------------------------

RULED = {
    "entity": (30, 90),
    "api": (30, 90),
    "chatter": (30, 90),
    "reporter": (30, 90),
    "seqera_agent": (30, 90),
    "system": (45, 90),
    "memory_coder": (45, 90),
    "graph": (60, 90),
    "memory": (60, 90),
    # Run 2 (operator, 2026-09-28): 35 -> 50 s, because Opus 5.5 always thinks. The move stays 60 s.
    "parser": (50, 60),
    "multi_parser": (50, 60),
    "report_writer": (240, 180),
    "followup": (60, 60),
    "pipeline_agent": (120, 120),
}


def test_the_table_holds_the_ruled_values():
    assert {agent: (b.first_try_s, b.moved_s) for agent, b in CALL_BUDGETS.items()} == RULED


def test_only_the_parsers_keep_a_timeout_from_marking_the_model():
    """D3: the parser's first try is a speed preference, not a stall test, at 35 s or 50 s."""
    assert {agent for agent, b in CALL_BUDGETS.items() if not b.timeout_marks_model} == {"parser", "multi_parser"}


def test_only_the_report_writer_skips_the_op_reserve():
    """Operator ruling on review finding 1 (option A, 2026-09-28): inside a CC op their first try is not cut to leave
    20 s for a move, because the move could not redo their work in 20 s (a 4k-token graph answer, a report).
    Round 6: the graph agent takes the reserve, but only inside an op (a nested NS turn's deadline is not one)."""
    assert {agent for agent, b in CALL_BUDGETS.items() if not b.op_move_reserve} == {"report_writer"}
    assert {agent for agent, b in CALL_BUDGETS.items() if b.move_reserve_only_in_op} == {"graph"}
    assert call_budgets.DEFAULT_BUDGET.op_move_reserve is True
    assert call_budgets.TOOL_LOOP_DEFAULT_BUDGET.op_move_reserve is True
    assert call_budgets.DEFAULT_BUDGET.move_reserve_only_in_op is False


def test_an_agent_the_table_does_not_name_keeps_the_old_budgets():
    assert budget_for("report_coder") == CallBudget(300, 180)
    assert budget_for("planner") == call_budgets.DEFAULT_BUDGET
    assert budget_for(None) == call_budgets.DEFAULT_BUDGET
    assert budget_for("unknown", default=call_budgets.TOOL_LOOP_DEFAULT_BUDGET) == CallBudget(120, 120)


# The largest success measured per agent that was not a stall (a normal-length answer at 4x or more its neighbours'
# time). Laptop ledger dev/logs/llm_calls.jsonl, 3,018 successful calls 2026-09-11 to 09-22; the entity batch of
# 2026-09-16 (1,224 calls); F345-PROPOSAL.md appendix A. A first try below one of these would move a healthy call.
LARGEST_GENUINE_SUCCESS_S = {
    "entity": 27.0,        # 2026-09-30 entity-only test at medium thinking, 84 questions (p95 12.5 s); run 2 max was 17.3 s; every laptop entity call over 20 s on 3.5 Flash was a stall
    "graph": 51.0,         # 4,351 output tokens
    "api": 25.3,
    "chatter": 13.9,
    "system": 22.4,        # 1,487 output tokens
    "reporter": 5.3,
    "memory_coder": 13.3,
    "parser": 30.4,        # Opus 4.7
    "report_writer": 16.0,
    "followup": 9.8,
    "pipeline_agent": 6.1,
}


@pytest.mark.parametrize("agent", sorted(LARGEST_GENUINE_SUCCESS_S))
def test_every_first_try_clears_the_largest_genuine_success_measured(agent):
    assert CALL_BUDGETS[agent].first_try_s > LARGEST_GENUINE_SUCCESS_S[agent]


# --------------------------------------------------------------------------
# The ladder reads the table.
# --------------------------------------------------------------------------

class _Plan(BaseModel):
    mode: str = "unsupported"


class _Client:
    """Replays scripted outcomes: a string answers, an exception is raised."""

    def __init__(self, provider, outcomes):
        self.provider = provider
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def reset_connections(self):
        return True

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.calls.append(model)
        outcome = self.outcomes.pop(0) if self.outcomes else ""
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResponse(content=outcome, raw=None, usage=None, model=model, provider=self.provider,
                           metadata={"stop_reason": "end_turn"})


class _Config:
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None, "low": 4000, "medium": 8000, "high": 16000}
    LLM_MODEL = "primary-model"

    def __init__(self, primary, fallback, agent, tmp_path):
        self.LOG_DIR = str(tmp_path)
        self.LLM_CLIENT = primary
        self.LLM_CLIENTS = {"gcp": primary, "anth": fallback}
        self.AGENT_MODEL_CATALOG = {
            "anth:current": {agent: {"provider": "anth", "model": "fallback-1", "thinking_level": None}},
        }


@pytest.fixture
def windows(monkeypatch):
    """Every attempt's wall clock, as (model, seconds)."""
    seen: list[tuple[str, float]] = []
    real = schema_helper._call_llm_with_timeout

    def spy(**kw):
        seen.append((kw["model_name"], kw["timeout_seconds"]))
        return real(**kw)

    monkeypatch.setattr(schema_helper, "_call_llm_with_timeout", spy)
    monkeypatch.setattr(schema_helper.time, "sleep", lambda s: None)
    return seen


def _structured(config, primary, **kw):
    return call_llm_structured(config, "q", _Plan, system="s", client=primary, model_name="primary-model", **kw)


@pytest.mark.parametrize("agent, log_label", [
    ("entity", None), ("api", "api_agent"), ("graph", "graph_agent"), ("system", "system_agent"),
    ("reporter", None), ("seqera_agent", None), ("memory_coder", None),
])
def test_a_call_that_names_no_budget_gets_its_agents_row(tmp_path, windows, agent, log_label):
    primary = _Client("gcp", [LLMTimeoutError("stalled")])
    fallback = _Client("bedrock", ['{"mode": "graph_query"}'])
    kw = {"agent_label": agent} if log_label is None else {"agent_label": agent, "log_label": log_label}
    assert _structured(_Config(primary, fallback, agent, tmp_path), primary, **kw).mode == "graph_query"
    first, moved = RULED[agent]
    assert windows == [("primary-model", first), ("fallback-1", moved)]


def test_the_text_path_reads_the_same_table(tmp_path, windows):
    primary = _Client("gcp", [LLMTimeoutError("stalled")])
    fallback = _Client("bedrock", ["Here are your samples."])
    config = _Config(primary, fallback, "chatter", tmp_path)
    reply = call_llm_text(config, messages=[{"role": "user", "content": "hi"}], client=primary,
                          model_name="primary-model", agent_label="chatter")
    assert reply == "Here are your samples."
    assert windows == [("primary-model", 30), ("fallback-1", 90)]


FIRST_FAILURES = [
    ("timeout", LLMTimeoutError("t")),
    ("unavailable", LLMServiceUnavailableError("503 UNAVAILABLE")),
    ("empty", ""),
    ("rate_limited", LLMRateLimitError("429 RESOURCE_EXHAUSTED")),
    ("connection", LLMAPIConnectionError("reset")),
    ("model_unusable", LLMModelUnusableError("ResourceNotFoundException: model not found")),
]


@pytest.mark.parametrize("reason, first", FIRST_FAILURES, ids=[r for r, _ in FIRST_FAILURES])
def test_the_moved_call_gets_one_budget_whatever_made_the_primary_fail(tmp_path, windows, reason, first):
    """F5.1. The parser's row: 50 s first, 60 s for the move, after every class."""
    primary = _Client("bedrock", [first])
    fallback = _Client("gcp", ['{"mode": "graph_query"}'])
    config = _Config(primary, fallback, "parser", tmp_path)
    config.LLM_CLIENTS = {"anth": primary, "gcp": fallback}
    config.AGENT_MODEL_CATALOG = {
        "gcp:current": {"parser": {"provider": "gcp", "model": "fallback-1", "thinking_level": None}},
    }
    assert _structured(config, primary, agent_label="parser").mode == "graph_query"
    assert windows == [("primary-model", 50), ("fallback-1", 60)]


@pytest.mark.parametrize("reason, first", FIRST_FAILURES, ids=[r for r, _ in FIRST_FAILURES])
def test_the_ledger_records_the_window_each_attempt_had(tmp_path, windows, reason, first):
    primary = _Client("gcp", [first])
    fallback = _Client("bedrock", ['{"mode": "graph_query"}'])
    _structured(_Config(primary, fallback, "entity", tmp_path), primary, agent_label="entity")
    entries = [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]
    assert [e["timeout_seconds"] for e in entries if e["model"] == "primary-model"][-1] == 30
    (moved,) = [e for e in entries if e["model"] == "fallback-1"]
    assert moved["timeout_seconds"] == 90 and moved["fallback_reason"] == reason


def test_a_caller_that_chooses_its_own_budget_keeps_it(tmp_path, windows):
    primary = _Client("gcp", [LLMServiceUnavailableError("503")])
    fallback = _Client("bedrock", ['{"mode": "graph_query"}'])
    _structured(_Config(primary, fallback, "entity", tmp_path), primary, agent_label="entity",
                timeout_seconds=7, timeout_retry_seconds=11)
    assert windows == [("primary-model", 7), ("fallback-1", 11)]


def test_a_timeout_with_no_chain_retries_the_same_model_on_the_moved_budget(tmp_path, windows):
    primary = _Client("gcp", [LLMTimeoutError("t"), '{"mode": "graph_query"}'])
    config = _Config(primary, None, "entity", tmp_path)
    config.LLM_CLIENTS = {"gcp": primary}
    config.AGENT_MODEL_CATALOG = {}
    assert _structured(config, primary, agent_label="entity").mode == "graph_query"
    assert windows == [("primary-model", 30), ("primary-model", 90)]


# --------------------------------------------------------------------------
# The tool loops read it too.
# --------------------------------------------------------------------------

OPUS = "us.anthropic.claude-opus-4-7"
SONNET = "us.anthropic.claude-sonnet-4-6"


class _ToolClient:
    provider = "bedrock"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def reset_connections(self):
        return True

    def chat_with_tools(self, *, model, **kw):
        self.calls.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": outcome}], "usage": {}, "metadata": {}}


@pytest.mark.parametrize("agent", ["followup", "pipeline_agent", "some_new_loop"])
def test_the_tool_loop_windows_come_from_the_table(tmp_path, monkeypatch, agent):
    from chat_nextseek import tool_loop

    seen: list[float] = []
    real = tool_loop._run_with_wall_clock
    monkeypatch.setattr(tool_loop, "_run_with_wall_clock", lambda fn, t: (seen.append(t), real(fn, t))[1])
    client = _ToolClient([LLMServiceUnavailableError("503"), "ok"])
    config = _Config(client, client, agent, tmp_path)
    config.LLM_CLIENTS = {"anth": client}
    config.AGENT_MODEL_CATALOG = {"_fallback": {agent: {"provider": "anth", "model": SONNET, "thinking_level": None}}}
    tool_loop.call_tools(config, messages=[], tools=[], system="s", model_name=OPUS, client=client, agent_label=agent)
    expected = RULED.get(agent, (120, 120))
    assert client.calls == [OPUS, SONNET]
    assert seen == list(expected)


def test_inside_an_op_the_parsers_and_the_graph_agent_follow_the_measured_speed():
    k = {agent: (b.op_speed_k, b.op_speed_floor_s) for agent, b in CALL_BUDGETS.items() if b.op_speed_k is not None}
    assert k == {"parser": (4, 20), "multi_parser": (4, 20), "graph": (5, 20)}


def test_inside_an_op_only_the_parsers_cut_their_first_try_to_20_s():
    cut = {agent: b.op_first_try_s for agent, b in CALL_BUDGETS.items() if b.op_first_try_s is not None}
    assert cut == {"parser": 20, "multi_parser": 20}

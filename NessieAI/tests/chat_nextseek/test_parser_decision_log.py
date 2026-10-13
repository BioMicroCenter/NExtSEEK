"""Parser decision log: raw LLM mode, final mode, the guardrail that rewrote it, LLM latency.

Telemetry only: the plan returned is the plan the guardrails always returned. No question text in the record.
"""
from __future__ import annotations

from unittest.mock import patch

from chat_nextseek.agents import parser as parser_mod
from chat_nextseek.agents.parser import _apply_parser_guardrails
from chat_nextseek.schemas import ParserPlan

UID_Q = "What sequencing data is associated with NHP-220524FLY-1-PUB and NHP-220524FLY-2-PUB?"


def test_trace_names_the_guardrail_that_changed_the_mode():
    trace: list[str] = []
    plan = _apply_parser_guardrails(UID_Q, ParserPlan(mode="new_search"), trace=trace)
    assert plan.mode == "graph_query"
    assert trace and trace[0] == "uid_lineage"


def test_trace_is_empty_when_nothing_rewrote_the_mode():
    trace: list[str] = []
    plan = _apply_parser_guardrails("hello", ParserPlan(mode="graph_query"), trace=trace)
    assert plan.mode == "graph_query" and trace == []


def test_guardrails_result_is_the_same_with_and_without_trace():
    a = _apply_parser_guardrails(UID_Q, ParserPlan(mode="new_search"))
    b = _apply_parser_guardrails(UID_Q, ParserPlan(mode="new_search"), trace=[])
    assert a.model_dump() == b.model_dump()


def test_parser_agent_fills_the_decision_without_changing_the_plan():
    from types import SimpleNamespace
    raw = ParserPlan(mode="new_search")
    cfg = SimpleNamespace(PARSER_SYSTEM_PROMPT="x", FORCE_PARSER_MODE=None, MIN_GRAPH_SCHEMA={},
                          get_agent_model=lambda name: (None, "m", 0))
    runs = []
    for decision in (None, {}):
        with patch.object(parser_mod, "call_llm_structured", return_value=raw.model_copy()), \
             patch.object(parser_mod, "_endpoints_for_prompt", return_value="[]"), \
             patch.object(parser_mod, "build_recent_results_summary", return_value=""), \
             patch("chat_nextseek.chat_memory.history_block", return_value=""):
            kw = {} if decision is None else {"decision": decision}
            runs.append(parser_mod.parser_agent({}, cfg, UID_Q, {}, **kw))
    assert runs[0].model_dump() == runs[1].model_dump()
    d = decision
    assert d["raw_mode"] == "new_search" and d["final_mode"] == "graph_query"
    assert d["guardrails_changed"][0] == "uid_lineage"
    assert isinstance(d["llm_ms"], int) and d["llm_ms"] >= 0
    assert UID_Q not in str(d)

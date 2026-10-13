"""Graph agent rules, nessie_fixes unit U4 (fixes 5, 6, 7, 8, 14, 16).

Code paths get behaviour tests with sibling inputs; prompt rules get pins that the rule text is present and that
the old wrong example is gone. No model, Neo4j or network call is made.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import chat_nextseek
from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.schemas.entity import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan

PROMPTS = Path(chat_nextseek.__file__).resolve().parent / "prompts"
GRAPH = (PROMPTS / "graph_agent.txt").read_text(encoding="utf-8")
PARSER = (PROMPTS / "multi_parser_agent.txt").read_text(encoding="utf-8")


class _StubConfig:
    CHATTER_SYSTEM_PROMPT = "SYSTEM PROMPT"
    LOG_DIR = ""

    def get_agent_model(self, agent_label):
        return (object(), "stub-model", None)


@pytest.fixture
def captured(monkeypatch):
    box: dict = {}

    def _fake(config, *, messages, model_name, client, agent_label, temperature=0, thinking_budget=None,
              usage_label=None):
        box["user_content"] = messages[-1]["content"]
        return "stub reply"

    monkeypatch.setattr(chatter_mod, "call_llm_text", _fake)
    return box


def _turn(captured, rows, *, total, truncated, question="List the samples"):
    chatter_mod.chatter_agent_answer(
        _StubConfig(), question, EntityAgentOutput().model_dump(), ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (s:Sample) RETURN s.id AS id, s.uuid AS uuid, s.type AS type",
                    "parameters": {}, "explanation": ""},
        graph_result={"ok": True, "count": len(rows), "total": total, "truncated": truncated, "data": rows},
        log_dir="",
    )
    return captured["user_content"]


def _rows(n):
    return [{"id": i, "uuid": f"TIS-{i}", "type": "TIS"} for i in range(n)]


# --- fix 5: no "full list" when the result stopped at the row cap -----------------------------------------------

@pytest.mark.parametrize("n,total", [(5000, 14163), (3000, 3500)])
def test_a_capped_result_is_not_called_the_full_list(captured, n, total):
    text = _turn(captured, _rows(n), total=total, truncated=True)
    assert "say the full list is available" not in text
    assert f"{n:,} of {total:,}" in text
    assert "never call it the full list" in text


@pytest.mark.parametrize("n", [60, 400])
def test_an_uncapped_result_keeps_the_full_list_line(captured, n):
    text = _turn(captured, _rows(n), total=n, truncated=False)
    assert "say the full list is available" in text
    assert "never call it the full list" not in text

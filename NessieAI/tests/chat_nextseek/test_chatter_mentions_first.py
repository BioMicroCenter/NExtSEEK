"""A "Yes" over text mentions: code puts the reviewer's "no record is typed or assayed as X data" sentence first.

The LLM is stubbed. ``_mentions_first`` runs right after ``_premise_first`` on the model's reply.
"""
from __future__ import annotations

from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.graph_review import FREE_TEXT_ONLY_FACT, PREMISE_FACT
from chat_nextseek.schemas.entity import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan

FACT = FREE_TEXT_ONLY_FACT.format(term="zorbex")
LEAD = "No record here is typed, assayed or labelled as zorbex data."
NOTE = f"What the result matched: {FACT} State this plainly in the first sentences."


class _Config:
    CHATTER_SYSTEM_PROMPT = "SYSTEM PROMPT"
    LOG_DIR = ""

    def get_agent_model(self, agent_label):
        return (object(), "stub-model", None)


def _answer(monkeypatch, text, notes):
    monkeypatch.setattr(chatter_mod, "call_llm_text", lambda *a, **k: text)
    out = chatter_mod.chatter_agent_answer(
        _Config(), "do we have any zorbex data?", EntityAgentOutput().model_dump(),
        ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE toLower(s.search_text) CONTAINS $t RETURN count(s) AS n",
                    "parameters": {"t": "zorbex"}},
        graph_result={"ok": True, "count": 1, "total": 1, "data": [{"n": 12}]},
        query_notes=notes, log_dir="")
    return out.split("**Debug info**")[0].strip()


def test_a_yes_over_text_mentions_is_replaced_by_the_fact():
    out = chatter_mod._mentions_first("Yes, but only as text mentions: 12 records mention it.", [NOTE])
    assert out.startswith(LEAD) and not out.lower().startswith("yes") and "12 records" in out


def test_it_runs_on_the_model_reply_of_a_graph_turn(monkeypatch):
    body = _answer(monkeypatch, "Yes, but only as text mentions: 12 records mention it.", [NOTE])
    assert body.startswith(LEAD) and "Yes" not in body[:10]


def test_a_reply_that_does_not_open_with_yes_is_left_alone():
    reply = "Only text mentions: 12 records mention it."
    assert chatter_mod._mentions_first(reply, [NOTE]) == reply


def test_a_yes_without_the_fact_is_left_alone():
    reply = "Yes, 12 records."
    assert chatter_mod._mentions_first(reply, [PREMISE_FACT.format(n="4,095")]) == reply
    assert chatter_mod._mentions_first(reply, []) == reply


def test_a_table_opening_stays_intact():
    out = chatter_mod._mentions_first("Yes\n| type | n |\n|---|---|\n| A | 3 |", [NOTE])
    assert out.startswith(LEAD + "\n\n| type | n |")

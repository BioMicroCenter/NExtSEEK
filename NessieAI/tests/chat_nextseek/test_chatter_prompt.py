"""What the chatter is actually handed before it writes the reply.

The chatter is the last step of a turn and the only voice the user hears, and until
now nothing tested the prompt it is built from. Three production-shaped failures are
pinned here.

* The ``Keywords:`` line has been dead since it was written. ``chatter.py`` read
  ``entity_result["filters"]["keywords"]``; ``EntityAgentOutput``
  (``schemas/entity.py``) carries ``keywords`` at the top level and has no ``filters``
  field at all, so every turn in every mode rendered ``(none)``.

* A reply can misreport what was asked because the writer was never told what ran.
  B7 (wesselr 462) asked for the PAT samples under ``MDL-250912LAU-1`` and got 1,904
  lineage records, none of them PAT. B13 (mplaster 501/502) was answered by Cypher
  whose own explanation said the ``CC`` filter could not be applied, and the reply
  still called the 731 a subset of the CC mice. In both, the gap between what the
  user asked for and what the query constrained was computed, sat in the arguments
  this function already receives, and was thrown away at the prompt boundary.

* A retry that changes the answer must be disclosed. ``_execute_graph_turn`` records
  ``graph_retry_changed_answer`` when the first query matched nothing and a changed
  filter produced a number; the comment on it says the user must not be told that
  number without being told the filter changed. Nothing carried it to the chatter.
"""
from __future__ import annotations

import json

import pytest

from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.schemas.entity import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan


class _StubConfig:
    """Only the attributes chatter_agent_answer touches."""

    CHATTER_SYSTEM_PROMPT = "SYSTEM PROMPT"
    LOG_DIR = ""

    def get_agent_model(self, agent_label):
        return (object(), "stub-model", None)


@pytest.fixture
def captured(monkeypatch):
    """Run the chatter with the LLM replaced, and hand back the prompt it built."""
    box: dict = {}

    def _fake_call_llm_text(config, *, messages, model_name, client, agent_label,
                            temperature=0, thinking_budget=None, usage_label=None):
        box["messages"] = messages
        box["user_content"] = messages[-1]["content"]
        return "stub reply"

    monkeypatch.setattr(chatter_mod, "call_llm_text", _fake_call_llm_text)
    return box


def _entity(**kw):
    return EntityAgentOutput(**kw).model_dump()


def _plan(**kw):
    return ParserPlan(**kw).model_dump()


# --------------------------------------------------------------------------
# 1. The dead Keywords line.
# --------------------------------------------------------------------------

def test_entity_keywords_reach_the_prompt(captured):
    """``keywords`` is top level on EntityAgentOutput, not under a ``filters`` key."""
    chatter_mod.chatter_agent_answer(
        _StubConfig(),
        "Find me mice treated with NDMA",
        _entity(keywords=["NDMA"]),
        _plan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/"),
        {"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST",
         "requestBody": {"filter_searchText": "NDMA"}},
        {"ok": True, "data": {"total": 12, "rows": [{"uid": "MUS-1"}]}},
        {"ok": True, "data": {"total": 12, "rows": [{"uid": "MUS-1"}]}},
        log_dir="",
    )

    assert "- Keywords: NDMA" in captured["user_content"]


def test_a_turn_with_no_keywords_still_says_none(captured):
    chatter_mod.chatter_agent_answer(
        _StubConfig(),
        "How many samples are there",
        _entity(),
        _plan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/"),
        {"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST", "requestBody": {}},
        {"ok": True, "data": {"total": 12, "rows": []}},
        {"ok": True, "data": {"total": 12, "rows": []}},
        log_dir="",
    )

    assert "- Keywords: (none)" in captured["user_content"]

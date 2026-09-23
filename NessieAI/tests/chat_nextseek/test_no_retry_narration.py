"""The reply never narrates the retry path (operator ruling, 2026-09-23).

Production turn 461, "Is there a scientist named Kate Bridges associated with the Impact
project?", replied "An initial search returned no matches, so a graph query over the sample
network was run instead, matching ...". e75e5527 had removed only the same-scope form of that
sentence from the chatter prompt. The sentence was requested, not improvised: the note the
orchestrator hands the chatter after a zero-row retry (``RETRY_CHANGED_ANSWER_NOTE``) ended
"so say that the original filter found nothing and what was used instead", and the graph
prompt told the agent to write the retry into its explanation "so the reply can tell the user
the first filter found nothing". All three now say the same thing: state the finding, and
qualify it only when what the RESULT means differs from the question.
"""
from __future__ import annotations

from pathlib import Path

import chat_nextseek
from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.graph_retry import RETRY_CHANGED_ANSWER_NOTE
from chat_nextseek.schemas.entity import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan

PROMPTS = Path(chat_nextseek.__file__).resolve().parent / "prompts"


def _prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def test_the_retry_note_no_longer_asks_for_the_narration():
    note = RETRY_CHANGED_ANSWER_NOTE

    assert "say that the original filter found nothing" not in note
    assert "Do not narrate that" in note
    assert "never say that a first or initial search found nothing" in note


def test_the_retry_note_keeps_the_qualification_that_changes_the_meaning():
    """The retry may have widened the match; that is the part the user needs."""
    note = RETRY_CHANGED_ANSWER_NOTE

    assert "free text" in note
    assert "titles that merely contain the name" in note
    assert "truncated or capped" in note
    assert "not how it was reached" in note


def test_the_chatter_prompt_forbids_narrating_the_retry_in_any_form():
    prompt = _prompt("chatter_agent.txt")
    at = prompt.index("NEVER NARRATE THE RETRY PATH")
    rule = prompt[at:at + 1600]

    assert "an initial search returned no matches" in rule
    assert "another search was run instead" in rule
    # the production reply is the counter-example, and the Shoulders one stays
    assert "never \"An initial search returned no matches, so a graph query over the sample network was run instead\"" in rule
    assert "There are 568 samples in the Shoulders project" in rule
    # and the qualification is about the result
    assert "free text" in rule and "truncated or capped" in rule


def test_the_substituted_search_disclosure_describes_the_result_not_the_path():
    prompt = _prompt("chatter_agent.txt")
    rule = prompt[prompt.index("**`search_text_substituted`**"):][:700]

    assert "your\n  exact terms returned no matches" not in rule
    assert "these are the results for `<used>`, not\n  for `<asked>`" in rule


def test_the_graph_prompt_no_longer_writes_the_retry_into_the_explanation():
    prompt = _prompt("graph_agent.txt")
    ladder = prompt[prompt.index("## When a query matched nothing"):prompt.index("## Rules for every query")]

    assert "tell the user the first filter found nothing" not in ladder
    assert "never describe the earlier query or that it found nothing" in ladder


def test_a_turn_with_the_retry_note_is_told_not_to_narrate_it(monkeypatch):
    box: dict = {}

    class _Config:
        CHATTER_SYSTEM_PROMPT = "SYSTEM PROMPT"
        LOG_DIR = ""

        def get_agent_model(self, agent_label):
            return (object(), "stub-model", None)

    def _fake(config, *, messages, model_name, client, agent_label, temperature=0, thinking_budget=None,
              usage_label=None):
        box["user_content"] = messages[-1]["content"]
        return "stub reply"

    monkeypatch.setattr(chatter_mod, "call_llm_text", _fake)
    chatter_mod.chatter_agent_answer(
        _Config(), "Is there a scientist named Kate Bridges associated with the Impact project?",
        EntityAgentOutput(scientists=["Kate Bridges"], projects=["Impact"]).model_dump(),
        ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE toLower(s.search_text) CONTAINS $scientist "
                              "RETURN count(s) AS n", "parameters": {"scientist": "kate bridges"},
                    "explanation": "Matches samples mentioning Kate Bridges anywhere in the record."},
        graph_result={"ok": True, "count": 1, "total": 1, "data": [{"n": 4}]},
        query_notes=[RETRY_CHANGED_ANSWER_NOTE],
        log_dir="",
    )
    text = box["user_content"]

    assert "Never narrate the retry path either" in text
    assert "Do not narrate that" in text

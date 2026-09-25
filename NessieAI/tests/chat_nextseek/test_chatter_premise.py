"""A wrong number in the question is corrected in the reply's first sentence, never repeated (Phase F, F-b).

R4-607 asked about "all the 4,095 Sequencing Data (D.SEQ) files"; the search matched 962 and the reply opened "Of
the 4,095 D.SEQ files, ...". The reviewer's ``premise_count`` fired, and its fact reached the chatter as a note, but
Task 9's backstop passed the reply because the reply holds the fact's only number (4,095): an echo is not a
correction. ``_premise_first`` puts the reviewer's premise sentence (``graph_review.PREMISE_FACT``) first when the
notes carry it and the reply does not correct the number in words of its own; it runs after Task 9's backstop on the
model's reply and on each graph fallback reply. It never fires on a number in the question alone.

The LLM is stubbed; no model, Neo4j or network call is made.
"""
from __future__ import annotations

import pytest

from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.graph_review import PREMISE_FACT
from chat_nextseek.llm_clients import LLMAPIConnectionError, LLMFatalError, LLMRateLimitError, LLMTimeoutError
from chat_nextseek.schemas.entity import EntityAgentOutput, EntityItem
from chat_nextseek.schemas.router import ParserPlan

QUESTION = "Can you search all the 4,095 Sequencing Data (D.SEQ) files for 'ABC' in their UID?"
FACT = PREMISE_FACT.format(n="4,095")
NOTE = f"What the result matched: {FACT} State this plainly in the first sentences. Do not mention a review or a second query."


class _Config:
    CHATTER_SYSTEM_PROMPT = "SYSTEM PROMPT"
    LOG_DIR = ""

    def get_agent_model(self, agent_label):
        return (object(), "stub-model", None)


def _answer(monkeypatch, llm, notes, **kw):
    monkeypatch.setattr(chatter_mod, "call_llm_text", llm)
    out = chatter_mod.chatter_agent_answer(
        _Config(), QUESTION,
        EntityAgentOutput(sampletypes=[EntityItem(code="D.SEQ", name="Sequencing Data")]).model_dump(),
        ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (s:T_D_SEQ) WHERE s.uuid CONTAINS $t RETURN count(s) AS n",
                    "parameters": {"t": "ABC"}},
        graph_result={"ok": True, "count": 1, "total": 1, "data": [{"n": 962}]},
        query_notes=notes, log_dir="", **kw)
    return out.split("**Debug info**")[0].strip()


def _says(text):
    return lambda *a, **k: text


def test_an_echoed_premise_is_corrected_in_the_first_sentence(monkeypatch):
    body = _answer(monkeypatch, _says("Of the 4,095 D.SEQ files, 962 have 'ABC' in their UID."), [NOTE])
    assert body.startswith(FACT) and "962" in body


def test_a_reply_that_drops_the_number_still_gets_the_correction(monkeypatch):
    assert _answer(monkeypatch, _says("962 D.SEQ files have 'ABC' in their UID."), [NOTE]).startswith(FACT)


def test_a_reply_that_already_corrects_it_is_left_as_written(monkeypatch):
    reply = "962 D.SEQ files have 'ABC' in their UID; this search did not reproduce the 4,095 in the question."
    assert _answer(monkeypatch, _says(reply), [NOTE]) == reply


def test_without_the_reviewers_note_nothing_is_added(monkeypatch):
    reply = "Of the 4,095 D.SEQ files, 962 have 'ABC' in their UID."
    assert _answer(monkeypatch, _says(reply), []) == reply


def test_the_fallback_reply_carries_the_correction(monkeypatch):
    def _down(*a, **k):
        raise chatter_mod.LLMFatalError("provider down")
    assert FACT in _answer(monkeypatch, _down, [NOTE])   # Task 9 may already place the disclosure in the fallback


def test_the_prompt_never_repeats_an_unconfirmed_number():
    from pathlib import Path
    import chat_nextseek
    prompt = (Path(chat_nextseek.__file__).resolve().parent / "prompts" / "chatter_agent.txt").read_text(encoding="utf-8")
    rule = prompt[prompt.index("A NUMBER IN THE QUESTION IS THE USER'S"):][:900]
    assert "repeat it as a fact only when the data block shows that same number" in rule
    assert "say so in your first sentence" in rule


# --------------------------------------------------------------------------- #
# The premise sentence is the reply's FIRST sentence
# --------------------------------------------------------------------------- #

OTHER = "The question names 'nanopore', but the search did not filter on it."


@pytest.mark.parametrize("exc", [LLMAPIConnectionError("down"), LLMRateLimitError("slow"),
                                 LLMFatalError("provider down"), LLMTimeoutError("late")])
def test_every_graph_fallback_opens_with_the_correction(monkeypatch, exc):
    def _down(*a, **k):
        raise exc
    assert _answer(monkeypatch, _down, [NOTE]).startswith(FACT)


def test_the_fallback_opens_with_the_correction_when_the_disclosure_holds_another_fact_first(monkeypatch):
    """Task 9's fallback puts the whole disclosure first; the premise sentence is moved to the front of it."""
    def _down(*a, **k):
        raise LLMFatalError("provider down")
    disclosure = f"{OTHER} {FACT}"
    body = _answer(monkeypatch, _down, [f"What the result matched: {disclosure} State this plainly."],
                   review_disclosure=disclosure)
    assert body.startswith(FACT) and OTHER in body and body.count(FACT) == 1


def test_the_correction_is_moved_first_when_the_backstop_put_it_second(monkeypatch):
    """A reply without the question's number gets Task 9's whole disclosure first, the other fact leading."""
    disclosure = f"{OTHER} {FACT}"
    body = _answer(monkeypatch, _says("962 D.SEQ files have 'ABC' in their UID."),
                   [f"What the result matched: {disclosure} State this plainly."], review_disclosure=disclosure)
    assert body.startswith(FACT) and OTHER in body and "962" in body and body.count(FACT) == 1


def test_a_reply_that_quotes_the_fact_later_gets_it_moved_first(monkeypatch):
    body = _answer(monkeypatch, _says(f"Of the 4,095 D.SEQ files, 962 have 'ABC' in their UID. {FACT}"), [NOTE])
    assert body.startswith(FACT) and body.count(FACT) == 1 and "962" in body


def test_a_reply_that_opens_with_the_fact_is_left_as_written(monkeypatch):
    reply = f"{FACT} 962 D.SEQ files have 'ABC' in their UID."
    assert _answer(monkeypatch, _says(reply), [NOTE]) == reply


def test_the_disclosure_carries_the_fact_when_the_note_was_cut(monkeypatch):
    """The note keeps whole facts from the front (orchestrator ``_review_note``); the premise fact, disclosed last,
    is the one a long note loses. The disclosure the chatter is handed still has it."""
    disclosure = f"{OTHER} {FACT}"
    body = _answer(monkeypatch, _says("Of the 4,095 D.SEQ files, 962 have 'ABC' in their UID."),
                   [f"What the result matched: {OTHER} State this plainly."], review_disclosure=disclosure)
    assert body.startswith(FACT)


def test_a_number_in_the_question_alone_adds_nothing(monkeypatch):
    """Another reviewer fact is no premise: only the premise sentence itself triggers the backstop."""
    reply = "Of the 4,095 D.SEQ files, 962 have 'ABC' in their UID."
    body = _answer(monkeypatch, _says(reply), ["What the result matched: The search matched 962 files."])
    assert body == reply


def test_a_threshold_question_gets_no_correction_end_to_end(monkeypatch):
    """Fix round 1: "500 or more samples" is a bound, not a set the user sized. Before the fix Tier 1 read 500 as a
    stated count and this backstop put "The question says 500; ..." ahead of a correct reply."""
    from chat_nextseek.graph_review import DictCatalog, ReviewInput, review_tier1
    from chat_nextseek.orchestrator import _review_note
    q = "Which projects have 500 or more samples?"
    inp = ReviewInput(question=q, cypher="MATCH (p:Project) RETURN p.title AS t", parameters={}, keyword_fields={},
                      rows=[{"t": "A"}, {"t": "B"}], count=2, total=2, ok=True, error=None)
    rv = review_tier1(inp, DictCatalog(None))
    disclosure = rv.disclosure if rv.verdict in ("note", "suggest") else None
    assert disclosure is None
    reply = "Two projects have 500 or more samples: A and B."
    monkeypatch.setattr(chatter_mod, "call_llm_text", _says(reply))
    out = chatter_mod.chatter_agent_answer(
        _Config(), q, EntityAgentOutput().model_dump(), ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": inp.cypher, "parameters": {}},
        graph_result={"ok": True, "count": 2, "total": 2, "data": inp.rows},
        query_notes=[_review_note(disclosure)] if disclosure else [], review_disclosure=disclosure, log_dir="")
    assert out.split("**Debug info**")[0].strip() == reply


@pytest.mark.parametrize("notes,reply,expected", [
    ([], "Of the 4,095 files, 962 match.", "Of the 4,095 files, 962 match."),
    (None, "Of the 4,095 files, 962 match.", "Of the 4,095 files, 962 match."),
    ([NOTE], "Of the 4,095 files, 962 match.", f"{FACT} Of the 4,095 files, 962 match."),
    ([NOTE], "962 match; I could not confirm the 4,095.", "962 match; I could not confirm the 4,095."),
    ([NOTE, NOTE], "962 match.", f"{FACT} 962 match."),
    ([NOTE], f"{OTHER} {FACT}\n\n962 match.", f"{FACT} {OTHER}\n\n962 match."),
    # only the line that held the fact changes: the list below keeps its indentation and spacing
    ([NOTE], f"962 match:\n\n  - D.SEQ-A   12\n  - D.SEQ-B   3\n\n{FACT}",
     f"{FACT} 962 match:\n\n  - D.SEQ-A   12\n  - D.SEQ-B   3"),
    ([NOTE], "", FACT),
])
def test_premise_first_is_pure(notes, reply, expected):
    assert chatter_mod._premise_first(reply, notes) == expected

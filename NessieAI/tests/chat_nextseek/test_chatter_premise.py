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
from chat_nextseek.graph_review import PREMISE_FACT, PREMISE_FACT_RE
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
    """A reply without the question's number gets Task 9's disclosure after its first sentence, the other fact
    leading; the premise sentence is moved to the front and the other fact stays where it was put."""
    disclosure = f"{OTHER} {FACT}"
    body = _answer(monkeypatch, _says("962 D.SEQ files have 'ABC' in their UID."),
                   [f"What the result matched: {disclosure} State this plainly."], review_disclosure=disclosure)
    assert body.startswith(FACT) and OTHER in body and "962" in body and body.count(FACT) == 1
    assert body == f"{FACT} 962 D.SEQ files have 'ABC' in their UID. {OTHER}"


def test_a_table_led_reply_keeps_its_table_when_the_correction_comes_first(monkeypatch):
    """Task 9's backstop puts the facts after a leading table as a paragraph; the premise sentence is moved before the
    table as a paragraph of its own, never onto the table's first row."""
    table = "| Project | n |\n|---|---|\n| A | 900 |\n| B | 62 |"
    body = _answer(monkeypatch, _says(f"{table}\n\nThese are the matches."), [NOTE], review_disclosure=FACT)
    assert body == f"{FACT}\n\n{table}\n\nThese are the matches."


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


TOP_N = "MATCH (s:T_D_SEQ) WITH s ORDER BY s.reads DESC LIMIT 500 WHERE s.paired = true RETURN count(s) AS n"


@pytest.mark.parametrize("q", ["Of the 500 D.SEQ files with the most reads, how many are paired?",
                               "Of the 500 top-ranked D.SEQ files, how many are paired?",
                               "Of the 500 D.SEQ files with the highest read count, how many are paired?"])
def test_a_top_n_aggregate_gets_no_correction_end_to_end(monkeypatch, q):
    """Fix round 2: an aggregate over the top 500 returns its aggregate (312), not 500, so the 500 must not be read
    as a stated count; otherwise the reply would open "The question says 500; ..." over a correct answer."""
    from chat_nextseek.graph_review import DictCatalog, ReviewInput, review_tier1
    from chat_nextseek.orchestrator import _review_note
    inp = ReviewInput(question=q, cypher=TOP_N, parameters={}, keyword_fields={}, rows=[{"n": 312}], count=1,
                      total=1, ok=True, error=None)
    rv = review_tier1(inp, DictCatalog(None))
    assert not next(c for c in rv.checks if c.name == "premise_count").fired
    disclosure = rv.disclosure if rv.verdict in ("note", "suggest") else None
    reply = "312 of the top 500 D.SEQ files by reads are paired."
    monkeypatch.setattr(chatter_mod, "call_llm_text", _says(reply))
    out = chatter_mod.chatter_agent_answer(
        _Config(), q, EntityAgentOutput().model_dump(), ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": TOP_N, "parameters": {}},
        graph_result={"ok": True, "count": 1, "total": 1, "data": inp.rows},
        query_notes=[_review_note(disclosure)] if disclosure else [], review_disclosure=disclosure, log_dir="")
    body = out.split("**Debug info**")[0].strip()
    assert not PREMISE_FACT_RE.search(body) and body.endswith(reply)


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
    # before a table, a heading or a list the sentence is a paragraph of its own, so the block stays intact
    ([NOTE], "| t | n |\n|---|---|\n| A | 962 |", f"{FACT}\n\n| t | n |\n|---|---|\n| A | 962 |"),
    ([NOTE], f"| t | n |\n|---|---|\n| A | 962 |\n\n{FACT}\n\n962 match.",
     f"{FACT}\n\n| t | n |\n|---|---|\n| A | 962 |\n\n962 match."),
    ([NOTE], "## Matches\n\n962 match.", f"{FACT}\n\n## Matches\n\n962 match."),
    ([NOTE], "- A 900\n- B 62", f"{FACT}\n\n- A 900\n- B 62"),
])
def test_premise_first_is_pure(notes, reply, expected):
    assert chatter_mod._premise_first(reply, notes) == expected


# --------------------------------------------------------------------------- #
# Task 24 (the independent validator)
# --------------------------------------------------------------------------- #

GROUPED = ("MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WITH p, count(s) AS n WHERE n >= 1000 "
           "RETURN p.title AS project, n ORDER BY n DESC")


@pytest.mark.parametrize("q,cypher,rows,total,reply", [
    ("Which projects have 1,000 samples?", GROUPED, [{"project": "MIT_SRP", "n": 57441}], 1,
     "One project has more than 1,000 samples: MIT_SRP (57,441)."),
    ("Show me 100 samples from mice", "MATCH (s:T_MUS) RETURN s.uuid AS uuid ORDER BY s.id",
     [{"uuid": f"M-{i}"} for i in range(200)], 705, "There are 705 mouse samples; here are the first 100."),
], ids=["criterion", "request"])
def test_a_criterion_or_a_request_gets_no_correction_end_to_end(monkeypatch, q, cypher, rows, total, reply):
    """M3: a number with no set-pointing word before it is what the user asks for, not what they say a set holds.
    premise_count read both, and the reply opened "The question says 1,000; this search did not reproduce that
    number." over a correct answer."""
    from chat_nextseek.graph_review import DictCatalog, ReviewInput, review_tier1
    from chat_nextseek.orchestrator import _review_note
    inp = ReviewInput(question=q, cypher=cypher, parameters={}, keyword_fields={}, rows=rows, count=len(rows),
                      total=total, ok=True, error=None)
    rv = review_tier1(inp, DictCatalog(None))
    assert not next(c for c in rv.checks if c.name == "premise_count").fired
    disclosure = rv.disclosure if rv.verdict in ("note", "suggest") else None
    monkeypatch.setattr(chatter_mod, "call_llm_text", _says(reply))
    out = chatter_mod.chatter_agent_answer(
        _Config(), q, EntityAgentOutput().model_dump(), ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": cypher, "parameters": {}},
        graph_result={"ok": True, "count": len(rows), "total": total, "data": rows},
        query_notes=[_review_note(disclosure)] if disclosure else [], review_disclosure=disclosure, log_dir="")
    body = out.split("**Debug info**")[0].strip()
    assert not PREMISE_FACT_RE.search(body) and body == reply


MATCHED = "The matched values were: Non-converter 57, Converter 32, Reverter 9."
BOTH = f"{MATCHED} {FACT}"


@pytest.mark.parametrize("reply,disclosure,expected", [
    # the validator's case: another fact whose numbers the reply lacks
    ("98 samples match; this search did not reproduce the 4,095 in the question.", BOTH,
     f"98 samples match; this search did not reproduce the 4,095 in the question. {MATCHED}"),
    ("98 samples match; I could not confirm the 4,095 in the question.", BOTH,
     f"98 samples match; I could not confirm the 4,095 in the question. {MATCHED}"),
    # the premise sentence alone, and a correction that does not repeat the number
    ("98 samples match; the number in the question was not confirmed.", FACT,
     "98 samples match; the number in the question was not confirmed."),
], ids=["other-fact", "other-fact-could-not-confirm", "number-not-repeated"])
def test_a_reply_that_corrects_it_gets_no_second_correction(monkeypatch, reply, disclosure, expected):
    """M4: whether the reply corrects the number is judged on the model's reply, before the facts backstop adds
    anything. The backstop put the whole disclosure after the first sentence, premise sentence included, and
    _premise_first then found that sentence and moved it to the front: two corrections in one reply."""
    body = _answer(monkeypatch, _says(reply), [f"What the result matched: {disclosure} State this plainly."],
                   review_disclosure=disclosure)
    assert body == expected
    assert not PREMISE_FACT_RE.search(body)


def test_the_backstop_leaves_the_premise_sentence_out_of_a_reply_that_corrects_it():
    reply = "98 samples match; this search did not reproduce the 4,095 in the question."
    assert chatter_mod._with_review_backstop(reply, BOTH, None, corrected=True) == f"{reply} {MATCHED}"
    assert chatter_mod._with_review_backstop("98 samples match.", FACT, None, corrected=True) == "98 samples match."
    assert chatter_mod._with_review_backstop("98 samples match.", BOTH, None) == f"98 samples match. {BOTH}"


@pytest.mark.parametrize("reply,corrected,expected", [
    ("962 match; I could not confirm the 4,095.", True, "962 match; I could not confirm the 4,095."),
    (f"962 match; I could not confirm the 4,095. {FACT}", True, "962 match; I could not confirm the 4,095."),
    ("Of the 4,095 files, 962 match.", False, f"{FACT} Of the 4,095 files, 962 match."),
    (f"Of the 4,095 files, 962 match. {FACT}", False, f"{FACT} Of the 4,095 files, 962 match."),
])
def test_premise_first_follows_the_models_own_correction(reply, corrected, expected):
    """With ``corrected`` carried from the model's reply: a reply that corrects the number keeps one correction, its
    own; any other gets the premise sentence first, once."""
    assert chatter_mod._premise_first(reply, [NOTE], corrected=corrected) == expected

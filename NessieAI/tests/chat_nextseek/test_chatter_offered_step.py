"""The reply states what the graph result matched first, and offers the reviewer's one next step last (Task C3).

The graph-result reviewer (``graph_review``) writes a facts-only disclosure ("The matched values were:
Non-converter 57, Converter 32, Reverter 9.") and, on a ``suggest``, a chip whose ``label`` ("Only Converter") the
chat panel shows. The orchestrator hands the chatter both, as ``review_disclosure`` and ``offered_step`` (the first
chip's label). The chatter:

* adds the line ``Offered next step: <label>`` to its input, beside the query notes rather than among them (a note
  is the query author's words, which the prompt says never to quote back; the step is to be offered in exactly those
  words), so the prompt rule for it can fire;
* after the model reply, adds the disclosure when it carries numbers and the reply is missing any of them
  (compared without thousands separators, so "1,306" matches "1306"); a disclosure without numbers gets no backstop.
  It goes right after the reply's first sentence, so the answer still leads (a reply that opens with a table, a
  heading, a list or a code block gets it as a paragraph after that first block);
* appends ``Would you like me to run: <label>?`` when the reply does not already name the step (case-insensitive)
  and does not already end with a question, which is the model's reworded offer;
* does the same on the fallback reply it writes when the model call raises, where the disclosure goes first.

With no offered step and a reply that already holds the facts, the reply is exactly the model's.

The LLM is stubbed the way ``test_chatter_prompt.py`` does it; the graph turn runs over the B3 harness's stubs
(``test_graph_review_wiring``). No model, Neo4j or network call is made.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import chat_nextseek
from chat_nextseek import orchestrator as orch
from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.llm_clients import LLMAPIConnectionError, LLMFatalError, LLMRateLimitError, LLMTimeoutError
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan
from chat_nextseek.graph_review import PREMISE_FACT
from chat_nextseek.graph_scope import SCOPE_ATTR
from NessieAI.tests.chat_nextseek.test_graph_review_wiring import (
    CONVERTER_CYPHER,
    CONVERTER_Q,
    CONVERTER_ROWS,
    MEMBER,
    NHP_CYPHER,
    install_graph_turn_stubs,
)

DISCLOSURE = "The matched values were: Non-converter 57, Converter 32, Reverter 9."
STEP = "Only Converter"
OFFER = f"Would you like me to run: {STEP}?"
DEBUG_MARK = "\n\n**Debug info**"

#: The prompt rule: the plan's, with "the notes include" made "the message includes the line", because the step is
#: its own line and no longer a note.
RULE = ("When the message includes the line 'Offered next step: X', end the reply with one sentence offering "
        "exactly X, and make no other offer. The facts in 'What the result matched' come first, in the opening "
        "sentences.")


class _StubConfig:
    """Only the attributes chatter_agent_answer touches."""

    CHATTER_SYSTEM_PROMPT = "SYSTEM PROMPT"
    LOG_DIR = ""

    def get_agent_model(self, agent_label):
        return (object(), "stub-model", None)


@pytest.fixture
def model(monkeypatch):
    """The chatter's LLM call, replaced: ``model["reply"]`` is what it answers (or an exception it raises), and
    ``model["user_content"]`` is the prompt it was handed."""
    box: dict = {"reply": "stub reply"}

    def _fake_call_llm_text(config, *, messages, model_name, client, agent_label,
                            temperature=0, thinking_budget=None, usage_label=None):
        box["messages"] = messages
        box["user_content"] = messages[-1]["content"]
        reply = box["reply"]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr(chatter_mod, "call_llm_text", _fake_call_llm_text)
    return box


def _graph_answer(*, rows=None, total=None, notes=None, **kwargs) -> str:
    """One graph-mode chatter call over the converter question."""
    rows = [{"Classification": "Non-converter", "n": 57}, {"Classification": "Converter", "n": 32},
            {"Classification": "Reverter", "n": 9}] if rows is None else rows
    return chatter_mod.chatter_agent_answer(
        _StubConfig(),
        CONVERTER_Q,
        EntityAgentOutput().model_dump(),
        ParserPlan(mode="graph_query", intent_summary=CONVERTER_Q).model_dump(),
        graph_plan={"cypher": CONVERTER_CYPHER, "explanation": "", "parameters": {}},
        graph_result={"ok": True, "data": rows, "count": len(rows),
                      "total": len(rows) if total is None else total},
        log_dir="",
        query_notes=notes,
        **kwargs,
    )


def _body(reply: str) -> str:
    """The reply the user reads, without the debug block the chatter appends."""
    return reply.split(DEBUG_MARK, 1)[0]


# --------------------------------------------------------------------------- #
# The brief's three
# --------------------------------------------------------------------------- #

def test_a_reply_missing_the_numbers_gets_the_facts_after_its_answer_and_the_offer_last(model):
    """(a) The model dropped 57 and never offered the step: the disclosure follows the reply's answer sentence, the
    offer closes it."""
    model["reply"] = "There are 98 samples for subjects who convert to Mtb infection positive."

    body = _body(_graph_answer(review_disclosure=DISCLOSURE, offered_step=STEP))

    assert body == f"{model['reply']} {DISCLOSURE}\n\n{OFFER}"


def test_with_no_offered_step_the_reply_is_exactly_the_models(model):
    """(b) Nothing offered and nothing disclosed: the reply is the model's, word for word."""
    model["reply"] = "There are 98 samples for subjects who convert to Mtb infection positive."

    assert _body(_graph_answer()) == model["reply"]


@pytest.mark.parametrize("error", [
    LLMAPIConnectionError("connection reset"),
    LLMRateLimitError("429"),
    LLMFatalError("every provider refused"),
    LLMTimeoutError("timed out"),
], ids=["connection", "rate-limit", "fatal", "timeout"])
def test_the_fallback_reply_carries_the_facts_and_the_offer(model, error):
    """(c) The model call raised, so the chatter writes the reply itself: the facts and the offer are still in it."""
    model["reply"] = error

    reply = _graph_answer(review_disclosure=DISCLOSURE, offered_step=STEP)

    assert reply.startswith(DISCLOSURE)
    assert reply.endswith(OFFER)
    assert "record(s)" in reply, "the fallback's own account of the result is kept"


def test_the_fallback_carries_facts_that_hold_no_number(model):
    """No model wrote the fallback, so nothing else can state a failed query: its facts go first, number or not."""
    model["reply"] = LLMAPIConnectionError("connection reset")
    facts = "The database query failed."

    reply = _graph_answer(rows=[], review_disclosure=facts)

    assert reply == f"{facts}\n\nGraph query returned 0 record(s), but had a connection issue summarizing the results."


def test_a_fallback_with_nothing_offered_is_unchanged(model):
    model["reply"] = LLMAPIConnectionError("connection reset")

    assert _graph_answer() == ("Graph query returned 3 record(s), but had a connection issue summarizing the "
                               "results.")


# --------------------------------------------------------------------------- #
# R3: the disclosure backstop
# --------------------------------------------------------------------------- #

def test_a_reply_that_states_every_number_is_not_prefixed(model):
    model["reply"] = ("98 samples matched: 57 Non-converter, 32 Converter and 9 Reverter. "
                      "Would you like me to run: Only Converter?")

    assert _body(_graph_answer(review_disclosure=DISCLOSURE, offered_step=STEP)) == model["reply"]


def test_one_missing_number_is_enough_to_add_the_facts(model):
    model["reply"] = "98 samples matched, 57 of them Non-converter. Would you like me to run: Only Converter?"

    body = _body(_graph_answer(review_disclosure=DISCLOSURE, offered_step=STEP))

    assert body == ("98 samples matched, 57 of them Non-converter. "
                    f"{DISCLOSURE} Would you like me to run: Only Converter?")


def test_thousands_separators_do_not_count_as_a_difference(model):
    """"1,306" in the facts and "1306" in the reply are the same number, and the other way round."""
    facts = ("The search matched 'tiff' only; stored values also include 'tif' and 'TIF'. "
             "Every spelling of 'tif' gives 1,556.")
    model["reply"] = "There are 1306 TIFF images; counting every spelling of tif gives 1556."

    assert _body(_graph_answer(review_disclosure=facts)) == model["reply"]

    model["reply"] = "There are 1,306 TIFF images."
    assert _body(_graph_answer(review_disclosure="Every spelling of 'tif' gives 1306.")) == model["reply"]


def test_a_number_inside_another_number_or_a_code_does_not_count(model):
    """"57" inside "1,570" is not the 57 of the facts, and the "14" in the code "T14" is not the count 14."""
    model["reply"] = "1,570 samples, 32 Converter and 9 Reverter."
    assert _body(_graph_answer(review_disclosure=DISCLOSURE)) == f"{model['reply']} {DISCLOSURE}"

    facts = "The matched values were: T14 15, T13 14."
    model["reply"] = "T14 has 15 samples and T13 has 12."
    assert _body(_graph_answer(review_disclosure=facts)) == f"{model['reply']} {facts}"

    model["reply"] = "T14 has 15 samples and T13 has 14."
    assert _body(_graph_answer(review_disclosure=facts)) == model["reply"]


def test_a_disclosure_with_no_numbers_gets_no_backstop(model):
    """Breakage, a zero proof or an unapplied value carries no number: the note reaches the model and that is all."""
    model["reply"] = "There are no NHP samples."

    for facts in ("The database query failed.",
                  "The search term also matches 'Non-converter'."):
        assert _body(_graph_answer(review_disclosure=facts)) == model["reply"]


def test_the_facts_backstop_needs_no_offered_step(model):
    """The facts are added whether or not a chip was offered, and no offer is invented without one."""
    model["reply"] = "There are 98 matching samples."

    body = _body(_graph_answer(review_disclosure=DISCLOSURE))

    assert body == f"{model['reply']} {DISCLOSURE}"
    assert "Would you like me to run" not in body


# --------------------------------------------------------------------------- #
# Where the facts go in a model's reply: after its first sentence, so the answer still leads
# --------------------------------------------------------------------------- #

def test_the_facts_go_between_the_first_sentence_and_the_rest(model):
    model["reply"] = "There are 98 matching samples. Most come from one project."

    body = _body(_graph_answer(review_disclosure=DISCLOSURE))

    assert body == f"There are 98 matching samples. {DISCLOSURE} Most come from one project."


def test_codes_numbers_and_abbreviations_do_not_end_the_first_sentence(model):
    """No split inside "D.SEQ", "T.TIS", a UID, "1,306" or "3.5", or after "e.g.", "i.e." or "vs."."""
    facts = "Every spelling of 'seq' gives 1,412."
    first = ("There are 1,306 D.SEQ files (e.g. D.SEQ-240910ABC-1), i.e. runs with a mean RIN of 3.5 vs. 7.2 for "
             "T.TIS samples.")
    model["reply"] = f"{first} Most were uploaded last year."

    body = _body(_graph_answer(review_disclosure=facts))

    # the UID is linked to its sample page last, as in every reply
    assert body == chatter_mod.link_sample_uids(f"{first} {facts} Most were uploaded last year.")


def test_a_one_sentence_reply_gets_the_facts_after_it(model):
    model["reply"] = "There are 98 matching samples."

    assert _body(_graph_answer(review_disclosure=DISCLOSURE)) == f"There are 98 matching samples. {DISCLOSURE}"


def test_a_reply_opening_with_a_table_gets_the_facts_after_the_table(model):
    table = "| Classification | n |\n|---|---|\n| Converter | 32 |\n| Reverter | 9 |"
    model["reply"] = f"{table}\n\nMost of these are Converter."

    body = _body(_graph_answer(review_disclosure=DISCLOSURE))

    assert body == f"{table}\n\n{DISCLOSURE}\n\nMost of these are Converter."


def test_the_premise_fact_still_comes_first(model):
    """F-b: the premise sentence opens the reply; the other facts stay after the first sentence."""
    premise = PREMISE_FACT.format(n="4,095")
    model["reply"] = "There are 98 matching samples. Most come from one project."

    body = _body(_graph_answer(review_disclosure=f"{DISCLOSURE} {premise}"))

    assert body == f"{premise} There are 98 matching samples. {DISCLOSURE} Most come from one project."


F = "FACTS 57."


@pytest.mark.parametrize("reply,expected", [
    ("One. Two.", f"One. {F} Two."),
    ("One.", f"One. {F}"),
    ("One?  Two.", f"One? {F}  Two."),
    ("One.\n\nTwo.", f"One. {F}\n\nTwo."),
    ("One.\nTwo.", f"One. {F}\nTwo."),
    ("The first match is D.SEQ-240910ABC-1. It has 3 files.",
     f"The first match is D.SEQ-240910ABC-1. {F} It has 3 files."),
    ("The mean is 3.5. Two.", f"The mean is 3.5. {F} Two."),
    ("About 98 matched, approx. half of them converters. Two.",
     f"About 98 matched, approx. half of them converters. {F} Two."),
    ("98 matched, etc. and more. Two.", f"98 matched, etc. and more. {F} Two."),
    ("57 and 32 matched resp. the rest had 9. Two.", f"57 and 32 matched resp. the rest had 9. {F} Two."),
    ("**98 samples matched.** Two.", f"**98 samples matched.** {F} Two."),
    ('It is "Converter." Two.', f'It is "Converter." {F} Two.'),
    ("The code is D.SEQ and T.TIS. Two.", f"The code is D.SEQ and T.TIS. {F} Two."),
    # no sentence end on the first line: the facts follow the first block as a paragraph of their own
    ("98 samples matched:\n- Converter 32\n- Reverter 9", f"98 samples matched:\n- Converter 32\n- Reverter 9\n\n{F}"),
    ("98 samples matched, by class", f"98 samples matched, by class\n\n{F}"),
    # a first block that is not prose: a table, a heading, a list, a numbered list, a quote or a code block
    ("| a | n |\n|---|---|\n| A | 3 |\n\nTwo.", f"| a | n |\n|---|---|\n| A | 3 |\n\n{F}\n\nTwo."),
    ("## Converter samples\n\n98 samples matched.", f"## Converter samples\n\n{F}\n\n98 samples matched."),
    ("- Converter 32\n- Reverter 9\n\nTwo.", f"- Converter 32\n- Reverter 9\n\n{F}\n\nTwo."),
    ("1. Converter 32\n2. Reverter 9", f"1. Converter 32\n2. Reverter 9\n\n{F}"),
    ("> Converter 32. Reverter 9.\n\nTwo.", f"> Converter 32. Reverter 9.\n\n{F}\n\nTwo."),
    ("```\nA 32\n\nB 9\n```\nMost are A.", f"```\nA 32\n\nB 9\n```\n\n{F}\n\nMost are A."),
    ("```\nA 32\n\nB 9", f"```\nA 32\n\nB 9\n\n{F}"),
    ("", F),
])
def test_after_first_sentence_is_pure(reply, expected):
    assert chatter_mod._after_first_sentence(reply, F) == expected


# --------------------------------------------------------------------------- #
# R4: the offer backstop
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ending", [
    "Would you like me to run: Only Converter?",
    "Would you like me to show only converter samples?",
    "I can run ONLY CONVERTER next.",
])
def test_a_reply_that_already_names_the_step_gets_no_second_offer(model, ending):
    model["reply"] = f"98 samples matched: Non-converter 57, Converter 32, Reverter 9. {ending}"

    assert _body(_graph_answer(review_disclosure=DISCLOSURE, offered_step=STEP)) == model["reply"]


def test_a_reply_without_the_step_gets_the_offer_appended(model):
    model["reply"] = "98 samples matched: Non-converter 57, Converter 32, Reverter 9."

    body = _body(_graph_answer(review_disclosure=DISCLOSURE, offered_step=STEP))

    assert body == f"{model['reply']}\n\n{OFFER}"


@pytest.mark.parametrize("ending", [
    "Shall I restrict this to the Converter subjects?",
    "Do you want the 32 Converter subjects on their own?",
    "**Want just the converters?**",
])
def test_a_reply_ending_with_a_reworded_offer_gets_no_second_offer(model, ending):
    """The model offered the step in its own words: its closing question is the offer, so none is added."""
    model["reply"] = f"98 samples matched: Non-converter 57, Converter 32, Reverter 9. {ending}"

    assert _body(_graph_answer(review_disclosure=DISCLOSURE, offered_step=STEP)) == model["reply"]


def test_a_reply_ending_with_a_statement_still_gets_the_offer(model):
    """Only the final sentence counts: a question earlier in the reply is not the closing offer."""
    model["reply"] = "Did you mean the converters? 98 samples matched: Non-converter 57, Converter 32, Reverter 9."

    body = _body(_graph_answer(review_disclosure=DISCLOSURE, offered_step=STEP))

    assert body == f"{model['reply']}\n\n{OFFER}"


def test_the_offer_goes_before_the_debug_block(model):
    model["reply"] = "98 samples matched: Non-converter 57, Converter 32, Reverter 9."

    reply = _graph_answer(review_disclosure=DISCLOSURE, offered_step=STEP)

    assert reply.index(OFFER) < reply.index("**Debug info**")
    assert reply.count(OFFER) == 1


# --------------------------------------------------------------------------- #
# What the model is handed
# --------------------------------------------------------------------------- #

def test_the_offered_step_is_its_own_line_outside_the_notes(model):
    """A note is the query author's words, which the prompt says never to quote back; the step is quoted exactly.
    So it is a line of its own, right after the block that holds the notes, and inside no note."""
    notes = [f"What the result matched: {DISCLOSURE} State this plainly in the first sentences."]
    _graph_answer(notes=notes, review_disclosure=DISCLOSURE, offered_step=STEP)
    text = model["user_content"]
    lines = text.splitlines()

    assert "Offered next step: Only Converter" in lines
    assert not any("Offered next step" in line for line in lines if "Note from whoever built the query" in line)
    scope_at = text.index("What the query actually did:")
    scope_end = text.index("\n\n", scope_at)
    assert "Offered next step" not in text[scope_at:scope_end], "not inside the scope block"
    assert text[scope_end:].startswith("\n\nOffered next step: Only Converter\n"), "right after it"


def test_no_offered_step_means_no_line_and_the_same_prompt(model):
    """R6: without an offered step the chatter is handed exactly what it was handed before, disclosure or not."""
    notes = [f"What the result matched: {DISCLOSURE} State this plainly in the first sentences."]
    _graph_answer(notes=notes)
    before = model["messages"]

    _graph_answer(notes=notes, review_disclosure=DISCLOSURE)

    assert model["messages"] == before
    assert "Offered next step" not in model["user_content"]


def test_a_count_turn_makes_no_offer_of_its_own_and_an_offered_step_is_the_only_one(model):
    """The count-only instruction makes no offer (F-d, Task 19); with a chip, the chip is the only offer."""
    no_offer = "make no offer of your own"

    _graph_answer(rows=[{"n": 98}])
    assert no_offer in model["user_content"], "without an offered step the reply ends on the answer"
    assert "Offered next step" not in model["user_content"]

    _graph_answer(rows=[{"n": 98}], review_disclosure=DISCLOSURE, offered_step=STEP)
    assert no_offer not in model["user_content"]
    assert "Offered next step" in model["user_content"]
    assert "make no other offer" in model["user_content"]


def _prompt_text() -> str:
    return (Path(chat_nextseek.__file__).resolve().parent / "prompts" / "chatter_agent.txt").read_text(encoding="utf-8")


def test_the_prompt_holds_the_rule_after_the_count_only_rule():
    text = _prompt_text()

    assert RULE in text
    assert text.index("WHEN THE RESULT IS ONLY A NUMBER") < text.index(RULE)
    assert text.index(RULE) < text.index("MODE: search")


def test_the_rule_wins_over_the_count_only_offer():
    """R5: the count-only rule asks for an offer; when a step is offered the new rule says, in plain words, it wins."""
    text = _prompt_text()
    after = text[text.index(RULE) + len(RULE):text.index("MODE: search")]

    assert "WHEN THE RESULT IS ONLY A NUMBER" in after
    assert "only offer" in after


def test_the_prompt_names_the_offered_step_among_its_inputs():
    """The prompt lists the blocks it is given and says there are no others, so the new line is listed."""
    text = _prompt_text()
    given = text[text.index("WHAT YOU ARE GIVEN"):text.index("Rules that apply in all modes")]

    assert "`Offered next step: X`" in given


def test_the_qualifying_list_names_what_the_result_matched():
    text = _prompt_text()
    at = text.index("HOW MUCH YOU MAY SAY ABOUT THE QUERY")
    qualifying = text[at:text.index("NEVER NARRATE THE RETRY PATH", at)]

    assert "What the result matched" in qualifying


# --------------------------------------------------------------------------- #
# The orchestrator hands both over, from this turn only
# --------------------------------------------------------------------------- #

def _graph_turn(monkeypatch, tmp_path, *, question, cypher, rows, ok=True, offer_suggestions=True) -> tuple[dict, dict]:
    """One ``_execute_graph_turn`` over the B3 stubs; returns the chatter's keyword arguments and the debug payload."""
    seen: dict = {}
    with monkeypatch.context() as m:
        install_graph_turn_stubs(m, cypher=cypher, rows=rows, ok=ok)

        def _chatter(*a, **k):
            seen.update(k)
            return "reply"

        m.setattr(orch, "chatter_agent_answer", _chatter)
        debug: dict = {}
        orch._execute_graph_turn(
            config=SimpleNamespace(MODEL_MODE="test", **{SCOPE_ATTR: MEMBER}), session={}, user_text=question,
            entity_result=EntityAgentOutput(), plan=ParserPlan(mode="graph_query", intent_summary=question),
            log_dir=str(tmp_path),
            artifact_store=SimpleNamespace(register_path=lambda **k: None, write_json=lambda **k: None),
            send_event=lambda name, data=None: None, debug_payload=debug, t_total_start=time.perf_counter(),
            offer_suggestions=offer_suggestions,
        )
    return seen, debug


def test_a_suggest_turn_hands_the_chatter_the_chip_label_and_the_disclosure(monkeypatch, tmp_path):
    kwargs, debug = _graph_turn(monkeypatch, tmp_path, question=CONVERTER_Q, cypher=CONVERTER_CYPHER,
                                rows=CONVERTER_ROWS)

    assert debug["suggestions"][0]["label"] == STEP
    assert kwargs["offered_step"] == STEP
    assert kwargs["review_disclosure"] == debug["graph_review"]["disclosure"] == DISCLOSURE


def test_an_ok_turn_hands_over_neither(monkeypatch, tmp_path):
    kwargs, debug = _graph_turn(monkeypatch, tmp_path, question="How many NHP samples are there?",
                                cypher=NHP_CYPHER, rows=[{"n": 725}])

    assert debug["graph_review"]["verdict"] == "ok"
    assert kwargs["offered_step"] is None and kwargs["review_disclosure"] is None


def test_a_turn_that_clicked_a_chip_offers_no_step(monkeypatch, tmp_path):
    """No chip on a click (no chains), so no offer either; the review's facts still go over."""
    kwargs, debug = _graph_turn(monkeypatch, tmp_path, question=CONVERTER_Q, cypher=CONVERTER_CYPHER,
                                rows=CONVERTER_ROWS, offer_suggestions=False)

    assert "suggestions" not in debug
    assert kwargs["offered_step"] is None
    assert kwargs["review_disclosure"] == DISCLOSURE


def test_a_note_turn_hands_over_the_facts_and_no_step(monkeypatch, tmp_path):
    kwargs, debug = _graph_turn(monkeypatch, tmp_path, question="How many NHP samples are there?",
                                cypher=NHP_CYPHER, rows=[], ok=False)

    assert debug["graph_review"]["verdict"] == "note"
    assert kwargs["offered_step"] is None
    assert kwargs["review_disclosure"] == "The database query failed."

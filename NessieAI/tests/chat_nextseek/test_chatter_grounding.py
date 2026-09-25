"""The NS reply says only what it was shown, ends with no stock closer, and writes a count against the set it is out
of when the data gives that set (Phase F, F-c and F-d).

* F-c, a prompt rule: a cause, a limit or something done earlier is stated only when a data block, a note or an
  earlier reply shows it; asked why with nothing that says why, the reply says it cannot tell from what it has.
* F-d, prompt rules: "N of M" only when the data block or a note already gives M (never an estimate); what the number
  counts belongs in the answer sentence, how it was found does not; a constraint is named in the user's words, never
  as "the keyword X"; the reply's only offer is the ``Offered next step`` line (Task 9), and the count-only rule no
  longer asks for one of its own.
* F-d, a code backstop: ``_drop_stock_closer`` takes a final stock-offer sentence with no digit in it ("If you would
  like ..., let me know.") off the model's reply when no step is offered. It runs on the model's reply before Task 9's
  backstops, so the offer Task 9 appends is never a candidate, and before the premise backstop.

The LLM is stubbed; no model, Neo4j or network call is made.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import chat_nextseek
from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.graph_review import PREMISE_FACT
from chat_nextseek.llm_clients import LLMFatalError
from chat_nextseek.schemas.entity import EntityAgentOutput, EntityItem
from chat_nextseek.schemas.router import ParserPlan

PROMPT = (Path(chat_nextseek.__file__).resolve().parent / "prompts" / "chatter_agent.txt").read_text(encoding="utf-8")


def _section(title, size=1500):
    at = PROMPT.index(title)
    return PROMPT[at:at + size]


class _Config:
    CHATTER_SYSTEM_PROMPT = PROMPT
    LOG_DIR = ""

    def get_agent_model(self, agent_label):
        return (object(), "stub-model", None)


def _answer(monkeypatch, model_reply, box=None, **kw):
    def _fake(config, *, messages, **k):
        if box is not None:
            box["messages"] = messages
        return model_reply
    monkeypatch.setattr(chatter_mod, "call_llm_text", _fake)
    out = chatter_mod.chatter_agent_answer(
        _Config(), "How many patients are in the TCGA GBM study?",
        EntityAgentOutput(sampletypes=[EntityItem(code="PAT", name="Patient")]).model_dump(),
        ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (s:T_PAT)-[:IN_STUDY]->(st:Study) WHERE st.title = $t RETURN count(s) AS n",
                    "parameters": {"t": "GBM"}},
        graph_result={"ok": True, "count": 1, "total": 1, "data": [{"n": 617}]},
        log_dir="", **kw)
    return out.split("**Debug info**")[0].strip()


def test_the_prompt_forbids_a_cause_nothing_showed():
    rule = _section("SAY ONLY WHAT YOU WERE SHOWN")
    assert "I showed you the first 20 rows" in rule
    assert "say you cannot tell from what you have" in rule


def test_the_rule_reaches_the_system_message(monkeypatch):
    box = {}
    _answer(monkeypatch, "There are 617 patients.", box)
    assert "SAY ONLY WHAT YOU WERE SHOWN" in box["messages"][0]["content"]


def test_a_count_only_answer_is_no_longer_asked_for_an_offer(monkeypatch):
    assert "offer that as the one next step" not in PROMPT
    box = {}
    _answer(monkeypatch, "There are 617 patients.", box)
    assert "offer that as the one next step" not in box["messages"][-1]["content"]
    assert "make no offer of your own" in box["messages"][-1]["content"]


def test_the_prompt_says_what_the_number_is_out_of_and_what_it_counts():
    rule = _section("WHAT THE NUMBER IS OUT OF")
    assert "Never estimate a total you were not given" in rule
    assert "not a description of the search" in rule
    assert "HOW MUCH YOU MAY SAY ABOUT THE QUERY" in rule


def test_the_prompt_has_one_offer_at_most():
    rule = _section("NO CLOSING OFFER", 700)
    assert "Offered next step" in rule and "If you would like" in rule


def test_the_prompt_never_names_a_constraint_as_a_keyword():
    assert 'never as "the keyword X"' in PROMPT


def test_a_stock_closer_is_dropped_when_no_step_is_offered(monkeypatch):
    body = _answer(monkeypatch, "There are 617 patients in the TCGA GBM study. If you would like to retrieve the "
                                "specific patient identifiers, please let me know.")
    assert body == "There are 617 patients in the TCGA GBM study."


def test_a_closing_sentence_with_a_number_stays(monkeypatch):
    reply = "There are 617 patients. Let me know if the 12 without a diagnosis should count."
    assert _answer(monkeypatch, reply) == reply


def test_an_offered_step_is_never_dropped(monkeypatch):
    body = _answer(monkeypatch, "There are 617 patients. Would you like me to run: Only primary tumors?",
                   offered_step="Only primary tumors")
    assert body.rstrip().endswith("Only primary tumors?")


# --------------------------------------------------------------------------- #
# Where each new rule sits in the prompt, and what it replaced
# --------------------------------------------------------------------------- #

def test_the_new_rules_sit_where_the_plan_puts_them():
    """SAY ONLY WHAT YOU WERE SHOWN follows the all-modes list; the two count rules follow COUNT THE THING YOU NAME
    and come before the count-only rule; every one of them is in the part that applies to all modes."""
    at = PROMPT.index
    assert at("- Keep answers short. Lead with the key finding.") < at("SAY ONLY WHAT YOU WERE SHOWN") < at(
        "ANSWER THE QUESTION FIRST")
    assert at("COUNT THE THING YOU NAME") < at("WHAT THE NUMBER IS OUT OF") < at("NO CLOSING OFFER") < at(
        "WHEN THE RESULT IS ONLY A NUMBER") < at("MODE: search")


def test_the_count_only_rule_says_it_is_a_count_and_makes_no_offer():
    rule = _section("WHEN THE RESULT IS ONLY A NUMBER", 900)
    rule = rule[:rule.index("\n")]
    assert "say in one clause that this is a count and the records themselves were not returned" in rule
    assert rule.rstrip().endswith("Make no offer of your own.")
    assert "next step" not in rule


def test_no_closing_offer_names_the_offered_step_line_not_a_note():
    """Task 9 made the offered step a line of its own ("It is not a note"), so the rule names the line."""
    rule = _section("NO CLOSING OFFER", 900)
    rule = rule[:rule.index("\n")]
    assert "When the message includes the `Offered next step` line" in rule
    assert "notes carry" not in rule and "Offered next step` note" not in rule
    for closer in ('"If you would like ..."', '"Let me know if ..."', '"Feel free to ..."', '"I can retrieve ..."'):
        assert closer in rule


def test_the_keyword_rule_is_in_name_things_from_the_query():
    rule = _section("NAME THINGS FROM THE QUERY", 900)
    rule = rule[:rule.index("\n")]
    assert 'Name a constraint in the user\'s words ("in the TCGA LUAD study")' in rule
    assert 'never as "the keyword X" or "matching the keyword X"' in rule


def test_the_new_prompt_text_has_no_em_dash():
    for title in ("SAY ONLY WHAT YOU WERE SHOWN", "WHAT THE NUMBER IS OUT OF", "NO CLOSING OFFER",
                  "WHEN THE RESULT IS ONLY A NUMBER", "NAME THINGS FROM THE QUERY"):
        paragraph = _section(title, 2000)
        paragraph = paragraph[:paragraph.index("\n")]
        assert "\u2014" not in paragraph and "\u2013" not in paragraph, title


# --------------------------------------------------------------------------- #
# _drop_stock_closer, pure
# --------------------------------------------------------------------------- #

DROPPED = [
    ("There are 617 patients. If you'd like, I can list them.", "There are 617 patients."),
    ("There are 617 patients.\n\nLet me know if you need the identifiers.", "There are 617 patients."),
    ("There are 617 patients. Feel free to ask for a breakdown by project.", "There are 617 patients."),
    ("There are 617 patients. I can also retrieve their identifiers.", "There are 617 patients."),
    ("There are 617 patients. Would you like the list?", "There are 617 patients."),
    ("There are 617 patients. Do you want the list", "There are 617 patients."),
    ("There are 617 patients! Should you need their identifiers, just ask.", "There are 617 patients!"),
    # a closer on a line of its own after a list or a table, and a typographic apostrophe
    ("- A\n- B\nIf you would like more detail, let me know.", "- A\n- B"),
    ("| a | n |\n|---|---|\n| A | 3 |\n\nLet me know if you want the identifiers.", "| a | n |\n|---|---|\n| A | 3 |"),
    ("There are 617 patients. If you\u2019d like, I can list them.", "There are 617 patients."),
    ("- A\n- B\n\nIf you\u2019d like the identifiers, let me know.", "- A\n- B"),
]


@pytest.mark.parametrize("reply,kept", DROPPED)
def test_a_final_stock_offer_is_dropped(reply, kept):
    assert chatter_mod._drop_stock_closer(reply) == kept


@pytest.mark.parametrize("reply", [
    "There are 617 patients.",
    "There are 617 patients. Let me know if the 12 without a diagnosis should count.",
    "If you would like the identifiers, they are attached to this turn. There are 617 patients.",
    "There are 617 patients. Most come from one site.",
    "Let me know if you want the list.",   # the whole reply: never emptied
    "",
    "- A\n- B\nLet me know if the 12 without a diagnosis should count.",   # a digit, on a line of its own
    "- A\n- I can list them",   # a list item is no closer
    # a closer leading into a list or a table holds the answer: it is one line, and never takes the list with it
    "There are 3 projects. Feel free to pick one of these:\n- Impact\n- MetNet\n- IMPAcTb",
    "Three projects match. I can show the samples for any of them:\n\n| Project |\n|---|\n| Impact |\n| MetNet |",
    # a line-start closer counts only after a sentence end, a colon, a list line or a table line
    "The samples all come from one site, and\nfeel free to ask for more.",
])
def test_anything_else_is_left_as_written(reply):
    assert chatter_mod._drop_stock_closer(reply) == reply


# --------------------------------------------------------------------------- #
# Where the drop runs: the model's reply only, before Task 9's backstops and the premise backstop
# --------------------------------------------------------------------------- #

FACTS = "The search matched the study title 'GBM' only; 'TCGA-GBM' holds 12 more patients."


def test_the_closer_goes_before_the_facts_are_added(monkeypatch):
    """The reviewer's facts are added to the reply without its closer: they follow the answer, and the reply does not
    end on an offer."""
    body = _answer(monkeypatch, "There are 617 patients. If you would like their identifiers, let me know.",
                   review_disclosure=FACTS)
    assert body == f"There are 617 patients. {FACTS}"


def test_the_closer_goes_and_the_premise_still_comes_first(monkeypatch):
    fact = PREMISE_FACT.format(n="4,095")
    note = f"What the result matched: {fact} State this plainly in the first sentences."
    body = _answer(monkeypatch, "Of the 4,095 patients, 617 are in the GBM study. Let me know if you want the list.",
                   query_notes=[note])
    assert body == f"{fact} Of the 4,095 patients, 617 are in the GBM study."


def test_a_closer_after_a_list_is_dropped_end_to_end(monkeypatch):
    reply = "617 patients match:\n- 600 primary\n- 17 recurrent\nIf you\u2019d like their identifiers, let me know."
    assert _answer(monkeypatch, reply) == "617 patients match:\n- 600 primary\n- 17 recurrent"


def test_with_an_offered_step_the_models_closer_is_not_touched(monkeypatch):
    """The drop runs only when no step is offered; with one, the reply's closing question is its offer (Task 9)."""
    reply = "There are 617 patients. Would you like to see only the primary tumors?"
    assert _answer(monkeypatch, reply, offered_step="Only primary tumors") == reply


def test_the_fallback_reply_is_not_a_candidate(monkeypatch):
    """No model wrote the fallback; its text is the chatter's own and is left alone."""
    def _down(*a, **k):
        raise LLMFatalError("every provider refused")
    monkeypatch.setattr(chatter_mod, "call_llm_text", _down)
    out = chatter_mod.chatter_agent_answer(
        _Config(), "How many RNA samples are there?", EntityAgentOutput().model_dump(),
        ParserPlan(mode="new_search", intent_summary="RNA samples").model_dump(),
        api_result_slim={"ok": True, "data": {"total": 140}}, log_dir="")
    assert out.rstrip().endswith("- total matches: 140")


def test_the_rest_reply_loses_its_closer_too(monkeypatch):
    """F-d holds for every NS reply the chatter writes, not only the graph's."""
    monkeypatch.setattr(chatter_mod, "call_llm_text",
                        lambda *a, **k: "140 RNA samples match. Let me know if you would like a breakdown by project.")
    out = chatter_mod.chatter_agent_answer(
        _Config(), "How many RNA samples are there?", EntityAgentOutput().model_dump(),
        ParserPlan(mode="new_search", intent_summary="RNA samples").model_dump(),
        api_result_slim={"ok": True, "data": {"total": 140}},
        api_result_full={"ok": True, "data": {"total": 140, "samples": []}}, log_dir="")
    assert out.split("**Debug info**")[0].strip() == "140 RNA samples match."


# --------------------------------------------------------------------------- #
# Only an answered result loses its closer: on a zero, a failed query or an error, the question is the answer
# --------------------------------------------------------------------------- #

def _graph_turn(monkeypatch, reply, graph_result, notes=None):
    monkeypatch.setattr(chatter_mod, "call_llm_text", lambda *a, **k: reply)
    out = chatter_mod.chatter_agent_answer(
        _Config(), "How many samples does the Qwerty lab have?", EntityAgentOutput().model_dump(),
        ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE s.search_text CONTAINS $t RETURN count(s) AS n",
                    "parameters": {"t": "qwerty"}},
        graph_result=graph_result, query_notes=notes, log_dir="")
    return out.split("**Debug info**")[0].strip()


def _rest_turn(monkeypatch, reply, *, total, error_context=None):
    monkeypatch.setattr(chatter_mod, "call_llm_text", lambda *a, **k: reply)
    out = chatter_mod.chatter_agent_answer(
        _Config(), "How many RNA samples are there?", EntityAgentOutput().model_dump(),
        ParserPlan(mode="new_search", intent_summary="RNA samples").model_dump(),
        api_result_slim={"ok": error_context is None, "data": {"total": total}},
        api_result_full={"ok": error_context is None, "data": {"total": total, "samples": []}},
        error_context=error_context, log_dir="")
    return out.split("**Debug info**")[0].strip()


def _report_turn(monkeypatch, reply, total_rows):
    monkeypatch.setattr(chatter_mod, "call_llm_text", lambda *a, **k: reply)
    out = chatter_mod.chatter_agent_answer(
        _Config(), "Summarise the Impact project", EntityAgentOutput().model_dump(),
        ParserPlan(mode="reporter").model_dump(), reporter_summary={"project": "Impact", "total_rows": total_rows},
        log_dir="")
    return out.split("**Debug info**")[0].strip()


@pytest.mark.parametrize("reply,kept", DROPPED)
def test_every_drop_case_still_drops_on_an_answered_turn(monkeypatch, reply, kept):
    assert _answer(monkeypatch, reply) == kept


NEAR_MISS_REPLY = ("There are no samples for the Qwerty lab. Would you like me to search the Qwertz lab (QWZ), the "
                   "closest on record?")


@pytest.mark.parametrize("data", [[{"n": 0}], []], ids=["count-zero", "no-rows"])
def test_a_lab_near_miss_offer_on_a_zero_is_kept(monkeypatch, data):
    """The misspelled lab's closest spelling is the answer to a zero (the 2026-09-22 fix): its question stays."""
    from chat_nextseek.helpers.lab_code import lab_near_miss_notes
    notes = lab_near_miss_notes([{"text": "Qwerty", "code": "QWZ", "name": "Qwertz"}])
    body = _graph_turn(monkeypatch, NEAR_MISS_REPLY, {"ok": True, "count": len(data), "total": len(data),
                                                      "data": data}, notes)
    assert body == NEAR_MISS_REPLY


def test_a_failed_graph_query_keeps_its_question(monkeypatch):
    """A failed query, even one whose result still carries a total from its count probe."""
    reply = "The query could not run. Do you want me to try it on the Impact project alone?"
    assert _graph_turn(monkeypatch, reply, {"ok": False, "count": 0, "total": 12, "data": [],
                                            "error": "timeout"}) == reply


CLARIFY = "The search needs a project to scope it. Do you want the Impact or the MetNet project?"


def test_a_clarifying_question_on_an_error_turn_is_kept(monkeypatch):
    """An error turn, even one whose envelope still carries a total from somewhere."""
    assert _rest_turn(monkeypatch, CLARIFY, total=5,
                      error_context={"status_code": 400, "error": "a project is required"}) == CLARIFY


def test_a_clarifying_question_on_a_zero_turn_is_kept(monkeypatch):
    assert _rest_turn(monkeypatch, CLARIFY, total=0) == CLARIFY


def test_a_rest_closer_on_an_answered_turn_still_goes(monkeypatch):
    assert _rest_turn(monkeypatch, f"140 RNA samples match. {CLARIFY.split('. ')[1]}", total=140) == (
        "140 RNA samples match.")


def test_a_report_closer_goes_only_when_the_report_has_rows(monkeypatch):
    reply = "The Impact report has {n} rows. Let me know if you want it by lab."
    assert _report_turn(monkeypatch, reply.format(n=12), 12) == "The Impact report has 12 rows."
    assert _report_turn(monkeypatch, reply.format(n=0), 0) == reply.format(n=0)


def test_no_closing_offer_says_it_applies_to_an_answered_result():
    rule = _section("NO CLOSING OFFER", 900)
    rule = rule[:rule.index("\n")]
    assert "This applies to an answered result." in rule
    assert ("When the result is empty or the query failed, a question that asks the user to choose, or a note's "
            "closest spelling, is part of the answer.") in rule


# --------------------------------------------------------------------------- #
# A lab near-miss note keeps the offer on any turn: the closest spelling is the answer's point
# --------------------------------------------------------------------------- #

def _near_miss_note():
    from chat_nextseek.helpers.lab_code import lab_near_miss_notes
    return lab_near_miss_notes([{"text": "Qwerty", "code": "QWZ", "name": "Qwertz"}])[0]


def test_the_near_miss_note_is_recognised_in_the_words_lab_code_writes():
    """Built with lab_code's own function, so a change to its wording fails here, not silently in a reply."""
    note = _near_miss_note()
    assert chatter_mod._has_lab_near_miss([note])
    assert chatter_mod._has_lab_near_miss(["What the result matched: 42 samples.", note])
    assert not chatter_mod._has_lab_near_miss(["What the result matched: 42 samples.", "What went wrong: x."])
    assert not chatter_mod._has_lab_near_miss(None) and not chatter_mod._has_lab_near_miss([])


ANSWERED_NEAR_MISS = ("There are 42 samples, not restricted to a lab: no lab is recorded as Qwerty. Would you like me "
                      "to search the Qwertz lab (QWZ) instead?")


def test_an_answered_turn_with_a_near_miss_note_keeps_its_offer(monkeypatch):
    body = _graph_turn(monkeypatch, ANSWERED_NEAR_MISS, {"ok": True, "count": 1, "total": 1, "data": [{"n": 42}]},
                       [_near_miss_note()])
    assert body == ANSWERED_NEAR_MISS and "Qwertz lab (QWZ)" in body


def test_the_same_answered_turn_without_the_note_drops_its_closer(monkeypatch):
    body = _graph_turn(monkeypatch, ANSWERED_NEAR_MISS, {"ok": True, "count": 1, "total": 1, "data": [{"n": 42}]})
    assert body == "There are 42 samples, not restricted to a lab: no lab is recorded as Qwerty."


def test_no_closing_offer_keeps_a_notes_closest_spelling_on_any_result():
    rule = _section("NO CLOSING OFFER", 1000)
    rule = rule[:rule.index("\n")]
    assert "A note's closest spelling is part of the answer whatever the result." in rule

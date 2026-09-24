"""The evaluation switch, parser side: `_force_parser_mode` (spec 4.6 and E2).

Evaluation only. The graph_search Nessie POC compares the graph agent with the API
agent on the same single-turn questions, so each arm forces the parser's retrieval
mode deterministically after the LLM call:

- graph: `new_search` becomes `graph_query` (target endpoint cleared, filters kept).
- api: `graph_query` becomes `new_search` on the parser's first REST endpoint
  candidate, else `advanced_search`.

Every retrieval-mode plan in a forced turn carries the note
`forced to <arm> by the evaluation switch (parser chose <mode>)`, so the harness
and the scorer can see that the switch landed and what the unforced product would
have run. Every other mode, and a turn with no switch, is returned as the same
object. The switch runs last in `_apply_parser_guardrails`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat_nextseek.agents import parser as parser_mod
from chat_nextseek.agents.parser import (
    ADVANCED_SEARCH_PATH,
    FORCE_MODES,
    FORCE_NOTE_MARKER,
    REFINE_WITHOUT_BUNDLE_NOTE,
    UID_LINEAGE_ROUTE_NOTE,
    _apply_parser_guardrails,
    _force_parser_mode,
)
from chat_nextseek.schemas import ParserFilters, ParserPlan
from chat_nextseek.schemas.router import EndpointCandidate

SAMPLE_TREE = "/nextseek_api/samples/sample-tree/"
RETRIEVE = "/nextseek_api/samples/retrieve/"
PLAIN_QUERY = "How many tissue samples are in the database?"
MULTI_UID_LINEAGE_QUERY = (
    "What sequencing data is associated with NHP-220524FLY-1-PUB and NHP-220524FLY-2-PUB?"
)
NON_RETRIEVAL_MODES = ("ask_about_last_results", "system_question", "reporter", "unsupported")


class _Session(dict):
    """Stand-in for SessionState: `.get` is all the refine guard uses."""


def _plan(mode="new_search", **over) -> ParserPlan:
    base = dict(
        mode=mode,
        target_endpoint=ADVANCED_SEARCH_PATH if mode == "new_search" else None,
        intent_summary="",
        filters=ParserFilters(sampletype_code="TIS", keywords=["lung"]),
    )
    base.update(over)
    return ParserPlan(**base)


# ------------------------------------------------------------------ constants


def test_the_constants_are_the_ones_the_harness_pins():
    assert FORCE_NOTE_MARKER == "by the evaluation switch"
    assert FORCE_MODES == ("graph", "api")
    assert ADVANCED_SEARCH_PATH == "/nextseek_api/samples/advanced_search/"


# ------------------------------------------------------------------ graph arm


def test_graph_turns_a_new_search_into_a_graph_query():
    plan = _plan("new_search")

    out = _force_parser_mode(plan, "graph")

    assert out.mode == "graph_query"
    assert out.target_endpoint is None
    assert out.filters == plan.filters
    assert out.filters.sampletype_code == "TIS"
    assert out.filters.keywords == ["lung"]


def test_graph_keeps_a_graph_query_and_still_records_the_switch():
    plan = _plan("graph_query")

    out = _force_parser_mode(plan, "graph")

    assert out.mode == "graph_query"
    assert FORCE_NOTE_MARKER in out.notes
    assert "parser chose graph_query" in out.notes


def test_graph_note_names_the_arm_and_the_parser_choice_exactly():
    out = _force_parser_mode(_plan("new_search"), "graph")
    assert out.notes == "forced to graph by the evaluation switch (parser chose new_search)"


def test_the_input_plan_is_not_mutated():
    plan = _plan("new_search")

    _force_parser_mode(plan, "graph")

    assert plan.mode == "new_search"
    assert plan.target_endpoint == ADVANCED_SEARCH_PATH
    assert plan.notes == ""


def test_an_existing_note_is_kept_ahead_of_the_force_note():
    out = _force_parser_mode(_plan("new_search", notes="parser rationale"), "graph")
    assert out.notes == (
        "parser rationale | forced to graph by the evaluation switch (parser chose new_search)"
    )


# ------------------------------------------------------------------ api arm


def test_api_turns_a_graph_query_into_a_new_search_on_the_first_rest_candidate():
    plan = _plan(
        "graph_query",
        endpoint_candidates=["graph", EndpointCandidate(endpoint=SAMPLE_TREE), RETRIEVE],
    )

    out = _force_parser_mode(plan, "api")

    assert out.mode == "new_search"
    assert out.target_endpoint == SAMPLE_TREE
    assert out.filters == plan.filters


def test_api_accepts_a_plain_string_rest_candidate():
    out = _force_parser_mode(_plan("graph_query", endpoint_candidates=[RETRIEVE]), "api")
    assert out.target_endpoint == RETRIEVE


def test_api_falls_back_to_advanced_search_without_a_rest_candidate():
    out = _force_parser_mode(_plan("graph_query", endpoint_candidates=["neo4j", ""]), "api")

    assert out.mode == "new_search"
    assert out.target_endpoint == ADVANCED_SEARCH_PATH


def test_api_falls_back_to_advanced_search_with_no_candidates_at_all():
    out = _force_parser_mode(_plan("graph_query"), "api")
    assert out.target_endpoint == ADVANCED_SEARCH_PATH


def test_api_keeps_a_new_search_and_its_endpoint_and_records_the_switch():
    plan = _plan("new_search", target_endpoint=SAMPLE_TREE)

    out = _force_parser_mode(plan, "api")

    assert out.mode == "new_search"
    assert out.target_endpoint == SAMPLE_TREE
    assert out.notes == "forced to api by the evaluation switch (parser chose new_search)"


def test_api_note_names_the_arm_and_the_parser_choice():
    out = _force_parser_mode(_plan("graph_query"), "api")
    assert out.notes == "forced to api by the evaluation switch (parser chose graph_query)"


def test_a_multi_uid_lineage_question_forced_to_graph_ends_on_rest_with_both_notes():
    """`_force_graph_for_uid_lineage` runs first; the api arm then wins, last."""
    out = _apply_parser_guardrails(MULTI_UID_LINEAGE_QUERY, _plan("new_search"), force_mode="api")

    assert out.mode == "new_search"
    assert out.target_endpoint == ADVANCED_SEARCH_PATH
    assert UID_LINEAGE_ROUTE_NOTE in out.notes
    assert FORCE_NOTE_MARKER in out.notes
    assert out.notes.index(UID_LINEAGE_ROUTE_NOTE) < out.notes.index(FORCE_NOTE_MARKER)
    # the lineage guard's UIDs survive the api force
    assert set(out.filters.uids) >= {"NHP-220524FLY-1-PUB", "NHP-220524FLY-2-PUB"}


# ------------------------------------------------------------------ left alone


@pytest.mark.parametrize("arm", FORCE_MODES)
@pytest.mark.parametrize("mode", NON_RETRIEVAL_MODES)
def test_non_retrieval_modes_are_the_same_object_in_both_arms(arm, mode):
    plan = _plan(mode, notes="kept")
    assert _force_parser_mode(plan, arm) is plan


@pytest.mark.parametrize("arm", FORCE_MODES)
@pytest.mark.parametrize("mode", NON_RETRIEVAL_MODES)
def test_non_retrieval_modes_pass_the_guardrail_entry_point_untouched(arm, mode):
    out = _apply_parser_guardrails(PLAIN_QUERY, _plan(mode, notes="kept"), force_mode=arm)

    assert out.mode == mode
    assert out.notes == "kept"
    assert FORCE_NOTE_MARKER not in out.notes


@pytest.mark.parametrize("mode", ["new_search", "graph_query", "reporter"])
def test_no_switch_returns_the_same_object(mode):
    plan = _plan(mode)
    assert _force_parser_mode(plan, None) is plan


@pytest.mark.parametrize("bogus", ["graph_legacy", "cypher", "", "GRAPH", "Api", 1])
def test_an_unknown_switch_value_returns_the_same_object(bogus):
    plan = _plan("new_search")
    assert _force_parser_mode(plan, bogus) is plan


# ------------------------------------------------------------------ the entry point


def test_the_guardrail_entry_point_without_the_switch_returns_the_plan_itself():
    plan = _plan("new_search", target_endpoint=SAMPLE_TREE)
    assert _apply_parser_guardrails(PLAIN_QUERY, plan) is plan
    assert _apply_parser_guardrails(PLAIN_QUERY, plan, force_mode=None) is plan


@pytest.mark.parametrize("arm, expected", [("graph", "graph_query"), ("api", "new_search")])
def test_the_guardrail_entry_point_applies_the_switch(arm, expected):
    out = _apply_parser_guardrails(PLAIN_QUERY, _plan("graph_query" if arm == "api" else "new_search"),
                                   force_mode=arm)
    assert out.mode == expected
    assert FORCE_NOTE_MARKER in out.notes


def test_the_switch_runs_after_the_refine_guard():
    """A refine on a fresh session is a new_search first, then forced."""
    out = _apply_parser_guardrails(
        "narrow those to males",
        _plan("refine_last_search", target_endpoint=SAMPLE_TREE),
        session=_Session(results_history=[]),
        force_mode="graph",
    )

    assert out.mode == "graph_query"
    assert REFINE_WITHOUT_BUNDLE_NOTE in out.notes
    assert "parser chose new_search" in out.notes


def test_the_bulk_export_guard_still_wins_over_the_switch():
    out = _apply_parser_guardrails("download all samples", _plan("new_search", filters=ParserFilters()),
                                   force_mode="graph")
    assert out.mode == "unsupported"
    assert FORCE_NOTE_MARKER not in out.notes


# ------------------------------------------------------------------ parser_agent wiring


def _fake_config(**over):
    base = dict(
        PARSER_SYSTEM_PROMPT="system",
        MIN_API_ENDPOINTS=[],
        MIN_GRAPH_SCHEMA={},
        get_agent_model=lambda name: (object(), "gemini-test", None),
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def _offline_parser(monkeypatch):
    """No model, no session store: the LLM always answers new_search on sample-tree (a REST endpoint the catalog keeps)."""
    calls = []

    def _fake_llm(**kwargs):
        calls.append(kwargs)
        return _plan("new_search", target_endpoint=SAMPLE_TREE)

    monkeypatch.setattr(parser_mod, "call_llm_structured", _fake_llm)
    monkeypatch.setattr(parser_mod, "build_recent_results_summary", lambda session: "")
    import chat_nextseek.chat_memory as chat_memory
    monkeypatch.setattr(chat_memory, "history_block", lambda session: "")
    return calls


def test_parser_agent_reads_the_switch_from_the_config(_offline_parser):
    out = parser_mod.parser_agent(_Session(), _fake_config(FORCE_PARSER_MODE="graph"), PLAIN_QUERY, {})

    assert _offline_parser, "the fake LLM was not called"
    assert out.mode == "graph_query"
    assert "forced to graph by the evaluation switch (parser chose new_search)" in out.notes


def test_parser_agent_without_the_switch_keeps_the_llm_choice(_offline_parser):
    out = parser_mod.parser_agent(_Session(), _fake_config(), PLAIN_QUERY, {})

    assert out.mode == "new_search"
    assert FORCE_NOTE_MARKER not in out.notes

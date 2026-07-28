"""
Regression lock for T1.9: UID-lineage questions must route to graph deterministically.

`repro.cypher_uid_dot` routed graph_query in the 2026-07-24 baseline (task 733,
returning exactly the correct six D.SEQ-220823SHA-1..6-PUB) and new_search in the
2026-07-27 rerun (task 797, wrong), with **no code change on that path**.

Three causes: the parser samples rather than decides (it runs
us.anthropic.claude-opus-4-7, which llm_clients.py treats as adaptive-thinking-only,
so temperature is never set); session context changed with harness isolation; and the
prompt contradicted itself — parser_core_routing.txt said do NOT use graph_query for
"lineage from a known UID" in one place and DO for "derivation chains, lineage,
ancestor/descendant" in another.

REST cannot answer it. A child record does not contain its parent's UID as text,
which is why task 797's correctly-formed first attempt returned total: 0. So the
guardrail forces graph, after the LLM call, so the model's choice is respected
everywhere else.
"""
from __future__ import annotations

import pytest

from chat_nextseek.agents.parser import _apply_parser_guardrails
from chat_nextseek.schemas import ParserFilters, ParserPlan

TASK_797_QUERY = (
    "What sequencing data is associated with NHP-220524FLY-1-PUB and NHP-220524FLY-2-PUB?"
)


def _plan(mode="new_search", **over) -> ParserPlan:
    base = dict(
        mode=mode,
        target_endpoint="/nextseek_api/samples/advanced_search/",
        intent_summary="",
        filters=ParserFilters(),
    )
    base.update(over)
    return ParserPlan(**base)


# ------------------------------------------------------------------ the repro


def test_task_797_is_forced_to_graph():
    """RED before the fix: the LLM said new_search and nothing corrected it."""
    out = _apply_parser_guardrails(TASK_797_QUERY, _plan("new_search"))

    assert out.mode == "graph_query"
    assert set(out.filters.uids) == {"NHP-220524FLY-1-PUB", "NHP-220524FLY-2-PUB"}


def test_the_forced_route_is_recorded_in_notes():
    out = _apply_parser_guardrails(TASK_797_QUERY, _plan("new_search"))
    assert "graph" in out.notes.lower()


def test_an_llm_choice_of_graph_is_left_alone():
    out = _apply_parser_guardrails(TASK_797_QUERY, _plan("graph_query"))
    assert out.mode == "graph_query"


@pytest.mark.parametrize(
    "query",
    [
        "What samples are derived from D.SEQ-220823SHA-1-PUB?",
        "Show me the children of TIS-240612KAM-1-PUB",
        "What is the lineage of MUS-200901ENG-23-PUB?",
        "Find the sequencing data for NHP-220524FLY-1-PUB",
        "What assays are associated with A.ADCD-250312ALT-1-PUB?",
        "Which samples came from these: NHP-220524FLY-1-PUB, NHP-220524FLY-2-PUB",
    ],
)
def test_uid_plus_relation_word_routes_to_graph(query):
    assert _apply_parser_guardrails(query, _plan("new_search")).mode == "graph_query"


# --------------------------------------------------------------- negative cases


@pytest.mark.parametrize(
    "query",
    [
        # A plain record lookup: REST answers this correctly and cheaply.
        "Get the full details for D.SEQ-221031SHA-67-PUB",
        "Show me D.SEQ-221031SHA-67-PUB",
        # No UID at all.
        "Find mice treated with NDMA",
        "What samples are derived from tissue?",
        # A relation word but no well-formed UID.
        "What is the lineage of the Impact study?",
    ],
)
def test_these_stay_on_the_llm_chosen_route(query):
    assert _apply_parser_guardrails(query, _plan("new_search")).mode == "new_search"


def test_a_malformed_uid_is_not_treated_as_one():
    assert _apply_parser_guardrails(
        "What is derived from NHP-22052-1?", _plan("new_search")
    ).mode == "new_search"


def test_the_bulk_export_guardrail_still_wins():
    """An unsupported bulk export must not be rerouted into a graph query."""
    out = _apply_parser_guardrails("download all samples", _plan("new_search"))
    assert out.mode == "unsupported"


def test_existing_uids_in_filters_are_preserved_and_merged():
    plan = _plan("new_search", filters=ParserFilters(uids=["TIS-240612KAM-1-PUB"]))

    out = _apply_parser_guardrails(TASK_797_QUERY, plan)

    assert "TIS-240612KAM-1-PUB" in out.filters.uids
    assert "NHP-220524FLY-1-PUB" in out.filters.uids


def test_other_filters_survive_the_reroute():
    plan = _plan("new_search", filters=ParserFilters(sampletype_code="D.SEQ",
                                                     lab_codes=["FLY"]))

    out = _apply_parser_guardrails(TASK_797_QUERY, plan)

    assert out.filters.sampletype_code == "D.SEQ"
    assert out.filters.lab_codes == ["FLY"]

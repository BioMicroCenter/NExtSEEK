"""A refine is not locked to the engine the previous turn used (F13).

The operator's most explicit routing ask: "REFINE_LAST SEARCH should be able to reroute to
GRAPH QUERY and RERUN GRAPH QUERIES". The orchestrator chose the engine from the PREVIOUS
bundle's mode alone, so a REST search could never be refined into the graph however clearly
the new turn needed it. Five production cases sit on this: swapping one attribute value for
another after a REST search answered the wrong question or none at all.

Two halves, and the second is the one that bites: the parser can now mark a refine as
graph-bound, and a re-routed refine has to CARRY the previous turn's filters. Without them it
loses the scope the user set in the turn before and silently widens the question -- which
would be a worse failure than the one being fixed.
"""
from __future__ import annotations

from pathlib import Path

from chat_nextseek.orchestrator import _build_graph_refine_context
from chat_nextseek.schemas.router import ParserCandidate, ParserPlan

PROMPTS = Path(__file__).resolve().parents[2] / "chat_nextseek" / "src" / "chat_nextseek" / "prompts"


# --------------------------------------------------------------------------- the mark


def test_the_plan_can_mark_a_refine_graph_bound():
    assert ParserPlan(mode="refine_last_search").refine_engine is None, "absent keeps the old behaviour"
    assert ParserPlan(mode="refine_last_search", refine_engine="graph").refine_engine == "graph"


def test_a_multi_parser_candidate_carries_the_same_mark():
    assert ParserCandidate(mode="refine_last_search", refine_engine="graph").refine_engine == "graph"


def test_both_wrappers_can_emit_it():
    for name in ("parser_agent.txt", "multi_parser_agent.txt"):
        assert '"refine_engine"' in (PROMPTS / name).read_text(encoding="utf-8"), name


def test_the_routing_core_says_a_refine_is_not_locked_to_its_engine():
    core = " ".join((PROMPTS / "parser_core_routing.txt").read_text(encoding="utf-8").split())
    assert "A refine is NOT locked to the engine the previous turn used" in core
    assert 'set "refine_engine": "graph"' in core
    assert "Leave refine_engine out to stay on the previous turn's engine" in core


# --------------------------------------------------------------------------- the context


def test_a_graph_prior_still_carries_its_cypher():
    text = _build_graph_refine_context({
        "user_query": "how many mice",
        "graph_plan": {"cypher": "MATCH (s:T_MUS) RETURN count(*) AS n"},
    })
    assert "Prior Cypher" in text
    assert "MATCH (s:T_MUS)" in text


def test_a_rest_prior_carries_the_filters_it_resolved():
    """This is the half that prevents a silent widening."""
    text = _build_graph_refine_context({
        "user_query": "tissue samples stored in RNAlater",
        "parser_plan": {"filters": {"sampletype_code": "TIS", "keywords": ["RNAlater"],
                                    "assay_codes": [], "uids": []}},
        "api_plan": {"requestBody": {"sampletype": "TIS", "filter_searchText": "RNAlater",
                                     "filter_matchType": "PARTIAL"}},
    })

    assert "moving to the graph" in text
    assert "TIS" in text and "RNAlater" in text
    assert "Keep every constraint above that the user has not changed in this turn." in text
    assert "Prior Cypher" not in text, "there is no Cypher on a REST prior to quote"


def test_empty_filters_are_not_offered_as_constraints():
    text = _build_graph_refine_context({
        "user_query": "everything",
        "parser_plan": {"filters": {"sampletype_code": None, "keywords": [], "uids": []}},
        "api_plan": {"requestBody": {"filter_searchText": ""}},
    })
    assert "[none]" in text


def test_a_bundle_with_nothing_in_it_still_renders():
    text = _build_graph_refine_context({})
    assert "[none]" in text
    assert text.strip()

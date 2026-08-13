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
        "What samples are derived from D.SEQ-220823SHA-1-PUB and D.SEQ-220823SHA-2-PUB?",
        "Show me the children of TIS-240612KAM-1-PUB and TIS-240612KAM-2-PUB",
        "Which samples came from these: NHP-220524FLY-1-PUB, NHP-220524FLY-2-PUB",
        "What assays are associated with A.ADCD-250312ALT-1-PUB / A.ADCD-250312ALT-2-PUB?",
    ],
)
def test_multiple_uids_plus_a_relation_word_route_to_graph(query):
    assert _apply_parser_guardrails(query, _plan("new_search")).mode == "graph_query"


# --------------------------------------------------------------- negative cases
#
# A SINGLE-UID lineage question is served by REST and must stay there: sample-tree
# returns the whole bidirectional tree around one UID, `retrieve` returns everything
# associated with one UID, and the reporter builds a submission file for one UID.
#
# The corpus has 20 such turns across the search_tree, retrieve, reporting and
# pipeline_nfcore families, every one asserting a REST endpoint. An earlier version of
# this guardrail fired on all of them; these cases pin the narrowing.


@pytest.mark.parametrize(
    "query",
    [
        # search_tree — sample-tree is GET-per-UID and answers these directly.
        "Show me all samples derived from CEL-250319WHI-1-PUB.",
        "What's the lineage of D.MSP-230828GRI-4-PUB?",
        "Show me children of XXX-999999ZZZ-1-PUB.",
        "What is D.MSP-230828GRI-4-PUB derived from?",
        "Show me tissue samples derived from NHP-220630FLY-1-PUB",
        # retrieve — a dedicated endpoint for "everything associated with one UID".
        "Retrieve all samples associated with NHP-220630FLY-5-PUB",
        "Can you return to me all samples associated with CEL-250319WHI-1-PUB",
        # reporting / pipeline — a single UID feeding a report or samplesheet.
        "Build an SRA metadata file for D.SEQ-230512FOR-29-PUB",
        "What has been derived from NHP-220630FLY-5-PUB",
        "Make me an nfcore samplesheet for the sequencing samples associated with "
        "NHP-220630FLY-1-PUB",
        # A plain record lookup, even with two UIDs.
        "Get the full details for D.SEQ-221031SHA-67-PUB and D.SEQ-221031SHA-65-PUB",
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
        "What is derived from NHP-22052-1 and NHP-22053-2?", _plan("new_search")
    ).mode == "new_search"


def test_the_same_uid_repeated_is_still_one_uid():
    """Deduped, so a restatement does not trip the multi-UID floor."""
    assert _apply_parser_guardrails(
        "What is derived from NHP-220630FLY-5-PUB? I mean NHP-220630FLY-5-PUB.",
        _plan("new_search"),
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

"""P6a and P6b of the Pilot A POC review: the two query shapes the graph agent must not run.

Evidence: ``PilotAPOC/review/FINDINGS.md``, ``PROPOSALS.md`` and ``compare_export.json`` — the
60-question forced A/B on run ``full-a``. ``fixtures/poc_full_a_queries.json`` is that export
trimmed to the fields these tests read: the agent's Cypher, the answer key's oracle, the
resolved keywords, and what the run measured. Every CYPHER constant below is verbatim from it,
not hand-written.

**P6a — an unbounded or unanchored variable-length path.** Two of the 56 executed queries used a
variable-length path at all, and both wrote ``*0..``:

- ``entity.find_pbmcs_that_were_sequenced_u`` ran **173.4 s** and was killed: the run's only
  timeout, and the reply told the user the database had timed out.
- ``advanced.show_me_all_facs_data_for_the`` returned 3,688 where the key says 3,728.

Both answer-key oracles for those two questions bound the hops — ``*1..6`` and ``*1..8`` — which
is why the refusal names a maximum instead of asking for a different question.

**P6b — an unscoped fulltext call whose term is not a single word.** The index analyser
tokenises, so an unquoted multi-token term is an OR over its tokens and the result is the union:

- ``search.chipseq_trap``: ``ChIP-seq`` -> ``chip OR seq`` -> **138,313** of 1,084,754 samples,
  reported as "Yes, there are 138,313 matching records" to a question whose true answer is none.
  The worst single reply in the run.
- ``advanced.find_me_samples_associated_wit``: ``CD8 depletion`` -> 320, where the key is 151
  (the oracle is ``toLower(s.search_text) CONTAINS 'cd8 depletion'``, which keeps adjacency).

The same index, unscoped, on a single clean word is exact and cheap and must still be allowed:
``harmon.immport_repository`` answered ``ImmPort`` with 4,081, which is the key. That is the
whole reason the rule is "unscoped **and** multi-token" rather than "unscoped".

Both guards are refusals, so the failure mode of getting them wrong is refusing a legitimate
query: the fixture replay below is the check that costs nothing, and it pins both directions —
exactly four of the 60 agent queries refused, and not one of the 58 answer-key oracles.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chat_nextseek import graph_catalog
from chat_nextseek.agents.graph import (
    MAX_DERIVATION_HOPS,
    _shape_refusal,
    _shape_repair_message,
    graph_agent,
    query_shape_problems,
    refused_query_shapes,
)
from chat_nextseek.schemas import GraphAgentPlan

FIXTURE = Path(__file__).with_name("fixtures") / "poc_full_a_queries.json"
ROWS = json.loads(FIXTURE.read_text(encoding="utf-8"))
BY_ID = {row["id"]: row for row in ROWS}


def kinds(cypher, parameters=None) -> list[str]:
    return [problem.kind for problem in query_shape_problems(cypher, parameters)]


def row_parameters(row: dict) -> dict:
    """Every ``$param`` in the row's Cypher bound to the run's resolved keywords.

    The export carries ``entities.keywords`` but not the bound parameter map, so the term is
    reconstructed. FINDINGS.md corroborates both reconstructions that matter: "ChIP-seq ...
    Lucene split it to `chip OR seq`" and "`cd8 depletion` -> index, unquoted, so adjacency is
    lost -> 320".
    """
    term = " ".join(row["keywords"])
    return {name: term for name in re.findall(r"\$([A-Za-z_]\w*)", row["cypher"] or "")}


# --------------------------------------------------------------- P6a, the real shapes

# entity.find_pbmcs_that_were_sequenced_u — 173.409 s, killed. The run's only timeout.
CYPHER_PBMC = BY_ID["entity.find_pbmcs_that_were_sequenced_u"]["cypher"]
ORACLE_PBMC = BY_ID["entity.find_pbmcs_that_were_sequenced_u"]["oracle"]

# advanced.show_me_all_facs_data_for_the — 3,688 returned, 3,728 true.
CYPHER_FACS = BY_ID["advanced.show_me_all_facs_data_for_the"]["cypher"]
ORACLE_FACS = BY_ID["advanced.show_me_all_facs_data_for_the"]["oracle"]

# --------------------------------------------------------------- P6b, the real shapes

CYPHER_CHIPSEQ = BY_ID["search.chipseq_trap"]["cypher"]
CYPHER_CD8 = BY_ID["advanced.find_me_samples_associated_wit"]["cypher"]
CYPHER_IMMPORT = BY_ID["harmon.immport_repository"]["cypher"]

# The four scoping forms the run's other fulltext calls used, all of which must pass.
CYPHER_LABEL_PREDICATE = BY_ID["advanced.find_me_extravasation_images"]["cypher"]  # WHERE node:T_D_IMG
CYPHER_EQUALITY_JOIN = BY_ID["advanced.find_me_all_fibrin_images_on_o"]["cypher"]  # MATCH (s:T_D_IMG) WHERE s = node
CYPHER_MAP_JOIN = BY_ID["advanced.find_me_images_associated_with"]["cypher"]  # (s:T_D_IMG {uuid: node.uuid})
CYPHER_YIELD_ALIAS = BY_ID["advanced.find_me_all_samples_associated"]["cypher"]  # YIELD node AS s ... WHERE s:T_AB


def test_the_fixture_is_the_run_this_guard_was_derived_from():
    """The two numbers the two guards exist to remove, read off the export."""
    assert len(ROWS) == 60
    assert BY_ID["entity.find_pbmcs_that_were_sequenced_u"]["elapsed_s"] == pytest.approx(173.409)
    assert BY_ID["search.chipseq_trap"]["graph_total"] == 138313
    assert BY_ID["search.chipseq_trap"]["expected_kind"] == "none"
    assert BY_ID["harmon.immport_repository"]["expected_value"] == 4081


# ------------------------------------------------------------------- P6a: bounds

def test_the_only_timeout_in_the_run_is_refused_as_unbounded():
    assert "unbounded_path" in kinds(CYPHER_PBMC, {"q": "PBMC"})


def test_the_other_variable_length_query_in_the_run_is_refused_too():
    assert "unbounded_path" in kinds(CYPHER_FACS)


def test_both_answer_key_oracles_for_those_questions_pass():
    """`*1..6` and `*1..8`: the shape the refusal asks for is the shape the key uses."""
    assert query_shape_problems(ORACLE_PBMC) == []
    assert query_shape_problems(ORACLE_FACS) == []


@pytest.mark.parametrize("hops", ["*", "*..", "*0..", "*1..", "*2..", "* 1 .."])
def test_every_form_with_no_upper_bound_is_refused(hops):
    cypher = f"MATCH (d:T_D_SEQ)-[:DERIVED_FROM{hops}]->(p:T_NHP) RETURN count(DISTINCT d) AS n"
    assert kinds(cypher) == ["unbounded_path"]


@pytest.mark.parametrize("hops", ["*1..8", "*0..8", "*..8", "*3", "*1..2", "* 1 .. 8"])
def test_every_form_with_an_upper_bound_passes(hops):
    cypher = f"MATCH (d:T_D_SEQ)-[:DERIVED_FROM{hops}]->(p:T_NHP) RETURN count(DISTINCT d) AS n"
    assert query_shape_problems(cypher) == []


def test_a_single_hop_is_not_a_variable_length_path():
    cypher = ("MATCH (c:Sample)-[r:DERIVED_FROM]->(p:T_NHP) WHERE r.internal_assay_title = $assay "
              "RETURN count(DISTINCT c) AS n")
    assert query_shape_problems(cypher) == []


def test_a_star_inside_a_list_comprehension_is_not_a_relationship():
    """`[x IN xs | x * 2]` is a list, not `-[...]-`. The guard must not read it as a path."""
    cypher = "WITH [1, 2, 3] AS xs RETURN [x IN xs | x * 2] AS doubled"
    assert query_shape_problems(cypher) == []


def test_an_empty_or_missing_cypher_is_not_a_problem():
    assert query_shape_problems("") == []
    assert query_shape_problems(None) == []
    assert query_shape_problems("   ") == []


# ------------------------------------------------------------------- P6a: anchors

def test_a_bounded_path_between_two_bare_sample_ends_is_refused_as_unanchored():
    cypher = "MATCH (a:Sample)-[:DERIVED_FROM*1..8]->(b:Sample) RETURN count(*) AS n"
    assert kinds(cypher) == ["unanchored_path"]


def test_a_type_label_on_either_end_anchors_the_path():
    left = "MATCH (d:T_D_SEQ)-[:DERIVED_FROM*1..8]->(p:Sample) RETURN count(*) AS n"
    right = "MATCH (d:Sample)-[:DERIVED_FROM*1..8]->(p:T_NHP) RETURN count(*) AS n"
    assert query_shape_problems(left) == []
    assert query_shape_problems(right) == []


def test_a_where_predicate_on_either_end_anchors_the_path():
    cypher = ("MATCH (a:Sample)-[:DERIVED_FROM*1..8]->(b:Sample) WHERE toLower(a.Organ) = 'lung' "
              "RETURN count(*) AS n")
    assert query_shape_problems(cypher) == []


def test_a_projection_alone_does_not_anchor_the_path():
    """`RETURN a.uuid` filters nothing; only a predicate, a label or an earlier binding does."""
    cypher = "MATCH (a:Sample)-[:DERIVED_FROM*1..8]->(b:Sample) RETURN a.uuid AS uuid LIMIT 10"
    assert kinds(cypher) == ["unanchored_path"]


def test_an_inline_property_map_anchors_the_path():
    cypher = "MATCH (s:Sample {uuid: $uid})-[:DERIVED_FROM*1..8]->(p:Sample) RETURN p.uuid AS uuid"
    assert query_shape_problems(cypher) == []


def test_a_variable_an_earlier_clause_bound_anchors_the_path():
    """The FACS shape with the hops bounded: `parent` comes from the clause above."""
    cypher = ("MATCH (df:T_D_FLOW)-[r:DERIVED_FROM]->(parent:Sample)\n"
              "WHERE r.internal_assay_title = $assay\n"
              "MATCH (parent)-[:DERIVED_FROM*0..8]->(nhp:T_NHP)\n"
              "RETURN count(DISTINCT df) AS n")
    assert query_shape_problems(cypher) == []


def test_a_fulltext_hit_anchors_the_path():
    cypher = ("CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node AS parent\n"
              "MATCH (child:Sample)-[:DERIVED_FROM*1..8]->(parent)\n"
              "RETURN count(DISTINCT child) AS n")
    assert query_shape_problems(cypher, {"q": "PBMC"}) == []


def test_an_unbounded_and_unanchored_path_is_reported_once_per_reason():
    cypher = "MATCH (a:Sample)-[:DERIVED_FROM*]->(b:Sample) RETURN count(*) AS n"
    assert sorted(kinds(cypher)) == ["unanchored_path", "unbounded_path"]


# --------------------------------------------------------------- P6b: fulltext scope

def test_the_worst_reply_in_the_run_is_refused():
    assert kinds(CYPHER_CHIPSEQ, {"query": "ChIP-seq"}) == ["unscoped_fulltext"]


def test_the_unscoped_two_word_term_that_lost_adjacency_is_refused():
    assert kinds(CYPHER_CD8, {"q": "CD8 depletion"}) == ["unscoped_fulltext"]


def test_the_unscoped_single_word_call_that_was_exactly_right_passes():
    """ImmPort -> 4,081, the key. One token, so the analyser unions nothing."""
    assert query_shape_problems(CYPHER_IMMPORT, {"query": "ImmPort"}) == []


@pytest.mark.parametrize("cypher", [
    CYPHER_LABEL_PREDICATE,
    CYPHER_EQUALITY_JOIN,
    CYPHER_MAP_JOIN,
    CYPHER_YIELD_ALIAS,
])
def test_a_type_scoped_fulltext_call_passes_whatever_the_term(cypher):
    assert query_shape_problems(cypher, {"q": "fibrin omero", "query": "fibrin omero"}) == []


def test_a_type_predicate_scopes_a_fulltext_call():
    cypher = ("CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node, score "
              "WHERE node.type IN $types RETURN count(DISTINCT node) AS n")
    assert query_shape_problems(cypher, {"q": "ChIP-seq", "types": ["D.SEQ"]}) == []


def test_a_quoted_phrase_term_passes_because_the_index_keeps_adjacency():
    cypher = ("CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node "
              "RETURN count(DISTINCT node) AS n")
    assert query_shape_problems(cypher, {"q": '"chip-seq"'}) == []


@pytest.mark.parametrize("term", ["chip AND seq", "+chip +seq", "chip && seq"])
def test_an_explicitly_conjunctive_term_passes(term):
    cypher = ("CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node "
              "RETURN count(DISTINCT node) AS n")
    assert query_shape_problems(cypher, {"q": term}) == []


def test_an_inline_multi_token_literal_is_refused_without_any_parameters():
    cypher = ("CALL db.index.fulltext.queryNodes('sample_search_text', 'ChIP-seq') YIELD node "
              "RETURN count(DISTINCT node) AS n")
    assert kinds(cypher) == ["unscoped_fulltext"]


def test_an_unresolvable_term_fails_open():
    """No parameter map, so the term is unknown. A guard that cannot see the term must not
    refuse on suspicion: over-refusal is this guard's own failure mode."""
    cypher = ("CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node "
              "RETURN count(DISTINCT node) AS n")
    assert query_shape_problems(cypher, None) == []
    assert query_shape_problems(cypher, {"other": "ChIP-seq"}) == []


def test_a_query_with_no_fulltext_call_is_not_a_fulltext_problem():
    cypher = ("MATCH (s:Sample) WHERE toLower(s.search_text) CONTAINS 'chip-seq' "
              "RETURN count(s) AS n")
    assert query_shape_problems(cypher, {"q": "ChIP-seq"}) == []


def test_the_oracle_shape_for_the_chipseq_question_passes():
    """Four CONTAINS branches over search_text: the shape the refusal points at."""
    assert query_shape_problems(BY_ID["search.chipseq_trap"]["oracle"]) == []


# ------------------------------------------------------------ the 60-row fixture replay

# The full set, and nothing else. Two unbounded paths (P6a) and two unscoped multi-token
# fulltext calls (P6b).
EXPECTED_REFUSALS = {
    "entity.find_pbmcs_that_were_sequenced_u",
    "advanced.show_me_all_facs_data_for_the",
    "search.chipseq_trap",
    "advanced.find_me_samples_associated_wit",
}


def test_the_guard_refuses_exactly_four_of_the_sixty_agent_queries():
    refused = {row["id"] for row in ROWS
               if row["cypher"] and query_shape_problems(row["cypher"], row_parameters(row))}
    assert refused == EXPECTED_REFUSALS


def test_not_one_answer_key_oracle_is_refused():
    """The strongest free check: every oracle is a query the reviewers ran and trusted."""
    refused = {row["id"]: refused_query_shapes(row["oracle"])
               for row in ROWS if row["oracle"] and refused_query_shapes(row["oracle"])}
    assert refused == {}


def test_every_refusal_string_names_the_offending_fragment():
    for row_id in sorted(EXPECTED_REFUSALS):
        row = BY_ID[row_id]
        strings = refused_query_shapes(row["cypher"], row_parameters(row))
        assert strings, row_id
        for text in strings:
            assert "*" in text or "queryNodes" in text, (row_id, text)


# ----------------------------------------------------------------------- the messages

def test_the_repair_message_for_an_unbounded_path_names_a_bound_to_use():
    message = _shape_repair_message(query_shape_problems(CYPHER_PBMC, {"q": "PBMC"}))
    assert f"*1..{MAX_DERIVATION_HOPS}" in message
    assert "173" in message  # what it cost, so the agent does not argue with the rule
    assert "[:DERIVED_FROM*0..]" in message


def test_the_repair_message_for_an_unanchored_path_names_the_three_anchors():
    cypher = "MATCH (a:Sample)-[:DERIVED_FROM*1..8]->(b:Sample) RETURN count(*) AS n"
    message = _shape_repair_message(query_shape_problems(cypher))
    assert "T_" in message
    assert "predicate" in message
    assert "earlier" in message


def test_the_repair_message_for_an_unscoped_fulltext_names_both_alternatives():
    message = _shape_repair_message(query_shape_problems(CYPHER_CHIPSEQ, {"query": "ChIP-seq"}))
    assert "search_text" in message and "CONTAINS" in message  # branch 1: the phrase
    assert "YIELD node" in message and "T_" in message  # branch 2: scope the index
    assert "chip" in message and "seq" in message  # the tokens it will actually search
    assert "138,313" in message


def test_the_repair_message_ends_by_asking_for_a_regenerated_query():
    message = _shape_repair_message(query_shape_problems(CYPHER_CHIPSEQ, {"query": "ChIP-seq"}))
    assert "Regenerate the Cypher" in message


def test_the_user_facing_refusal_says_what_was_wrong_and_what_would_work():
    refusal = _shape_refusal(query_shape_problems(CYPHER_CHIPSEQ, {"query": "ChIP-seq"}))
    assert refusal.startswith("Graph agent could not produce valid Cypher")
    assert "queryNodes" in refusal
    refusal_path = _shape_refusal(query_shape_problems(CYPHER_PBMC, {"q": "PBMC"}))
    assert "*" in refusal_path


# ----------------------------------------------------------------- graph_agent wiring


@pytest.fixture(autouse=True)
def _catalog_unavailable(monkeypatch):
    """These tests pin the fallback path: the committed JSON and the type-blind property guard."""
    def unavailable(*args, **kwargs):
        raise graph_catalog.CatalogUnavailable("no graph in this test")

    monkeypatch.setattr(graph_catalog, "get_snapshot", unavailable)


SCHEMA = {"node_properties": {
    "Sample": ["uuid", "id", "type", "Organ", "search_text"],
    "Study": ["title"],
}}


def _config():
    c = MagicMock()
    c.NEO4J_SCHEMA = SCHEMA
    c.GRAPH_AGENT_SYSTEM_PROMPT = "system prompt"
    c.PROTOCOL_SCHEMA = None
    c.ASSAY_SAMPLE_CONNECTIONS = None
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


UNBOUNDED_CYPHER = "MATCH (a:Sample)-[:DERIVED_FROM*1..]->(b:T_NHP) RETURN count(DISTINCT a) AS n"
BOUNDED_CYPHER = "MATCH (a:Sample)-[:DERIVED_FROM*1..8]->(b:T_NHP) RETURN count(DISTINCT a) AS n"


def unbounded() -> GraphAgentPlan:
    """A fresh plan per call: graph_agent mutates the plan it is handed, so a shared instance
    leaks one test's guard note into the next."""
    return GraphAgentPlan(cypher=UNBOUNDED_CYPHER, explanation="unbounded", parameters={})


def bounded() -> GraphAgentPlan:
    return GraphAgentPlan(cypher=BOUNDED_CYPHER, explanation="bounded", parameters={})


def test_graph_agent_re_prompts_once_and_returns_the_bounded_query():
    with patch("chat_nextseek.agents.graph.call_llm_structured",
               side_effect=[unbounded(), bounded()]) as call:
        out = graph_agent(_config(), user_query="what NHP samples were sequenced",
                          entity_result={})
    assert out.cypher == BOUNDED_CYPHER
    assert call.call_count == 2
    sent = call.call_args_list[-1].kwargs["messages"][-1]["content"]
    assert f"*1..{MAX_DERIVATION_HOPS}" in sent


def test_graph_agent_returns_an_empty_plan_when_the_repair_keeps_the_shape():
    with patch("chat_nextseek.agents.graph.call_llm_structured",
               side_effect=[unbounded(), unbounded()]):
        out = graph_agent(_config(), user_query="what NHP samples were sequenced",
                          entity_result={})
    assert out.cypher == ""
    assert "Graph agent could not produce valid Cypher" in out.explanation
    assert "*1.." in out.explanation


def test_graph_agent_refuses_the_unscoped_fulltext_call_the_run_shipped():
    bad = GraphAgentPlan(cypher=CYPHER_CHIPSEQ, explanation="fulltext",
                         parameters={"query": "ChIP-seq"})
    with patch("chat_nextseek.agents.graph.call_llm_structured", return_value=bad):
        out = graph_agent(_config(), user_query="Do we have any ChIP-seq datasets?",
                          entity_result={})
    assert out.cypher == ""
    assert "queryNodes" in out.explanation


def test_graph_agent_leaves_a_well_shaped_query_alone():
    with patch("chat_nextseek.agents.graph.call_llm_structured", return_value=bounded()) as call:
        out = graph_agent(_config(), user_query="what NHP samples were sequenced",
                          entity_result={})
    assert out.cypher == BOUNDED_CYPHER
    assert call.call_count == 1
    assert "shape guard" not in out.explanation


def test_graph_agent_records_the_shape_guard_in_the_explanation_after_a_repair():
    with patch("chat_nextseek.agents.graph.call_llm_structured",
               side_effect=[unbounded(), bounded()]):
        out = graph_agent(_config(), user_query="what NHP samples were sequenced",
                          entity_result={})
    assert "shape guard" in out.explanation

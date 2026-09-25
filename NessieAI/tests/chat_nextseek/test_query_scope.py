"""What the reply is allowed to know about the query that ran.

D1, decided from the code and from the 84 production turns: the chatter gets a
structured description of what the executed query CONSTRAINED, and of what the user
asked for that it did not, and nothing about how the query was expressed.

Three production failures are the argument for the first half.

* B7 (wesselr 462) — "list all the human patient samples (PAT) associated with
  MDL-250912LAU-1". The request fetched the model's whole lineage and never filtered
  to PAT. The reply reported 1,904 matching records and showed none that were PAT.
* B8 (wesselr 437) — the query searched `D.FLOW` and `D.CYTOF`; the reply named
  `D.FCS`. The report's fix is "name types in the reply from the query that ran".
* B13 (mplaster 501/502) — the Cypher counted every mouse with transcriptomic
  descendants and its own plan note said the `CC` keyword filter could not be
  applied in the graph. The reply still presented the 731 as a subset of the CC
  mice.

In all three the gap was computable from arguments `chatter_agent_answer` already
receives, and was discarded at the prompt boundary.

The argument for the second half is the comment at `chatter.py` that says the LLM
never sees endpoint names, Cypher, requestBody or filter operators. That still holds:
`test_nothing_in_the_rendered_block_leaks_mechanics` is the guard.
"""
from __future__ import annotations

import pytest

from chat_nextseek.helpers.query_scope import describe_query_scope, render_query_scope
from chat_nextseek.schemas.entity import EntityAgentOutput, EntityItem, LabMatch
from chat_nextseek.schemas.router import ParserPlan


def _entity(**kw):
    return EntityAgentOutput(**kw).model_dump()


def _plan(**kw):
    return ParserPlan(**kw).model_dump()


# --------------------------------------------------------------------------
# B13: the filter the query could not apply.
# --------------------------------------------------------------------------

_B13_CYPHER = (
    "MATCH (s:Sample {SampleType: 'MUS'})<-[:DERIVED_FROM*1..6]-(d:Sample) "
    "WHERE d.Assay IN ['A.GEX', 'A.SCXP', 'A.SPTX'] RETURN count(DISTINCT s) AS n"
)


def test_a_filter_the_cypher_never_carried_is_reported_as_not_applied():
    scope = describe_query_scope(
        entity_result=_entity(
            sampletypes=[EntityItem(code="MUS", name="Mouse")], keywords=["CC"],
        ),
        parser_plan=_plan(mode="graph_query", intent_summary="count CC mice with transcriptomics"),
        graph_plan={"cypher": _B13_CYPHER, "explanation": "the 'CC' keyword filter cannot be applied in the graph"},
    )

    assert scope.measurable is True
    assert any("CC" in item for item in scope.not_applied)
    assert any("MUS" in item for item in scope.applied)


def test_the_query_authors_own_caveat_is_carried_as_a_note():
    scope = describe_query_scope(
        entity_result=_entity(keywords=["CC"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": _B13_CYPHER,
                    "explanation": "the 'CC' keyword filter cannot be applied in the graph"},
    )

    assert any("cannot be applied" in note for note in scope.notes)


# --------------------------------------------------------------------------
# B7: the type the lineage request dropped.
# --------------------------------------------------------------------------

def test_a_lineage_request_that_dropped_the_requested_type_says_so():
    scope = describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code="PAT", name="Human Patient")]),
        parser_plan=_plan(
            mode="new_search",
            target_endpoint="/nextseek_api/admin/samples/retrieve/",
            filters={"sampletype_code": "PAT", "uids": ["MDL-250912LAU-1"]},
        ),
        api_plan={
            "endpoint": "/nextseek_api/admin/samples/retrieve/",
            "method": "POST",
            "requestBody": {"identifiers": ["MDL-250912LAU-1"], "include_tree": True},
            "queryParameters": {},
        },
    )

    assert any("PAT" in item for item in scope.not_applied)
    assert any("MDL-250912LAU-1" in item for item in scope.applied)


def test_a_constraint_the_request_body_really_carries_is_applied():
    scope = describe_query_scope(
        entity_result=_entity(keywords=["NDMA"], sampletypes=[EntityItem(code="MUS", name="Mouse")]),
        parser_plan=_plan(
            mode="new_search",
            target_endpoint="/nextseek_api/samples/advanced_search/",
            filters={"sampletype_code": "MUS", "keywords": ["NDMA"]},
        ),
        api_plan={
            "endpoint": "/nextseek_api/samples/advanced_search/",
            "method": "POST",
            "requestBody": {"filter_sampletype": "MUS", "filter_searchText": "NDMA"},
            "queryParameters": {},
        },
    )

    assert scope.not_applied == []
    assert any("NDMA" in item for item in scope.applied)
    assert any("MUS" in item for item in scope.applied)


# --------------------------------------------------------------------------
# A gap that cannot be measured must not be invented.
# --------------------------------------------------------------------------

def test_a_reporter_turn_claims_no_gap_because_it_has_no_executed_query():
    scope = describe_query_scope(
        entity_result=_entity(projects=["MetNet"], keywords=["mice"]),
        parser_plan=_plan(mode="reporter", report_mode="summary"),
    )

    assert scope.measurable is False
    assert scope.not_applied == []
    assert scope.applied == []


def test_a_short_value_is_not_matched_inside_a_longer_word():
    """"CC" must not read as applied because the Cypher says ACCESSION."""
    scope = describe_query_scope(
        entity_result=_entity(keywords=["CC"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE s.ACCESSION IS NOT NULL RETURN s", "explanation": ""},
    )

    assert any("CC" in item for item in scope.not_applied)
    assert scope.applied == []


def test_a_value_bounded_by_punctuation_still_matches():
    """`PAT` inside `PAT-250912LAU-1` is the same scope, so it counts as applied."""
    scope = describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code="PAT", name="Human Patient")]),
        parser_plan=_plan(mode="graph_query", filters={"sampletype_code": "PAT"}),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE s.uuid STARTS WITH 'PAT-' RETURN s", "explanation": ""},
    )

    assert any("PAT" in item for item in scope.applied)
    assert scope.not_applied == []


# --------------------------------------------------------------------------
# What "Searched" is allowed to say.
# --------------------------------------------------------------------------

def test_the_search_kind_is_named_in_user_facing_words():
    graph = describe_query_scope(
        entity_result=_entity(), parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s) RETURN s", "explanation": ""},
    )
    keyword = describe_query_scope(
        entity_result=_entity(),
        parser_plan=_plan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/"),
        api_plan={"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST", "requestBody": {}},
    )
    lineage = describe_query_scope(
        entity_result=_entity(),
        parser_plan=_plan(mode="new_search", target_endpoint="/nextseek_api/admin/samples/retrieve/"),
        api_plan={"endpoint": "/nextseek_api/admin/samples/retrieve/", "method": "POST", "requestBody": {}},
    )

    assert "graph" in graph.searched.lower()
    assert "keyword" in keyword.searched.lower()
    assert "derived" in lineage.searched.lower()
    for scope in (graph, keyword, lineage):
        assert "/nextseek_api/" not in scope.searched
        assert "POST" not in scope.searched


def test_an_unknown_endpoint_falls_back_to_a_generic_phrase():
    scope = describe_query_scope(
        entity_result=_entity(),
        parser_plan=_plan(mode="new_search", target_endpoint="/nextseek_api/something/new/"),
        api_plan={"endpoint": "/nextseek_api/something/new/", "method": "GET", "requestBody": {}},
    )

    assert "/nextseek_api/" not in scope.searched
    assert scope.searched.strip() != ""


# --------------------------------------------------------------------------
# The guard on the half of D1 that stays withheld.
# --------------------------------------------------------------------------

def test_nothing_in_the_rendered_block_leaks_mechanics():
    scope = describe_query_scope(
        entity_result=_entity(keywords=["NDMA"], sampletypes=[EntityItem(code="MUS", name="Mouse")]),
        parser_plan=_plan(
            mode="new_search",
            target_endpoint="/nextseek_api/samples/advanced_search/",
            filters={"sampletype_code": "MUS", "keywords": ["NDMA"]},
            notes="endpoint auto-corrected to /samples/advanced_search/",
        ),
        api_plan={
            "endpoint": "/nextseek_api/samples/advanced_search/",
            "method": "POST",
            "requestBody": {"filter_sampletype": "MUS", "filter_searchText": "NDMA"},
            "queryParameters": {"page_size": 1000},
        },
    )
    block = render_query_scope(scope)

    for forbidden in ("/nextseek_api/", "advanced_search", "filter_searchText",
                      "filter_sampletype", "requestBody", "page_size", "POST", "MATCH ("):
        assert forbidden not in block, f"{forbidden!r} leaked into the reply prompt"


def test_the_rendered_block_puts_the_gap_where_it_cannot_be_missed():
    scope = describe_query_scope(
        entity_result=_entity(keywords=["CC"], sampletypes=[EntityItem(code="MUS", name="Mouse")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": _B13_CYPHER, "explanation": ""},
    )
    block = render_query_scope(scope)

    assert "NOT APPLIED" in block
    assert "CC" in block.split("NOT APPLIED", 1)[1]


def test_an_unmeasurable_scope_renders_no_gap_line_at_all():
    scope = describe_query_scope(
        entity_result=_entity(projects=["MetNet"]), parser_plan=_plan(mode="reporter"),
    )
    block = render_query_scope(scope)

    assert "NOT APPLIED" not in block


# --------------------------------------------------------------------------
# Scientists and labs (spec 2026-09-18-projects-labs-context.md section 7.7). The
# entity agent appends every scientist to keywords too (E4), so the scientist must not
# be counted twice, and a graph query may carry the surname alone. Invented names only.
# --------------------------------------------------------------------------

_ASH = LabMatch(text="Ashgrove lab", code="ASH", name="Ashgrove", affiliation="BWH",
                project_ids=[4], rule="lab_phrase")


def test_a_scientist_the_query_dropped_is_reported_once_as_a_scientist():
    scope = describe_query_scope(
        entity_result=_entity(scientists=["Dana Example"], keywords=["Dana Example"]),
        parser_plan=_plan(mode="graph_query", filters={"keywords": ["dana example"]}),
        graph_plan={"cypher": "MATCH (s:Sample {SampleType: 'MUS'}) RETURN s", "explanation": ""},
    )

    mentions = [item for item in scope.applied + scope.not_applied if "Dana Example" in item
                or "dana example" in item]
    assert mentions == ["scientist Dana Example"]
    assert mentions[0] in scope.not_applied


def test_a_scientist_counts_as_applied_when_the_surname_alone_is_in_the_query():
    scope = describe_query_scope(
        entity_result=_entity(scientists=["Dana Example"], keywords=["Dana Example"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE toLower(s.Scientist) CONTAINS 'example' "
                              "RETURN s", "explanation": ""},
    )

    assert "scientist Dana Example" in scope.applied
    assert scope.not_applied == []


def test_a_scientist_counts_as_applied_when_the_full_name_is_in_the_query():
    scope = describe_query_scope(
        entity_result=_entity(scientists=["Dana Example"], keywords=["Dana Example"]),
        parser_plan=_plan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/"),
        api_plan={"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST",
                  "requestBody": {"filter_searchText": "Dana Example"}, "queryParameters": {}},
    )

    assert scope.applied == ["scientist Dana Example"]
    assert scope.not_applied == []


def test_a_last_comma_first_scientist_is_matched_by_the_surname():
    scope = describe_query_scope(
        entity_result=_entity(scientists=["Example, D."]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE s.Scientist CONTAINS 'Example' RETURN s",
                    "explanation": ""},
    )

    assert scope.applied == ["scientist Example, D."]


def test_a_keyword_that_is_not_a_scientist_is_still_a_keyword():
    scope = describe_query_scope(
        entity_result=_entity(scientists=["Dana Example"], keywords=["Dana Example", "CC"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": _B13_CYPHER, "explanation": ""},
    )

    assert 'keyword "CC"' in scope.not_applied
    assert "scientist Dana Example" in scope.not_applied


def test_a_lab_is_labelled_by_its_name_and_counted_once():
    scope = describe_query_scope(
        entity_result=_entity(labs=["Ashgrove"], lab_codes=["ASH"], lab_matches=[_ASH]),
        parser_plan=_plan(mode="graph_query", filters={"lab_codes": ["ASH"]}),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE s.uuid CONTAINS 'ASH' RETURN s",
                    "explanation": ""},
    )

    assert scope.applied == ["lab ASH (Ashgrove)"]
    assert scope.not_applied == []


def test_a_dropped_lab_is_named_in_the_gap():
    scope = describe_query_scope(
        entity_result=_entity(labs=["Ashgrove"], lab_codes=["ASH"], lab_matches=[_ASH]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample) RETURN s", "explanation": ""},
    )

    assert scope.not_applied == ["lab ASH (Ashgrove)"]
    assert "lab ASH (Ashgrove)" in render_query_scope(scope).split("NOT APPLIED", 1)[1]


def test_a_code_two_labs_share_names_both():
    other = LabMatch(text="ASH lab", code="ASH", name="Hollins", rule="code")
    scope = describe_query_scope(
        entity_result=_entity(lab_codes=["ASH"], lab_matches=[_ASH, other]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample) RETURN s", "explanation": ""},
    )

    assert scope.not_applied == ["lab ASH (Ashgrove or Hollins)"]


def test_a_lab_code_with_no_match_record_keeps_the_bare_label():
    """A parser-only lab code (no lab_matches) is labelled as before."""
    scope = describe_query_scope(
        entity_result=_entity(),
        parser_plan=_plan(mode="graph_query", filters={"lab_codes": ["XYZ"]}),
        graph_plan={"cypher": "MATCH (s:Sample) RETURN s", "explanation": ""},
    )

    assert scope.not_applied == ["lab XYZ"]


# --------------------------------------------------------------------------
# Pilot A v2 (2026-09-18): the graph writes a sample type as a label.
#
# 19 of the 30 v2 replies opened by saying the sample type was not applied, on queries
# that were nothing but that type ("Although I could not restrict the search to NHP",
# on MATCH (s:T_NHP)). The graph agent writes a type as the label T_<code>, with every
# character outside [A-Za-z0-9_] replaced by _, and the containment test only looked
# for the bare code, which the label hides behind an identifier character.
# --------------------------------------------------------------------------

def test_a_sample_type_written_as_a_graph_label_is_applied():
    scope = describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code="RNA", name="RNA Sample"),
                                           EntityItem(code="D.WBLT", name="Western Blot Data File")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_RNA) WHERE s.RIN > $x RETURN count(*) AS n "
                              "UNION MATCH (d:T_D_WBLT) RETURN count(*) AS n",
                    "parameters": {"x": 7}},
    )

    assert scope.not_applied == []
    assert any("RNA" in item for item in scope.applied)
    assert any("D.WBLT" in item for item in scope.applied)


def test_a_label_counts_only_as_a_whole_label():
    scope = describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code="SEQ", name="Sequence")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_D_SEQ) RETURN count(*) AS n"},
    )

    assert any("SEQ" in item for item in scope.not_applied)


# --------------------------------------------------------------------------
# Entities the question never named. The entity step over-resolves: an "Antibody
# Treatment" assay for "cd8 depletion", a "Published Data" project read into a -PUB
# UID, "Chromatin Sequencing Analysis" for "ChIP-seq". A query that rightly ignored
# them was reported as having dropped them. With the question in hand, an assay,
# project or keyword that the question does not mention is not treated as asked for.
# Sample types, UIDs and lab codes are always checked: "monkeys" names NHP without
# saying it.
# --------------------------------------------------------------------------

def test_an_entity_the_question_never_named_is_not_reported_as_dropped():
    scope = describe_query_scope(
        entity_result=_entity(assays=[EntityItem(code="Antibody Treatment", name="Antibody Treatment")],
                              projects=["Published Data"], keywords=["Published Data"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE toLower(s.search_text) CONTAINS $a RETURN s.id AS id",
                    "parameters": {"a": "cd8"}},
        user_query="Find me samples associated with cd8 depletion",
    )

    assert scope.not_applied == []


def test_an_assay_the_question_names_is_still_reported_when_dropped():
    scope = describe_query_scope(
        entity_result=_entity(assays=[EntityItem(code="Western Blot", name="Western Blot")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_TIS) RETURN count(*) AS n"},
        user_query="Which tissues underwent western blot?",
    )

    assert any("Western Blot" in item for item in scope.not_applied)


def test_an_assay_named_without_its_generic_last_word_still_counts_as_named():
    scope = describe_query_scope(
        entity_result=_entity(assays=[EntityItem(code="CometChip Assay", name="CometChip Assay")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_D_IMG) RETURN count(*) AS n"},
        user_query="How many CometChip imaging datasets are there?",
    )

    assert any("CometChip Assay" in item for item in scope.not_applied)


def test_a_sample_type_is_checked_even_when_the_question_uses_another_word():
    scope = describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code="NHP", name="Non Human Primate")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_MUS) RETURN count(*) AS n"},
        user_query="Find me monkeys",
    )

    assert any("NHP" in item for item in scope.not_applied)


def test_without_the_question_every_resolved_entity_is_still_checked():
    scope = describe_query_scope(
        entity_result=_entity(assays=[EntityItem(code="Antibody Treatment", name="Antibody Treatment")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample) RETURN count(*) AS n"},
    )

    assert any("Antibody Treatment" in item for item in scope.not_applied)


def test_a_multiword_keyword_counts_as_applied_when_one_of_its_words_is_used():
    # "RIN score": the query compares s.RIN; "score" is the user's word, not a field.
    scope = describe_query_scope(
        entity_result=_entity(keywords=["RIN score"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_RNA) WHERE s.RIN > $x RETURN count(*) AS n", "parameters": {"x": 7}},
        user_query="Find RNA samples with a RIN score greater than 7.",
    )

    assert scope.not_applied == []


# --------------------------------------------------------------------------
# F2: an assay is asked for by its full title and almost never written that way.
# Six correct answers in the 2026-09-18 runs opened by saying the assay had not
# been applied. The decisive one filtered internal_assay_title on the right term.
# --------------------------------------------------------------------------


def test_an_assay_filtered_on_the_edge_property_counts_as_applied():
    """The case that made a right answer read as a partial one."""
    scope = describe_query_scope(
        entity_result=_entity(assays=[EntityItem(code="CometChip Assay", name="CometChip Assay")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={
            "cypher": "MATCH (img:T_D_IMG) WHERE EXISTS { MATCH (img)-[r:DERIVED_FROM]->(:Sample) "
                      "WHERE toLower(r.internal_assay_title) CONTAINS $term } RETURN count(img) AS n",
            "parameters": {"term": "cometchip"},
        },
        user_query="How many CometChip imaging datasets are there?",
    )

    assert not scope.not_applied, scope.not_applied
    assert any("CometChip" in item for item in scope.applied)


def test_an_assay_reached_through_its_data_type_label_counts_as_applied():
    """Flow Cytometry's data lands on D.FLOW samples, written as the label T_D_FLOW."""
    scope = describe_query_scope(
        entity_result=_entity(assays=[EntityItem(code="Flow Cytometry", name="Flow Cytometry")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (d:T_D_FLOW)-[:DERIVED_FROM*1..12]->(s:T_PAT) RETURN count(DISTINCT s) AS n"},
        user_query="Which patients have flow cytometry data?",
    )

    assert not scope.not_applied, scope.not_applied


@pytest.mark.parametrize("assay_code,assay_name,cypher", [
    # Found by adversarial review: collapsing a label and asking "is the word inside it"
    # reported three assays as applied against a query that only constrained a sample type.
    ("A.TIS", "Tissue Collection", "MATCH (s:T_TIS) WHERE toLower(trim(toString(s.Organ))) = $o RETURN count(*) AS n"),
    ("A.RNASEQ", "RNA Sequencing", "MATCH (s:T_RNA) WHERE toLower(s.search_text) CONTAINS $t RETURN count(*) AS n"),
    ("A.DNAX", "DNA Extraction", "MATCH (s:T_DNA) RETURN count(*) AS n"),
])
def test_a_biological_type_label_is_not_evidence_the_assay_ran(assay_code, assay_name, cypher):
    """T_TIS constrains the sample type to tissue. It says nothing about a collection assay."""
    scope = describe_query_scope(
        entity_result=_entity(assays=[EntityItem(code=assay_code, name=assay_name)]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": cypher},
        user_query=f"How many samples went through {assay_name}?",
    )

    assert any(assay_code in item or assay_name in item for item in scope.not_applied), scope.applied


def test_an_assay_word_does_not_count_against_an_unrelated_property():
    """The type-label route is not a free word match: Workflow is not Flow Cytometry."""
    scope = describe_query_scope(
        entity_result=_entity(assays=[EntityItem(code="Flow Cytometry", name="Flow Cytometry")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_TIS) WHERE s.Workflow IS NOT NULL RETURN count(*) AS n"},
        user_query="Which tissues went through flow cytometry?",
    )

    assert any("Flow Cytometry" in item for item in scope.not_applied)


def test_a_keyword_the_entity_step_resolved_to_a_type_is_applied_through_it():
    """"mouse" is realised as the label T_MUS and appears nowhere in the query as a word."""
    scope = describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code="MUS", name="Mouse")], keywords=["mouse"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_MUS) RETURN count(*) AS n"},
        user_query="How many RNA-seq files come from mice?",
    )

    assert not scope.not_applied, scope.not_applied


def test_a_sample_type_asked_for_on_both_sides_is_reported_once():
    """The entity agent resolves it with a name and the parser's filter carries the code."""
    scope = describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code="TIS", name="Tissue")]),
        parser_plan=_plan(mode="graph_query", filters={"sampletype_code": "TIS"}),
        graph_plan={"cypher": "MATCH (s:T_TIS) RETURN count(*) AS n"},
        user_query="How many tissue samples are there?",
    )

    assert len([i for i in scope.applied + scope.not_applied if "TIS" in i]) == 1


# --------------------------------------------------------------------------
# `_` is an identifier character, so SRP was never found in 'MIT_SRP'. Local run
# 2026-09-22: "Break the MIT_SRP project down by sample type" answered 57,441 correctly
# and opened by saying it could not apply the SRP project or keyword.
# --------------------------------------------------------------------------

_SRP_CYPHER = (
    "MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WHERE p.title = 'MIT_SRP' "
    "RETURN s.type AS type, count(s) AS n ORDER BY n DESC"
)


def test_a_project_inside_an_underscored_title_is_applied():
    scope = describe_query_scope(
        entity_result=_entity(projects=["SRP"], keywords=["SRP"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": _SRP_CYPHER},
        user_query="Break the MIT_SRP project down by sample type.",
    )

    assert scope.not_applied == []


def test_a_project_in_a_parameter_with_an_underscore_is_applied():
    scope = describe_query_scope(
        entity_result=_entity(projects=["SRP"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WHERE p.title = $p RETURN count(s)",
                    "parameters": {"p": "MIT_SRP"}},
        user_query="How many samples are in the SRP project?",
    )

    assert scope.not_applied == []


def test_a_keyword_is_not_applied_by_a_segment_of_a_graph_label():
    scope = describe_query_scope(
        entity_result=_entity(keywords=["SEQ"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_D_SEQ) RETURN count(*) AS n"},
        user_query="How many SEQ samples?",
    )

    assert any("SEQ" in item for item in scope.not_applied)


def test_a_dropped_project_is_still_reported():
    scope = describe_query_scope(
        entity_result=_entity(projects=["SRP"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_TIS) RETURN count(s) AS n"},
        user_query="How many tissue samples are in the SRP project?",
    )

    assert any("SRP" in item for item in scope.not_applied)


# --------------------------------------------------------------------------
# Q1: every keyword a filtered field stands for. R3-602 and R7-709 filtered
# Classification with CONTAINS 'convert' and declared only 'convert' in
# keyword_fields, so Mtb, infection and positive were reported NOT APPLIED.
# --------------------------------------------------------------------------

_CONVERTER_QUESTION = "show samples for human subjects who convert to Mtb infection positive"
_CONVERTER_KEYWORDS = ["convert", "Mtb", "infection", "positive"]
_ALL_TO_CLASSIFICATION = {k: ["Classification"] for k in _CONVERTER_KEYWORDS}


def _converter_scope(cypher):
    return describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code="PAT", name="Patient")], keywords=_CONVERTER_KEYWORDS),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": cypher, "parameters": {}, "keyword_fields": _ALL_TO_CLASSIFICATION},
        user_query=_CONVERTER_QUESTION,
    )


def test_every_keyword_declared_for_a_filtered_field_counts_as_applied():
    scope = _converter_scope(
        "MATCH (s:T_PAT) WHERE toLower(toString(s.Classification)) CONTAINS 'convert' "
        "RETURN s.id AS id, s.uuid AS uuid, s.Classification AS Classification ORDER BY id LIMIT 5000"
    )

    assert scope.not_applied == [], scope.not_applied
    for keyword in _CONVERTER_KEYWORDS:
        assert f'keyword "{keyword}"' in scope.applied


@pytest.mark.parametrize("cypher", [
    # the field is only returned, never filtered
    "MATCH (s:T_PAT) RETURN s.id AS id, s.uuid AS uuid, s.Classification AS Classification ORDER BY id LIMIT 5000",
    # another field is filtered
    "MATCH (s:T_PAT) WHERE s.QFT_Result IS NOT NULL RETURN s.id AS id, s.uuid AS uuid ORDER BY id LIMIT 5000",
])
def test_keywords_declared_for_a_field_the_query_does_not_filter_stay_not_applied(cypher):
    scope = _converter_scope(cypher)

    assert scope.not_applied == [f'keyword "{keyword}"' for keyword in _CONVERTER_KEYWORDS]


# --------------------------------------------------------------------------
# Phase F (D2): the false caveats of the 2026-09-23 runs, one route each, and
# the gaps each route must leave alone.
# --------------------------------------------------------------------------

def test_a_samples_question_about_a_topic_asks_for_no_type():
    """R5-678: "all samples associated with CD8 Antibodies" is a text search over every sample."""
    scope = describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code="AB", name="Antibody")], keywords=["CD8"]),
        parser_plan=_plan(mode="graph_query", filters={"sampletype_code": "AB", "keywords": ["CD8"]}),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE toLower(s.search_text) CONTAINS $a "
                              "AND toLower(s.search_text) CONTAINS $b RETURN s.id AS id",
                    "parameters": {"a": "cd8", "b": "antibod"}},
        user_query="Find me all samples associated with CD8 Antibodies",
    )
    assert scope.not_applied == []


def test_samples_processed_via_an_assay_ask_for_no_data_type():
    """R5-646: the entity step read D.MSP and A.MSP out of "mass spectrometry"."""
    scope = describe_query_scope(
        entity_result=_entity(assays=[EntityItem(code="Mass Spectrometry", name="Mass Spectrometry")],
                              sampletypes=[EntityItem(code="D.MSP", name="Mass Spectrometry Data"),
                                           EntityItem(code="A.MSP", name="Mass Spectrometry Analysis")]),
        parser_plan=_plan(mode="graph_query", filters={"assay_codes": ["Mass Spectrometry"]}),
        graph_plan={"cypher": "MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample) WHERE r.internal_assay_title IN $assays "
                              "RETURN DISTINCT p.id AS id",
                    "parameters": {"assays": ["Mass Spectrometry", "Mass Spectrometry Proteomics"]}},
        user_query="Show me samples processed via mass spectrometry",
    )
    assert scope.not_applied == []


@pytest.mark.parametrize("question", [
    "Show me samples from mice treated with NDMA",
    "Which mouse samples have sequencing data?",
    "Find samples associated with NDMA of type MUS",
])
def test_a_type_the_question_itself_asks_for_is_still_checked(question):
    scope = describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code="MUS", name="Mouse")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE toLower(s.search_text) CONTAINS $t RETURN s.id AS id",
                    "parameters": {"t": "ndma"}},
        user_query=question,
    )
    assert any("MUS" in item for item in scope.not_applied)


def test_an_assay_whose_data_type_the_query_reached_by_name_is_applied():
    """R5-653: "Imaging" is the title of the assay whose data type is D.IMG "Imaging Data"."""
    scope = describe_query_scope(
        entity_result=_entity(assays=[EntityItem(code="Imaging", name="Imaging")], keywords=["fibrin"],
                              sampletypes=[EntityItem(code="D.IMG", name="Imaging Data")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_D_IMG) WHERE toLower(s.search_text) CONTAINS toLower($keyword) "
                              "RETURN s.id AS id",
                    "parameters": {"keyword": "fibrin"}},
        user_query="Find me fibrin imaging data",
    )
    assert scope.not_applied == []


@pytest.mark.parametrize("assay", ["CometChip Assay", "Imaging Mass Cytometry"])
def test_a_narrower_assay_is_not_applied_by_its_broader_data_type(assay):
    scope = describe_query_scope(
        entity_result=_entity(assays=[EntityItem(code=assay, name=assay)],
                              sampletypes=[EntityItem(code="D.IMG", name="Imaging Data")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_D_IMG) RETURN count(s) AS n"},
        user_query=f"How many {assay} imaging datasets are there?",
    )
    assert any(assay in item for item in scope.not_applied)


def test_a_project_scoped_by_its_stored_title_is_applied():
    """R7-708: "Impact" is stored as 'IMPAcTb', and the plan's project_titles came back empty."""
    scope = describe_query_scope(
        entity_result=_entity(projects=["Impact"], keywords=["Impact", "species"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WHERE p.title = $project_title "
                              "UNWIND keys(s) AS key WITH s, key WHERE toLower(key) CONTAINS 'species' "
                              "RETURN key AS attribute, s[key] AS value, count(*) AS n",
                    "parameters": {"project_title": "IMPAcTb"}, "project_titles": {}},
        user_query="What species are the samples in the IMPACT project?",
    )
    assert scope.not_applied == []


_STUDY_CYPHER = ("MATCH (s:T_PAT)-[:IN_STUDY]->(st:Study)-[:IN_INVESTIGATION]->(inv:Investigation) "
                 "WHERE toLower(st.title) = toLower($study_title) AND toLower(inv.title) = toLower($inv_title) "
                 "RETURN count(DISTINCT s) AS n")


def test_a_study_scoped_by_its_title_is_applied():
    """R6-1221: the entity step filed the whole phrase as a project."""
    project = "TCGA glioblastoma (GBM) study"
    scope = describe_query_scope(
        entity_result=_entity(projects=[project], keywords=[project],
                              sampletypes=[EntityItem(code="PAT", name="Patient")]),
        parser_plan=_plan(mode="graph_query", filters={"sampletype_code": "PAT"}),
        graph_plan={"cypher": _STUDY_CYPHER, "parameters": {"study_title": "gbm", "inv_title": "tcga"}},
        user_query=f"How many patients are in the {project}?",
    )
    assert scope.not_applied == []


def test_a_gloss_of_an_applied_study_is_applied():
    """R6-1227: "LUAD (lung adenocarcinoma)"."""
    scope = describe_query_scope(
        entity_result=_entity(projects=["TCGA LUAD"], keywords=["TCGA LUAD", "lung adenocarcinoma"],
                              sampletypes=[EntityItem(code="PAT", name="Patient")]),
        parser_plan=_plan(mode="graph_query", filters={"sampletype_code": "PAT"}),
        graph_plan={"cypher": _STUDY_CYPHER, "parameters": {"study_title": "LUAD", "inv_title": "TCGA"}},
        user_query="Find the patients in the TCGA LUAD (lung adenocarcinoma) study.",
    )
    assert scope.not_applied == []


def test_a_gloss_of_a_term_the_query_did_not_apply_stays_a_gap():
    scope = describe_query_scope(
        entity_result=_entity(keywords=["lung adenocarcinoma"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s)-[:IN_STUDY]->(st:Study) WHERE st.title = $t RETURN count(s) AS n",
                    "parameters": {"t": "LUSC"}},
        user_query="How many patients are in TCGA LUAD (lung adenocarcinoma)?",
    )
    assert any("lung adenocarcinoma" in item for item in scope.not_applied)


@pytest.mark.parametrize("project,cypher,params", [
    ("MetNet", "MATCH (s)-[:IN_STUDY]->(st:Study) WHERE st.title CONTAINS $t RETURN count(s) AS n",
     {"t": "impact of fibrinogen"}),
    ("Impact", "MATCH (s)-[:IN_PROJECT]->(p:Project) WHERE p.title = 'MIT_SRP' RETURN count(s) AS n", {}),
    ("Impact", "MATCH (s:Sample) WHERE s.title CONTAINS 'impactb' RETURN count(s) AS n", {}),
])
def test_a_different_title_or_a_sample_title_does_not_apply_a_project(project, cypher, params):
    scope = describe_query_scope(
        entity_result=_entity(projects=[project]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": cypher, "parameters": params},
        user_query=f"How many samples are in the {project} project?",
    )
    assert any(project in item for item in scope.not_applied)


def test_a_word_of_a_matched_paper_title_is_applied_with_it():
    """R7-711: the query matched the title without its first word, which is stored hyphenated."""
    title = "Noncanonical T cell responses are associated with protection from tuberculosis in mice and humans"
    scope = describe_query_scope(
        entity_result=_entity(keywords=["T cell", "tuberculosis", "Noncanonical", "protection"],
                              sampletypes=[EntityItem(code="CEL", name="Cell"), EntityItem(code="MUS", name="Mouse"),
                                           EntityItem(code="PAT", name="Patient")]),
        parser_plan=_plan(mode="graph_query", filters={"keywords": [title]}),
        graph_plan={"cypher": "MATCH (s:Sample)-[:IN_STUDY]->(st:Study) "
                              "WHERE toLower(st.title) CONTAINS toLower($title_part) RETURN s.id AS id",
                    "parameters": {"title_part": title.split(" ", 1)[1].lower()}},
        user_query=f"What are the samples associated with this paper: {title}.",
    )
    assert scope.not_applied == []


def test_a_rest_list_named_in_the_plural_applies_the_keyword():
    """R5-667: "SOP" was never found in /nextseek_api/sops/, because the "s" is an identifier character."""
    scope = describe_query_scope(
        entity_result=_entity(keywords=["SOP"]),
        parser_plan=_plan(mode="new_search"),
        api_plan={"endpoint": "/nextseek_api/sops/", "method": "GET", "requestBody": {}, "queryParameters": {}},
        user_query="What SOPs are on file?",
    )
    assert scope.not_applied == []


def test_a_stem_and_a_plural_apply_a_keyword():
    """R5-631: the query searched the Attribute catalog for 'vocab'."""
    scope = describe_query_scope(
        entity_result=_entity(keywords=["controlled vocabulary", "sample attributes"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (a:Attribute) WHERE toLower(toString(a.value_type)) CONTAINS 'vocab' "
                              "RETURN a.title AS attribute"},
        user_query="Which sample attributes use a controlled vocabulary?",
    )
    assert scope.not_applied == []


def test_a_short_stem_widens_nothing():
    scope = describe_query_scope(
        entity_result=_entity(keywords=["positive"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_PAT) WHERE s.QFT CONTAINS 'pos' RETURN count(s) AS n"},
        user_query="Which patients are positive?",
    )
    assert any("positive" in item for item in scope.not_applied)


@pytest.mark.parametrize("cypher", [
    "MATCH (nhp:T_NHP) WHERE EXISTS { MATCH (nhp)<-[:DERIVED_FROM*1..12]-(:T_D_SEQ) } RETURN nhp.id AS id",
    "MATCH (s:T_D_SEQ) WHERE EXISTS { (s)-[:DERIVED_FROM*1..12]->(:T_NHP) } RETURN s.id AS id",
])
def test_monkey_is_applied_by_the_nhp_label(cypher):
    """R5-650 and R5-670: the entity step kept "monkey" as a keyword; the query used T_NHP."""
    scope = describe_query_scope(
        entity_result=_entity(keywords=["monkey"], sampletypes=[EntityItem(code="D.SEQ", name="Sequencing Data")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": cypher},
        user_query="Which monkeys have sequencing data?",
    )
    assert scope.not_applied == []


@pytest.mark.parametrize("keyword,label", [("CC", "T_MUS"), ("rhesus macaque", "T_NHP")])
def test_a_narrowing_term_is_not_applied_by_its_whole_type(keyword, label):
    """The catalog Tags list CC under MUS and rhesus macaque under NHP; neither is the whole type (B13)."""
    scope = describe_query_scope(
        entity_result=_entity(keywords=[keyword]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": f"MATCH (s:{label}) RETURN count(s) AS n"},
        user_query=f"How many {keyword} samples are there?",
    )
    assert any(keyword in item for item in scope.not_applied)


# --- the Phase F routes never add a gap: what the review of the first version found ---

@pytest.mark.parametrize("code,name,keyword", [("AB", "Antibody", "antibody"), ("PAT", "Patient", "patient")])
def test_a_keyword_naming_a_type_an_every_sample_question_skips_is_applied_by_its_label(code, name, keyword):
    """The every-sample route skips the type; the keyword that names it still reaches the type's label."""
    scope = describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code=code, name=name)], keywords=[keyword]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": f"MATCH (s:T_{code}) RETURN count(s) AS n"},
        user_query=f"Find me all samples associated with {keyword}",
    )
    assert scope.not_applied == []


@pytest.mark.parametrize("project,where,params", [
    # a different study under the same investigation
    ("TCGA GBM", "st.title = $study_title AND inv.title = $investigation_title",
     {"study_title": "LUAD", "investigation_title": "TCGA"}),
    # the parent investigation alone
    ("TCGA LUAD", "inv.title = $investigation_title", {"investigation_title": "TCGA"}),
])
def test_an_investigation_title_inside_the_asked_name_does_not_apply_it(project, where, params):
    """'TCGA' sits inside every TCGA study's name; only a Project or Study title may sit inside the asked name."""
    scope = describe_query_scope(
        entity_result=_entity(projects=[project], sampletypes=[EntityItem(code="PAT", name="Patient")]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:T_PAT)-[:IN_STUDY]->(st:Study)-[:IN_INVESTIGATION]->(inv:Investigation) "
                              f"WHERE {where} RETURN count(DISTINCT s) AS n",
                    "parameters": params},
        user_query=f"How many patients are in the {project} study?",
    )
    assert f"project {project}" in scope.not_applied


@pytest.mark.parametrize("code,name,question", [
    ("MUS", "Mouse", "Find all samples associated with NDMA from mice"),
    ("PAT", "Patient", "How many samples associated with tuberculosis are from patients?"),
    ("MUS", "Mouse", "Find all samples associated with NDMA in mouse samples"),
])
def test_a_type_named_after_the_topic_with_a_cue_is_still_checked(code, name, question):
    """"from <type>" and "<type> samples" keep the type asked for. "in <type>" is not a cue (R7-711's paper title
    holds "in mice and humans"), so "... associated with NDMA in mouse liver" still drops MUS: an under-report, the
    direction this module allows."""
    scope = describe_query_scope(
        entity_result=_entity(sampletypes=[EntityItem(code=code, name=name)], keywords=["NDMA"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE toLower(s.search_text) CONTAINS $t RETURN count(s) AS n",
                    "parameters": {"t": "ndma"}},
        user_query=question,
    )
    assert any(code in item for item in scope.not_applied)


def test_a_short_keyword_inside_a_word_of_a_matched_title_keeps_its_gap():
    """B13's CC: the title part route matches whole words of three or more characters, never "cc" in "Vaccine"."""
    title = "Vaccine-induced T cell responses protect mice"
    scope = describe_query_scope(
        entity_result=_entity(keywords=[title, "CC"]),
        parser_plan=_plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample)-[:IN_STUDY]->(st:Study) WHERE toLower(st.title) CONTAINS toLower($t) "
                              "RETURN count(s) AS n",
                    "parameters": {"t": title.lower()}},
        user_query=f"How many CC samples are in the study {title}?",
    )
    assert 'keyword "CC"' in scope.not_applied

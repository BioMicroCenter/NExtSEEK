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

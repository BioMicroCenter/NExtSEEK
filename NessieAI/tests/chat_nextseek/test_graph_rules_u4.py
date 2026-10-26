"""Graph agent rules, nessie_fixes unit U4 (fixes 5, 6, 7, 8, 14, 16).

Code paths get behaviour tests with sibling inputs; prompt rules get pins that the rule text is present and that
the old wrong example is gone. No model, Neo4j or network call is made.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import chat_nextseek
from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.schemas.entity import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan

PROMPTS = Path(chat_nextseek.__file__).resolve().parent / "prompts"
GRAPH = (PROMPTS / "graph_agent.txt").read_text(encoding="utf-8")
PARSER = (PROMPTS / "multi_parser_agent.txt").read_text(encoding="utf-8")


class _StubConfig:
    CHATTER_SYSTEM_PROMPT = "SYSTEM PROMPT"
    LOG_DIR = ""

    def get_agent_model(self, agent_label):
        return (object(), "stub-model", None)


@pytest.fixture
def captured(monkeypatch):
    box: dict = {}

    def _fake(config, *, messages, model_name, client, agent_label, temperature=0, thinking_budget=None,
              usage_label=None):
        box["user_content"] = messages[-1]["content"]
        return "stub reply"

    monkeypatch.setattr(chatter_mod, "call_llm_text", _fake)
    return box


def _turn(captured, rows, *, total, truncated, question="List the samples"):
    chatter_mod.chatter_agent_answer(
        _StubConfig(), question, EntityAgentOutput().model_dump(), ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (s:Sample) RETURN s.id AS id, s.uuid AS uuid, s.type AS type",
                    "parameters": {}, "explanation": ""},
        graph_result={"ok": True, "count": len(rows), "total": total, "truncated": truncated, "data": rows},
        log_dir="",
    )
    return captured["user_content"]


def _rows(n):
    return [{"id": i, "uuid": f"TIS-{i}", "type": "TIS"} for i in range(n)]


# --- fix 5: no "full list" when the result stopped at the row cap -----------------------------------------------

@pytest.mark.parametrize("n,total", [(5000, 14163), (3000, 3500)])
def test_a_capped_result_is_not_called_the_full_list(captured, n, total):
    text = _turn(captured, _rows(n), total=total, truncated=True)
    assert "say the full list is available" not in text
    assert f"{n:,} of {total:,}" in text
    assert "never call it the full list" in text


@pytest.mark.parametrize("n", [60, 400])
def test_an_uncapped_result_keeps_the_full_list_line(captured, n):
    text = _turn(captured, _rows(n), total=n, truncated=False)
    assert "say the full list is available" in text
    assert "never call it the full list" not in text


# --- fix 6: a sum of groups is not a count of distinct items ----------------------------------------------------

def _grouped(captured, cypher, rows):
    chatter_mod.chatter_agent_answer(
        _StubConfig(), "How many in total?", EntityAgentOutput().model_dump(),
        ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": cypher, "parameters": {}, "explanation": ""},
        graph_result={"ok": True, "count": len(rows), "total": len(rows), "truncated": False, "data": rows},
        log_dir="",
    )
    return captured["user_content"]


@pytest.mark.parametrize("cypher,col", [
    ("MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) RETURN p.title AS project, count(DISTINCT s) AS n", "project"),
    ("MATCH (s:Sample)-[:IN_STUDY]->(st:Study) RETURN st.title AS study, count(DISTINCT s) AS n", "study"),
    ("MATCH (c:Sample)-[r:DERIVED_FROM]->(:Sample) UNWIND [r.internal_assay_title] + "
     "coalesce(r.internal_assay_titles, []) AS a RETURN a AS assay, count(*) AS n", "assay"),
    ("MATCH (s:Sample)-[:IN_STUDY]->(st:Study) RETURN st.title AS study_title, count(DISTINCT s) AS n", "study_title"),
    ("MATCH (s:Sample)-[:IN_INVESTIGATION]->(i:Investigation) RETURN i.title AS title, count(*) AS n", "title"),
])
def test_a_sum_over_groups_that_can_overlap_is_not_offered_as_the_total(captured, cypher, col):
    text = _grouped(captured, cypher, [{col: "A", "n": 10}, {col: "B", "n": 7}])
    assert "Sum of n across all 2 rows: 17" in text
    assert "NOT a count of distinct" in text
    assert "give this total first" not in text


@pytest.mark.parametrize("cypher,col", [
    ("MATCH (s:Sample) RETURN s.Organ AS organ, count(*) AS n", "organ"),
    ("MATCH (s:Sample) RETURN s.type AS type, count(*) AS n", "type"),
    # R4: a scope through IN_PROJECT / IN_STUDY or an UNWIND of named ids does not make the groups overlap; what the
    # rows are grouped by does.
    ("MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WHERE p.title = $project_title "
     "RETURN s.type AS type, count(DISTINCT s) AS n", "type"),
    ("MATCH (s:Sample)-[:IN_STUDY]->(st:Study) WHERE st.title = $study RETURN s.Organ AS organ, count(*) AS n", "organ"),
    ("UNWIND $uids AS u MATCH (s:Sample {uuid: u}) RETURN s.Sex AS sex, count(*) AS n", "sex"),
])
def test_a_sum_over_disjoint_groups_is_still_the_total(captured, cypher, col):
    text = _grouped(captured, cypher, [{col: "A", "n": 10}, {col: "B", "n": 7}])
    assert "give this total first" in text
    assert "NOT a count of distinct" not in text


# --- fix 14: no invented split, no repeated internal instruction ------------------------------------------------

def test_the_writer_is_told_not_to_invent_a_split_or_repeat_a_note(captured):
    text = _turn(captured, _rows(30), total=30, truncated=False)
    assert "never one you worked out from the preview rows" in text
    assert "never repeat it to the user" in text


# --- fix 8: the scientist the entity step resolved survives the parser ------------------------------------------

from chat_nextseek import orchestrator as orch  # noqa: E402


@pytest.mark.parametrize("name", ["Ada Quill", "Bo Marsh"])
@pytest.mark.parametrize("as_dict", [True, False])
def test_the_entity_scientist_is_restored_after_the_parser(name, as_dict):
    plan = ParserPlan(mode="graph_query", resolved=EntityAgentOutput(keywords=[name]))
    entity = EntityAgentOutput(scientists=[name])
    out = orch._clamp_lab_codes_to_entity(plan, entity.model_dump() if as_dict else entity)
    assert out.resolved.scientists == [name]


def test_a_scientist_the_parser_kept_is_left_alone_and_none_is_invented():
    plan = ParserPlan(mode="graph_query", resolved=EntityAgentOutput(scientists=["Kept One"]))
    assert orch._clamp_lab_codes_to_entity(plan, EntityAgentOutput(scientists=["Other"])).resolved.scientists == ["Kept One"]
    bare = ParserPlan(mode="graph_query")
    assert orch._clamp_lab_codes_to_entity(bare, EntityAgentOutput()).resolved.scientists == []


def test_the_parser_template_has_a_scientists_field():
    assert '"scientists": ["string"]' in PARSER


# --- prompt pins (fixes 6, 7, 8, 14, 16, 18 and the example project) ---------------------------------------------

def test_the_graph_prompt_carries_the_rules():
    for text in ("A total across groups counts each item once", "Data records or the samples they came from?",
                 "A category word covers every type of its kind", "`resolved.category_types`",
                 "An assay and a field naming the same kind", "both signs were used",
                 "has no records of the asked type", "`Seq 100` and `Seq100`",
                 "A keyword for what a sample was exposed to", "never address the reply writer",
                 '"All", "every" and "across all" are not a scope'):
        assert text in GRAPH, text


def test_the_graph_prompt_has_no_real_project_as_an_example():
    for name in ("IMPAcTb", "MIT_SRP", "Shoulders Independent Projects", "fibrinogen"):
        assert name not in GRAPH


# --- review R5, R6, N6, N7: the rules say when they apply ------------------------------------------------------

def _line(start):
    at = GRAPH.index(start)
    return GRAPH[at:GRAPH.index("\n", at)]


def test_an_exposure_keyword_is_matched_on_the_asked_type_first():
    rule = _line("- **A keyword for what a sample was exposed to")
    assert "a field on the asked type itself is matched first" in rule
    assert "When the asked type has no field for it and a parent type does" in rule


def test_every_means_one_total_and_each_still_groups():
    rule = _line('**"All", "every" and "across all" are not a scope.**')
    assert "When the question asks for one total" in rule
    assert '"each", "per", "by" or "for every"' in rule.lower()


def test_a_true_zero_through_the_lineage_may_be_reported():
    rule = _line("- A name in `resolved.scientists` is a scientist for the whole question")
    assert "never settle on zero" not in rule
    assert "only when the lineage also finds none" in rule


def test_a_kind_field_needs_every_word_of_the_kind():
    rule = _line("- **An assay and a field naming the same kind.**")
    assert "contains every word of the kind" in rule



def test_the_total_beside_groups_example_has_no_call_subquery_and_is_a_proven_shape():
    from NessieAI.tests.chat_nextseek.graph_scope.battery import TAUGHT

    rule = _line("- **A total across groups counts each item once.**")
    assert "CALL {" not in rule
    case = next(c for c in TAUGHT if c.id == "default.total_beside_groups")
    assert case.cypher in rule


# --- dev_test_deep P1: method word upstream, total without collect, no pattern comprehension, both directions ---

def test_a_method_word_on_a_sample_upstream_of_the_data_is_tested_on_the_descendant():
    rule = _line("- **A method word on a sample upstream of the data.**")
    assert "descendant" in rule and "EXISTS" in rule and "search_text" in rule
    assert "floor" in rule  # an edge-only test is a floor: lineage edges often carry no assay title
    assert "every spelling" in rule


def test_a_total_beside_groups_never_collects_nodes_to_size_them():
    rule = _line("- **A total across groups counts each item once.**")
    assert "never `collect(DISTINCT s)` then `size()`" in rule
    assert "WITH count(DISTINCT s) AS total" in rule


def test_the_whole_node_repair_names_the_count_alternative():
    from chat_nextseek.agents import graph as graph_mod

    q = ("MATCH (s:T_X) WITH collect(DISTINCT s) AS xs UNWIND xs AS s "
         "RETURN s.type AS type, count(*) AS n, size(xs) AS total")
    assert graph_mod.whole_node_returns(q)
    assert "count(DISTINCT" in graph_mod._WHOLE_NODE_ALTERNATIVE


def test_the_rules_for_every_query_ban_a_pattern_comprehension():
    assert "Never a pattern comprehension" in GRAPH
    assert "OPTIONAL MATCH" in _line("- Never a pattern comprehension")


def test_two_named_types_are_tested_in_both_directions():
    rule = _line("- **Two named sample types: either may be the child.**")
    assert "both directions" in rule and "which direction held" in rule


@pytest.mark.parametrize("q", [
    "MATCH (s:T_X) RETURN s.uuid AS u, [(s)-[:INPUT_TO]->(a:Assay) | a.title] AS assays",
    "MATCH (s:T_X) RETURN [p = (s)<-[:DERIVED_FROM]-(c) | c.uuid] AS kids",
    "MATCH (s:T_X) RETURN size([(s)-[:INPUT_TO|OUTPUT_OF]->(a) | a]) AS n",
])
def test_a_pattern_comprehension_is_a_problem_for_a_scoped_request_only(q):
    from chat_nextseek.agents import graph as graph_mod

    assert [p.kind for p in graph_mod.query_shape_problems(q, scoped=True)] == ["pattern_comprehension"]
    assert graph_mod.query_shape_problems(q) == []


@pytest.mark.parametrize("q", [
    "MATCH (s:T_X) RETURN [x IN [1, 2] | x * 2] AS a",
    "MATCH p = (s:T_X)<-[:DERIVED_FROM*1..3]-(c) RETURN [n IN nodes(p) | n.uuid] AS ids",
    "MATCH (s:T_X) WHERE s.type IN ['A', 'B'] RETURN [s.uuid, s.type] AS row",
    "MATCH (s:T_X) RETURN count { (s)<-[:DERIVED_FROM]-(:Sample) } AS k, COUNT { (s)-[:INPUT_TO]->() } AS j",
    "MATCH (s:T_X) WHERE EXISTS { (s)-[:INPUT_TO|OUTPUT_OF]->(:Assay) } RETURN s.uuid",
])
def test_list_comprehensions_literals_and_subqueries_are_not_flagged(q):
    from chat_nextseek.agents import graph as graph_mod

    assert graph_mod.query_shape_problems(q, scoped=True) == []

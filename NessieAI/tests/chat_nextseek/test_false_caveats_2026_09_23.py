"""Two false "not applied" caveats from the production runs of 2026-09-23 (build b2352f55).

* **A1, bonniethiel.** "show samples for human subjects who convert to Mtb infection
  positive" answered 98 PAT samples by the conversion-status attribute and the
  QuantiFERON-TB result, and opened with "the search could not be constrained by the
  keywords 'Mtb', 'infection', or 'positive'". ``query_scope`` counts a keyword as applied
  only when its text is in the executed query; a keyword realised as a field constraint
  never is. The graph agent now records that step in ``keyword_fields``, and the keyword
  counts only when the executed Cypher really filters on a declared field.
* **A2.** "How many different D. file types exist?" answered 43, correct, and said "I could
  not restrict this to the D.FILE sample type": the entity step read the D.* prefix family
  as the one type D.FILE.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import chat_nextseek
from chat_nextseek.helpers.query_scope import describe_query_scope
from chat_nextseek.schemas import GraphAgentPlan
from chat_nextseek.schemas.entity import EntityAgentOutput, EntityItem
from chat_nextseek.schemas.router import ParserPlan

PROMPTS = Path(chat_nextseek.__file__).resolve().parent / "prompts"

QUESTION = "show samples for human subjects who convert to Mtb infection positive"
CYPHER = (
    "MATCH (s:T_PAT) WHERE toLower(toString(s.Classification)) CONTAINS $converter "
    "OR (toString(s.QFT_Baseline) = $neg AND toString(s.QFT_Result) = $pos) "
    "RETURN s.id AS id, s.uuid AS uuid ORDER BY id LIMIT 5000"
)
PARAMETERS = {"converter": "converter", "neg": "0", "pos": "1"}


def _scope(keyword_fields, cypher=CYPHER):
    plan = {"cypher": cypher, "parameters": PARAMETERS}
    if keyword_fields is not None:
        plan["keyword_fields"] = keyword_fields
    return describe_query_scope(
        entity_result=EntityAgentOutput(
            sampletypes=[EntityItem(code="PAT", name="Patient")],
            keywords=["Mtb", "infection", "positive"],
        ).model_dump(),
        parser_plan=ParserPlan(mode="graph_query").model_dump(),
        graph_plan=plan,
        user_query=QUESTION,
    )


def test_the_production_turn_without_a_declaration_reports_all_three_as_dropped():
    """The failure as it was: containment finds none of the three words in the query."""
    scope = _scope(None)

    assert scope.not_applied == ['keyword "Mtb"', 'keyword "infection"', 'keyword "positive"']


def test_a_keyword_the_query_realised_as_a_field_counts_as_applied():
    scope = _scope({"Mtb": ["Classification"], "infection": ["QFT_Result"], "positive": ["QFT_Result"]})

    assert not scope.not_applied, scope.not_applied
    assert 'keyword "positive"' in scope.applied


def test_the_declaration_is_matched_on_the_folded_keyword_and_a_variable_prefix():
    scope = _scope({"mtb": "s.Classification", "INFECTION": ["`QFT_Result`"], "positive": ["QFT_Baseline"]})

    assert not scope.not_applied, scope.not_applied


def test_only_the_declared_keywords_are_rescued():
    scope = _scope({"positive": ["QFT_Result"]})

    assert scope.not_applied == ['keyword "Mtb"', 'keyword "infection"']


@pytest.mark.parametrize("fields", [
    ["Genotype"],          # declared, but the query never reads it
    ["search_text"],       # text, not a field
    ["uuid"],              # a system property proves nothing
    ["classification"],    # property names are case-sensitive
    [],
])
def test_a_declaration_the_query_does_not_bear_out_changes_nothing(fields):
    scope = _scope({"Mtb": fields})

    assert 'keyword "Mtb"' in scope.not_applied


def test_a_field_that_is_only_returned_is_not_a_constraint():
    cypher = "MATCH (s:T_PAT) RETURN s.Classification AS c, count(*) AS n"
    scope = _scope({"Mtb": ["Classification"]}, cypher=cypher)

    assert 'keyword "Mtb"' in scope.not_applied


def test_a_field_written_with_backticks_or_brackets_is_read():
    for written in ("s.`QuantiFERON-TB`", "s['QuantiFERON-TB']"):
        cypher = f"MATCH (s:T_PAT) WHERE toString({written}) = $pos RETURN count(*) AS n"
        scope = _scope({"positive": ["QuantiFERON-TB"]}, cypher=cypher)
        assert 'keyword "positive"' in scope.applied, written


def test_the_graph_plan_carries_the_field_and_defaults_to_empty():
    assert GraphAgentPlan(cypher="x").keyword_fields == {}
    assert "keyword_fields" in GraphAgentPlan.model_json_schema()["properties"]


def test_the_graph_prompt_asks_for_the_map_and_names_what_does_not_count():
    prompt = (PROMPTS / "graph_agent.txt").read_text(encoding="utf-8")
    fmt = prompt[prompt.index("## Output format"):]

    assert '"keyword_fields"' in fmt
    rule = fmt[fmt.index("`keyword_fields` records"):]
    assert "never `search_text`" in rule
    assert "really filters on" in rule


# --- A2: a code prefix is a family, not D.FILE -----------------------------------------------------------------------


def _entity_prompt() -> str:
    return (PROMPTS / "entity_agent.txt").read_text(encoding="utf-8")


def test_the_entity_prompt_reads_a_code_prefix_as_a_family():
    prompt = _entity_prompt()
    at = prompt.index("A code prefix names a family of types")
    rule = prompt[at:at + 700]

    for phrase in ('"D. types"', '"D. file types"', '"D.* types"', '"data types starting with D."'):
        assert phrase in rule, phrase
    assert "How many different D. file types exist?" in rule
    assert "Emit NO sample type for a prefix family" in rule
    assert "never D.FILE" in rule


def test_the_family_rule_sits_beside_the_file_words_rule():
    """Both are ways a question is misread as D.FILE; keep them together in the ontology section."""
    prompt = _entity_prompt()
    ontology = prompt[prompt.index("ONTOLOGY (GRAPH)"):prompt.index("CORE PRIORITY")]

    assert "is not the sample type D.FILE" in ontology
    assert "A code prefix names a family of types" in ontology

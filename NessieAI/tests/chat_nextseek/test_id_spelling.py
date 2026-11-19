"""An identifier written with other separators must be found, every time, by code.

Production 2026-11-16 (slevine): "Show me the sample tree for patient DFCI-4 from the BTC study" found 0. The
patient is stored as GBM1.DFCI4. The zero-row retry asked the graph agent again, and the agent kept 'DFCI-4' (its
retry message calls a UID that matches nothing "a real zero"). The next day the same question, worded a little
differently, made the agent add 'DFCI4' on its own and found 343 samples. Whether the second spelling was tried was
the model's call; it is now a deterministic retry that runs before the agent's.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from chat_nextseek import graph_retry as gr

STUART = ("MATCH (s:Sample) WHERE toLower(s.search_text) CONTAINS toLower($term) "
          "RETURN s.id AS id, s.uuid AS uuid, s.type AS type, s.title AS title ORDER BY id LIMIT 5000")


def matches(pattern: str, text: str) -> bool:
    # Neo4j's =~ is a whole-string Java match; Python's fullmatch reads the same lookarounds the same way.
    return re.fullmatch(pattern, text) is not None


class TestTheVariant:
    def test_stuarts_query_gets_a_separator_tolerant_match(self):
        v = gr.id_spelling_variant(STUART, {"term": "DFCI-4"})
        assert v is not None
        assert "CONTAINS" not in v.cypher and "=~ $id_spelling" in v.cypher
        assert v.cypher.endswith("ORDER BY id LIMIT 5000")
        assert v.parameters["term"] == "DFCI-4"           # the original parameter is left alone
        assert v.term == "DFCI-4" and v.joined == "DFCI4"
        p = v.parameters["id_spelling"]
        for stored in ("Patient GBM1.DFCI4 glioblastoma", "dfci-4", "DFCI 4 biopsy", "x DFCI_4", "dfci4"):
            assert matches(p, stored), stored

    @pytest.mark.parametrize("stored", ["DFCI40", "DFCI-41", "DFCI 4.5", "XDFCI4"])
    def test_it_keeps_the_identifier_boundaries(self, stored):
        p = gr.id_spelling_variant(STUART, {"term": "DFCI-4"}).parameters["id_spelling"]
        assert not matches(p, stored), stored

    def test_a_term_without_separators_also_finds_the_separated_spelling(self):
        p = gr.id_spelling_variant(STUART, {"term": "DFCI4"}).parameters["id_spelling"]
        assert matches(p, "DFCI-4") and matches(p, "GBM1.DFCI4") and not matches(p, "DFCI45")

    def test_another_identifier_shape_an_animal_number(self):
        cy = "MATCH (n:T_NHP) WHERE n.search_text CONTAINS '16-219' RETURN n.uuid AS uuid"
        v = gr.id_spelling_variant(cy, {})
        assert v is not None and "=~ $id_spelling" in v.cypher and "'16-219'" not in v.cypher
        p = v.parameters["id_spelling"]
        assert matches(p, "animal 16219") and matches(p, "16 219") and not matches(p, "116219")

    def test_a_multiline_record_still_matches(self):
        p = gr.id_spelling_variant(STUART, {"term": "DFCI-4"}).parameters["id_spelling"]
        assert matches(p, "line one\nName: GBM1.DFCI4\nline three")

    @pytest.mark.parametrize("term", ["human lung", "TCR", "Kate Bridges", "4", "2026-11-12", "0.5"])
    def test_words_numbers_and_values_are_not_identifiers(self, term):
        assert gr.id_spelling_variant(STUART, {"term": term}) is None, term

    def test_only_free_text_is_rewritten(self):
        cy = "MATCH (s:Sample) WHERE s.uuid CONTAINS $u RETURN s.uuid AS uuid"
        assert gr.id_spelling_variant(cy, {"u": "DFCI-4"}) is None

    def test_a_quoted_identifier_means_exactly_that(self):
        assert gr.quoted_in('Search for "DFCI-4" only', "DFCI-4")
        assert gr.quoted_in("Search for 'dfci-4'", "DFCI-4")
        assert not gr.quoted_in("Search for DFCI-4", "DFCI-4")

    def test_the_note_names_both_spellings(self):
        v = gr.id_spelling_variant(STUART, {"term": "DFCI-4"})
        note = gr.id_spelling_note(v)
        assert "'DFCI-4'" in note and "DFCI4" in note


# ---------------------------------------------------------------- the retry loop ----------------------------------
def _plan(cypher, parameters):
    return SimpleNamespace(cypher=cypher, parameters=parameters)


@pytest.fixture
def loop(monkeypatch):
    from chat_nextseek import orchestrator as orch
    calls = {"queries": [], "agent": 0}

    def fake_query(config, cypher, parameters):
        calls["queries"].append((cypher, dict(parameters or {})))
        return calls["answer"](cypher, parameters or {})

    def fake_agent(*a, **k):
        calls["agent"] += 1
        return _plan(STUART, {"term": "DFCI-4"})     # the agent keeps its zero, as it did on Nov 16

    monkeypatch.setattr(orch, "tool_neo4j_query", fake_query)
    monkeypatch.setattr(orch, "graph_agent", fake_agent)

    def run(question, answer):
        calls["answer"] = answer
        return orch._run_graph_with_retries({}, question, None, None, None,
                                            graph_plan=_plan(STUART, {"term": "DFCI-4"})), calls
    return run


EMPTY = {"ok": True, "count": 0, "data": []}
FOUND = {"ok": True, "count": 2, "data": [{"uuid": "PAT-241113DFC-4"}, {"uuid": "TIS-240408DFC-4"}]}


def test_a_zero_on_an_identifier_is_retried_with_the_tolerant_spelling_first(loop):
    run, calls = loop("Show me the sample tree for DFCI-4",
                      lambda cy, p: FOUND if "id_spelling" in p else EMPTY)
    assert run.graph_result["count"] == 2 and run.changed_answer
    assert calls["agent"] == 0                                   # no model call was needed
    assert [a["reason"] for a in run.attempts] == ["initial", "id_spelling"]
    assert "DFCI4" in (run.spelling_note or "")


def test_when_the_tolerant_spelling_also_finds_nothing_the_agents_retry_still_runs(loop):
    run, calls = loop("Show me the sample tree for DFCI-4", lambda cy, p: EMPTY)
    assert [a["reason"] for a in run.attempts] == ["initial", "id_spelling", "zero_rows"]
    assert calls["agent"] == 1 and run.spelling_note is None and run.graph_result["count"] == 0


def test_a_quoted_identifier_gets_no_tolerant_retry(loop):
    run, calls = loop('Show me the sample tree for "DFCI-4"', lambda cy, p: EMPTY)
    assert "id_spelling" not in [a["reason"] for a in run.attempts]


def test_an_answer_that_found_something_is_left_alone(loop):
    run, calls = loop("Show me the sample tree for DFCI-4", lambda cy, p: FOUND)
    assert [a["reason"] for a in run.attempts] == ["initial"] and run.spelling_note is None


# ---------------------------------------------------------------- the scope prover --------------------------------
STUART_TREE = ("MATCH (p:T_PAT)-[:IN_PROJECT]->(pr:Project) WHERE pr.title = $project_title AND "
               "toLower(p.search_text) CONTAINS toLower($kw) WITH DISTINCT p MATCH (d:Sample)-[:DERIVED_FROM*1..12]->(p) "
               "MATCH (d)-[:DERIVED_FROM]->(par:Sample) RETURN DISTINCT p.uuid AS patient_uuid, d.uuid AS uuid "
               "ORDER BY patient_uuid, uuid LIMIT 5000")


@pytest.mark.parametrize("cypher, params", [
    (STUART, {"term": "DFCI-4"}),
    (STUART_TREE, {"kw": "DFCI-4", "project_title": "Breakthrough_Cancer"}),
])
def test_a_member_login_may_run_the_rewritten_statement(cypher, params):
    """Every graph statement runs through the scope prover; the rewrite must stay provable for a member, like the
    statement the agent wrote."""
    from chat_nextseek.cypher_scope import Refused, scope_cypher
    from chat_nextseek.graph_scope import GraphScope
    member = GraphScope.for_projects([13, 2], source="test")
    assert not isinstance(scope_cypher(cypher, params, member), Refused)       # the agent's own statement
    v = gr.id_spelling_variant(cypher, params)
    scoped = scope_cypher(v.cypher, v.parameters, member)
    assert not isinstance(scoped, Refused), scoped

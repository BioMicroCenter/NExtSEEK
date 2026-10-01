"""A click on a reviewer chip reruns from the turn that offered it (operator ruling 2026-09-25).

Typed text keeps the whole pipeline. A click is a message whose text is exactly a chip's query on the very next turn
(``helpers/suggestions.accept``); the chip the server stored for that turn says what the click runs:

* ``direct`` ("Only RNA-Seq"): the statement the reviewer built from the turn's own, then the reviewer and the
  chatter. No entity agent, no parser, no graph agent.
* ``graph_agent`` (a value chip, "Only Converter"): the graph agent, handed the turn's statement and the one change
  (``orchestrator.CHIP_RERUN_CONTEXT``), then the reviewer and the chatter. No entity agent, no parser.

The statement never comes from the request: the chat panel sends only the chip's text, and what runs is the
session's own copy. It runs through ``tool_neo4j_query`` with the clicking request's config, so it is proven and
scoped for whoever clicks; a refusal, or stored context that does not load, gives the text the whole pipeline.

Every agent and tool is stubbed (``install_graph_turn_stubs``); the Neo4j stub runs the real scope prover.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from chat_nextseek import orchestrator as orch
from chat_nextseek.cypher_scope import Refused, scope_cypher
from chat_nextseek.graph_scope import SCOPE_PARAM, GraphScope, scope_of
from chat_nextseek.helpers import suggestions as sg
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan
from NessieAI.tests.chat_nextseek.test_graph_review_wiring import (
    CONVERTER_CYPHER,
    CONVERTER_Q,
    CONVERTER_ROWS,
    MEMBER,
    RNA_CATALOG,
    RNA_CYPHER,
    RNA_Q,
    _split_tool,
    install_graph_turn_stubs,
)
from NessieAI.tests.chat_nextseek.test_suggestions_wiring import CHIP_QUERY, CREDS, _cc_turn, _TurnConfig

OTHER = GraphScope.for_projects([7], source="test")
RNA_PARAMS = {"investigation_title": "TCGA"}
RNA_CHIP_QUERY = RNA_Q + " Count only Sequence Alignment Analysis records whose DataType is RNA-Seq."


@dataclass
class Calls:
    entity: int = 0
    parser: int = 0
    shortlist: int = 0
    graph_agent: list = field(default_factory=list)      # the refine_context of each graph agent call
    neo4j: list = field(default_factory=list)            # (cypher, parameters, the scope's project ids or None)


@dataclass
class Turn:
    debug: dict
    payload: dict
    calls: Calls


def _neo4j(calls: Calls):
    """The graph tool: proves the statement for the request's own scope with the real prover, refuses without one,
    and answers 10,517 for the statement with the value applied, 10,761 otherwise."""
    def tool(config, cypher, parameters=None, **kwargs):
        scope = scope_of(config)
        calls.neo4j.append((cypher, dict(parameters or {}), None if scope is None else list(scope.project_ids)))
        refused = {"ok": False, "error": "refused", "data": None, "cypher": cypher, "submitted_cypher": cypher,
                   "parameters": dict(parameters or {}),
                   "scope": {"decision": "refused", "source": "test", "project_ids": [], "injected": [],
                             "joined": [], "codes": ["no_scope"], "reasons": []}}
        if scope is None:
            return refused
        outcome = scope_cypher(cypher, parameters, scope)
        assert not isinstance(outcome, Refused), outcome.reasons
        n = 10517 if "review_value" in (parameters or {}) else 10761
        rows = CONVERTER_ROWS if "Classification" in cypher else [{"n": n}]
        return {"ok": True, "data": rows, "count": len(rows), "total": len(rows), "truncated": False, "limit": None,
                "cypher": outcome.cypher, "submitted_cypher": cypher, "parameters": outcome.parameters,
                "counters": {}, "scope": {"decision": outcome.decision, "source": "test",
                                          "project_ids": outcome.parameters.get(SCOPE_PARAM, []), "injected": [],
                                          "joined": [], "codes": [], "reasons": []}}
    return tool


@pytest.fixture
def click_turn(monkeypatch, tmp_path):
    """One NS turn through ``run_query`` over the RNA-Seq (or converter) stubs, with the request's scope."""

    def run(session, text, *, scope=MEMBER, question_cypher=RNA_CYPHER, parameters=None, parser_mode="graph_query"):
        calls = Calls()
        plan = GraphAgentPlan(cypher=question_cypher, parameters=dict(RNA_PARAMS if parameters is None else parameters),
                              context_mode="catalog")

        def _graph_agent(config, user_text, entity_result, parser_plan, retry_context=None, refine_context=None):
            calls.graph_agent.append(refine_context)
            return plan

        def _entity(*a, **k):
            calls.entity += 1
            return EntityAgentOutput()

        def _parser(*a, **k):
            calls.parser += 1
            return ParserPlan(mode=parser_mode, intent_summary=text)

        def _shortlist(*a, **k):
            calls.shortlist += 1
            return [], [], {}

        with monkeypatch.context() as m:
            install_graph_turn_stubs(m, cypher=question_cypher, rows=[{"n": 10761}], parameters=plan.parameters,
                                     catalog=RNA_CATALOG, count_tool=_split_tool([]), keep_append_turn=True)
            m.setattr(orch, "graph_agent", _graph_agent)
            m.setattr(orch, "tool_neo4j_query", _neo4j(calls))
            m.setattr(orch.pipeline_agent, "is_active", lambda session: False)
            m.setattr(orch.pipeline_agent, "snapshot_for_chat_log", lambda session: {})
            m.setattr(orch, "_ensure_query_log_dir", lambda session, config: str(tmp_path))
            m.setattr(orch, "ArtifactStore",
                      lambda log_dir: SimpleNamespace(register_path=lambda **k: None, write_json=lambda **k: None))
            m.setattr(orch, "shortlist_catalog", _shortlist)
            m.setattr(orch, "entity_agent", _entity)
            m.setattr(orch, "parser_agent", _parser)
            payload = orch.run_query(session, _TurnConfig(), text, lambda name, data=None: None,
                                     credentials=CREDS, graph_scope=scope)
        return Turn(debug=payload["debug"], payload=payload, calls=calls)

    return run


def _offer(click_turn, session, **kw):
    first = click_turn(session, RNA_Q, **kw)
    [chip] = first.debug["suggestions"]
    assert chip["label"] == "Only RNA-Seq" and chip["query"] == RNA_CHIP_QUERY and chip["expected_count"] == 10517
    return first, chip


# --------------------------------------------------------------------------- #
# direct: the reviewer's own statement
# --------------------------------------------------------------------------- #

def test_a_click_on_only_rna_seq_runs_the_stored_statement(click_turn):
    session: dict = {}
    first, chip = _offer(click_turn, session)
    assert "rerun" not in chip                                                   # the client's copy carries none
    [stored] = session[sg.SESSION_KEY]["items"]
    rerun = stored["rerun"]
    assert rerun["mode"] == "direct"
    assert "WHERE aln.DataType = $review_value }" in rerun["cypher"]
    assert rerun["cypher"].endswith("RETURN count(DISTINCT p) AS n")             # the model's form, not a row count
    assert rerun["parameters"] == {**RNA_PARAMS, "review_value": "RNA-Seq"}
    assert first.calls.entity == 1 and first.calls.parser == 1 and len(first.calls.graph_agent) == 1

    second = click_turn(session, RNA_CHIP_QUERY)
    assert second.calls.entity == 0 and second.calls.parser == 0 and second.calls.shortlist == 0
    assert second.calls.graph_agent == []                                        # no model wrote this statement
    assert [(c, p) for c, p, _s in second.calls.neo4j] == [(rerun["cypher"], rerun["parameters"])]
    assert second.debug["chip_rerun"] == {"mode": "direct", "suggestion_id": chip["id"]}
    assert second.debug["suggestion_accepted"] == {"id": chip["id"], "source": "reviewer", "kind": "narrow_value"}
    assert second.debug["graph_plan"]["cypher"] == rerun["cypher"] and second.debug["graph_context"] == "chip"
    assert second.debug["graph_review"]["verdict"] == "ok"                       # the value is applied now
    assert "suggestions" not in second.debug and sg.SESSION_KEY not in session   # chips never chain
    assert second.payload["bundle_id"] == first.payload["bundle_id"] + 1         # a graph turn like any other


def test_the_click_runs_under_the_clicking_scope_not_the_offering_one(click_turn):
    session: dict = {}
    _offer(click_turn, session, scope=MEMBER)
    second = click_turn(session, RNA_CHIP_QUERY, scope=OTHER)
    [(_cypher, _params, scoped_to)] = second.calls.neo4j
    assert scoped_to == [7]                                                      # proven and scoped for project 7


def test_a_click_without_a_scope_never_runs_the_statement_unscoped(click_turn):
    session: dict = {}
    _offer(click_turn, session)
    second = click_turn(session, RNA_CHIP_QUERY, scope=None, parser_mode="unsupported")
    # the stored statement was sent once, with no scope, and the tool refused it; the text then got the whole
    # pipeline (entity and parser ran), which this stub routes to an unsupported reply
    assert second.calls.neo4j[0][2] is None and second.calls.neo4j[0][0].count("$review_value") == 1
    assert second.calls.entity == 1 and second.calls.parser == 1
    assert "chip_rerun" not in second.debug


# --------------------------------------------------------------------------- #
# graph_agent: a value chip
# --------------------------------------------------------------------------- #

def test_a_value_chip_click_hands_the_graph_agent_the_statement_and_the_change(click_turn):
    session: dict = {}
    first = click_turn(session, CONVERTER_Q, question_cypher=CONVERTER_CYPHER, parameters={})
    [chip] = first.debug["suggestions"]
    assert chip["query"] == CHIP_QUERY
    second = click_turn(session, CHIP_QUERY, question_cypher=CONVERTER_CYPHER, parameters={})
    assert second.calls.entity == 0 and second.calls.parser == 0
    assert second.calls.graph_agent == [orch.CHIP_RERUN_CONTEXT.format(
        cypher=CONVERTER_CYPHER, parameters="{}",
        change="match Classification exactly 'Converter' instead of every value containing 'convert'.")]
    assert second.calls.graph_agent[0] == (
        "The user chose a suggested narrower search. Start from this statement, which answered the question "
        f"before:\n{CONVERTER_CYPHER}\nParameters: {{}}\nChange only this: match Classification exactly 'Converter' "
        "instead of every value containing 'convert'. Keep every other filter as it is.")
    assert second.debug["chip_rerun"] == {"mode": "graph_agent", "suggestion_id": chip["id"]}


# --------------------------------------------------------------------------- #
# what is not a click
# --------------------------------------------------------------------------- #

def test_any_turn_in_between_cancels_the_offer(click_turn):
    session: dict = {}
    _offer(click_turn, session)
    _cc_turn(session, "plot those by site")
    later = click_turn(session, RNA_CHIP_QUERY)
    assert "chip_rerun" not in later.debug and "suggestion_accepted" not in later.debug
    assert later.calls.entity == 1 and later.calls.parser == 1 and len(later.calls.graph_agent) == 1


@pytest.mark.parametrize("text", [RNA_CHIP_QUERY + "!", "Only RNA-Seq", RNA_CHIP_QUERY.lower()])
def test_text_that_is_not_the_chip_query_is_an_ordinary_turn(click_turn, text):
    session: dict = {}
    _offer(click_turn, session)
    typed = click_turn(session, text)
    assert "chip_rerun" not in typed.debug
    assert typed.calls.entity == 1 and typed.calls.parser == 1 and len(typed.calls.graph_agent) == 1


def test_stored_context_that_does_not_load_gives_the_text_the_whole_pipeline(click_turn):
    session: dict = {}
    _offer(click_turn, session)
    session[sg.SESSION_KEY]["items"][0]["rerun"]["entity"] = {"sampletypes": 5}      # not an entity output
    second = click_turn(session, RNA_CHIP_QUERY)
    assert "chip_rerun" not in second.debug
    assert second.calls.entity == 1 and second.calls.parser == 1


@pytest.mark.parametrize("rerun", [
    {"mode": "direct", "cypher": "", "parameters": {}},
    {"mode": "direct", "cypher": "MATCH (n) RETURN n"},                        # no parameters
    {"mode": "shell", "cypher": "MATCH (n) RETURN n", "parameters": {}},
    {"mode": "graph_agent", "base_cypher": "MATCH (n) RETURN n", "base_parameters": {}},   # no change
    "MATCH (n) RETURN n",
], ids=["empty", "no-parameters", "unknown-mode", "no-change", "not-a-dict"])
def test_only_the_two_rerun_shapes_are_kept(rerun):
    assert sg.clean_rerun(rerun) is None


def test_a_chip_keeps_its_rerun_only_in_the_session_copy():
    review = {"verdict": "suggest", "suggestion": {
        "kind": "narrow_value", "label": "Only RNA-Seq", "query": RNA_CHIP_QUERY, "reason": "r",
        "rerun": {"mode": "direct", "cypher": "MATCH (s:T_PAT) RETURN count(s) AS n", "parameters": {}}}}
    [chip] = sg.suggestions_from_review(review, bundle_id=3, refers_back=lambda q: None)
    assert chip["rerun"]["mode"] == "direct"
    assert "rerun" not in sg.public_chip(chip) and sg.public_chip(chip)["label"] == "Only RNA-Seq"

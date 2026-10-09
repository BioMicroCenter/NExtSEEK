"""Board step 14 (chip_memory): what an earlier step knew reaches the user and the next turn.

1. The reviewer reads a free-text term the graph agent wrote as a list (``any(t IN $terms WHERE ... CONTAINS t)``),
   so ``unapplied_value`` still checks the stored values and the chip fires (dev QueryTask 1917, 2026-10-09).
2. A chip a graph turn offered is saved with the turn, so a reloaded chat shows it again.
3. A follow-up turn hands the agent that answers it the earlier turn's set: the graph agent gets the earlier query,
   the pipeline wizard gets the samples the parser carried over (prod chat d01fdd43 turn 2, dev chat 0ddfba7a turn 2).
"""
from __future__ import annotations

from types import SimpleNamespace

from chat_nextseek import graph_review_counts as _g2
from chat_nextseek.graph_review import _free_text_terms, review_tier1
from chat_nextseek.pipeline import agent as pipeline_agent
from chat_nextseek.schemas.router import ParserPlan
from NessieAI.tests.chat_nextseek.test_chip_click import click_turn  # noqa: F401  (the fixture)
from NessieAI.tests.chat_nextseek.test_graph_review_tier1 import _catalog, _check, _inp, _live, _rec
from NessieAI.tests.chat_nextseek.test_graph_review_wiring import RNA_Q

# --------------------------------------------------------------------------- #
# 1. a list of terms is still free text
# --------------------------------------------------------------------------- #

# dev QueryTask 1917, word for word (2026-10-09, dev 80ef83f2): no chip, the reviewer ran in 0.6 s.
QT1917_CYPHER = ("MATCH (p:T_PAT)-[:IN_STUDY]->(:Study)-[:IN_INVESTIGATION]->(inv:Investigation)\n"
                 "WHERE inv.title = $inv\n"
                 " AND EXISTS {\n"
                 " MATCH (p)<-[:DERIVED_FROM*1..12]-(a:T_A_ALN)\n"
                 " WHERE any(t IN $terms WHERE toLower(a.search_text) CONTAINS t)\n"
                 " }\n"
                 "RETURN count(DISTINCT p) AS n")
QT1917_PARAMS = {"inv": "TCGA", "terms": ["rna-seq", "rnaseq", "rna seq"]}


def _qt1917():
    return {**_rec("r6-1225"), "cypher": QT1917_CYPHER, "parameters": QT1917_PARAMS}


def test_a_term_list_is_read_one_term_per_item():
    assert _free_text_terms(QT1917_CYPHER, QT1917_PARAMS) == [
        ("a", "$terms", "rna-seq"), ("a", "$terms", "rnaseq"), ("a", "$terms", "rna seq")]


def test_a_literal_term_list_is_read_too():
    cy = "MATCH (s:T_A_ALN) WHERE any(x IN ['rna-seq', 'RNAseq'] WHERE toLower(s.search_text) CONTAINS x) RETURN count(s)"
    assert [term for _v, _t, term in _free_text_terms(cy, {})] == ["rna-seq", "rnaseq"]


def test_the_list_form_offers_only_rna_seq():
    rv = review_tier1(_inp(_qt1917(), reply=False), _catalog(_qt1917()))
    assert _check(rv, "unapplied_value").fired, rv.checks
    assert rv.suggestion["label"] == "Only RNA-Seq"


def test_the_list_form_reads_the_stored_values_live(monkeypatch):
    config, _statements = _live(monkeypatch, _qt1917())
    provider = _g2.live_values(config)
    rv = review_tier1(_inp(_qt1917(), reply=False), provider)
    assert "T_A_ALN.DataType" in [c["key"] for c in provider.lookups()["calls"] if c["kind"] == "values"]
    assert _check(rv, "unapplied_value").fired and rv.suggestion["label"] == "Only RNA-Seq"


# --------------------------------------------------------------------------- #
# 2. the chip is saved with its turn
# --------------------------------------------------------------------------- #

def _reloaded(session: dict) -> list[dict]:
    """The turns a reloaded chat renders (``GET /assistant/sessions/{id}/?include=turns``)."""
    from nextseek_api.assistant.session_export import turn_rows
    stored = SimpleNamespace(results_history=session.get("results_history"), extra_state=session)
    return [row.payload for row in turn_rows(stored)]


def test_the_chip_a_graph_turn_offered_is_there_after_a_reload(click_turn):  # noqa: F811
    session: dict = {}
    live = click_turn(session, RNA_Q).debug["suggestions"]
    assert live and "rerun" not in live[0]
    [turn] = _reloaded(session)
    assert turn["suggestions"] == live


def test_a_turn_without_a_chip_reloads_without_one(click_turn):  # noqa: F811
    session: dict = {}
    click_turn(session, RNA_Q)
    click_turn(session, "How many projects are there?", parser_mode="unsupported")
    first, second = _reloaded(session)
    assert first["suggestions"] and not second.get("suggestions")


# --------------------------------------------------------------------------- #
# 3. a follow-up keeps the earlier turn's set
# --------------------------------------------------------------------------- #

NHP_Q = "what nhps have sequencing data associated with it?"
NHP_CYPHER = ("MATCH (s:T_NHP) WHERE EXISTS { MATCH (d:T_D_SEQ)-[:DERIVED_FROM*1..12]->(s) } "
              "RETURN s.id AS id, s.uuid AS uuid, s.type AS type ORDER BY id LIMIT 5000")
NHP_FOLLOWUP = "what studies exist for those 351 nhps?"


def test_a_graph_follow_up_hands_the_graph_agent_the_earlier_query(click_turn):  # noqa: F811
    """Prod chat d01fdd43 turn 2: the parser said graph_query (not a refine, not ask_about_last_results), and the
    graph agent wrote a query over every NHP because nothing of turn 1 reached it."""
    session: dict = {}
    click_turn(session, NHP_Q, question_cypher=NHP_CYPHER, parameters={})
    second = click_turn(session, NHP_FOLLOWUP, question_cypher=NHP_CYPHER, parameters={})
    [context] = second.calls.graph_agent
    assert context and NHP_CYPHER in context


def test_a_self_contained_question_gets_no_earlier_query(click_turn):  # noqa: F811
    session: dict = {}
    click_turn(session, NHP_Q, question_cypher=NHP_CYPHER, parameters={})
    second = click_turn(session, "How many TCGA patients are there?", question_cypher=NHP_CYPHER, parameters={})
    assert second.calls.graph_agent == [None]


def test_the_wizard_starts_with_the_samples_the_parser_carried_over(monkeypatch):
    """Dev chat 0ddfba7a turn 2: "Yes, build it me nf-core/rna-seq then". The parser copied turn 1's two UIDs into
    filters.uids; the wizard started from the new text alone and asked for samples."""
    monkeypatch.setattr(pipeline_agent, "_run_loop", lambda session, config, **k: {"action": "ask", "reply": ""})
    plan = ParserPlan(mode="reporter", filters={"uids": ["D.SEQ-250409KAM-2", "D.SEQ-250409KAM-20"]})
    session: dict = {}
    pipeline_agent.start(session, SimpleNamespace(), user_query="Yes, build it me nf-core/rna-seq then. Good catch",
                         parser_plan=plan)
    seed = session[pipeline_agent.PIPELINE_AGENT_KEY]["messages"][0]["content"]
    assert "D.SEQ-250409KAM-2" in seed and "D.SEQ-250409KAM-20" in seed

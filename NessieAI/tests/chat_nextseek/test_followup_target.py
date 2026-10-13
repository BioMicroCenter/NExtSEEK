"""Fix 4: a graph follow-up gets the earlier result the parser names, not a phrase-list guess at the newest one."""

from __future__ import annotations

from types import SimpleNamespace

from chat_nextseek import orchestrator as orch
from chat_nextseek.chat_memory import CHAT_LOG_KEY


def _bundle(bid, question, cypher, parameters=None):
    return {"id": bid, "user_query": question, "mode": "graph_query",
            "graph_plan": {"cypher": cypher, "parameters": parameters or {}}}


def _session(*bundles):
    """Each bundle is one turn; the last is the newest."""
    return {CHAT_LOG_KEY: [{"turn_id": i + 1, "bundle_id": b["id"]} for i, b in enumerate(bundles)],
            "results_history": list(bundles)}


FIRST = _bundle(1, "which mouse samples have imaging data?", "MATCH (s:T_MUS) RETURN s.id AS id", {"proj": "Alpha"})
SECOND = _bundle(2, "which of those have rna data?", "MATCH (s:T_MUS)-[:X]-(r:T_RNA) RETURN s.id AS id")
THIRD = _bundle(3, "how many labs are there?", "MATCH (l:Lab) RETURN count(l) AS n")


def _plan(target=None, summary="the question in the parser's words"):
    return SimpleNamespace(target_result_id=target, intent_summary=summary)


def test_the_parser_named_result_is_handed_over_whatever_the_wording():
    session = _session(FIRST, SECOND)
    context, debug = orch._followup_graph_context(
        session, "What else is known about the 129 UIDs you mentioned above?", _plan(target=1))
    assert FIRST["graph_plan"]["cypher"] in context and SECOND["graph_plan"]["cypher"] not in context
    assert '{"proj": "Alpha"}' in context
    assert "the question in the parser's words" in context
    assert debug["bundle_id"] == 1 and debug["by"] == "parser"


def test_the_named_result_wins_over_the_newest_when_the_count_points_at_an_older_search():
    session = _session(FIRST, SECOND, THIRD)
    context, debug = orch._followup_graph_context(session, "and the 343 samples, what assays do they have?",
                                                  _plan(target=2))
    assert SECOND["graph_plan"]["cypher"] in context and THIRD["graph_plan"]["cypher"] not in context
    assert debug["bundle_id"] == 2


def test_other_wordings_of_a_back_reference_work_when_the_parser_names_the_result():
    session = _session(FIRST, SECOND)
    for text in ("you listed some samples, which are female?", "for the same samples, list the labs",
                 "of the samples above, which are older than 5?"):
        context, debug = orch._followup_graph_context(session, text, _plan(target=1))
        assert FIRST["graph_plan"]["cypher"] in context and debug["bundle_id"] == 1


def test_the_phrase_list_is_the_backup_when_the_parser_names_nothing():
    session = _session(FIRST, SECOND)
    context, debug = orch._followup_graph_context(session, "what studies exist for those?", _plan())
    assert SECOND["graph_plan"]["cypher"] in context and debug["bundle_id"] == 2 and debug["by"] == "cue"


def test_a_question_that_names_no_earlier_result_gets_no_context():
    session = _session(FIRST, SECOND)
    assert orch._followup_graph_context(session, "How many labs are there?", _plan()) is None
    assert orch._followup_graph_context(session, "How many labs are there?") is None


def test_a_named_id_the_session_does_not_hold_falls_back_to_the_cue_not_the_newest():
    session = _session(FIRST, SECOND)
    assert orch._followup_graph_context(session, "How many labs are there?", _plan(target=99)) is None


def test_a_named_result_bound_to_uids_is_not_carried():
    bound = _bundle(1, "q", "MATCH (s) WHERE s.id IN $uids RETURN s.id AS id")
    assert orch._followup_graph_context(_session(bound), "the samples you listed", _plan(target=1)) is None


# --- R9: the parser-named lead keeps its escape, and REST prose stays out ----------------------------------------

def test_a_named_result_still_lets_a_question_that_names_its_own_set_ignore_it():
    session = _session(FIRST, SECOND)
    for text in ("How many mice from the Beta project have rna data?", "count the female rats in study Gamma"):
        context, _ = orch._followup_graph_context(session, text, _plan(target=1))
        assert "If the question names its own set, ignore the earlier search." in context


def test_a_parser_summary_that_names_an_endpoint_is_left_out():
    session = _session(FIRST, SECOND)
    for summary in ("Call /nextseek_api/samples/retrieve/ with the UIDs", "GET /nextseek_api/people/ for the list"):
        context, _ = orch._followup_graph_context(session, "those samples, which are female?",
                                                  _plan(target=1, summary=summary))
        assert "/nextseek_api/" not in context and "parser's reading" not in context
    context, _ = orch._followup_graph_context(session, "those samples, which are female?",
                                              _plan(target=1, summary="female samples among the earlier set"))
    assert "The parser's reading of this question: female samples among the earlier set" in context

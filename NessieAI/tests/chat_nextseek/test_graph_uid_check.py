"""A UID the user names is checked against the graph before the graph agent writes a query.

Pilot A v2 (2026-09-18) asked two questions about UIDs ending in -PUB: "How many samples
were derived directly from TIS-230830ENG-1-PUB?" and "What is the parent chain for
NHP-220830FLY-42-PUB". The local graph stores no UID with that suffix (0 of 1,084,754);
TIS-230830ENG-1 exists and has 938 direct children. Both turns queried the -PUB spelling,
found nothing, and the first reply said "0 samples are directly derived", a confident
answer about a sample the query never found. Other graphs have stored the suffix (the
2026-07-24 run returned D.SEQ-220823SHA-1..6-PUB), so the check goes both ways: with the
suffix stripped, and with a -PUB suffix added.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

from chat_nextseek import orchestrator as orch
from chat_nextseek.cypher_scope import Scoped, scope_cypher
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.helpers import uid_check
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan


def _rows(*rows):
    return {"ok": True, "count": len(rows), "data": list(rows)}


# --------------------------------------------------------------------------
# Finding the UIDs.
# --------------------------------------------------------------------------

def test_uids_come_from_the_filters_and_the_question_once_each():
    found = uid_check.uids_in("What is the parent chain for nhp-220830fly-42-PUB and NHP-220830FLY-42-PUB?",
                              ["NHP-220830FLY-42-PUB", "TIS-230830ENG-1"])

    assert found == ["NHP-220830FLY-42-PUB", "TIS-230830ENG-1"]


def test_a_question_without_a_uid_has_none():
    assert uid_check.uids_in("How many HeLa cell-line samples do we have?", []) == []


# --------------------------------------------------------------------------
# Reading the check query's rows.
# --------------------------------------------------------------------------

def test_a_uid_stored_under_its_own_spelling_needs_no_note():
    run = lambda config, cypher, params: _rows(
        {"uid": "TIS-230830ENG-1", "exact": True, "base_uuid": "TIS-230830ENG-1", "suffixed": []})
    checks = uid_check.check_uids(MagicMock(), ["TIS-230830ENG-1"], run=run)

    assert checks == [uid_check.UidCheck(asked="TIS-230830ENG-1", stored="TIS-230830ENG-1")]
    assert uid_check.uid_notes(checks) == (None, [])


def test_a_pub_uid_stored_without_its_suffix_is_renamed():
    run = lambda config, cypher, params: _rows(
        {"uid": "TIS-230830ENG-1-PUB", "exact": False, "base_uuid": "TIS-230830ENG-1", "suffixed": []})
    checks = uid_check.check_uids(MagicMock(), ["TIS-230830ENG-1-PUB"], run=run)
    agent_note, reply_notes = uid_check.uid_notes(checks)

    assert checks[0].stored == "TIS-230830ENG-1"
    assert "TIS-230830ENG-1-PUB" in agent_note and "use TIS-230830ENG-1" in agent_note
    assert any("TIS-230830ENG-1" in note and "TIS-230830ENG-1-PUB" in note for note in reply_notes)


def test_a_bare_uid_stored_with_a_pub_suffix_is_renamed():
    run = lambda config, cypher, params: _rows(
        {"uid": "D.SEQ-220823SHA-1", "exact": False, "base_uuid": None, "suffixed": ["D.SEQ-220823SHA-1-PUB"]})
    checks = uid_check.check_uids(MagicMock(), ["D.SEQ-220823SHA-1"], run=run)

    assert checks[0].stored == "D.SEQ-220823SHA-1-PUB"


def test_a_uid_missing_under_every_spelling_is_reported_as_not_found():
    run = lambda config, cypher, params: _rows(
        {"uid": "NHP-999999XXX-1-PUB", "exact": False, "base_uuid": None, "suffixed": []})
    checks = uid_check.check_uids(MagicMock(), ["NHP-999999XXX-1-PUB"], run=run)
    agent_note, reply_notes = uid_check.uid_notes(checks)

    assert checks[0].stored is None
    assert "was not found" in agent_note
    assert any("not found" in note for note in reply_notes)


def test_an_ambiguous_suffix_match_is_not_guessed():
    run = lambda config, cypher, params: _rows(
        {"uid": "MUS-1", "exact": False, "base_uuid": None, "suffixed": ["MUS-1-PUB", "MUS-1-PUB2"]})
    checks = uid_check.check_uids(MagicMock(), ["MUS-1"], run=run)

    assert checks[0].stored is None


def test_a_failed_check_claims_nothing():
    run = lambda config, cypher, params: {"ok": False, "error": "unavailable", "data": []}

    assert uid_check.check_uids(MagicMock(), ["TIS-230830ENG-1-PUB"], run=run) is None
    assert uid_check.uid_notes(None) == (None, [])


def test_the_check_query_is_read_only_and_parameterised():
    seen = {}

    def run(config, cypher, params):
        seen["cypher"], seen["params"] = cypher, params
        return _rows()

    uid_check.check_uids(MagicMock(), ["TIS-230830ENG-1-PUB"], run=run)

    assert "TIS-230830ENG-1-PUB" not in seen["cypher"]
    assert seen["params"]["checks"] == [{"uid": "TIS-230830ENG-1-PUB", "base": "TIS-230830ENG-1"}]
    for word in ("CREATE", "MERGE", "SET ", "DELETE", "REMOVE"):
        assert word not in seen["cypher"].upper()


def test_the_scope_prover_proves_the_check_for_a_caller_limited_to_projects():
    """7.1 refuses what it cannot prove; the first form of this query used a COLLECT subquery and was refused
    for every caller who is not a superuser. Every Sample node must carry the project clause."""
    out = scope_cypher(uid_check.CHECK_CYPHER, {"checks": [{"uid": "X-1-PUB", "base": "X-1"}]},
                       GraphScope.for_projects([2, 13], source="test"))

    assert isinstance(out, Scoped) and out.decision == "proven"
    assert sorted(out.injected) == ["a: sample clause", "b: sample clause", "p: sample clause"]


# --------------------------------------------------------------------------
# The graph turn: the agent is told before it writes, the reply is told before it speaks.
# --------------------------------------------------------------------------

def _turn(monkeypatch, tmp_path, *, uids, neo4j_results, user_text):
    calls = {"agent_contexts": [], "neo4j": 0}
    result_iter = iter(neo4j_results)

    def _agent(config, text, entity_result, plan, retry_context=None, refine_context=None):
        calls["agent_contexts"].append(refine_context)
        return GraphAgentPlan(cypher="MATCH (c:Sample)-[:DERIVED_FROM]->(p:Sample {uuid: $u}) RETURN count(c) AS n",
                              parameters={"u": "X"}, context_mode="catalog")

    def _neo4j(config, cypher, params=None):
        calls["neo4j"] += 1
        return next(result_iter)

    def _chatter(*a, **k):
        calls["query_notes"] = k.get("query_notes")
        return "reply"

    monkeypatch.setattr(orch, "graph_agent", _agent)
    monkeypatch.setattr(orch, "tool_neo4j_query", _neo4j)
    monkeypatch.setattr(orch, "chatter_agent_answer", _chatter)
    monkeypatch.setattr(orch, "append_turn", lambda *a, **k: None)

    config = MagicMock()
    config.MODEL_MODE = "test"
    debug: dict = {}
    plan = ParserPlan(mode="graph_query", intent_summary="children of a UID")
    plan.filters.uids = list(uids)
    orch._execute_graph_turn(
        config=config, session={}, user_text=user_text, entity_result=EntityAgentOutput(),
        plan=plan, log_dir=str(tmp_path),
        artifact_store=MagicMock(register_path=MagicMock(return_value=None)),
        send_event=lambda *a, **k: None, debug_payload=debug, t_total_start=time.perf_counter(),
    )
    return debug, calls


def test_the_graph_agent_and_the_reply_both_learn_the_stored_spelling(monkeypatch, tmp_path):
    debug, calls = _turn(
        monkeypatch, tmp_path, uids=["TIS-230830ENG-1-PUB"],
        user_text="How many samples were derived directly from TIS-230830ENG-1-PUB?",
        neo4j_results=[
            _rows({"uid": "TIS-230830ENG-1-PUB", "exact": False, "base_uuid": "TIS-230830ENG-1", "suffixed": []}),
            _rows({"n": 938}),
        ],
    )

    assert "use TIS-230830ENG-1" in calls["agent_contexts"][0]
    assert any("TIS-230830ENG-1-PUB" in note for note in calls["query_notes"])
    assert debug["uid_checks"] == [{"asked": "TIS-230830ENG-1-PUB", "stored": "TIS-230830ENG-1"}]


def test_a_missing_uid_reaches_the_reply_as_not_found(monkeypatch, tmp_path):
    _, calls = _turn(
        monkeypatch, tmp_path, uids=["NHP-999999XXX-1-PUB"],
        user_text="What is the parent chain for NHP-999999XXX-1-PUB",
        neo4j_results=[
            _rows({"uid": "NHP-999999XXX-1-PUB", "exact": False, "base_uuid": None, "suffixed": []}),
            _rows(), _rows(),
        ],
    )

    assert any("not found" in note for note in calls["query_notes"])


def test_a_turn_without_a_uid_runs_no_check(monkeypatch, tmp_path):
    debug, calls = _turn(
        monkeypatch, tmp_path, uids=[], user_text="How many samples are in the database?",
        neo4j_results=[_rows({"n": 1084754})],
    )

    assert calls["neo4j"] == 1
    assert calls["agent_contexts"] == [None]
    assert "uid_checks" not in debug

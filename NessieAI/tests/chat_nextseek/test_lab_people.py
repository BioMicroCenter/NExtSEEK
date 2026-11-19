"""Who a lab code names: the Scientist values stored on the samples whose UID carries the code.

A question for a lab code's person ("full name of the investigator with code X") was answered from the lab record
alone, which held only a surname, and the reply said no full name was recorded although the Scientist field of most
of the code's samples held one. The graph turn now reads the most common Scientist values on the code's samples
before the agent writes, and both the agent and the reply see them. Every code and name here is invented.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

from chat_nextseek import orchestrator as orch
from chat_nextseek.cypher_scope import Scoped, scope_cypher
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.helpers import lab_code
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan


def _rows(*rows):
    return {"ok": True, "count": len(rows), "data": list(rows)}


QQZ_ROWS = _rows({"scientist": "Ada Quill", "n": 14}, {"scientist": "A Quill", "n": 6})


# --------------------------------------------------------------------------
# Which questions ask for a code's person.
# --------------------------------------------------------------------------

def test_person_questions_about_a_code_are_recognised():
    for q in ("What is the full name of the investigator with code QQZ?", "Who is QQZ?",
              "Which PI does the code ZRV stand for?", "Name the scientist behind lab code ZRV"):
        assert lab_code.asks_lab_person(q), q


def test_a_lab_scoped_count_is_not_a_person_question():
    for q in ("How many tissue samples does the QQZ lab have?", "List RNA samples from ZRV with RIN above 8",
              "patients who are HIV positive from the QQZ lab", "RNA from the QQZ lab whose tissue came from a mouse",
              "samples from people with asthma in the ZRV lab", "name the protocol the QQZ lab used",
              "principal component scores for ZRV samples", "head of the femur samples from QQZ",
              "pi values in the QQZ lab",
              # review F7: PI as propidium iodide modifies the next word
              "How many PI-stained slides does code XYZ have?", "Show the PI staining results for lab code QQZ",
              "Show QQZ samples with PI uptake", "PI positive cells from code ZRV"):
        assert not lab_code.asks_lab_person(q), q


def test_pi_as_a_person_heads_its_phrase():
    for q in ("Name the PI of lab code QQZ", "What is the PI's name for code ZRV?", "code QQZ belongs to which PI",
              "who is the PI?", "Is the PI for QQZ known?", "List the PIs behind codes QQZ and ZRV"):
        assert lab_code.asks_lab_person(q), q


# --------------------------------------------------------------------------
# The read and its notes.
# --------------------------------------------------------------------------

def test_the_code_names_its_people_with_counts():
    seen = []

    def run(config, cypher, params=None, **kwargs):
        seen.append({**params, **kwargs})
        return QQZ_ROWS

    people = lab_code.lab_people(None, ["qqz"], run=run)
    agent, reply = lab_code.lab_people_notes(people)

    assert people == {"QQZ": [("Ada Quill", 14), ("A Quill", 6)]}
    assert seen[0]["pattern"] == "(?i)^[^-]+-[0-9]{6}QQZ-.*"
    assert seen[0]["timeout_s"] == lab_code.LAB_PEOPLE_TIMEOUT_S, "review F7: the scan is bounded in time"
    assert "Ada Quill (14 samples)" in agent and "A Quill (6 samples)" in agent
    assert len(reply) == 1 and "Ada Quill on 14 samples" in reply[0] and "Scientist field" in reply[0]
    assert "not necessarily the lab head" in reply[0], "review C26"


def test_the_reply_note_keeps_its_instruction_inside_the_note_cut_with_five_long_names():
    rows = _rows(*({"scientist": f"Bartholomew Quillington-Smythe {i}", "n": 1000 + i} for i in range(5)))
    _, reply = lab_code.lab_people_notes(lab_code.lab_people(None, ["QQZ"], run=lambda c, q, p=None, **k: rows))
    head = reply[0][:400]   # helpers.query_scope cuts a reply note at 400 characters
    assert "not necessarily the lab head" in head and "Scientist field" in head and "QQZ" in head


def test_a_code_whose_samples_hold_two_people_lists_both():
    rows = _rows({"scientist": "Bo Tarn", "n": 9}, {"scientist": "Cy Vell", "n": 5})
    people = lab_code.lab_people(None, ["ZRV"], run=lambda c, q, p=None, **k: rows)
    agent, reply = lab_code.lab_people_notes(people)

    assert "Bo Tarn (9 samples)" in agent and "Cy Vell (5 samples)" in agent
    assert "Bo Tarn on 9 samples" in reply[0] and "Cy Vell on 5" in reply[0]


def test_a_failed_or_empty_read_claims_nothing():
    assert lab_code.lab_people(None, ["QQZ"], run=lambda c, q, p=None, **k: {"ok": False}) is None
    assert lab_code.lab_people_notes(None) == (None, [])
    empty = lab_code.lab_people(None, ["QQZ"], run=lambda c, q, p=None, **k: _rows())
    assert lab_code.lab_people_notes(empty) == (None, [])


def test_a_value_that_is_not_a_three_letter_code_never_enters_the_regex():
    calls = []
    out = lab_code.lab_people(None, ["Q.Z", "QQZ|.*", ""], run=lambda c, q, p=None, **k: calls.append(p) or _rows())
    assert out == {} and calls == []


def test_the_scope_prover_proves_the_read_for_a_caller_limited_to_projects():
    out = scope_cypher(lab_code.LAB_PEOPLE_CYPHER, {"pattern": "(?i)^[^-]+-[0-9]{6}QQZ-.*", "top": 5},
                       GraphScope.for_projects([2, 13], source="test"))

    assert isinstance(out, Scoped) and out.decision == "proven", getattr(out, "reasons", None)


# --------------------------------------------------------------------------
# The graph turn: the agent and the reply both see the names.
# --------------------------------------------------------------------------

def _turn(monkeypatch, tmp_path, *, user_text, lab_codes, neo4j_results, graph_plan=None):
    calls = {"agent_contexts": [], "neo4j": 0}
    result_iter = iter(neo4j_results)

    def _agent(config, text, entity_result, plan, retry_context=None, refine_context=None):
        calls["agent_contexts"].append(refine_context)
        return GraphAgentPlan(cypher="MATCH (s:Sample) RETURN s.type AS type, count(*) AS n",
                              parameters={}, context_mode="catalog")

    def _neo4j(config, cypher, params=None, **kwargs):
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
    plan = ParserPlan(mode="graph_query", intent_summary="a lab code")
    plan.filters.lab_codes = list(lab_codes)
    orch._execute_graph_turn(
        config=config, session={}, user_text=user_text,
        entity_result=EntityAgentOutput(labs=["Quill"], lab_codes=list(lab_codes)),
        plan=plan, log_dir=str(tmp_path),
        artifact_store=MagicMock(register_path=MagicMock(return_value=None)),
        send_event=lambda *a, **k: None, debug_payload=debug, t_total_start=time.perf_counter(),
        graph_plan=graph_plan,
    )
    return debug, calls


def test_full_name_for_a_code_reaches_the_agent_and_the_reply(monkeypatch, tmp_path):
    debug, calls = _turn(
        monkeypatch, tmp_path, user_text="What is the full name of the investigator with code QQZ?",
        lab_codes=["QQZ"], neo4j_results=[QQZ_ROWS, _rows({"type": "TIS", "n": 20})],
    )

    assert "Ada Quill (14 samples)" in calls["agent_contexts"][0]
    assert any("Ada Quill on 14 samples" in note for note in calls["query_notes"])
    assert debug["lab_people"] == {"QQZ": [["Ada Quill", 14], ["A Quill", 6]]}


def test_a_lab_scoped_count_runs_no_extra_read(monkeypatch, tmp_path):
    debug, calls = _turn(
        monkeypatch, tmp_path, user_text="How many tissue samples does the QQZ lab have?",
        lab_codes=["QQZ"], neo4j_results=[_rows({"type": "TIS", "n": 20})],
    )

    assert calls["neo4j"] == 1
    assert calls["agent_contexts"] == [None]
    assert "lab_people" not in debug


def test_a_filter_with_a_relative_who_runs_no_extra_read(monkeypatch, tmp_path):
    _, calls = _turn(
        monkeypatch, tmp_path, user_text="Show patients who are HIV positive from the QQZ lab",
        lab_codes=["QQZ"], neo4j_results=[_rows({"type": "PAT", "n": 3})],
    )

    assert calls["neo4j"] == 1 and calls["agent_contexts"] == [None]


def test_a_chip_click_runs_its_own_statement_and_no_people_read(monkeypatch, tmp_path):
    """Review F7c: a chip's statement is already written; the people read would only cost a scan."""
    chip = GraphAgentPlan(cypher="MATCH (s:Sample) RETURN s.type AS type, count(*) AS n", parameters={},
                          context_mode="catalog")
    debug, calls = _turn(
        monkeypatch, tmp_path, user_text="What is the full name of the investigator with code QQZ?",
        lab_codes=["QQZ"], neo4j_results=[_rows({"type": "TIS", "n": 20})], graph_plan=chip,
    )
    assert calls["neo4j"] == 1 and "lab_people" not in debug

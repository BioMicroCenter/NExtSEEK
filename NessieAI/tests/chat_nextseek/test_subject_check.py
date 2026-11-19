"""A named subject resolves to its one record, then its tree, before the graph agent writes a query.

Production 2026-11-17: "show me the sample tree for <patient name>" matched the name as free text over every record.
Hundreds of records mentioned it, their trees were combined, matches under another patient came along, and the
patient's own descendants that never repeat the name were missed. The fixture here is invented: a patient named
AB1.XY7 with 30 descendants (10 repeat the name), a visit under it whose Name carries the name too, and a second
patient whose 3 descendants mention it.
"""
from __future__ import annotations

import re
import time
from unittest.mock import MagicMock

from chat_nextseek import orchestrator as orch
from chat_nextseek.cypher_scope import Scoped, scope_cypher
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.helpers import subject_check as sc
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan

SOURCE_CATALOG = [{"SampleType": "PAT", "Clade": "Source"}, {"SampleType": "PAV", "Clade": "Source"},
                  {"SampleType": "NHP", "Clade": "Source"}, {"SampleType": "TIS", "Clade": "Processed"}]


def _graph():
    """uuid -> (type, Name, ID, parent uuid)."""
    g = {"PAT-990101ZZZ-1": ("PAT", "AB1.XY7", None, None),
         "PAV-990101ZZZ-1": ("PAV", "AB1.XY7 visit 1", None, "PAT-990101ZZZ-1"),
         "PAT-990101ZZZ-2": ("PAT", "AB1.XY8", None, None),
         "NHP-990101ZZZ-1": ("NHP", "Q9", "77001", None)}
    for i in range(30):
        g[f"TIS-990101ZZZ-{i}"] = ("TIS", f"AB1.XY7 slice {i}" if i < 10 else f"slice {i}", None, "PAV-990101ZZZ-1")
    for i in range(3):
        g[f"TIS-990101ZZZ-{100 + i}"] = ("TIS", f"compared with XY7 {i}", None, "PAT-990101ZZZ-2")
    return g


def _ancestors(g, uid):
    out, parent = [], g[uid][3]
    while parent:
        out.append(parent)
        parent = g[parent][3]
    return out


def fake_neo4j(g):
    """Answers the two subject-check statements over the fixture as Neo4j would (``=~`` is a whole-string match,
    which ``re.fullmatch`` reads the same way); anything else is the graph agent's statement."""
    calls = []

    def run(config, cypher, params=None):
        calls.append((cypher, params))
        if cypher == sc.LOOKUP_CYPHER:
            p = re.compile(params["pattern"])
            rows = [{"uuid": u, "type": t, "name": n, "id": i} for u, (t, n, i, _) in sorted(g.items())
                    if t in params["types"] and any(v is not None and p.fullmatch(str(v).lower())
                                                    for v in (n, i, u))]
            return {"ok": True, "count": len(rows), "data": rows[:params["cap"]]}
        if cypher == sc.DERIVED_CYPHER:
            uids = set(params["uids"])
            rows = [{"uuid": u} for u in params["uids"] if uids & set(_ancestors(g, u))]
            return {"ok": True, "count": len(rows), "data": rows}
        uid = (params or {}).get("uid")
        rows = [{"uuid": u, "type": g[u][0]} for u in g if uid in _ancestors(g, u)]
        return {"ok": True, "count": len(rows), "data": rows}
    run.calls = calls
    return run


def _plan(keywords, code=None):
    plan = ParserPlan(mode="graph_query", intent_summary="tree of a named subject")
    plan.filters.keywords = list(keywords)
    plan.filters.sampletype_code = code
    plan.resolved.keywords = list(keywords)
    return plan


def _config():
    config = MagicMock()
    config.FULL_SAMPLETYPES = SOURCE_CATALOG
    config.MODEL_MODE = "test"
    return config


# --- the lookup ------------------------------------------------------------------------------------------------------

def test_a_dashed_name_resolves_to_the_one_source_record_not_the_records_that_mention_it():
    check = sc.check_subject(_config(), "sample tree for XY-7", _plan(["XY-7"]), run=fake_neo4j(_graph()))
    assert check is not None
    assert [r["uuid"] for r in check.roots] == ["PAT-990101ZZZ-1"]   # the visit under it dropped out
    assert check.types == ("PAT", "PAV", "NHP")                      # Source clade, from the catalog


def test_sibling_an_animal_number_written_with_a_dash_finds_the_number_stored_without_one():
    check = sc.check_subject(_config(), "show me the samples from animal 77-001", _plan(["77-001"]),
                             run=fake_neo4j(_graph()))
    assert [r["uuid"] for r in check.roots] == ["NHP-990101ZZZ-1"]


def test_a_named_type_is_searched_alone():
    check = sc.check_subject(_config(), "tree for patient XY-7", _plan(["XY-7"], code="PAT"),
                             run=fake_neo4j(_graph()))
    assert check.types == ("PAT",) and [r["uuid"] for r in check.roots] == ["PAT-990101ZZZ-1"]


def test_a_mention_search_is_not_a_subject_and_runs_no_lookup():
    run = fake_neo4j(_graph())
    assert sc.check_subject(_config(), "samples mentioning XY-7", _plan(["XY-7"]), run=run) is None
    assert sc.check_subject(_config(), "Search for XY-7", _plan(["XY-7"]), run=run) is None
    assert run.calls == []


def test_a_word_or_a_single_number_is_not_a_subject_name():
    run = fake_neo4j(_graph())
    assert sc.check_subject(_config(), "sample tree for monkey 45512", _plan(["45512"]), run=run) is None
    assert sc.check_subject(_config(), "lineage of lung tissue", _plan(["lung"]), run=run) is None
    assert run.calls == []


def test_two_separate_records_are_listed_and_the_reply_asks():
    g = _graph()
    g["PAT-990101ZZZ-3"] = ("PAT", "CD2.XY7", None, None)
    check = sc.check_subject(_config(), "sample tree for XY7", _plan(["XY7"]), run=fake_neo4j(g))
    agent, reply = sc.subject_notes(check)
    assert len(check.roots) == 2
    assert "PAT-990101ZZZ-1" in reply[0] and "PAT-990101ZZZ-3" in reply[0] and "ask which" in reply[0]


def test_no_match_says_so_before_a_labelled_text_search():
    check = sc.check_subject(_config(), "sample tree for QQ-404", _plan(["QQ-404"]), run=fake_neo4j(_graph()))
    agent, reply = sc.subject_notes(check)
    assert check.roots == ()
    assert "No PAT, PAV, NHP record" in reply[0] and "text search" in reply[0]
    assert "search_text" in agent


def test_a_failed_read_claims_nothing():
    def broken(config, cypher, params=None):
        return {"ok": False, "error": "refused"}
    assert sc.check_subject(_config(), "sample tree for XY-7", _plan(["XY-7"]), run=broken) is None
    assert sc.subject_notes(None) == (None, [])


def test_both_statements_are_read_only_and_proven_for_a_member():
    for cypher, params in ((sc.LOOKUP_CYPHER, {"types": ["PAT"], "pattern": ".*", "cap": 50}),
                           (sc.DERIVED_CYPHER, {"uids": ["PAT-990101ZZZ-1"]})):
        for word in ("CREATE", "MERGE", "SET ", "DELETE", "REMOVE"):
            assert word not in cypher.upper()
        out = scope_cypher(cypher, params, GraphScope.for_projects([2, 13], source="test"))
        assert isinstance(out, Scoped) and out.decision == "proven", out


# --- the graph turn --------------------------------------------------------------------------------------------------

def _turn(monkeypatch, tmp_path, *, user_text, plan, g):
    seen = {}
    run = fake_neo4j(g)

    def _agent(config, text, entity_result, plan, retry_context=None, refine_context=None):
        seen["plan"], seen["context"] = plan, refine_context
        uid = (plan.filters.uids or [None])[0]
        return GraphAgentPlan(cypher="MATCH (d:Sample)-[:DERIVED_FROM*1..12]->(:Sample {uuid: $uid}) "
                                     "RETURN DISTINCT d.uuid AS uuid, d.type AS type",
                              parameters={"uid": uid}, context_mode="catalog")

    def _chatter(*a, **k):
        seen["query_notes"] = k.get("query_notes")
        return "reply"

    monkeypatch.setattr(orch, "graph_agent", _agent)
    monkeypatch.setattr(orch, "tool_neo4j_query", run)
    monkeypatch.setattr(orch, "chatter_agent_answer", _chatter)
    monkeypatch.setattr(orch, "append_turn", lambda *a, **k: None)
    debug: dict = {}
    orch._execute_graph_turn(
        config=_config(), session={}, user_text=user_text, entity_result=EntityAgentOutput(), plan=plan,
        log_dir=str(tmp_path), artifact_store=MagicMock(register_path=MagicMock(return_value=None)),
        send_event=lambda *a, **k: None, debug_payload=debug, t_total_start=time.perf_counter())
    return debug, seen


def test_the_tree_of_a_dashed_name_anchors_on_its_one_uid_and_returns_its_descendants(monkeypatch, tmp_path):
    debug, seen = _turn(monkeypatch, tmp_path, user_text="sample tree for XY-7", plan=_plan(["XY-7"]), g=_graph())

    assert seen["plan"].filters.uids == ["PAT-990101ZZZ-1"]
    assert "XY-7" not in seen["plan"].filters.keywords and "XY-7" not in seen["plan"].resolved.keywords
    assert "SUBJECT CHECK" in seen["context"] and "PAT-990101ZZZ-1" in seen["context"]
    descendants = [a for a in debug["graph_attempts"] if a.get("reason") == "initial"][0]
    assert descendants["count"] == 31                      # the visit and the 30 below it, none of the 3 mentions
    assert any("/seek/sampletree/uid=PAT-990101ZZZ-1/" in n for n in seen["query_notes"])
    assert debug["subject_check"]["roots"] == ["PAT-990101ZZZ-1"]


def test_a_turn_without_a_named_subject_runs_no_lookup(monkeypatch, tmp_path):
    debug, seen = _turn(monkeypatch, tmp_path, user_text="How many samples mention lung?", plan=_plan(["lung"]),
                        g=_graph())
    assert "subject_check" not in debug and seen["context"] is None

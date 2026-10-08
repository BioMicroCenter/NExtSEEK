"""Round 6: the graph op runs the UID check the aggregate runs (one read-only query) before its graph agent.

Synthetic UIDs only. The chain (``run_graph_question``) is faked; the check itself runs through the stand-in
``neo4j_exec`` the way ``aggregate._prelude`` runs it.
"""
from types import SimpleNamespace
from unittest.mock import patch

from chat_nextseek.schemas import GraphAgentPlan, ParserFilters, ParserPlan
from NessieAI.ns import granular


def _answer():
    return granular.GraphAnswer(plan={}, result={"ok": True, "data": []}, fallback=None, parser_plan=None, cypher=None)


def test_the_graph_op_resolves_a_uid_typed_without_pub(monkeypatch):
    seen = {}

    def fake_chain(query, **kw):
        seen["refine_context"] = kw.get("refine_context")
        return _answer()

    def exec_fn(config, cypher, params):
        return {"ok": True, "data": [{"uid": "TIS-220101ABC-7", "exact": False, "base_uuid": None,
                                      "suffixed": ["TIS-220101ABC-7-PUB"]}]}

    monkeypatch.setattr(granular, "run_graph_question", fake_chain)
    out = granular._graph({"query": "parents of TIS-220101ABC-7"}, None, None, lambda *a: None, exec_fn, None,
                          limit_s=90.0)
    assert "TIS-220101ABC-7-PUB" in seen["refine_context"]
    assert any("TIS-220101ABC-7-PUB" in note for note in out["notes"])


def test_a_uid_not_found_says_so(monkeypatch):
    def fake_chain(query, **kw):
        assert "was not found" in kw["refine_context"]
        return _answer()

    def exec_fn(config, cypher, params):
        return {"ok": True, "data": [{"uid": "TIS-220101ABC-8", "exact": False, "base_uuid": None, "suffixed": []}]}

    monkeypatch.setattr(granular, "run_graph_question", fake_chain)
    out = granular._graph({"query": "parents of TIS-220101ABC-8"}, None, None, lambda *a: None, exec_fn, None)
    assert any("was not found" in note for note in out["notes"])


def test_a_failed_check_claims_nothing(monkeypatch):
    def fake_chain(query, **kw):
        assert kw.get("refine_context") is None
        return _answer()

    def exec_fn(config, cypher, params):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(granular, "run_graph_question", fake_chain)
    out = granular._graph({"query": "parents of TIS-220101ABC-7"}, None, None, lambda *a: None, exec_fn, None)
    assert "notes" not in out


def test_a_question_without_a_uid_runs_no_check(monkeypatch):
    def fake_chain(query, **kw):
        assert kw.get("refine_context") is None
        return _answer()

    def exec_fn(config, cypher, params):
        raise AssertionError("no UID, no check query")

    monkeypatch.setattr(granular, "run_graph_question", fake_chain)
    out = granular._graph({"query": "how many tissue samples"}, None, None, lambda *a: None, exec_fn, None)
    assert "notes" not in out


def test_the_parser_plan_carries_the_stored_spelling():
    """As the NS path (R4, ``plan_with_stored_uids``): the parser plan's filters.uids, handed to the graph agent and
    back in ``parser_plan`` for nextseek-api-read, name the UID as the graph stores it, matching the UID CHECK note."""
    plan = ParserPlan(mode="graph_query", filters=ParserFilters(uids=["TIS-220101ABC-7"]))
    seen = {}

    def graph_agent(config, query, entity_out, parser_plan, **kw):
        seen["uids"] = list(parser_plan.filters.uids)
        seen["note"] = kw.get("refine_context")
        return GraphAgentPlan(cypher="MATCH (s:Sample) RETURN count(s) AS n", parameters={})

    def exec_fn(config, cypher, params):
        if "checks" in params:
            return {"ok": True, "data": [{"uid": "TIS-220101ABC-7", "exact": False, "base_uuid": None,
                                          "suffixed": ["TIS-220101ABC-7-PUB"]}]}
        return {"ok": True, "data": [{"n": 1}]}

    with patch("chat_nextseek.portable.entity_agent", return_value=SimpleNamespace(model_dump=lambda: {})), \
         patch("chat_nextseek.portable.parser_agent", return_value=plan), \
         patch("chat_nextseek.portable.graph_agent", side_effect=graph_agent):
        out = granular._graph({"query": "parents of TIS-220101ABC-7"}, None, None, lambda *a: None, exec_fn, None,
                              limit_s=90.0)
    assert seen["uids"] == ["TIS-220101ABC-7-PUB"]
    assert "TIS-220101ABC-7-PUB" in seen["note"]
    assert out["parser_plan"]["filters"]["uids"] == ["TIS-220101ABC-7-PUB"]
    assert plan.filters.uids == ["TIS-220101ABC-7"], "the parser's own plan (a turn's cached one) is not changed"


def test_a_uid_only_in_the_agents_plan_is_checked_and_written_as_stored():
    """Round 7 (T1): the agent may name the UID in its plan's filters.uids and not in the question."""
    seen = {}

    def graph_agent(config, query, entity_out, parser_plan, **kw):
        seen["uids"] = list(parser_plan.filters.uids)
        return GraphAgentPlan(cypher="MATCH (s:Sample) RETURN count(s) AS n", parameters={})

    def exec_fn(config, cypher, params):
        if "checks" in params:
            return {"ok": True, "data": [{"uid": c["uid"], "exact": False, "base_uuid": None,
                                          "suffixed": [c["uid"] + "-PUB"]} for c in params["checks"]]}
        return {"ok": True, "data": [{"n": 1}]}

    plan = '{"filters": {"uids": ["TIS-220101ABC-7"]}}'
    with patch("chat_nextseek.portable.entity_agent", return_value=SimpleNamespace(model_dump=lambda: {})), \
         patch("chat_nextseek.portable.parser_agent", side_effect=AssertionError("no parser call")), \
         patch("chat_nextseek.portable.graph_agent", side_effect=graph_agent):
        out = granular._graph({"query": "parents of that sample", "plan": plan}, None, None, lambda *a: None,
                              exec_fn, None, limit_s=90.0)
    assert seen["uids"] == ["TIS-220101ABC-7-PUB"]
    assert out["parser_plan"]["filters"]["uids"] == ["TIS-220101ABC-7-PUB"]

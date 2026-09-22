"""
The graph turn under a project scope: the scope reaches the config, and a refused query falls back to graph_search.

Spec docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 4.2, 7 and 9:

- ``run_query``, ``run_query_plan`` and ``run_pipeline_launch`` take ``graph_scope``; ``_identity_gate`` puts it on a
  per-request copy (a ``GraphScope`` as is, a mapping through ``from_plain``, anything else or a malformed mapping as
  ``None``, which refuses). Leaving the keyword out keeps the config's own scope: that is how single-operator
  surfaces set theirs.
- A scope refusal ends the generate-execute loop before any retry. When the final result is a scope refusal, the
  NS turn answers through the REST branch against graph_search: the plan becomes ``new_search`` on
  ``GRAPH_SEARCH_ENDPOINT``, the chatter is told why (``SCOPE_FALLBACK_NOTE``) and the reply ends with
  ``SCOPE_FALLBACK_FOOTER`` whatever the chatter wrote. The graph-origin refine falls back the same way.
- A proven query never falls back, and a write refusal keeps today's retry.
- The debug payload records the Cypher that ran and the decision for every attempt.

Every agent and tool is stubbed; no model, Neo4j or network call is made.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from chat_nextseek import orchestrator as orch
from chat_nextseek.agents.planner import tools as planner_tools
from chat_nextseek.graph_scope import SCOPE_ATTR, GraphScope, scope_of
from chat_nextseek.helpers.tools import nextseek_api as api_tool
from chat_nextseek.helpers.tools.neo4j import NO_SCOPE_REFUSED, SCOPE_REFUSED
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan
from chat_nextseek.schemas.tools import APIRequestPlan

CREDS = {"api_user": "someone", "api_pass": "secret"}
REFUSED_CYPHER = "MATCH (a:Attribute) RETURN a.title AS title"


# --------------------------------------------------------------------------- #
# Results as the tool returns them
# --------------------------------------------------------------------------- #

def _scope(decision, codes=(), reasons=()):
    return {"decision": decision, "source": "request", "project_ids": [2], "injected": [], "joined": [],
            "codes": list(codes), "reasons": list(reasons)}


def _refused(cypher=REFUSED_CYPHER):
    return {"ok": False, "error": f"{SCOPE_REFUSED} Reasons: line 1, column 8: not allowed.", "data": None,
            "cypher": cypher, "submitted_cypher": cypher, "parameters": {},
            "scope": _scope("refused", ["label_not_allowed"], ["line 1, column 8: not allowed"])}


def _no_scope(cypher="MATCH (s:T_TIS) RETURN count(s) AS n"):
    return {"ok": False, "error": NO_SCOPE_REFUSED, "data": None, "cypher": cypher, "submitted_cypher": cypher,
            "parameters": {}, "scope": _scope("refused", ["no_scope"], ["the request carries no project scope"])}


def _proven(count, cypher="MATCH (s:T_TIS) RETURN count(s) AS n", data=None):
    ran = cypher + " /* scoped */"
    return {"ok": True, "count": count, "total": count, "truncated": False, "limit": None,
            "data": data if data is not None else [{"n": 1}] * count, "cypher": ran, "submitted_cypher": cypher,
            "parameters": {"__scope_projects": [2]}, "counters": {}, "scope": _scope("proven")}


def _error(cypher="BAD"):
    return {"ok": False, "error": "Variable `x` not defined", "data": None, "cypher": cypher + " /* scoped */",
            "submitted_cypher": cypher, "parameters": {}, "scope": _scope("proven")}


def _write(cypher="MATCH (s) DELETE s"):
    return {"ok": False, "error": "Write operations are not permitted; only read (MATCH/RETURN) queries are "
            "allowed. Refused: DELETE.", "data": None, "cypher": cypher, "submitted_cypher": cypher,
            "parameters": {}, "scope": _scope("not_checked", ["write"], ["write check: DELETE"])}


# --------------------------------------------------------------------------- #
# The constants
# --------------------------------------------------------------------------- #

def test_the_fallback_constants():
    assert orch.GRAPH_SEARCH_ENDPOINT == "/nextseek_api/samples/graph_search/"
    assert orch.SCOPE_FALLBACK_NOTE == (
        "The graph query written for this question could not be confirmed to stay within the user's projects, so "
        "it was not run. This answer comes from the project-scoped sample search instead. Say so, and say which "
        "conditions of the question that search could not apply.")
    assert orch.SCOPE_FALLBACK_FOOTER == (
        "Note: this answer comes from the project-scoped sample search, because the graph query for it could not "
        "be confirmed to stay within your projects. That search cannot express every condition a graph query can.")


def test_the_fallback_endpoint_is_a_read_the_api_tool_allows():
    assert api_tool._READ_POST_PATHS.__contains__(orch.GRAPH_SEARCH_ENDPOINT)


def test_the_fallback_record_is_frozen_plain_data():
    fields = [f.name for f in dataclasses.fields(orch.GraphScopeFallback)]
    assert fields == ["codes", "reasons", "submitted_cypher", "attempts"]
    record = orch.GraphScopeFallback(codes=("union",), reasons=("r",), submitted_cypher="X", attempts=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.codes = ()


# --------------------------------------------------------------------------- #
# graph_scope reaches the per-request config
# --------------------------------------------------------------------------- #

class _Stop(Exception):
    pass


class _Config:
    API_USER = "service"
    API_PASS = "service-pw"
    NEXTSEEK_ALLOW_SERVICE_ACCOUNT_FALLBACK = False


def _config_seen(entry, config, **kwargs):
    """Run one entry point until its first step after the identity gate; return the config that step received."""
    seen = {}

    def capture(session, cfg):
        seen["config"] = cfg
        raise _Stop

    with patch.object(orch, "_ensure_query_log_dir", side_effect=capture):
        with pytest.raises(_Stop):
            entry({}, config, "how many tissue samples", None, **kwargs)
    return seen["config"]


ENTRIES = [orch.run_query, orch.run_query_plan, orch.run_pipeline_launch]
ENTRY_IDS = ["run_query", "run_query_plan", "run_pipeline_launch"]


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRY_IDS)
def test_a_plain_scope_is_set_on_a_copy(entry):
    config = _Config()

    seen = _config_seen(entry, config, credentials=CREDS, graph_scope={"is_admin": False, "project_ids": [13, 2]})

    assert seen is not config
    assert scope_of(seen) == GraphScope(is_admin=False, project_ids=(2, 13), source="request")
    assert not hasattr(config, SCOPE_ATTR)


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRY_IDS)
def test_a_graph_scope_is_used_as_it_is(entry):
    scope = GraphScope.admin("test")

    seen = _config_seen(entry, _Config(), credentials=CREDS, graph_scope=scope)

    assert scope_of(seen) is scope


@pytest.mark.parametrize("value", [None, {"is_admin": "yes"}, {"is_admin": False, "project_ids": ["2"]},
                                   {"is_admin": False, "project_ids": [True]}, "admin", 7],
                         ids=["None", "string admin flag", "string id", "bool id", "a string", "an int"])
def test_anything_else_stores_no_scope_even_over_the_configs_own(value, caplog):
    config = _Config()
    setattr(config, SCOPE_ATTR, GraphScope.admin("test"))

    with caplog.at_level(logging.WARNING):
        seen = _config_seen(orch.run_query, config, credentials=CREDS, graph_scope=value)

    assert seen is not config
    assert hasattr(seen, SCOPE_ATTR) and getattr(seen, SCOPE_ATTR) is None
    assert scope_of(config) == GraphScope.admin("test")


def test_leaving_the_keyword_out_keeps_the_configs_own_scope():
    """The single-operator surfaces put their scope on their own config and pass credentials=None."""
    config = _Config()
    setattr(config, SCOPE_ATTR, GraphScope.admin("cli"))

    seen = _config_seen(orch.run_query, config)

    assert scope_of(seen) == GraphScope.admin("cli")


def test_a_single_operator_call_with_a_scope_still_copies():
    config = _Config()

    seen = _config_seen(orch.run_query, config, graph_scope=GraphScope.admin("cli"))

    assert seen is not config
    assert scope_of(seen) == GraphScope.admin("cli")
    assert not hasattr(config, SCOPE_ATTR)


def test_a_refused_identity_never_reaches_the_scope():
    payload = orch.run_query({}, _Config(), "q", None, credentials={"api_user": None, "api_pass": None},
                             graph_scope={"is_admin": True, "project_ids": []})
    assert payload["debug"]["identity_refused"] is True


# --------------------------------------------------------------------------- #
# The graph turn on its own
# --------------------------------------------------------------------------- #

def _graph_turn(monkeypatch, tmp_path, plans, results):
    plan_iter, result_iter = iter(plans), iter(results)
    calls = {"agent": 0, "neo4j": 0, "chatter": 0}

    def agent(config, user_text, entity_result, plan, retry_context=None, refine_context=None):
        calls["agent"] += 1
        return next(plan_iter)

    def neo4j(config, cypher, params=None):
        calls["neo4j"] += 1
        return next(result_iter)

    def chatter(*a, **k):
        calls["chatter"] += 1
        return "graph reply"

    monkeypatch.setattr(orch, "graph_agent", agent)
    monkeypatch.setattr(orch, "tool_neo4j_query", neo4j)
    monkeypatch.setattr(orch, "chatter_agent_answer", chatter)
    monkeypatch.setattr(orch, "append_turn", lambda *a, **k: None)
    written = []
    monkeypatch.setattr(orch, "_write_graph_debug", lambda log_dir, ts, payload: written.append(payload))
    config = MagicMock()
    config.MODEL_MODE = "test"
    debug: dict = {}
    events: list = []
    session: dict = {}
    out = orch._execute_graph_turn(
        config=config, session=session, user_text="how many", entity_result=EntityAgentOutput(),
        plan=ParserPlan(mode="graph_query", intent_summary="count"), log_dir=str(tmp_path),
        artifact_store=MagicMock(register_path=MagicMock(return_value=None)),
        send_event=lambda name, payload: events.append((name, payload)), debug_payload=debug,
        t_total_start=time.perf_counter(),
    )
    return SimpleNamespace(out=out, debug=debug, calls=calls, written=written, events=events, session=session)


def _plan(cypher):
    return GraphAgentPlan(cypher=cypher, context_mode="catalog")


def test_a_refused_first_attempt_makes_no_second_agent_call_and_falls_back(monkeypatch, tmp_path):
    run = _graph_turn(monkeypatch, tmp_path, [_plan(REFUSED_CYPHER), _plan("NEVER")], [_refused()])

    assert isinstance(run.out, orch.GraphScopeFallback)
    assert run.calls == {"agent": 1, "neo4j": 1, "chatter": 0}
    assert run.out.codes == ("label_not_allowed",)
    assert run.out.reasons == ("line 1, column 8: not allowed",)
    assert run.out.submitted_cypher == REFUSED_CYPHER
    assert [a["reason"] for a in run.out.attempts] == ["initial"]
    assert run.debug["graph_scope_fallback"] == {
        "endpoint": orch.GRAPH_SEARCH_ENDPOINT, "codes": ["label_not_allowed"],
        "reasons": ["line 1, column 8: not allowed"], "submitted_cypher": REFUSED_CYPHER}
    assert run.debug["graph_scope"]["decision"] == "refused"
    attempt = run.debug["graph_attempts"][0]
    assert attempt["executed_cypher"] == REFUSED_CYPHER and attempt["scope_decision"] == "refused"
    assert run.debug["graph_result"]["submitted_cypher"] == REFUSED_CYPHER
    assert "results_history" not in run.session, "a refused query leaves no graph bundle behind"
    assert not any(name == "query_complete" for name, _ in run.events)


def test_no_scope_falls_back_the_same_way(monkeypatch, tmp_path):
    run = _graph_turn(monkeypatch, tmp_path, [_plan("MATCH (s:T_TIS) RETURN count(s) AS n")], [_no_scope()])

    assert isinstance(run.out, orch.GraphScopeFallback)
    assert run.out.codes == ("no_scope",)
    assert run.calls["agent"] == 1


def test_a_proven_query_never_falls_back(monkeypatch, tmp_path):
    run = _graph_turn(monkeypatch, tmp_path, [_plan("MATCH (s:T_TIS) RETURN count(s) AS n")], [_proven(1)])

    assert not isinstance(run.out, orch.GraphScopeFallback)
    assert run.out["reply"] == "graph reply"
    assert "graph_scope_fallback" not in run.debug
    assert run.debug["graph_scope"]["decision"] == "proven"
    attempt = run.debug["graph_attempts"][0]
    assert attempt["executed_cypher"].endswith("/* scoped */")
    assert attempt["scope_decision"] == "proven"
    assert run.debug["graph_result"]["cypher"].endswith("/* scoped */")
    neo4j_output = run.written[0]["neo4j_output"]
    assert neo4j_output["cypher"].endswith("/* scoped */")
    assert neo4j_output["scope"]["decision"] == "proven"


def test_a_write_refusal_keeps_todays_retry_and_never_falls_back(monkeypatch, tmp_path):
    run = _graph_turn(monkeypatch, tmp_path,
                      [_plan("MATCH (s) DELETE s"), _plan("MATCH (s:T_TIS) RETURN count(s) AS n")],
                      [_write(), _proven(1)])

    assert not isinstance(run.out, orch.GraphScopeFallback)
    assert run.calls["agent"] == 2
    assert [a["scope_decision"] for a in run.debug["graph_attempts"]] == ["not_checked", "proven"]


def test_a_refused_retry_after_a_cypher_error_ends_the_loop_with_the_refusal(monkeypatch, tmp_path):
    run = _graph_turn(monkeypatch, tmp_path, [_plan("BAD"), _plan(REFUSED_CYPHER), _plan("NEVER")],
                      [_error(), _refused()])

    assert isinstance(run.out, orch.GraphScopeFallback)
    assert run.calls["agent"] == 2 and run.calls["neo4j"] == 2
    assert [a["reason"] for a in run.out.attempts] == ["initial", "cypher_error"]
    assert run.out.submitted_cypher == REFUSED_CYPHER


def test_a_refused_retry_after_a_proven_zero_keeps_the_zero(monkeypatch, tmp_path):
    run = _graph_turn(monkeypatch, tmp_path,
                      [_plan("MATCH (s:T_TIS) RETURN count(s) AS n"), _plan(REFUSED_CYPHER)],
                      [_proven(0), _refused()])

    assert not isinstance(run.out, orch.GraphScopeFallback)
    assert run.calls["agent"] == 2
    assert run.debug["graph_scope"]["decision"] == "proven"
    assert [a["scope_decision"] for a in run.debug["graph_attempts"]] == ["proven", "refused"]


# --------------------------------------------------------------------------- #
# The whole NS turn: the fallback runs through the REST branch against graph_search
# --------------------------------------------------------------------------- #

class _TurnConfig:
    MIN_SAMPLETYPES: list = []
    MIN_ASSAYS: list = []
    MODEL_MODE = "test"

    def get_schema_for_endpoint(self, endpoint):
        return None


def _run_turn(parser_plan, graph_results, *, history=None):
    graph_results = iter(graph_results)
    seen = SimpleNamespace(agent=0, api_plans=[], api_requests=[], chatter=[], graph_chatter=0)

    def graph_agent(config, user_text, entity_result, plan, retry_context=None, refine_context=None):
        seen.agent += 1
        seen.refine_context = refine_context
        return _plan(REFUSED_CYPHER)

    def api_agent(config, plan):
        seen.api_plans.append(plan)
        return APIRequestPlan(endpoint=plan.target_endpoint, method="POST",
                              requestBody={"filter_searchText": "lung"})

    def api_request(config=None, endpoint=None, method=None, requestBody=None, queryParameters=None):
        seen.api_requests.append((endpoint, method, requestBody))
        return {"ok": True, "status_code": 200, "url": endpoint, "method": method,
                "data": {"results": [{"uuid": "TIS-1"}], "total": 1}}

    def chatter(config, user_text, entity, plan, *args, **kwargs):
        if kwargs.get("graph_plan") is not None:
            seen.graph_chatter += 1
            return "graph reply"
        seen.chatter.append({"plan": plan, "kwargs": kwargs})
        return "Found one tissue sample."

    session = {"results_history": list(history or [])}
    with patch.object(orch.pipeline_agent, "is_active", return_value=False), \
            patch.object(orch, "_ensure_query_log_dir", return_value="/tmp/log"), \
            patch.object(orch, "ArtifactStore", return_value=MagicMock(write_json=MagicMock(return_value=None),
                                                                       register_path=MagicMock(return_value=None))), \
            patch.object(orch, "shortlist_catalog", return_value=([], [], {})), \
            patch.object(orch, "entity_agent", return_value=EntityAgentOutput()), \
            patch.object(orch, "parser_agent", return_value=parser_plan), \
            patch.object(orch, "graph_agent", graph_agent), \
            patch.object(orch, "tool_neo4j_query", lambda config, cypher, params=None: next(graph_results)), \
            patch("chat_nextseek.agents.api_agent_build_request", api_agent), \
            patch.object(orch, "tool_nextseek_api_request", api_request), \
            patch.object(orch, "chatter_agent_answer", chatter), \
            patch.object(orch, "append_turn"), \
            patch.object(orch, "log_api_call"), \
            patch.object(orch, "_write_graph_debug", return_value=None), \
            patch.object(orch, "_artifacts_for", return_value=None):
        payload = orch.run_query(session, _TurnConfig(), "how many lung tissue samples", None,
                                 credentials=CREDS, graph_scope={"is_admin": False, "project_ids": [2]})
    return payload, seen, session


def _assert_fell_back(payload, seen):
    assert len(seen.api_plans) == 1
    fallback_plan = seen.api_plans[0]
    assert fallback_plan.mode == "new_search"
    assert fallback_plan.target_endpoint == orch.GRAPH_SEARCH_ENDPOINT
    assert seen.api_requests[0][0] == orch.GRAPH_SEARCH_ENDPOINT
    assert len(seen.chatter) == 1 and seen.graph_chatter == 0
    assert seen.chatter[0]["kwargs"]["query_notes"] == [orch.SCOPE_FALLBACK_NOTE]
    assert payload["reply"].startswith("Found one tissue sample.")
    assert payload["reply"].endswith(orch.SCOPE_FALLBACK_FOOTER)
    debug = payload["debug"]
    assert debug["graph_scope_fallback"]["endpoint"] == orch.GRAPH_SEARCH_ENDPOINT
    assert debug["graph_scope_fallback"]["codes"] == ["label_not_allowed"]
    assert debug["graph_scope"]["decision"] == "refused"
    assert debug["api_plan"]["endpoint"] == orch.GRAPH_SEARCH_ENDPOINT


def test_a_refused_graph_question_is_answered_by_graph_search_with_the_note(monkeypatch):
    payload, seen, session = _run_turn(ParserPlan(mode="graph_query", intent_summary="count lung"), [_refused()])

    assert seen.agent == 1, "a scope refusal makes no agent retry"
    _assert_fell_back(payload, seen)
    assert [b["mode"] for b in session["results_history"]] == ["new_search"]


def test_the_graph_origin_refine_falls_back_the_same_way(monkeypatch):
    prior = {"id": 1, "mode": "graph_query", "user_query": "tissue samples",
             "graph_plan": {"cypher": "MATCH (s:T_TIS) RETURN s.uuid AS uuid"}}
    payload, seen, _ = _run_turn(ParserPlan(mode="refine_last_search", intent_summary="only lung"), [_refused()],
                                 history=[prior])

    assert seen.agent == 1
    assert "Prior Cypher" in (seen.refine_context or "")
    _assert_fell_back(payload, seen)


def test_a_proven_graph_question_is_answered_from_the_graph(monkeypatch):
    payload, seen, _ = _run_turn(ParserPlan(mode="graph_query", intent_summary="count"), [_proven(1)])

    assert seen.graph_chatter == 1 and seen.api_plans == [] and seen.api_requests == []
    assert payload["reply"] == "graph reply"
    assert orch.SCOPE_FALLBACK_FOOTER not in payload["reply"]
    assert "graph_scope_fallback" not in payload["debug"]


def test_an_ordinary_search_gets_no_note_and_no_footer(monkeypatch):
    payload, seen, _ = _run_turn(ParserPlan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/",
                                            intent_summary="lung"), [])

    assert seen.agent == 0
    assert seen.chatter[0]["kwargs"].get("query_notes") in (None, [])
    assert orch.SCOPE_FALLBACK_FOOTER not in payload["reply"]


# --------------------------------------------------------------------------- #
# The other callers of the tool
# --------------------------------------------------------------------------- #

def test_the_follow_up_seam_reports_a_refusal_as_a_failed_query(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "graph_agent", lambda *a, **k: _plan(REFUSED_CYPHER))
    monkeypatch.setattr(orch, "tool_neo4j_query", lambda *a, **k: _refused())
    captured = {}

    def fake_followup(config, *, user_text, bundle, run_query, log_dir):
        captured["result"] = run_query(question="how many", seed_uids=["TIS-1"])
        return {"reply": "from the stored result"}

    monkeypatch.setattr(orch, "run_followup", fake_followup)

    orch._run_followup_agent(MagicMock(), session={}, user_text="how many", bundle={}, log_dir=str(tmp_path))

    assert captured["result"]["ok"] is False
    assert captured["result"]["error"].startswith(SCOPE_REFUSED)


def _planner_step():
    execution = SimpleNamespace(tool_query="how many", metadata={}, target_endpoint=None, filters={})
    return SimpleNamespace(execution=execution, notes="")


def _planner(monkeypatch, results):
    results = iter(results)
    calls = {"agent": 0}

    def agent(config, query, entity_result, parser_plan=None, retry_context=None):
        calls["agent"] += 1
        return _plan(REFUSED_CYPHER)

    monkeypatch.setattr(planner_tools, "graph_agent", agent)
    monkeypatch.setattr(planner_tools, "tool_neo4j_query", lambda *a, **k: next(results))
    out = planner_tools._plan_tool_graph_query(MagicMock(), {}, _planner_step(), "how many", {}, None, {})
    return out, calls


def test_the_planner_step_is_not_retried_on_a_scope_refusal(monkeypatch):
    out, calls = _planner(monkeypatch, [_refused(), _proven(1)])

    assert calls["agent"] == 1
    assert out["ok"] is False
    assert out["error"].startswith(SCOPE_REFUSED)
    assert orch.GRAPH_SEARCH_ENDPOINT in out["error"]


def test_the_planner_step_still_retries_a_cypher_error_once(monkeypatch):
    out, calls = _planner(monkeypatch, [_error(), _proven(1)])

    assert calls["agent"] == 2
    assert out["ok"] is True
    assert orch.GRAPH_SEARCH_ENDPOINT not in (out["error"] or "")

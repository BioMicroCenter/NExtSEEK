"""
A refine modifies the stored result the parser names, not whichever one is newest.

The parser plan carries ``target_result_id`` on a refine as well as on a memory question
(the recent-results summary lists every bundle's id for exactly that purpose), but the
refine branch of ``run_query`` read ``results_history[-1]`` in both of its places: the
check that sends a graph-origin refine back through the graph, and the REST prep that
carries the previous endpoint, filters and request forward. So "rerun the first search
but only females" refined the newest result, whatever the parser had said.

``chat_memory.select_refine_bundle`` is the one place the choice is made now: the bundle
the parser named, else the newest. A named id that is not in the session falls back to
the newest and the debug payload says so (``refine_target``), rather than failing a turn
that worked before.

Every agent and tool is stubbed; no model, Neo4j or network call is made.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from chat_nextseek import orchestrator as orch
from chat_nextseek import chat_memory
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan
from chat_nextseek.schemas.tools import APIRequestPlan

CREDS = {"api_user": "someone", "api_pass": "secret"}
ADVANCED = "/nextseek_api/samples/advanced_search/"
GRAPH_SEARCH = "/nextseek_api/samples/graph_search/"


def _rest_bundle(bundle_id, query, *, endpoint=ADVANCED, sampletype=None, text=None):
    body = {k: v for k, v in (("sampletype", sampletype), ("filter_searchText", text)) if v}
    api_plan = {"endpoint": endpoint, "method": "POST", "requestBody": body, "queryParameters": {}}
    return {
        "id": bundle_id, "mode": "new_search", "user_query": query,
        "parser_plan": {"mode": "new_search", "target_endpoint": endpoint,
                        "filters": {"sampletype_code": sampletype, "assay_codes": [],
                                    "keywords": [text] if text else [], "uids": [], "lab_codes": []}},
        "api_plan": api_plan, "endpoint": endpoint, "request_body": body,
        "search_context": {"endpoint": endpoint, "method": "POST", "request_body": body},
    }


def _graph_bundle(bundle_id, query, cypher):
    return {"id": bundle_id, "mode": "graph_query", "user_query": query, "endpoint": "neo4j",
            "graph_plan": {"cypher": cypher}, "graph_result": {"ok": True, "count": 1, "total": 1}}


# --------------------------------------------------------------------------- #
# The selection itself
# --------------------------------------------------------------------------- #

HISTORY = [_rest_bundle(1, "NDMA mice", sampletype="MUS", text="NDMA"),
           _rest_bundle(2, "lung tissue", sampletype="TIS", text="lung")]


def test_a_named_bundle_is_the_one_refined():
    bundle, record = chat_memory.select_refine_bundle(HISTORY, 1)
    assert bundle["id"] == 1
    assert record == {"bundle_id": 1, "requested": 1, "chosen_by": "named"}


def test_no_reference_refines_the_newest():
    bundle, record = chat_memory.select_refine_bundle(HISTORY, None)
    assert bundle["id"] == 2
    assert record == {"bundle_id": 2, "requested": None, "chosen_by": "newest"}


def test_a_reference_to_a_bundle_that_is_not_there_falls_back_to_the_newest_and_says_so():
    bundle, record = chat_memory.select_refine_bundle(HISTORY, 99)
    assert bundle["id"] == 2
    assert record == {"bundle_id": 2, "requested": 99, "chosen_by": "named_missing"}


def test_an_empty_history_selects_nothing():
    bundle, record = chat_memory.select_refine_bundle([], 1)
    assert bundle is None
    assert record == {"bundle_id": None, "requested": 1, "chosen_by": "none"}


# --------------------------------------------------------------------------- #
# The whole NS turn
# --------------------------------------------------------------------------- #

class _TurnConfig:
    MIN_SAMPLETYPES: list = []
    MIN_ASSAYS: list = []
    MODEL_MODE = "test"

    def get_schema_for_endpoint(self, endpoint):
        return None


def _proven(cypher):
    return {"ok": True, "count": 1, "total": 1, "truncated": False, "limit": None, "data": [{"uuid": "MUS-1"}],
            "cypher": cypher, "submitted_cypher": cypher, "parameters": {}, "counters": {},
            "scope": {"decision": "proven", "source": "request", "project_ids": [2], "injected": [], "joined": [],
                      "codes": [], "reasons": []}}


def _run_refine(history, target_result_id):
    seen = SimpleNamespace(graph_calls=[], api_plans=[])

    def graph_agent(config, user_text, entity_result, plan, retry_context=None, refine_context=None):
        seen.graph_calls.append(refine_context)
        return GraphAgentPlan(cypher="MATCH (s:T_MUS) RETURN s.uuid AS uuid", context_mode="catalog")

    def api_agent(config, plan):
        seen.api_plans.append(plan)
        return APIRequestPlan(endpoint=plan.target_endpoint, method="POST", requestBody={"filter_searchText": "x"})

    def api_request(config=None, endpoint=None, method=None, requestBody=None, queryParameters=None):
        return {"ok": True, "status_code": 200, "url": endpoint, "method": method,
                "data": {"results": [{"uuid": "MUS-1"}], "total": 1}}

    plan = ParserPlan(mode="refine_last_search", intent_summary="only the females",
                      target_result_id=target_result_id)
    session = {"results_history": list(history)}
    with patch.object(orch.pipeline_agent, "is_active", return_value=False), \
            patch.object(orch, "_ensure_query_log_dir", return_value="/tmp/log"), \
            patch.object(orch, "ArtifactStore", return_value=MagicMock(write_json=MagicMock(return_value=None),
                                                                       register_path=MagicMock(return_value=None))), \
            patch.object(orch, "shortlist_catalog", return_value=([], [], {})), \
            patch.object(orch, "entity_agent", return_value=EntityAgentOutput()), \
            patch.object(orch, "parser_agent", return_value=plan), \
            patch.object(orch, "graph_agent", graph_agent), \
            patch.object(orch, "tool_neo4j_query", lambda config, cypher, params=None: _proven(cypher)), \
            patch("chat_nextseek.agents.api_agent_build_request", api_agent), \
            patch.object(orch, "tool_nextseek_api_request", api_request), \
            patch.object(orch, "chatter_agent_answer", return_value="reply"), \
            patch.object(orch, "append_turn"), \
            patch.object(orch, "log_api_call"), \
            patch.object(orch, "_write_graph_debug", return_value=None), \
            patch.object(orch, "_artifacts_for", return_value=None):
        payload = orch.run_query(session, _TurnConfig(), "same again but only the females", None,
                                 credentials=CREDS, graph_scope={"is_admin": False, "project_ids": [2]})
    return payload, seen


def test_a_rest_refine_that_names_an_earlier_search_carries_that_search_forward():
    payload, seen = _run_refine(HISTORY, 1)

    assert seen.graph_calls == []
    refined = seen.api_plans[0]
    assert refined.previous_user_query == "NDMA mice"
    assert refined.previous_api_plan["requestBody"] == {"sampletype": "MUS", "filter_searchText": "NDMA"}
    assert refined.filters.sampletype_code == "MUS"
    assert refined.target_endpoint == ADVANCED
    assert payload["debug"]["refine_target"] == {"bundle_id": 1, "requested": 1, "chosen_by": "named"}


def test_a_refine_that_names_an_earlier_graph_result_reruns_the_graph_with_its_cypher():
    history = [_graph_bundle(1, "NDMA mice", "MATCH (s:T_MUS) WHERE s.Treatment = 'NDMA' RETURN s"),
               _rest_bundle(2, "lung tissue", endpoint=GRAPH_SEARCH, sampletype="TIS")]
    payload, seen = _run_refine(history, 1)

    assert seen.api_plans == []
    assert len(seen.graph_calls) == 1
    assert "WHERE s.Treatment = 'NDMA'" in seen.graph_calls[0]
    assert "NDMA mice" in seen.graph_calls[0]
    assert payload["debug"]["refine_target"]["bundle_id"] == 1


def test_a_refine_that_names_an_earlier_rest_search_does_not_follow_a_newer_graph_result():
    history = [_rest_bundle(1, "NDMA mice", sampletype="MUS", text="NDMA"),
               _graph_bundle(2, "lung tissue", "MATCH (s:T_TIS) RETURN s")]
    payload, seen = _run_refine(history, 1)

    assert seen.graph_calls == []
    assert seen.api_plans[0].previous_user_query == "NDMA mice"


def test_a_refine_with_no_reference_still_refines_the_newest():
    payload, seen = _run_refine(HISTORY, None)

    assert seen.api_plans[0].previous_user_query == "lung tissue"
    assert payload["debug"]["refine_target"] == {"bundle_id": 2, "requested": None, "chosen_by": "newest"}


def test_a_refine_naming_a_bundle_that_is_gone_refines_the_newest_and_says_so():
    payload, seen = _run_refine(HISTORY, 99)

    assert seen.api_plans[0].previous_user_query == "lung tissue"
    assert payload["debug"]["refine_target"] == {"bundle_id": 2, "requested": 99, "chosen_by": "named_missing"}

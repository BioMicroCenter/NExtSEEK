"""Guardrails on the API agent's request construction.

Both behaviours here were real production failures observed in the 2026-07-24
nessie run, and both were invisible because the request still came back
``ok: true`` (or a plausible-looking 4xx the user was blamed for).
"""
from __future__ import annotations

import pytest

from chat_nextseek.agents import api as api_agent
from chat_nextseek.schemas import APIRequestPlan

ADVANCED_SEARCH = "/nextseek_api/samples/advanced_search/"


class _Cfg:
    """Minimal ChatConfig stand-in for the agent's schema lookups."""

    API_AGENT_SYSTEM_PROMPT = "sys"
    MIN_API_ENDPOINTS: list[dict] = []

    def __init__(self, schema=None, endpoints=None):
        self._schema = schema or {}
        self.MIN_API_ENDPOINTS = endpoints or []

    def get_schema_for_endpoint(self, endpoint):
        return self._schema

    def get_agent_model(self, _name):
        return (None, "model", None)


def _patch_llm(monkeypatch, returned: APIRequestPlan | Exception):
    def fake(**_kwargs):
        if isinstance(returned, Exception):
            raise returned
        return returned
    monkeypatch.setattr(api_agent, "call_llm_structured", fake)


# ── filter_searchText must not silently become "match everything" ──────────

def test_omitted_search_text_is_backfilled_from_parser_keywords(monkeypatch):
    """An empty filter_searchText returns the WHOLE database.

    Observed: entity resolved keywords ["4 week"], the agent emitted no
    filter_searchText, the guardrail defaulted it to "", and "find samples from
    a 4 week study" answered with all 50,886 samples.
    """
    _patch_llm(monkeypatch, APIRequestPlan(
        endpoint=ADVANCED_SEARCH, method="POST", requestBody={}, queryParameters={}, notes=""))

    plan = api_agent.api_agent_build_request(
        _Cfg(schema={"method": "POST"}),
        {"target_endpoint": ADVANCED_SEARCH, "filters": {"keywords": ["4 week"]}},
    )

    assert plan.requestBody["filter_searchText"] == "4 week"
    assert plan.requestBody["filter_matchType"] == "PARTIAL"


def test_multiple_keywords_are_joined(monkeypatch):
    _patch_llm(monkeypatch, APIRequestPlan(
        endpoint=ADVANCED_SEARCH, method="POST",
        requestBody={"filter_searchText": "   "}, queryParameters={}, notes=""))

    plan = api_agent.api_agent_build_request(
        _Cfg(schema={"method": "POST"}),
        {"target_endpoint": ADVANCED_SEARCH, "filters": {"keywords": ["NDMA", "liver"]}},
    )
    assert plan.requestBody["filter_searchText"] == "NDMA liver"


def test_no_keywords_still_allows_a_deliberately_unfiltered_search(monkeypatch):
    """"How many samples are in the database?" legitimately filters nothing."""
    _patch_llm(monkeypatch, APIRequestPlan(
        endpoint=ADVANCED_SEARCH, method="POST", requestBody={}, queryParameters={}, notes=""))

    plan = api_agent.api_agent_build_request(
        _Cfg(schema={"method": "POST"}),
        {"target_endpoint": ADVANCED_SEARCH, "filters": {"keywords": []}},
    )
    assert plan.requestBody["filter_searchText"] == ""


def test_agent_supplied_search_text_is_left_alone(monkeypatch):
    _patch_llm(monkeypatch, APIRequestPlan(
        endpoint=ADVANCED_SEARCH, method="POST",
        requestBody={"filter_searchText": "NDMA", "filter_matchType": "EXACT"},
        queryParameters={}, notes=""))

    plan = api_agent.api_agent_build_request(
        _Cfg(schema={"method": "POST"}),
        {"target_endpoint": ADVANCED_SEARCH, "filters": {"keywords": ["ignored"]}},
    )
    assert plan.requestBody["filter_searchText"] == "NDMA"
    assert plan.requestBody["filter_matchType"] == "EXACT"


# ── a failed parse must not ship a request we know is malformed ────────────

PARENTS_BY_CHILD = "/nextseek_api/sample_types/get_parents/parents_by_child_types/"


def test_parse_failure_refuses_to_call_a_body_requiring_endpoint(monkeypatch):
    """Observed: HTTP 422 from an empty body after "structured parsing failed".

    The user was then told their question could not be completed, which reads as
    their fault rather than ours. Returning no endpoint makes the orchestrator
    say the request could not be built, which is the truth.
    """
    _patch_llm(monkeypatch, RuntimeError("schema parse blew up"))

    plan = api_agent.api_agent_build_request(
        _Cfg(schema={"method": "POST", "request_schemas": {"POST": {"required": ["child_types"]}}}),
        {"target_endpoint": PARENTS_BY_CHILD, "filters": {}},
    )

    assert plan.endpoint is None          # nothing was sent
    assert plan.requestBody == {}
    assert "requires a request body" in plan.notes


def test_parse_failure_still_allows_a_bodyless_get(monkeypatch):
    _patch_llm(monkeypatch, RuntimeError("boom"))

    plan = api_agent.api_agent_build_request(
        _Cfg(schema={"method": "GET"}),
        {"target_endpoint": "/nextseek_api/assays/", "filters": {}},
    )

    assert plan.endpoint == "/nextseek_api/assays/"
    assert plan.method == "GET"


@pytest.mark.parametrize("method,schema,enriched,expected", [
    ("GET", {}, None, False),
    ("POST", {"request_schemas": {"POST": {"required": ["x"]}}}, None, True),
    ("POST", {}, {"request_body": {"a": 1}}, True),
    ("POST", {}, None, False),          # nothing declares a body -> don't assume one
])
def test_requires_request_body(method, schema, enriched, expected):
    assert api_agent._requires_request_body(method, schema, enriched) is expected

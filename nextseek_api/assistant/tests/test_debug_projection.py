"""Tests for the NExtSEEK-engine debug projection.

The live SSE path builds Search Details from ephemeral progress events that are
never persisted in that shape. On rehydrate those events are gone, so the panel
has to be rebuilt from the ``results_history`` bundle instead. These tests pin
that reconstruction against a bundle captured from a real production turn.
"""

from __future__ import annotations

from nextseek_api.assistant.debug_projection import bundle_debug_entries


# A real graph_query bundle (production, trimmed to the projected fields).
GRAPH_BUNDLE = {
    "id": 1,
    "mode": "graph_query",
    "endpoint": "neo4j",
    "user_query": "Find all NHP samples in IMPAcTb",
    "parser_plan": {
        "mode": "graph_query",
        "target_endpoint": None,
        "intent_summary": "Find all NHP samples belonging to the Impactb Investigation.",
        "filters": {
            "sampletype_code": "NHP",
            "assay_codes": [],
            "keywords": ["Impact"],
            "uids": [],
            "lab_codes": [],
        },
    },
    "graph_plan": {
        "cypher": "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)\nWHERE s.type = $type\nRETURN s",
        "explanation": "This query finds all Sample nodes of type 'NHP'.",
        "parameters": {"type": "NHP", "project": "Impact"},
    },
    "graph_result": {
        "ok": True,
        "count": 704,
        "total": 704,
        "truncated": False,
        "limit": 5000,
    },
}

REST_BUNDLE = {
    "id": 2,
    "mode": "new_search",
    "endpoint": "/nextseek_api/samples/advanced_search/",
    "parser_plan": {"mode": "new_search", "intent_summary": "Find mice treated with NDMA."},
    "api_plan": {"method": "POST", "endpoint": "/nextseek_api/samples/advanced_search/"},
    "api_result_slim": {"total": 195, "row_count": 195},
}


def _by_agent(entries):
    return {e["agent"]: e["summary"] for e in entries}


class TestGraphBundle:
    def test_emits_parser_graph_and_result_entries(self):
        agents = [e["agent"] for e in bundle_debug_entries(GRAPH_BUNDLE)]
        assert agents == ["parser", "graph", "neo4j"]

    def test_parser_entry_carries_mode_and_intent(self):
        summary = _by_agent(bundle_debug_entries(GRAPH_BUNDLE))["parser"]
        assert "graph_query" in summary
        assert "Find all NHP samples belonging to the Impactb Investigation." in summary

    def test_parser_entry_carries_resolved_filters(self):
        summary = _by_agent(bundle_debug_entries(GRAPH_BUNDLE))["parser"]
        assert "sampletype_code=NHP" in summary
        assert "keywords=Impact" in summary
        # Empty filter lists are noise in a details panel.
        assert "assay_codes" not in summary
        assert "uids" not in summary

    def test_graph_entry_carries_cypher_and_parameters(self):
        summary = _by_agent(bundle_debug_entries(GRAPH_BUNDLE))["graph"]
        assert "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)" in summary
        assert "type=NHP" in summary
        assert "project=Impact" in summary

    def test_result_entry_reports_count_and_truncation(self):
        summary = _by_agent(bundle_debug_entries(GRAPH_BUNDLE))["neo4j"]
        assert "704 rows" in summary
        assert "total 704" in summary
        assert "truncated=False" in summary

    def test_truncation_is_surfaced_when_it_happens(self):
        bundle = {
            **GRAPH_BUNDLE,
            "graph_result": {"ok": True, "count": 5000, "total": 10688,
                             "truncated": True, "limit": 5000},
        }
        summary = _by_agent(bundle_debug_entries(bundle))["neo4j"]
        assert "truncated=True" in summary
        assert "total 10,688" in summary or "total 10688" in summary


class TestRestBundle:
    def test_emits_parser_and_api_entries(self):
        agents = [e["agent"] for e in bundle_debug_entries(REST_BUNDLE)]
        assert "parser" in agents
        assert "api" in agents

    def test_api_entry_names_method_and_endpoint(self):
        summary = _by_agent(bundle_debug_entries(REST_BUNDLE))["api"]
        assert "POST" in summary
        assert "/nextseek_api/samples/advanced_search/" in summary

    def test_api_entry_reports_row_count(self):
        summary = _by_agent(bundle_debug_entries(REST_BUNDLE))["api"]
        assert "195" in summary


class TestDegenerateInput:
    def test_none_returns_empty(self):
        assert bundle_debug_entries(None) == []

    def test_empty_dict_returns_empty(self):
        assert bundle_debug_entries({}) == []

    def test_non_dict_returns_empty(self):
        assert bundle_debug_entries("not a bundle") == []
        assert bundle_debug_entries([1, 2, 3]) == []

    def test_bundle_with_only_a_reply_returns_empty(self):
        # A wizard/CC turn writes no plans; it must not produce a stray panel.
        assert bundle_debug_entries({"id": 9, "terminal_reply": "hello"}) == []

    def test_entries_are_json_safe(self):
        import json
        json.dumps(bundle_debug_entries(GRAPH_BUNDLE))

    def test_every_entry_has_exactly_agent_and_summary(self):
        for e in bundle_debug_entries(GRAPH_BUNDLE) + bundle_debug_entries(REST_BUNDLE):
            assert set(e) == {"agent", "summary"}
            assert isinstance(e["agent"], str) and e["agent"]
            assert isinstance(e["summary"], str) and e["summary"]

    def test_malformed_plans_do_not_raise(self):
        bundle = {"id": 3, "parser_plan": "nope", "graph_plan": 42, "graph_result": []}
        assert bundle_debug_entries(bundle) == []

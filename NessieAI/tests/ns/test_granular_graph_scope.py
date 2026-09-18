"""The CC ``graph`` op under a project scope (spec docs/superpowers/specs/2026-09-18-graph-cypher-scope.md 7.2).

The op returns ``{"plan", "result"}`` as before. ``result`` is the Neo4j tool's dict, so it carries the ``scope``
record. When the statement was refused for its scope, the op answers the question itself through
``/nextseek_api/samples/graph_search/``, the way the NS orchestrator does: the parser plan retargeted to graph_search,
built by the API agent, gated as a read and run, returned under ``fallback`` with a note the agent must disclose. The
view never injects ``neo4j_exec``, so the op always runs the real tool, which refuses a config without a scope before
any driver opens.

Every agent and the REST call are patched; no model, database or server is reached.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from chat_nextseek.graph_scope import GraphScope, with_scope
from chat_nextseek.helpers.tools.neo4j import NO_SCOPE_REFUSED, SCOPE_REFUSED
from chat_nextseek.schemas import ParserPlan
from NessieAI.ns.granular import run_op
from NessieAI.ns.write_gate import WriteBlockedError

GRAPH_SEARCH = "/nextseek_api/samples/graph_search/"


def _dumpable(payload):
    m = MagicMock()
    m.model_dump.return_value = payload
    return m


def _refused():
    return {"ok": False, "error": f"{SCOPE_REFUSED} Reasons: line 1, column 8: not allowed.", "data": None,
            "cypher": "MATCH (a:Attribute) RETURN a", "submitted_cypher": "MATCH (a:Attribute) RETURN a",
            "parameters": {}, "scope": {"decision": "refused", "codes": ["label_not_allowed"],
                                        "reasons": ["line 1, column 8: not allowed"]}}


def _api_plan(endpoint=GRAPH_SEARCH, method="POST"):
    return SimpleNamespace(
        endpoint=endpoint, method=method, requestBody={"sample_type": "T_TIS"}, queryParameters=None,
        model_dump=lambda: {"endpoint": endpoint, "method": method, "requestBody": {"sample_type": "T_TIS"}},
    )


GRAPH_SEARCH_ROWS = {"ok": True, "data": {"total": 2, "rows": [{"uuid": "TIS-1"}, {"uuid": "TIS-2"}]}}


class GraphOpScopeTests(SimpleTestCase):
    def setUp(self):
        self.write_gate = MagicMock()
        self.parser_plan = ParserPlan(mode="graph_query", target_endpoint=None)
        self.build = MagicMock(return_value=_api_plan())
        self.rest = MagicMock(return_value=GRAPH_SEARCH_ROWS)

    def _run(self, config, cypher="MATCH (s:T_TIS) RETURN count(s) AS n", **kw):
        with patch("chat_nextseek.portable.entity_agent", return_value=_dumpable({})), \
             patch("chat_nextseek.portable.parser_agent", return_value=self.parser_plan), \
             patch("chat_nextseek.portable.graph_agent",
                   return_value=_dumpable({"cypher": cypher, "parameters": {}})), \
             patch("chat_nextseek.portable.api_agent_build_request", self.build), \
             patch("chat_nextseek.helpers.tool_nextseek_api_request", self.rest):
            return run_op("graph", {"query": "how many tissue samples"}, config=config, session=SimpleNamespace(),
                          write_gate=self.write_gate, **kw)

    def test_a_scope_refusal_is_answered_through_graph_search(self):
        config = SimpleNamespace()
        out = self._run(config, neo4j_exec=MagicMock(return_value=_refused()))

        result, fallback = out["result"], out["fallback"]
        self.assertEqual(set(out), {"plan", "result", "fallback"})
        self.assertEqual(result["scope"]["decision"], "refused")
        self.assertTrue(result["error"].startswith(SCOPE_REFUSED))
        self.assertIn(GRAPH_SEARCH, result["error"])
        self.assertIn("fallback", result["error"])
        (built_config, built_plan), _ = self.build.call_args
        self.assertIs(built_config, config)
        self.assertEqual(built_plan.target_endpoint, GRAPH_SEARCH)
        self.assertEqual(built_plan.mode, "new_search")
        self.write_gate.assert_called_once_with("api-read", GRAPH_SEARCH, "POST", False)
        self.rest.assert_called_once_with(config, GRAPH_SEARCH, "POST", requestBody={"sample_type": "T_TIS"},
                                          queryParameters=None)
        self.assertTrue(fallback["ok"])
        self.assertEqual(fallback["endpoint"], GRAPH_SEARCH)
        self.assertEqual(fallback["codes"], ["label_not_allowed"])
        self.assertEqual(fallback["response"], GRAPH_SEARCH_ROWS)
        self.assertIn("project-scoped sample search", fallback["note"])

    def test_the_parser_plan_is_not_mutated_by_the_retarget(self):
        self._run(SimpleNamespace(), neo4j_exec=MagicMock(return_value=_refused()))

        self.assertEqual(self.parser_plan.mode, "graph_query")
        self.assertIsNone(self.parser_plan.target_endpoint)

    def test_a_plain_dict_parser_plan_is_retargeted_too(self):
        self.parser_plan = {"mode": "graph_query", "filters": {"sampletype_code": "TIS"}}

        out = self._run(SimpleNamespace(), neo4j_exec=MagicMock(return_value=_refused()))

        (_, built_plan), _ = self.build.call_args
        self.assertEqual(built_plan["target_endpoint"], GRAPH_SEARCH)
        self.assertEqual(built_plan["mode"], "new_search")
        self.assertEqual(built_plan["filters"], {"sampletype_code": "TIS"})
        self.assertTrue(out["fallback"]["ok"])

    def test_an_api_plan_for_another_endpoint_is_refused_and_nothing_runs(self):
        self.build.return_value = _api_plan("/nextseek_api/samples/advanced_search/")

        out = self._run(SimpleNamespace(), neo4j_exec=MagicMock(return_value=_refused()))

        self.assertFalse(out["fallback"]["ok"])
        self.assertIn("advanced_search", out["fallback"]["error"])
        self.write_gate.assert_not_called()
        self.rest.assert_not_called()

    def test_a_blocked_read_is_reported_not_raised(self):
        self.write_gate.side_effect = WriteBlockedError("not in read_safe_endpoints.json")

        out = self._run(SimpleNamespace(), neo4j_exec=MagicMock(return_value=_refused()))

        self.assertFalse(out["fallback"]["ok"])
        self.assertIn("read_safe_endpoints", out["fallback"]["error"])
        self.rest.assert_not_called()

    def test_a_failed_fallback_still_returns_the_refusal(self):
        self.build.side_effect = RuntimeError("model unavailable")

        out = self._run(SimpleNamespace(), neo4j_exec=MagicMock(return_value=_refused()))

        self.assertEqual(out["result"]["scope"]["decision"], "refused")
        self.assertFalse(out["fallback"]["ok"])
        self.assertIn("model unavailable", out["fallback"]["error"])
        self.rest.assert_not_called()

    def test_the_real_tool_refuses_a_config_without_a_scope(self):
        out = self._run(SimpleNamespace(NEO4J_PASSWORD="p", NEO4J_URI="bolt://nowhere:7687", NEO4J_USER="u"))

        result = out["result"]
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"].startswith(NO_SCOPE_REFUSED))
        self.assertEqual(result["scope"]["codes"], ["no_scope"])
        self.assertIn(GRAPH_SEARCH, result["error"])
        self.assertTrue(out["fallback"]["ok"])

    def test_the_real_tool_runs_past_the_scope_for_an_admin(self):
        config = with_scope(SimpleNamespace(NEO4J_PASSWORD=None, NEO4J_URI="bolt://nowhere:7687", NEO4J_USER="u"),
                            GraphScope.admin("test"))

        result = self._run(config)["result"]

        self.assertEqual(result["error"], "NEO4J_PASSWORD not configured")
        self.assertEqual(result["scope"]["decision"], "admin")
        self.assertNotIn(GRAPH_SEARCH, result["error"])
        self.build.assert_not_called()

    def test_a_proven_result_is_returned_untouched(self):
        proven = {"ok": True, "data": [{"n": 3}], "scope": {"decision": "proven"}}

        out = self._run(SimpleNamespace(), neo4j_exec=MagicMock(return_value=proven))

        self.assertIs(out["result"], proven)
        self.assertNotIn("fallback", out)
        self.build.assert_not_called()

    def test_a_write_refusal_is_not_given_the_graph_search_hint(self):
        write = {"ok": False, "error": "Write operations are not permitted; Refused: DELETE.", "data": None,
                 "scope": {"decision": "not_checked", "codes": ["write"]}}

        out = self._run(SimpleNamespace(), neo4j_exec=MagicMock(return_value=write))

        self.assertIs(out["result"], write)
        self.assertNotIn("fallback", out)
        self.build.assert_not_called()

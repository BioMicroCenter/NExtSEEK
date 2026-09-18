"""The CC ``graph`` op under a project scope (spec docs/superpowers/specs/2026-09-18-graph-cypher-scope.md 7.2).

The op returns ``{"plan", "result"}`` as before. ``result`` is the Neo4j tool's dict, so it carries the ``scope``
record; when the statement was refused for its scope, the error also tells the CC agent where to go instead:
``/nextseek_api/samples/graph_search/`` through ``nextseek-api-read``. The view never injects ``neo4j_exec``, so the
op always runs the real tool, which refuses a config without a scope before any driver opens.

Every agent is patched; no model or database is reached.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from chat_nextseek.graph_scope import GraphScope, with_scope
from chat_nextseek.helpers.tools.neo4j import NO_SCOPE_REFUSED, SCOPE_REFUSED
from NessieAI.ns.granular import run_op

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


class GraphOpScopeTests(SimpleTestCase):
    def _run(self, config, cypher="MATCH (s:T_TIS) RETURN count(s) AS n", **kw):
        with patch("chat_nextseek.portable.entity_agent", return_value=_dumpable({})), \
             patch("chat_nextseek.portable.parser_agent", return_value=_dumpable({"mode": "graph_query"})), \
             patch("chat_nextseek.portable.graph_agent",
                   return_value=_dumpable({"cypher": cypher, "parameters": {}})):
            return run_op("graph", {"query": "how many tissue samples"}, config=config, session=SimpleNamespace(),
                          write_gate=MagicMock(), **kw)

    def test_a_scope_refusal_names_graph_search_through_api_read(self):
        out = self._run(SimpleNamespace(), neo4j_exec=MagicMock(return_value=_refused()))

        result = out["result"]
        self.assertEqual(set(out), {"plan", "result"})
        self.assertEqual(result["scope"]["decision"], "refused")
        self.assertTrue(result["error"].startswith(SCOPE_REFUSED))
        self.assertIn(GRAPH_SEARCH, result["error"])
        self.assertIn("nextseek-api-read", result["error"])

    def test_the_real_tool_refuses_a_config_without_a_scope(self):
        out = self._run(SimpleNamespace(NEO4J_PASSWORD="p", NEO4J_URI="bolt://nowhere:7687", NEO4J_USER="u"))

        result = out["result"]
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"].startswith(NO_SCOPE_REFUSED))
        self.assertEqual(result["scope"]["codes"], ["no_scope"])
        self.assertIn(GRAPH_SEARCH, result["error"])

    def test_the_real_tool_runs_past_the_scope_for_an_admin(self):
        config = with_scope(SimpleNamespace(NEO4J_PASSWORD=None, NEO4J_URI="bolt://nowhere:7687", NEO4J_USER="u"),
                            GraphScope.admin("test"))

        result = self._run(config)["result"]

        self.assertEqual(result["error"], "NEO4J_PASSWORD not configured")
        self.assertEqual(result["scope"]["decision"], "admin")
        self.assertNotIn(GRAPH_SEARCH, result["error"])

    def test_a_proven_result_is_returned_untouched(self):
        proven = {"ok": True, "data": [{"n": 3}], "scope": {"decision": "proven"}}

        out = self._run(SimpleNamespace(), neo4j_exec=MagicMock(return_value=proven))

        self.assertIs(out["result"], proven)

    def test_a_write_refusal_is_not_given_the_graph_search_hint(self):
        write = {"ok": False, "error": "Write operations are not permitted; Refused: DELETE.", "data": None,
                 "scope": {"decision": "not_checked", "codes": ["write"]}}

        out = self._run(SimpleNamespace(), neo4j_exec=MagicMock(return_value=write))

        self.assertIs(out["result"], write)

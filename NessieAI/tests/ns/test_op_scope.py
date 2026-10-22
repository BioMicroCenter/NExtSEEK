"""Each Container-CC op remembers its failed models, and its parts share what the others learned (F3/F4, 2026-09-28).

``run_op`` opens one ``call_scope.CallScope`` per op, so a Gemini stall found by the entity agent is not paid again by
the parser's fallback or the graph agent in the same op. The aggregate op answers its parts on a thread pool; each part
runs in a copy of the op's context, so every part sees the same scope object: a mark made by the prelude or by one
part is seen by all the others. Every agent is faked; no model is called.
"""
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from chat_nextseek import call_scope
from chat_nextseek.schemas import GraphAgentPlan, ParserPlan
from NessieAI.ns.granular import run_op


def _ok(rows):
    return {"ok": True, "data": rows, "count": len(rows), "total": len(rows), "truncated": False,
            "scope": {"decision": "proven", "project_ids": [2]}}


class OpScopeTests(SimpleTestCase):
    def test_an_op_runs_inside_its_own_scope_and_leaves_none_behind(self):
        seen = []

        def entity(config, query):
            seen.append(call_scope.current())
            return SimpleNamespace(model_dump=lambda: {"sampletypes": []})

        with patch("chat_nextseek.portable.entity_agent", side_effect=entity):
            run_op("entity", {"query": "mice"}, config=SimpleNamespace(), session=None, write_gate=MagicMock())
            run_op("entity", {"query": "rats"}, config=SimpleNamespace(), session=None, write_gate=MagicMock())

        self.assertIsInstance(seen[0], call_scope.CallScope)
        self.assertIsNot(seen[0], seen[1], "each op starts clean")
        self.assertIsNone(call_scope.current())

    def test_the_aggregate_parts_share_the_ops_scope(self):
        lock = threading.Lock()
        scopes = []

        def remember(where):
            with lock:
                scopes.append((where, call_scope.current()))

        def entity(config, query):
            remember("prelude")
            call_scope.current().mark_failed(("gcp", "gemini-3.5-flash"), reason="timeout", agent="entity")
            return SimpleNamespace(model_dump=lambda: {"sampletypes": [{"code": "NHP"}]})

        def parser(session, config, query, entity_out):
            remember(query)
            return ParserPlan(mode="graph_query", target_endpoint=None)

        def graph(config, query, entity_out, parser_plan, retry_context=None, refine_context=None):
            remember(query)
            assert call_scope.current().failed(("gcp", "gemini-3.5-flash")) is not None, "the part sees the mark"
            return GraphAgentPlan(cypher="MATCH (s:T_TIS) RETURN count(DISTINCT s) AS n", parameters={})

        parts = ["How many TIS samples?", "How many NHP samples?"]
        with patch("chat_nextseek.portable.entity_agent", side_effect=entity), \
             patch("chat_nextseek.portable.parser_agent", side_effect=parser), \
             patch("chat_nextseek.portable.graph_agent", side_effect=graph):
            out = run_op("aggregate", {"query": "TIS and NHP", "parts": json.dumps(parts)},
                         config=SimpleNamespace(), session=SimpleNamespace(), write_gate=MagicMock(),
                         neo4j_exec=lambda config, cypher, params: _ok([{"n": 3}]))

        self.assertTrue(out["complete"], out)
        self.assertEqual({where for where, _ in scopes},
                         {"prelude", "TIS and NHP\n\nParts:\n- " + "\n- ".join(parts), *parts})
        self.assertEqual(len({id(s) for _, s in scopes}), 1, "one scope object across the pool's threads")
        self.assertIsNotNone(scopes[0][1])

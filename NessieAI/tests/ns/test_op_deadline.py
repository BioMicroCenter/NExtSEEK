"""Each Container-CC op carries its deadline into every model call it makes (F4, ruling D5, 2026-09-28).

The sidecar waits 60 s for an op. ``run_op`` opens the op's ``call_scope`` with a 55 s deadline (5 s left for the
Neo4j step and the answer), and the aggregate op, which answers at its own 85 s, tightens it to that. The ladder then
cuts every attempt to fit (pinned in NessieAI/tests/chat_nextseek/test_call_scope_deadline.py). A part of the
aggregate op that runs out of time before its model call could start is a ``timed_out`` part, as a part still
running at the deadline always was, not a failed op. Every agent is faked; no model is called.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import SimpleTestCase

from chat_nextseek import call_scope
from chat_nextseek.llm_clients import LLMFatalError
from chat_nextseek.schemas import GraphAgentPlan, ParserPlan
from NessieAI.ns import aggregate, granular
from NessieAI.ns.granular import run_op
from NessieAI.ns.op_limits import OP_LIMITS_S, SIDECAR_ROAD_CAP_S

SIDECAR_CLIENT = Path(granular.__file__).resolve().parents[1] / "docker" / "ns-sidecar" / "app" / "ns_client.py"


class _Clock:
    def __init__(self):
        self.now = 500.0

    def __call__(self):
        return self.now


class OpDeadlineTests(SimpleTestCase):
    def setUp(self):
        self.clock = _Clock()
        patcher = patch.object(call_scope, "_monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_on_the_sidecar_road_the_graph_op_still_fits_the_sidecars_wait(self):
        sidecar_s = float(re.search(r"^_TIMEOUT = ([0-9.]+)", SIDECAR_CLIENT.read_text(), re.M).group(1))
        self.assertEqual(sidecar_s, 60.0)
        self.assertLess(min(OP_LIMITS_S["graph"], SIDECAR_ROAD_CAP_S), sidecar_s)
        self.assertEqual(aggregate.op_deadline_s(None), 85.0)

    def test_the_limit_passed_in_replaces_the_table(self):
        seen = []

        def entity(config, query):
            seen.append(call_scope.current().total_s)
            return SimpleNamespace(model_dump=lambda: {"sampletypes": []})

        with patch("chat_nextseek.portable.entity_agent", side_effect=entity):
            run_op("entity", {"query": "mice"}, config=SimpleNamespace(), session=None, write_gate=MagicMock(),
                   limit_s=30.0)

        self.assertEqual(seen, [30.0])

    def test_a_report_op_gets_its_150_s_and_the_handler_is_told(self):
        seen = []

        def report(args, config, session, write_gate, neo4j_exec, outputs_dir, **op_ctx):
            seen.append((call_scope.current().total_s, op_ctx["limit_s"], op_ctx["turn"]))
            return {}

        with patch.dict(granular._HANDLERS, {"report": report}):
            run_op("report", {"mode": "samples", "project": "p"}, config=SimpleNamespace(), session=None,
                   write_gate=MagicMock())

        self.assertEqual(seen, [(150.0, 150.0, None)])

    def test_the_aggregate_op_answers_five_seconds_inside_its_limit(self):
        seen = []

        def entity(config, query):
            seen.append(call_scope.current().remaining())
            return SimpleNamespace(model_dump=lambda: {"sampletypes": []})

        def graph(config, query, entity_out, parser_plan, retry_context=None, refine_context=None):
            return GraphAgentPlan(cypher="MATCH (s:T_TIS) RETURN count(DISTINCT s) AS n", parameters={})

        with patch("chat_nextseek.portable.entity_agent", side_effect=entity), \
             patch("chat_nextseek.portable.parser_agent", return_value=ParserPlan(mode="graph_query")), \
             patch("chat_nextseek.portable.graph_agent", side_effect=graph):
            out = run_op("aggregate", {"query": "How many TIS samples?", "parts": ""}, config=SimpleNamespace(),
                         session=SimpleNamespace(), write_gate=MagicMock(), limit_s=40.0,
                         neo4j_exec=lambda config, cypher, params: {"ok": True, "data": [{"n": 3}], "count": 1,
                                                                    "total": 1, "truncated": False,
                                                                    "scope": {"decision": "proven"}})

        self.assertEqual(seen, [pytest.approx(35.0)])
        self.assertEqual(out["deadline_s"], 35.0)

    def test_a_graph_op_with_a_short_limit_starts_no_fallback_late(self):
        self.assertEqual(granular.fallback_start_budget_s(None), 60.0)
        self.assertEqual(granular.fallback_start_budget_s(40.0), 10.0)
        self.assertEqual(granular.fallback_start_budget_s(20.0), 0.0)

    def test_an_op_runs_with_its_55_s_deadline(self):
        seen = []

        def entity(config, query):
            scope = call_scope.current()
            seen.append((scope.total_s, scope.remaining()))
            return SimpleNamespace(model_dump=lambda: {"sampletypes": []})

        with patch("chat_nextseek.portable.entity_agent", side_effect=entity):
            run_op("entity", {"query": "mice"}, config=SimpleNamespace(), session=None, write_gate=MagicMock())

        self.assertEqual(seen, [(55.0, pytest.approx(55.0))])

    def test_the_aggregate_op_tightens_the_deadline_to_its_own_85_s(self):
        seen = []

        def entity(config, query):
            seen.append(call_scope.current().remaining())
            return SimpleNamespace(model_dump=lambda: {"sampletypes": []})

        def graph(config, query, entity_out, parser_plan, retry_context=None, refine_context=None):
            return GraphAgentPlan(cypher="MATCH (s:T_TIS) RETURN count(DISTINCT s) AS n", parameters={})

        with patch("chat_nextseek.portable.entity_agent", side_effect=entity), \
             patch("chat_nextseek.portable.parser_agent", return_value=ParserPlan(mode="graph_query")), \
             patch("chat_nextseek.portable.graph_agent", side_effect=graph):
            run_op("aggregate", {"query": "How many TIS samples?", "parts": ""}, config=SimpleNamespace(),
                   session=SimpleNamespace(), write_gate=MagicMock(),
                   neo4j_exec=lambda config, cypher, params: {"ok": True, "data": [{"n": 3}], "count": 1,
                                                              "total": 1, "truncated": False,
                                                              "scope": {"decision": "proven"}})

        self.assertEqual(seen, [pytest.approx(85.0)])

    def test_a_part_that_ran_out_of_time_is_timed_out_not_a_failed_op(self):
        parts = ["How many TIS samples?", "How many NHP samples?"]

        def graph(config, query, entity_out, parser_plan, retry_context=None, refine_context=None):
            if query == parts[1]:
                raise LLMFatalError("deadline: the op's 50 s ran out before the graph call could start",
                                    agent="graph", reason="deadline")
            return GraphAgentPlan(cypher="MATCH (s:T_TIS) RETURN count(DISTINCT s) AS n", parameters={})

        with patch("chat_nextseek.portable.entity_agent",
                   return_value=SimpleNamespace(model_dump=lambda: {"sampletypes": []})), \
             patch("chat_nextseek.portable.parser_agent", return_value=ParserPlan(mode="graph_query")), \
             patch("chat_nextseek.portable.graph_agent", side_effect=graph):
            out = run_op("aggregate", {"query": "TIS and NHP", "parts": json.dumps(parts)},
                         config=SimpleNamespace(), session=SimpleNamespace(), write_gate=MagicMock(),
                         neo4j_exec=lambda config, cypher, params: {"ok": True, "data": [{"n": 3}], "count": 1,
                                                                    "total": 1, "truncated": False,
                                                                    "scope": {"decision": "proven"}})

        self.assertEqual([p["status"] for p in out["parts"]], ["ok", "timed_out"])
        self.assertFalse(out["complete"])
        self.assertTrue(any("Part 2 did not finish" in n for n in out["notes"]), out["notes"])

    def test_a_prelude_that_ran_out_of_time_is_the_vocabulary_note_not_a_failed_op(self):
        def entity(config, query):
            raise LLMFatalError("deadline: the op's 50 s ran out before the entity call could start",
                                agent="entity", reason="deadline")

        with patch("chat_nextseek.portable.entity_agent", side_effect=entity):
            out = run_op("aggregate", {"query": "How many TIS samples?", "parts": ""}, config=SimpleNamespace(),
                         session=SimpleNamespace(), write_gate=MagicMock(), neo4j_exec=MagicMock())

        self.assertEqual([p["status"] for p in out["parts"]], ["timed_out"])
        self.assertTrue(any("vocabulary was not resolved" in n for n in out["notes"]), out["notes"])

    def test_a_models_unavailable_fatal_still_fails_the_op(self):
        """Only the deadline is softened: both models down is AGENT_FAILED model_unavailable, as before."""
        def entity(config, query):
            raise LLMFatalError("All provider fallbacks exhausted", agent="entity", unavailable=True, reason="timeout")

        with patch("chat_nextseek.portable.entity_agent", side_effect=entity), self.assertRaises(LLMFatalError):
            run_op("aggregate", {"query": "How many TIS samples?", "parts": ""}, config=SimpleNamespace(),
                   session=SimpleNamespace(), write_gate=MagicMock(), neo4j_exec=MagicMock())

"""Dispatch-logic tests for the 7 granular ops (chat_nextseek agents mocked).

These are FREE tests: every chat_nextseek agent is patched so no LLM/DB call is
made. They prove the routing, argument order, result shape, and — critically —
that the write gate fires BEFORE any agent/LLM call on an unconfirmed write.
The real chat_nextseek runs only in the paid real-stack acceptance tier.
"""
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase

from nextseek_api.assistant.granular import OpValidationError, run_op
from nextseek_api.assistant.write_gate import (
    WriteBlockedError,
    build_gate,
    load_allowlist_from_entries,
)


ALLOWLIST = load_allowlist_from_entries(
    [{"endpoint": "/nextseek_api/samples/advanced_search/", "methods": ["POST"]}]
)


def _dumpable(payload):
    """A stand-in agent return value exposing .model_dump()."""
    m = MagicMock()
    m.model_dump.return_value = payload
    return m


class DispatchTests(SimpleTestCase):
    def setUp(self):
        self.config = SimpleNamespace()
        self.session = SimpleNamespace()
        self.gate = build_gate(ALLOWLIST)

    def _run(self, op, args, **kw):
        return run_op(op, args, config=self.config, session=self.session,
                      write_gate=self.gate, **kw)

    # --- entity ---
    def test_entity_routes_to_entity_agent(self):
        with patch("chat_nextseek.portable.entity_agent") as ent:
            ent.return_value = _dumpable({"sampletypes": [{"code": "MUS"}]})
            out = self._run("entity", {"query": "mouse samples"})
        ent.assert_called_once_with(self.config, "mouse samples")
        self.assertEqual(out, {"sampletypes": [{"code": "MUS"}]})

    # --- parse ---
    def test_parse_runs_entity_then_parser_with_session(self):
        with patch("chat_nextseek.portable.entity_agent") as ent, \
             patch("chat_nextseek.portable.parser_agent") as par:
            ent.return_value = _dumpable({"sampletypes": []})
            par.return_value = _dumpable({"target_endpoint": "/nextseek_api/samples/advanced_search/"})
            out = self._run("parse", {"query": "mouse samples"})
        # parser_agent(session, config, query, entity_out)
        self.assertEqual(par.call_args.args[0], self.session)
        self.assertEqual(par.call_args.args[1], self.config)
        self.assertEqual(par.call_args.args[2], "mouse samples")
        self.assertEqual(out["target_endpoint"], "/nextseek_api/samples/advanced_search/")

    # --- graph (plan + executed rows, per design decision) ---
    def test_graph_returns_plan_and_executed_rows(self):
        neo4j_exec = MagicMock(return_value={"ok": True, "data": [{"uuid": "MUS-1"}]})
        with patch("chat_nextseek.portable.entity_agent") as ent, \
             patch("chat_nextseek.portable.graph_agent") as gr:
            ent.return_value = _dumpable({"sampletypes": [{"code": "MUS"}]})
            gr.return_value = _dumpable({"cypher": "MATCH (s:Sample) RETURN s", "parameters": {}})
            out = self._run("graph", {"query": "lineage of mouse samples"}, neo4j_exec=neo4j_exec)
        self.assertEqual(out["plan"]["cypher"], "MATCH (s:Sample) RETURN s")
        self.assertEqual(out["result"], {"ok": True, "data": [{"uuid": "MUS-1"}]})
        neo4j_exec.assert_called_once()

    # --- api-read (allowlist-gated; builds plan then gates) ---
    def test_api_read_builds_plan_then_gates_then_requests(self):
        plan = SimpleNamespace(endpoint="/nextseek_api/samples/advanced_search/", method="POST",
                               requestBody={}, queryParameters={})
        plan.model_dump = lambda: {"endpoint": plan.endpoint, "method": "POST"}
        with patch("chat_nextseek.portable.api_agent_build_request", return_value=plan) as build, \
             patch("chat_nextseek.helpers.tool_nextseek_api_request") as req:
            req.return_value = {"ok": True, "data": {"rows": [{"uid": "MUS-240101ABC-1"}]}}
            out = self._run("api-read", {"parser_plan": "{\"target_endpoint\": \"x\"}"})
        build.assert_called_once()
        req.assert_called_once()
        self.assertEqual(out["endpoint"], "/nextseek_api/samples/advanced_search/")
        self.assertEqual(out["method"], "POST")
        self.assertTrue(out["response"]["ok"])

    def test_api_read_non_allowlisted_blocks_after_build(self):
        plan = SimpleNamespace(endpoint="/nextseek_api/samples/", method="POST",
                               requestBody={}, queryParameters={})
        plan.model_dump = lambda: {}
        with patch("chat_nextseek.portable.api_agent_build_request", return_value=plan), \
             patch("chat_nextseek.helpers.tool_nextseek_api_request") as req:
            with self.assertRaises(WriteBlockedError):
                self._run("api-read", {"parser_plan": "{}"})
            req.assert_not_called()

    # --- api-write: the safety-critical assertions ---
    def test_api_write_unconfirmed_blocks_before_any_agent_call(self):
        with patch("chat_nextseek.portable.api_agent_build_request") as build, \
             patch("chat_nextseek.helpers.tool_nextseek_api_request") as req:
            with self.assertRaises(WriteBlockedError):
                self._run("api-write", {"parser_plan": "{}", "confirmed_write": False})
            # No plan built, no request issued => no LLM, no DB mutation possible.
            build.assert_not_called()
            req.assert_not_called()

    def test_api_write_string_true_still_blocks(self):
        with patch("chat_nextseek.portable.api_agent_build_request") as build, \
             patch("chat_nextseek.helpers.tool_nextseek_api_request") as req:
            with self.assertRaises(WriteBlockedError):
                self._run("api-write", {"parser_plan": "{}", "confirmed_write": "true"})
            build.assert_not_called()
            req.assert_not_called()

    def test_api_write_confirmed_executes(self):
        plan = SimpleNamespace(endpoint="/nextseek_api/samples/advanced_search/", method="POST",
                               requestBody={}, queryParameters={})
        plan.model_dump = lambda: {"endpoint": plan.endpoint, "method": "POST"}
        with patch("chat_nextseek.portable.api_agent_build_request", return_value=plan) as build, \
             patch("chat_nextseek.helpers.tool_nextseek_api_request") as req:
            req.return_value = {"ok": True, "data": {}}
            out = self._run("api-write", {"parser_plan": "{}", "confirmed_write": True})
        build.assert_called_once()
        req.assert_called_once()
        self.assertTrue(out["response"]["ok"])

    # --- report (no LLM; SQL/Neo4j) ---
    def test_report_routes_to_run_reporter_summary(self):
        with patch("chat_nextseek.helpers.run_reporter_summary") as rrs:
            rrs.return_value = ({"ok": True, "rows_returned": 3},
                                {"published_report": "/tmp/x.json"},
                                {"summary_mode": "published"})
            out = self._run("report", {"mode": "published", "project": "Published Data"},
                            outputs_dir="/tmp")
        rrs.assert_called_once()
        self.assertEqual(out["saved_files"], {"published_report": "/tmp/x.json"})
        self.assertEqual(out["summary"], {"summary_mode": "published"})
        self.assertEqual(out["rows"], {"ok": True, "rows_returned": 3})

    def test_report_rppr_maps_summary_mode(self):
        captured = {}

        def fake_rrs(config, reporter_plan, log_dir):
            captured["summary_mode"] = reporter_plan.summary_mode
            return ({}, {}, {})
        with patch("chat_nextseek.helpers.run_reporter_summary", side_effect=fake_rrs):
            self._run("report", {"mode": "rppr", "project": "P"}, outputs_dir="/tmp")
        self.assertEqual(captured["summary_mode"], "RPPR")

    # --- generate-submission ---
    def test_generate_submission_routes_to_report_writer(self):
        captured = {}

        def fake_writer(config, query, plan):
            captured["report_type"] = plan.report_type
            captured["uids"] = plan.reporter_context.get("uids")
            return _dumpable({"report_type": "GEO", "report": {"samples": []}})
        with patch("chat_nextseek.portable.report_writer_agent", side_effect=fake_writer):
            out = self._run("generate-submission", {"type": "GEO", "uids": "MUS-1, MUS-2"})
        self.assertEqual(captured["report_type"], "GEO")
        self.assertEqual(captured["uids"], ["MUS-1", "MUS-2"])
        self.assertEqual(out["report_type"], "GEO")

    def test_generate_submission_defaults_blank_query(self):
        # A blank/absent query must be replaced with a non-empty, type-aware default
        # (some providers reject an empty user message content block).
        captured = {}

        def fake_writer(config, query, plan):
            captured["query"] = query
            return _dumpable({"report_type": "GEO", "report": {}})
        with patch("chat_nextseek.portable.report_writer_agent", side_effect=fake_writer):
            self._run("generate-submission", {"type": "GEO", "uids": "MUS-1"})  # no query key
        self.assertTrue(captured["query"].strip(), "blank query reached report_writer")
        self.assertIn("GEO", captured["query"])

    # --- validation / unknown op ---
    def test_api_read_invalid_json_raises_validation(self):
        with self.assertRaises(OpValidationError):
            self._run("api-read", {"parser_plan": "{not json"})

    def test_unknown_op_raises_validation(self):
        with self.assertRaises(OpValidationError):
            self._run("bogus", {"query": "x"})

"""Dispatch-logic tests for the 7 granular ops (chat_nextseek agents mocked).

These are FREE tests: every chat_nextseek agent is patched so no LLM/DB call is
made. They prove the routing, argument order, result shape, and — critically —
that the write gate fires BEFORE any agent/LLM call on an unconfirmed write.
The real chat_nextseek runs only in the paid real-stack acceptance tier.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase

from NessieAI.ns.granular import OpValidationError, run_op
from NessieAI.ns.write_gate import (
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
             patch("chat_nextseek.portable.parser_agent") as par, \
             patch("chat_nextseek.portable.graph_agent") as gr:
            ent.return_value = _dumpable({"sampletypes": [{"code": "MUS"}]})
            par.return_value = _dumpable({"target_endpoint": "graph"})
            gr.return_value = _dumpable({"cypher": "MATCH (s:Sample) RETURN s", "parameters": {}})
            out = self._run("graph", {"query": "lineage of mouse samples"}, neo4j_exec=neo4j_exec)
        self.assertEqual(out["plan"]["cypher"], "MATCH (s:Sample) RETURN s")
        self.assertEqual(out["result"], {"ok": True, "data": [{"uuid": "MUS-1"}]})
        neo4j_exec.assert_called_once()

    def test_graph_runs_parser_and_passes_plan_to_graph_agent(self):
        """The CC/granular path MUST run the parser and pass its plan to
        graph_agent, mirroring the NS orchestrator (orchestrator.py:869). Without
        the parser_plan, graph_agent gets no PARSER PLAN block and emits
        unbounded, pathological Cypher that 504s (GitHub issue #20)."""
        neo4j_exec = MagicMock(return_value={"ok": True, "data": []})
        with patch("chat_nextseek.portable.entity_agent") as ent, \
             patch("chat_nextseek.portable.parser_agent") as par, \
             patch("chat_nextseek.portable.graph_agent") as gr:
            ent.return_value = _dumpable({"sampletypes": [{"code": "MUS"}]})
            par.return_value = _dumpable({"target_endpoint": "graph"})
            gr.return_value = _dumpable({"cypher": "MATCH (s:Sample) RETURN s", "parameters": {}})
            self._run("graph", {"query": "lineage of mouse samples"}, neo4j_exec=neo4j_exec)
        # parser_agent(session, config, query, entity_out) — same order as _parse
        self.assertTrue(par.called, "granular._graph must invoke parser_agent")
        self.assertEqual(par.call_args.args[0], self.session)
        self.assertEqual(par.call_args.args[1], self.config)
        self.assertEqual(par.call_args.args[2], "lineage of mouse samples")
        self.assertIs(par.call_args.args[3], ent.return_value)
        # graph_agent(config, query, entity_out, parser_plan) — the plan is passed
        # as the 4th positional arg and is the parser_agent output, NOT None.
        self.assertGreaterEqual(
            len(gr.call_args.args), 4,
            "graph_agent must receive the parser_plan positionally",
        )
        self.assertIs(gr.call_args.args[3], par.return_value)

    # --- graph with the agent's own plan (round 7 T1) ---
    def _graph_with_plan(self, plan, vocab=None, neo4j=None):
        neo4j_exec = neo4j or MagicMock(return_value={"ok": True, "data": []})
        vocab = vocab if vocab is not None else {"sampletypes": [], "lab_codes": ["WAD"]}
        with patch("chat_nextseek.portable.entity_agent") as ent, \
             patch("chat_nextseek.portable.parser_agent", side_effect=AssertionError("no parser call")) as par, \
             patch("chat_nextseek.portable.graph_agent") as gr:
            ent.return_value = _dumpable(vocab)
            gr.return_value = _dumpable({"cypher": "MATCH (s:Sample) RETURN s", "parameters": {}})
            args = {"query": "q", "plan": plan if isinstance(plan, str) else json.dumps(plan)}
            out = self._run("graph", args, neo4j_exec=neo4j_exec)
        return out, par, gr

    def test_a_valid_plan_skips_the_parser_and_reaches_the_graph_agent_as_graph_query(self):
        plan = {"intent_summary": "NHP at WAD", "notes": "n",
                "filters": {"sampletype_code": "NHP", "keywords": ["x"], "lab_codes": ["WAD"], "uids": ["NHP-1"]}}
        out, par, gr = self._graph_with_plan(plan)
        par.assert_not_called()
        sent = gr.call_args.args[3]
        self.assertEqual(sent.mode, "graph_query")
        self.assertEqual(sent.filters.sampletype_code, "NHP")
        self.assertEqual(sent.filters.lab_codes, ["WAD"])
        self.assertEqual(sent.intent_summary, "NHP at WAD")
        self.assertEqual(sent.resolved.lab_codes, ["WAD"], "resolved is the vocabulary, set by Django")
        self.assertEqual(out["plan_source"], "agent")

    def test_every_invalid_plan_is_a_validation_error_naming_its_field_and_calls_no_model(self):
        cases = {
            "not json": "plan",
            '{"mode": "graph_query"}': "plan.mode",
            '{"resolved": {}}': "plan.resolved",
            '{"filters": {"bogus": 1}}': "plan.filters.bogus",
            '{"filters": {"keywords": "x"}}': "plan.filters.keywords",
            '{"filters": {"keywords": ["x"] }, "intent_summary": 5}': "plan.intent_summary",
            json.dumps({"filters": {"keywords": ["k"] * 21}}): "plan.filters.keywords",
            json.dumps({"filters": {"uids": ["u" * 201]}}): "plan.filters.uids.0",
            json.dumps({"intent_summary": "s" * 1001}): "plan.intent_summary",
            json.dumps({"notes": "s" * 1001}): "plan.notes",
            '[]': "plan",
        }
        for raw, field in cases.items():
            with self.subTest(raw=raw[:40]), patch("chat_nextseek.portable.entity_agent") as ent, \
                 patch("chat_nextseek.portable.parser_agent") as par, patch("chat_nextseek.portable.graph_agent") as gr:
                with self.assertRaises(OpValidationError) as ctx:
                    self._run("graph", {"query": "q", "plan": raw}, neo4j_exec=MagicMock())
                self.assertEqual(ctx.exception.field, field)
                for agent in (ent, par, gr):
                    agent.assert_not_called()

    def test_a_lab_code_outside_the_vocabulary_is_refused_not_dropped_and_no_graph_or_parser_call_is_made(self):
        with patch("chat_nextseek.portable.entity_agent") as ent, \
             patch("chat_nextseek.portable.parser_agent") as par, patch("chat_nextseek.portable.graph_agent") as gr:
            ent.return_value = _dumpable({"lab_codes": ["WAD"]})
            with self.assertRaises(OpValidationError) as ctx:
                self._run("graph", {"query": "q", "plan": json.dumps({"filters": {"lab_codes": ["XYZ"]}})},
                          neo4j_exec=MagicMock())
        self.assertEqual(ctx.exception.field, "plan.filters.lab_codes")
        self.assertEqual(ctx.exception.field_error()["type"], "unknown_lab_code")
        par.assert_not_called()
        gr.assert_not_called()

    def test_cypher_projects_and_endpoint_in_a_plan_are_refused(self):
        for key in ("cypher", "projects", "target_endpoint", "parameters"):
            for raw in (json.dumps({key: "x"}), json.dumps({"filters": {key: "x"}})):
                with self.subTest(raw=raw), patch("chat_nextseek.portable.graph_agent") as gr:
                    with self.assertRaises(OpValidationError):
                        self._run("graph", {"query": "q", "plan": raw}, neo4j_exec=MagicMock())
                    gr.assert_not_called()

    def test_an_agent_plan_runs_through_the_scoped_tool_like_any_other(self):
        neo4j = MagicMock(return_value={"ok": True, "data": [{"uuid": "NHP-1"}]})
        out, _, _ = self._graph_with_plan({"intent_summary": "x"}, neo4j=neo4j)
        neo4j.assert_called_once()
        self.assertEqual(out["result"]["data"], [{"uuid": "NHP-1"}])

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

    # --- generate-submission (routes through the NS orchestration) ---
    def test_generate_submission_routes_to_generate_report_outputs(self):
        """generate-submission runs the same NS orchestration the run_query
        report_generation path uses (generate_report_outputs) — NOT the leaf
        report_writer_agent directly — so it gets the type template, full
        context, and the emitter workbooks. It returns the flat writer output
        plus the real saved_files."""
        captured = {}

        def fake_gro(**kw):
            captured.update(kw)
            rwo = {"all_samples": {"report_type": "GEO", "report": {"study": {}},
                                   "narrative": None, "notes": None}}
            saved = {"geo_seq_workbooks": ["/o/geo.xlsx"], "merged_report": "/o/m.json"}
            return {"reports": []}, rwo, saved, "done"
        with patch("chat_nextseek.portable.generate_report_outputs", side_effect=fake_gro) as gro, \
             patch("chat_nextseek.portable.report_writer_agent") as rwa:
            out = self._run("generate-submission", {"type": "GEO", "uids": "MUS-1, MUS-2"},
                            outputs_dir="/o")
        self.assertTrue(gro.called, "generate-submission must route through generate_report_outputs")
        self.assertEqual(captured["reporter_plan"].report_type, "GEO")
        self.assertEqual(captured["parser_plan"]["report_type"], "GEO")
        self.assertEqual(captured["uids"], ["MUS-1", "MUS-2"])
        self.assertFalse(captured["per_sample_reports"])       # combined mode
        self.assertEqual(captured["log_dir"], "/o")
        self.assertIs(captured["report_writer_fn"], rwa)        # passes the portable writer
        # flat writer output (unwrapped from all_samples) + real saved_files
        self.assertEqual(out["report_type"], "GEO")
        self.assertNotIn("all_samples", out)
        self.assertEqual(out["saved_files"]["geo_seq_workbooks"], ["/o/geo.xlsx"])

    def test_generate_submission_is_report_type_agnostic(self):
        """report_type flows through for SRA / PRIDE alike — no GEO hardcoding."""
        seen = []

        def fake_gro(**kw):
            rt = kw["reporter_plan"].report_type
            seen.append(rt)
            return {"reports": []}, {"all_samples": {"report_type": rt, "report": {}}}, {}, ""
        with patch("chat_nextseek.portable.generate_report_outputs", side_effect=fake_gro), \
             patch("chat_nextseek.portable.report_writer_agent"):
            for t in ("SRA", "PRIDE"):
                out = self._run("generate-submission", {"type": t, "uids": "X-1"}, outputs_dir="/o")
                self.assertEqual(out["report_type"], t)
        self.assertEqual(seen, ["SRA", "PRIDE"])

    def test_generate_submission_defaults_blank_query(self):
        # A blank/absent query must be replaced with a non-empty, type-aware default
        # (some providers reject an empty user message content block).
        captured = {}

        def fake_gro(**kw):
            captured.update(kw)
            return {"reports": []}, {"all_samples": {"report_type": "GEO", "report": {}}}, {}, ""
        with patch("chat_nextseek.portable.generate_report_outputs", side_effect=fake_gro), \
             patch("chat_nextseek.portable.report_writer_agent"):
            self._run("generate-submission", {"type": "GEO", "uids": "MUS-1"}, outputs_dir="/o")
        self.assertTrue(captured["user_query"].strip(), "blank query reached generate_report_outputs")
        self.assertIn("GEO", captured["user_query"])

    # --- validation / unknown op ---
    def test_api_read_invalid_json_raises_validation(self):
        with self.assertRaises(OpValidationError):
            self._run("api-read", {"parser_plan": "{not json"})

    def test_unknown_op_raises_validation(self):
        with self.assertRaises(OpValidationError):
            self._run("bogus", {"query": "x"})

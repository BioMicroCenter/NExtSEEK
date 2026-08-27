"""Endpoint tests for the 7 native granular-op actions on AssistantViewSet.

chat_nextseek agents are patched so these are FREE (no LLM/DB-of-record calls).
They cover the HTTP envelope, request validation (422), the write-blocked path
(403 + WRITE_BLOCKED, agent never called), and auth (401/403). The real-stack
provenance + write-DB-unchanged proofs live in the paid acceptance tier.
"""
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession


def _dumpable(payload):
    m = MagicMock()
    m.model_dump.return_value = payload
    return m


def _plan(endpoint, method="POST"):
    p = MagicMock()
    p.endpoint = endpoint
    p.method = method
    p.requestBody = {}
    p.queryParameters = {}
    p.model_dump.return_value = {"endpoint": endpoint, "method": method,
                                 "requestBody": {}, "queryParameters": {}, "notes": ""}
    return p


class GranularEndpointBase(TestCase):
    databases = {"default"}
    BASE = "/nextseek_api/assistant"

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class EntityEndpointTests(GranularEndpointBase):
    def test_entity_returns_op_result_envelope(self):
        with patch("chat_nextseek.portable.entity_agent",
                   return_value=_dumpable({"sampletypes": [{"code": "MUS"}], "assays": [],
                                           "keywords": [], "projects": []})):
            resp = self.client.post(f"{self.BASE}/entity/", {"query": "mouse"}, format="json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["op"], "entity")
        self.assertEqual(body["result"]["sampletypes"][0]["code"], "MUS")

    def test_entity_missing_query_returns_422(self):
        resp = self.client.post(f"{self.BASE}/entity/", {}, format="json")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], "VALIDATION")


class GraphEndpointTests(GranularEndpointBase):
    def test_graph_returns_plan_and_executed_rows(self):
        with patch("chat_nextseek.portable.entity_agent",
                   return_value=_dumpable({"sampletypes": [{"code": "MUS"}]})), \
             patch("chat_nextseek.portable.parser_agent",
                   return_value=_dumpable({"target_endpoint": "graph"})), \
             patch("chat_nextseek.portable.graph_agent",
                   return_value=_dumpable({"cypher": "MATCH (s) RETURN s", "parameters": {}})), \
             patch("chat_nextseek.helpers.tool_neo4j_query",
                   return_value={"ok": True, "data": [{"uuid": "MUS-1"}]}):
            resp = self.client.post(f"{self.BASE}/graph/", {"query": "lineage"}, format="json")
        self.assertEqual(resp.status_code, 200)
        result = resp.json()["result"]
        self.assertEqual(result["plan"]["cypher"], "MATCH (s) RETURN s")
        self.assertEqual(result["result"]["data"][0]["uuid"], "MUS-1")

    def test_graph_endpoint_builds_a_session_for_the_parser(self):
        """graph now runs parser_agent, which reads results_history off the
        session (build_recent_results_summary). The viewset must build a
        (transient) session for graph like it does for parse — passing None
        makes the real parser_agent crash with 'NoneType has no attribute get'."""
        captured = {}

        def fake_parser(session, config, query, entity):
            captured["session"] = session
            return _dumpable({"target_endpoint": "graph"})

        with patch("chat_nextseek.portable.entity_agent",
                   return_value=_dumpable({"sampletypes": []})), \
             patch("chat_nextseek.portable.parser_agent", side_effect=fake_parser), \
             patch("chat_nextseek.portable.graph_agent",
                   return_value=_dumpable({"cypher": "MATCH (s) RETURN s", "parameters": {}})), \
             patch("chat_nextseek.helpers.tool_neo4j_query",
                   return_value={"ok": True, "data": []}):
            resp = self.client.post(f"{self.BASE}/graph/", {"query": "lineage"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(
            captured.get("session"),
            "graph endpoint must build a session for parser_agent, not pass None",
        )


class ApiReadEndpointTests(GranularEndpointBase):
    def test_api_read_allowlisted_returns_response(self):
        with patch("chat_nextseek.portable.api_agent_build_request",
                   return_value=_plan("/nextseek_api/samples/advanced_search/", "POST")), \
             patch("chat_nextseek.helpers.tool_nextseek_api_request",
                   return_value={"ok": True, "data": {"rows": [{"uid": "MUS-1"}]}}):
            resp = self.client.post(f"{self.BASE}/api-read/",
                                    {"parser_plan": "{\"target_endpoint\": \"x\"}"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["result"]["response"]["ok"])


class ApiWriteEndpointTests(GranularEndpointBase):
    def test_unconfirmed_write_returns_403_and_never_calls_agent(self):
        with patch("chat_nextseek.portable.api_agent_build_request") as build, \
             patch("chat_nextseek.helpers.tool_nextseek_api_request") as req:
            resp = self.client.post(f"{self.BASE}/api-write/",
                                    {"parser_plan": "{}", "confirmed_write": False}, format="json")
            build.assert_not_called()
            req.assert_not_called()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "WRITE_BLOCKED")

    def test_string_true_confirmed_write_returns_422(self):
        # Strict bool request model rejects "true" before the op runs.
        with patch("chat_nextseek.portable.api_agent_build_request") as build:
            resp = self.client.post(f"{self.BASE}/api-write/",
                                    {"parser_plan": "{}", "confirmed_write": "true"}, format="json")
            build.assert_not_called()
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], "VALIDATION")

    def test_confirmed_write_executes(self):
        with patch("chat_nextseek.portable.api_agent_build_request",
                   return_value=_plan("/nextseek_api/samples/advanced_search/", "POST")), \
             patch("chat_nextseek.helpers.tool_nextseek_api_request",
                   return_value={"ok": True, "data": {}}):
            resp = self.client.post(f"{self.BASE}/api-write/",
                                    {"parser_plan": "{}", "confirmed_write": True}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["result"]["response"]["ok"])


class ReportEndpointTests(GranularEndpointBase):
    def test_report_returns_result(self):
        with patch("chat_nextseek.helpers.run_reporter_summary",
                   return_value=({"ok": True}, {"published_report": "/tmp/x.json"},
                                 {"summary_mode": "published"})):
            resp = self.client.post(f"{self.BASE}/report/",
                                    {"mode": "published", "project": "Published Data"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["result"]["saved_files"], {"published_report": "/tmp/x.json"})

    def test_report_bad_mode_returns_422(self):
        resp = self.client.post(f"{self.BASE}/report/", {"mode": "bogus", "project": "P"}, format="json")
        self.assertEqual(resp.status_code, 422)


class SubmissionEndpointTests(GranularEndpointBase):
    def test_generate_submission_returns_result(self):
        captured = {}

        def fake_gro(**kw):
            captured.update(kw)
            rwo = {"all_samples": {"report_type": "GEO", "report": {"samples": []},
                                   "narrative": "", "notes": ""}}
            return {"reports": []}, rwo, {"merged_report": "/tmp/m.json"}, ""
        with patch("chat_nextseek.portable.generate_report_outputs", side_effect=fake_gro), \
             patch("chat_nextseek.portable.report_writer_agent"):
            resp = self.client.post(f"{self.BASE}/generate-submission/",
                                    {"type": "GEO", "uids": "MUS-1"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["result"]["report_type"], "GEO")
        self.assertIn("saved_files", resp.json()["result"])
        # A3: the viewset hands generate-submission a writable log_dir under outputs/
        self.assertTrue(
            str(captured["log_dir"]).startswith(str(Path(settings.BASE_DIR) / "outputs")),
            f"log_dir {captured.get('log_dir')!r} not under an allowed artifact root",
        )

    def test_bad_type_returns_422(self):
        resp = self.client.post(f"{self.BASE}/generate-submission/",
                                {"type": "BOGUS", "uids": "X-1"}, format="json")
        self.assertEqual(resp.status_code, 422)


class ArtifactBundleRegistrationTests(GranularEndpointBase):
    """report / generate-submission must register a downloadable bundle so dmac can
    fetch the produced artifacts over HTTP (closes the no-HTTP-download-path gap)."""

    def test_report_registers_bundle_and_url_serves_the_file(self):
        out = Path(settings.BASE_DIR) / "outputs" / "granular_bundle_test"
        out.mkdir(parents=True, exist_ok=True)
        f = out / f"{uuid.uuid4().hex}.json"
        f.write_text('{"samples": {"rows_returned": 3}}')
        try:
            with patch("chat_nextseek.helpers.run_reporter_summary",
                       return_value=({"ok": True}, {"published_report": str(f)},
                                     {"summary_mode": "published"})):
                resp = self.client.post(f"{self.BASE}/report/",
                                        {"mode": "published", "project": "Published Data"},
                                        format="json")
            self.assertEqual(resp.status_code, 200, resp.content)
            dl = resp.json()["download"]
            self.assertTrue(dl["session_id"])
            self.assertEqual(dl["bundle_id"], 1)
            keys = {a["key"]: a["url"] for a in dl["artifacts"]}
            self.assertIn("published_report", keys)
            # a bundle is really persisted
            cs = ChatSession.objects.get(session_id=dl["session_id"])
            self.assertEqual(cs.results_history[0]["report_saved_files"]["published_report"], str(f))
            # the returned URL actually serves the file bytes
            resp2 = self.client.get(keys["published_report"])
            self.assertEqual(resp2.status_code, 200)
            body = b"".join(resp2.streaming_content) if resp2.streaming else resp2.content
            self.assertIn(b"rows_returned", body)
        finally:
            f.unlink(missing_ok=True)

    def test_generate_submission_registers_bundle_with_output(self):
        # A4: the REAL emitter workbooks (saved_files) are registered in the
        # bundle AND downloadable, alongside the on-the-fly all_tables xlsx.
        out = Path(settings.BASE_DIR) / "outputs" / "gensub_bundle_test"
        out.mkdir(parents=True, exist_ok=True)
        wb = out / f"{uuid.uuid4().hex}.xlsx"
        wb.write_bytes(b"PK\x03\x04 fake-xlsx-bytes")  # download streams bytes; not parsed

        def fake_gro(**kw):
            rwo = {"all_samples": {"report_type": "GEO",
                                   "report": {"samples": [{"uid": "X-1"}]},
                                   "narrative": "", "notes": ""}}
            return {"reports": []}, rwo, {"geo_seq_workbooks": [str(wb)]}, ""
        try:
            with patch("chat_nextseek.portable.generate_report_outputs", side_effect=fake_gro), \
                 patch("chat_nextseek.portable.report_writer_agent"):
                resp = self.client.post(f"{self.BASE}/generate-submission/",
                                        {"type": "GEO", "uids": "X-1"}, format="json")
            self.assertEqual(resp.status_code, 200, resp.content)
            dl = resp.json()["download"]
            self.assertEqual(dl["bundle_id"], 1)
            cs = ChatSession.objects.get(session_id=dl["session_id"])
            # A4: real saved_files land in the bundle (no longer hardcoded {})
            result_saved_files = cs.results_history[0]["report_saved_files"]["geo_seq_workbooks"]
            self.assertEqual(result_saved_files, [str(wb)])
            # Smoke: the registered workbook is a real, nonzero-byte file on disk
            # (not a hardcoded {} / empty placeholder). Shape here is a list of
            # path strings (not dicts with "bytes"/"path" keys), so stat() directly.
            assert any(Path(p).stat().st_size > 0 for p in result_saved_files), \
                "submission produced no nonzero workbook"
            self.assertEqual(cs.results_history[0]["report_writer_output"]["report_type"], "GEO")
            keys = {a["key"]: a["url"] for a in dl["artifacts"]}
            # both the real workbook AND the on-the-fly all_tables are exposed
            self.assertIn("geo_seq_workbooks", keys)
            self.assertIn("all_tables", keys)
            # the real workbook serves its bytes from disk
            resp2 = self.client.get(keys["geo_seq_workbooks"])
            self.assertEqual(resp2.status_code, 200)
        finally:
            wb.unlink(missing_ok=True)

    def test_entity_has_no_download_field(self):
        with patch("chat_nextseek.portable.entity_agent",
                   return_value=_dumpable({"sampletypes": [], "assays": [], "keywords": [], "projects": []})):
            resp = self.client.post(f"{self.BASE}/entity/", {"query": "x"}, format="json")
        self.assertNotIn("download", resp.json())


class AuthTests(GranularEndpointBase):
    def test_unauthenticated_returns_401(self):
        anon = APIClient()
        resp = anon.post(f"{self.BASE}/entity/", {"query": "x"}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_non_participating_user_returns_403(self):
        with patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=False,
        ):
            resp = self.client.post(f"{self.BASE}/entity/", {"query": "x"}, format="json")
        self.assertEqual(resp.status_code, 403)


def test_chat_nextseek_resolves_to_merged_tree():
    import chat_nextseek
    assert "chat_nextseek/src" in chat_nextseek.__file__.replace("\\", "/")


class ReingestEndpointTests(GranularEndpointBase):
    """The run-ls / build-upload-xlsx HTTP endpoints must be routed and
    CSRF-exempt like every other granular op. Regression lock: Wave 5 shipped
    the handlers, models, and sidecar forwarders but omitted these two @action
    methods, so the sidecar's POST hit a CSRF-enforcing fallback and 403'd —
    which the agent misread as an auth failure. Handler-only tests (test_run_ls_op)
    did not catch it because they call run_op directly, never the URL."""

    def test_run_ls_endpoint_is_routed_and_csrf_exempt(self):
        # patch run_op so the endpoint returns without SSHing Luria; the point is
        # that the URL resolves and Basic/session auth passes CSRF, not the SSH.
        with patch("nextseek_api.services.assistant.run_op",
                   return_value={"run_dir": "/x/runs/r", "truncated": False, "tree": "total 0"}):
            resp = self.client.post(f"{self.BASE}/run-ls/",
                                    {"run_dir": "/x/runs/r"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["op"], "run-ls")
        self.assertEqual(body["result"]["tree"], "total 0")

    def test_run_ls_missing_run_dir_returns_422(self):
        resp = self.client.post(f"{self.BASE}/run-ls/", {}, format="json")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], "VALIDATION")

    def test_build_upload_xlsx_endpoint_is_routed_and_returns_bundle(self):
        with patch("nextseek_api.services.assistant.run_op",
                   return_value={"summary": {}, "saved_files": {}, "rows": []}):
            resp = self.client.post(f"{self.BASE}/build-upload-xlsx/",
                                    {"rows": "[]"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["op"], "build-upload-xlsx")
        # build-upload-xlsx is an artifact op -> a download bundle is registered
        self.assertIn("download", body)
        self.assertEqual(body["download"]["bundle_id"], 1)

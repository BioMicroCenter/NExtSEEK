"""Endpoint tests for the 7 native granular-op actions on AssistantViewSet.

chat_nextseek agents are patched so these are FREE (no LLM/DB-of-record calls).
They cover the HTTP envelope, request validation (422), the write-blocked path
(403 + WRITE_BLOCKED, agent never called), and auth (401/403). The real-stack
provenance + write-DB-unchanged proofs live in the paid acceptance tier.
"""
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient


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
             patch("chat_nextseek.portable.graph_agent",
                   return_value=_dumpable({"cypher": "MATCH (s) RETURN s", "parameters": {}})), \
             patch("chat_nextseek.helpers.tool_neo4j_query",
                   return_value={"ok": True, "data": [{"uuid": "MUS-1"}]}):
            resp = self.client.post(f"{self.BASE}/graph/", {"query": "lineage"}, format="json")
        self.assertEqual(resp.status_code, 200)
        result = resp.json()["result"]
        self.assertEqual(result["plan"]["cypher"], "MATCH (s) RETURN s")
        self.assertEqual(result["result"]["data"][0]["uuid"], "MUS-1")


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
        with patch("chat_nextseek.portable.report_writer_agent",
                   return_value=_dumpable({"report_type": "GEO", "report": {"samples": []},
                                           "narrative": "", "notes": ""})):
            resp = self.client.post(f"{self.BASE}/generate-submission/",
                                    {"type": "GEO", "uids": "MUS-1"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["result"]["report_type"], "GEO")

    def test_bad_type_returns_422(self):
        resp = self.client.post(f"{self.BASE}/generate-submission/",
                                {"type": "BOGUS", "uids": "X-1"}, format="json")
        self.assertEqual(resp.status_code, 422)


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

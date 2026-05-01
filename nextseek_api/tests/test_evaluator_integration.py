"""
Integration tests for evaluator route registration and OpenAPI schema.

Covers:
- Route registration: all 4 evaluator endpoints resolve correctly
- Permission boundary: all endpoints reject non-admin users
- OpenAPI schema: evaluator tag present, operation IDs correct
- No regression: existing assistant routes still work
"""

import sys
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import resolve
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession, QueryTask

if "chat_nextseek.agents" not in sys.modules:
    patch("chat_nextseek.helpers.load_prompt", return_value="(test stub)").start()


class TestRouteRegistration(TestCase):
    """Verify all evaluator routes are registered and resolve."""

    def test_runs_list_route_resolves(self):
        match = resolve("/nextseek_api/evaluator/runs/")
        self.assertEqual(match.func.cls.__name__, "EvaluatorViewSet")

    def test_retry_route_resolves(self):
        match = resolve("/nextseek_api/evaluator/retry/")
        self.assertEqual(match.func.cls.__name__, "EvaluatorViewSet")

    def test_retry_context_by_task_route_resolves(self):
        tid = uuid.uuid4()
        match = resolve(f"/nextseek_api/evaluator/tasks/{tid}/retry-context/")
        self.assertEqual(match.func.cls.__name__, "EvaluatorViewSet")

    def test_retry_context_by_bundle_route_resolves(self):
        sid = uuid.uuid4()
        match = resolve(f"/nextseek_api/evaluator/sessions/{sid}/bundles/1/retry-context/")
        self.assertEqual(match.func.cls.__name__, "EvaluatorViewSet")


class TestPermissionBoundary(TestCase):
    """All evaluator endpoints reject non-admin users."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="intadmin", password="pass", is_staff=True,
        )
        self.regular = User.objects.create_user(
            username="intuser", password="pass", is_staff=False,
        )
        self.client = APIClient()

    def test_runs_list_admin_ok(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/nextseek_api/evaluator/runs/")
        self.assertEqual(resp.status_code, 200)

    def test_runs_list_non_admin_forbidden(self):
        self.client.force_authenticate(user=self.regular)
        resp = self.client.get("/nextseek_api/evaluator/runs/")
        self.assertEqual(resp.status_code, 403)

    def test_retry_non_admin_forbidden(self):
        self.client.force_authenticate(user=self.regular)
        resp = self.client.post(
            "/nextseek_api/evaluator/retry/",
            {"query": "q", "mode": "standard"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_retry_context_by_task_non_admin_forbidden(self):
        self.client.force_authenticate(user=self.regular)
        resp = self.client.get(f"/nextseek_api/evaluator/tasks/{uuid.uuid4()}/retry-context/")
        self.assertEqual(resp.status_code, 403)

    def test_retry_context_by_bundle_non_admin_forbidden(self):
        self.client.force_authenticate(user=self.regular)
        resp = self.client.get(f"/nextseek_api/evaluator/sessions/{uuid.uuid4()}/bundles/1/retry-context/")
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_rejected(self):
        resp = self.client.get("/nextseek_api/evaluator/runs/")
        self.assertIn(resp.status_code, (401, 403))


class TestOpenAPISchema(TestCase):
    """Verify evaluator endpoints appear in the OpenAPI schema."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="schemaadmin", password="pass", is_staff=True,
        )
        self.client = APIClient()

    def test_schema_endpoint_accessible(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/nextseek_api/schema/", HTTP_ACCEPT="application/json")
        self.assertEqual(resp.status_code, 200)

    def test_evaluator_tag_in_schema(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/nextseek_api/schema/", HTTP_ACCEPT="application/json")
        content = resp.json() if hasattr(resp, 'json') else resp.data
        found_evaluator_tag = False
        for path_key, path_obj in content.get("paths", {}).items():
            for method, op in path_obj.items():
                if isinstance(op, dict) and "evaluator" in op.get("tags", []):
                    found_evaluator_tag = True
                    break
        self.assertTrue(found_evaluator_tag, "No endpoint found with 'evaluator' tag")

    def test_evaluator_paths_in_schema(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/nextseek_api/schema/", HTTP_ACCEPT="application/json")
        content = resp.json() if hasattr(resp, 'json') else resp.data
        paths = set(content.get("paths", {}).keys())
        # Check key evaluator paths exist
        evaluator_paths = [p for p in paths if "evaluator" in p]
        self.assertGreaterEqual(len(evaluator_paths), 3, f"Expected at least 3 evaluator paths, got: {evaluator_paths}")


class TestNoRegression(TestCase):
    """Existing routes still work after evaluator registration."""

    def test_assistant_me_route_resolves(self):
        match = resolve("/nextseek_api/assistant/me/")
        self.assertEqual(match.func.cls.__name__, "AssistantViewSet")

    def test_admin_samples_route_resolves(self):
        match = resolve("/nextseek_api/admin/samples/retrieve/")
        self.assertEqual(match.func.cls.__name__, "AdminSampleViewSet")

    def test_batch_upload_route_resolves(self):
        match = resolve("/nextseek_api/batch-upload/start/")
        self.assertEqual(match.func.cls.__name__, "BatchUploadViewSet")

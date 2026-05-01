"""
Unit tests for CORS configuration.

Validates that django-cors-headers is correctly configured to allow
the React/Vite frontend at http://localhost:5173 to make cross-origin
requests to /nextseek_api/* endpoints.
"""

import re

from django.conf import settings
from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient


class CorsSettingsTest(TestCase):
    """Verify CORS settings are correctly defined."""

    def test_cors_middleware_is_first(self):
        self.assertEqual(
            settings.MIDDLEWARE[0],
            "corsheaders.middleware.CorsMiddleware",
            "CorsMiddleware must be the first middleware for CORS to work.",
        )

    def test_cors_allowed_origins_contains_vite(self):
        self.assertIn(
            "http://localhost:5173",
            settings.CORS_ALLOWED_ORIGINS,
        )

    def test_cors_credentials_enabled(self):
        self.assertTrue(
            settings.CORS_ALLOW_CREDENTIALS,
            "CORS_ALLOW_CREDENTIALS must be True for Authorization header.",
        )

    def test_cors_urls_regex_matches_api(self):
        pattern = re.compile(settings.CORS_URLS_REGEX)
        # Should match nextseek_api paths
        self.assertIsNotNone(pattern.match("/nextseek_api/assistant/me/"))
        self.assertIsNotNone(pattern.match("/nextseek_api/samples/"))
        self.assertIsNotNone(pattern.match("/nextseek_api/schema_rag/retrieve/"))

    def test_cors_urls_regex_excludes_non_api(self):
        pattern = re.compile(settings.CORS_URLS_REGEX)
        # Should NOT match non-API paths
        self.assertIsNone(pattern.match("/admin/"))
        self.assertIsNone(pattern.match("/seek/"))
        self.assertIsNone(pattern.match("/login"))

    def test_cors_expose_headers_includes_sse_headers(self):
        exposed = settings.CORS_EXPOSE_HEADERS
        self.assertIn("Cache-Control", exposed)
        self.assertIn("X-Accel-Buffering", exposed)


class CorsPreflightTest(TestCase):
    """Verify CORS preflight (OPTIONS) responses include correct headers."""

    VITE_ORIGIN = "http://localhost:5173"

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="cors_test_user", password="testpass123"
        )

    def setUp(self):
        self.client = APIClient()

    def test_preflight_returns_allow_origin(self):
        resp = self.client.options(
            "/nextseek_api/assistant/me/",
            HTTP_ORIGIN=self.VITE_ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="Authorization",
        )
        self.assertEqual(
            resp.get("Access-Control-Allow-Origin"),
            self.VITE_ORIGIN,
        )

    def test_preflight_allows_credentials(self):
        resp = self.client.options(
            "/nextseek_api/assistant/me/",
            HTTP_ORIGIN=self.VITE_ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )
        self.assertEqual(resp.get("Access-Control-Allow-Credentials"), "true")

    def test_preflight_allows_authorization_header(self):
        resp = self.client.options(
            "/nextseek_api/assistant/me/",
            HTTP_ORIGIN=self.VITE_ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="Authorization",
        )
        allowed = resp.get("Access-Control-Allow-Headers", "")
        self.assertIn("authorization", allowed.lower())

    def test_non_api_path_has_no_cors_headers(self):
        resp = self.client.options(
            "/admin/",
            HTTP_ORIGIN=self.VITE_ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )
        self.assertIsNone(resp.get("Access-Control-Allow-Origin"))

    def test_disallowed_origin_gets_no_cors_headers(self):
        resp = self.client.options(
            "/nextseek_api/assistant/me/",
            HTTP_ORIGIN="http://evil.com",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )
        self.assertIsNone(resp.get("Access-Control-Allow-Origin"))

    def test_actual_get_includes_expose_headers(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(
            "/nextseek_api/assistant/me/",
            HTTP_ORIGIN=self.VITE_ORIGIN,
        )
        self.assertEqual(
            resp.get("Access-Control-Allow-Origin"),
            self.VITE_ORIGIN,
        )
        exposed = resp.get("Access-Control-Expose-Headers", "")
        self.assertIn("Cache-Control", exposed)

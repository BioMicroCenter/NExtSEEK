"""Integration tests for DRF AcceptHeaderVersioning on nextseek_api endpoints."""
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestAcceptHeaderVersioning:
    def _mock_upstream(self):
        """Minimal mock for samples/advanced_search upstream so the view returns 200."""
        patcher = patch("nextseek_api.services.samples.DBtable_sample")
        m = patcher.start()
        m.return_value.searchAdvanced.return_value = '{"rows": [], "total": 0}'
        return patcher

    def test_default_version_is_v1_when_no_accept_header(self, auth_client):
        p = self._mock_upstream()
        try:
            resp = auth_client.post("/nextseek_api/samples/advanced_search/", {}, format="json")
            assert resp.wsgi_request.version == "v1"
        finally:
            p.stop()

    def test_v1_explicit_accept_resolves_to_v1(self, auth_client):
        p = self._mock_upstream()
        try:
            resp = auth_client.post(
                "/nextseek_api/samples/advanced_search/", {}, format="json",
                HTTP_ACCEPT="application/vnd.nextseek.v1+json",
            )
            assert resp.wsgi_request.version == "v1"
        finally:
            p.stop()

    def test_v2_explicit_accept_resolves_to_v2(self, auth_client):
        p = self._mock_upstream()
        try:
            resp = auth_client.post(
                "/nextseek_api/samples/advanced_search/", {}, format="json",
                HTTP_ACCEPT="application/vnd.nextseek.v2+json",
            )
            assert resp.wsgi_request.version == "v2"
        finally:
            p.stop()

    def test_unknown_version_returns_406(self, auth_client):
        p = self._mock_upstream()
        try:
            resp = auth_client.post(
                "/nextseek_api/samples/advanced_search/", {}, format="json",
                HTTP_ACCEPT="application/vnd.nextseek.v99+json",
            )
            assert resp.status_code == 406
        finally:
            p.stop()

    def test_application_json_accept_resolves_to_default_v1(self, auth_client):
        p = self._mock_upstream()
        try:
            resp = auth_client.post(
                "/nextseek_api/samples/advanced_search/", {}, format="json",
                HTTP_ACCEPT="application/json",
            )
            assert resp.wsgi_request.version == "v1"
        finally:
            p.stop()

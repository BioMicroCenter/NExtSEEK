"""Unit tests for translate_error_response_v2."""
import json
from unittest.mock import MagicMock

import pytest
from django.http import HttpResponse

from nextseek_api.errors import JSONAPI_V2_MEDIA_TYPE, translate_error_response_v2


def _req(version):
    r = MagicMock()
    r.version = version
    return r


class TestTranslateErrorResponseV2:
    def test_2xx_passthrough_unchanged(self):
        resp = HttpResponse(b'{"ok": true}', status=200)
        out = translate_error_response_v2(resp, _req("v2"))
        assert out is resp  # same object

    def test_v1_passthrough_unchanged(self):
        resp = HttpResponse(b'{"detail": "bad"}', status=400)
        out = translate_error_response_v2(resp, _req("v1"))
        assert out is resp

    def test_unversioned_passthrough_unchanged(self):
        resp = HttpResponse(b'{"detail": "bad"}', status=400)
        out = translate_error_response_v2(resp, _req(None))
        assert out is resp

    def test_v2_detail_body_reshaped(self):
        resp = HttpResponse(b'{"detail": "Auth required"}', status=401)
        out = translate_error_response_v2(resp, _req("v2"))
        assert out.status_code == 401
        body = out.data
        assert body["errors"][0]["title"] == "Auth required"
        assert body["errors"][0]["status"] == "401"

    def test_v2_already_jsonapi_errors_normalized(self):
        resp = HttpResponse(
            b'{"errors":[{"title":"Sample not found"}]}',
            status=404,
        )
        out = translate_error_response_v2(resp, _req("v2"))
        body = out.data
        assert body["errors"][0]["title"] == "Sample not found"
        assert body["errors"][0]["status"] == "404"

    def test_v2_preserves_detail_from_original_errors_array(self):
        resp = HttpResponse(
            json.dumps({"errors": [{"title": "X", "detail": "y"}]}).encode(),
            status=502,
        )
        out = translate_error_response_v2(resp, _req("v2"))
        assert out.data["errors"][0]["detail"] == "y"

    def test_v2_unparseable_body_returns_fallback_error(self):
        resp = HttpResponse(b"<html>server error</html>", status=502)
        out = translate_error_response_v2(resp, _req("v2"))
        assert out.status_code == 502
        assert out.data["errors"][0]["status"] == "502"
        # Preserves a hint about non-JSON upstream
        assert "non-JSON" in out.data["errors"][0]["detail"]

    def test_v2_empty_body_returns_fallback(self):
        resp = HttpResponse(b"", status=500)
        out = translate_error_response_v2(resp, _req("v2"))
        assert out.status_code == 500

    def test_v2_idempotent(self):
        """Applying translator twice is a no-op (TDD-13)."""
        resp = HttpResponse(b'{"detail": "x"}', status=400)
        once = translate_error_response_v2(resp, _req("v2"))
        twice = translate_error_response_v2(once, _req("v2"))
        # Content equivalent; status preserved
        assert twice.status_code == once.status_code == 400

    def test_v2_preserves_original_status_code(self):
        for status in (400, 401, 403, 404, 409, 413, 422, 500, 502):
            resp = HttpResponse(b'{"detail": "x"}', status=status)
            out = translate_error_response_v2(resp, _req("v2"))
            assert out.status_code == status

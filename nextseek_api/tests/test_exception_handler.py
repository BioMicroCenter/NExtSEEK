"""Unit tests for the version-aware DRF exception handler."""
from unittest.mock import MagicMock

import pytest
from rest_framework import exceptions as drf_exc
from rest_framework.test import APIRequestFactory

from nextseek_api.exception_handler import (
    handle_api_exception,
    pointer_from_loc,
)


def _context(version):
    request = APIRequestFactory().get("/")
    request.version = version
    return {"request": request, "view": MagicMock()}


class TestHandleApiExceptionV1:
    def test_v1_delegates_to_drf_default_for_validation(self):
        ctx = _context("v1")
        resp = handle_api_exception(drf_exc.ValidationError({"name": ["req"]}), ctx)
        assert resp is not None
        assert resp.status_code == 400
        # v1 default shape retains the DRF dict - NOT reshaped
        assert "errors" not in resp.data

    def test_v1_delegates_for_not_found(self):
        ctx = _context("v1")
        resp = handle_api_exception(drf_exc.NotFound("gone"), ctx)
        assert resp.status_code == 404
        assert "errors" not in resp.data

    def test_unversioned_request_delegates_to_default(self):
        ctx = _context(None)
        resp = handle_api_exception(drf_exc.ValidationError("bad"), ctx)
        assert "errors" not in resp.data


class TestHandleApiExceptionV2:
    def test_v2_validation_error_reshapes_with_pointer(self):
        ctx = _context("v2")
        exc = drf_exc.ValidationError({"name": ["required"]})
        resp = handle_api_exception(exc, ctx)
        assert resp.status_code == 400
        assert resp.data["errors"][0]["source"]["pointer"] == "/data/attributes/name"
        assert resp.data["errors"][0]["detail"] == "required"

    def test_v2_nested_validation_error_pointer_reflects_depth(self):
        ctx = _context("v2")
        exc = drf_exc.ValidationError({"rows": [{"title": ["short"]}]})
        resp = handle_api_exception(exc, ctx)
        pointers = [e["source"]["pointer"] for e in resp.data["errors"]]
        assert "/data/attributes/rows/0/title" in pointers

    def test_v2_flat_validation_error_no_pointer(self):
        ctx = _context("v2")
        exc = drf_exc.ValidationError("flat string message")
        resp = handle_api_exception(exc, ctx)
        assert resp.status_code == 400
        assert resp.data["errors"][0]["detail"] == "flat string message"
        assert "source" not in resp.data["errors"][0]

    def test_v2_not_found_reshaped(self):
        ctx = _context("v2")
        resp = handle_api_exception(drf_exc.NotFound("gone"), ctx)
        assert resp.status_code == 404
        assert resp.data["errors"][0]["title"] == "Not found"
        assert resp.data["errors"][0]["detail"] == "gone"

    def test_v2_permission_denied_reshaped(self):
        ctx = _context("v2")
        resp = handle_api_exception(drf_exc.PermissionDenied("no"), ctx)
        assert resp.status_code == 403
        assert resp.data["errors"][0]["title"] == "Permission denied"

    def test_v2_not_authenticated_reshaped(self):
        ctx = _context("v2")
        resp = handle_api_exception(drf_exc.NotAuthenticated("who?"), ctx)
        assert resp.status_code == 401
        assert resp.data["errors"][0]["title"] == "Authentication required"

    def test_v2_authentication_failed_reshaped(self):
        ctx = _context("v2")
        resp = handle_api_exception(drf_exc.AuthenticationFailed("bad token"), ctx)
        assert resp.status_code == 401
        assert resp.data["errors"][0]["title"] == "Authentication failed"

    def test_v2_unknown_exception_falls_through_to_default(self):
        """Non-DRF exception that DRF's default returns None for - handler delegates."""
        ctx = _context("v2")
        resp = handle_api_exception(ValueError("unknown"), ctx)
        # default handler returns None for unknown; we delegate and propagate None
        assert resp is None


class TestPointerFromLoc:
    @pytest.mark.parametrize("loc,expected", [
        (("title",), "/data/attributes/title"),
        (("rows", 0, "title"), "/data/attributes/rows/0/title"),
        (("meta", "example"), "/data/attributes/meta/example"),
        (("a", "b", "c"), "/data/attributes/a/b/c"),
    ])
    def test_pointer_maps_correctly(self, loc, expected):
        assert pointer_from_loc(loc) == expected

    def test_empty_loc_returns_attributes_root(self):
        assert pointer_from_loc(()) == "/data/attributes/"

    def test_integer_indices_stringified(self):
        assert pointer_from_loc(("rows", 5)) == "/data/attributes/rows/5"

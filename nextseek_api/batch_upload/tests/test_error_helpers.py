"""Unit tests for v1_or_v2_error helper and pydantic_errors_to_api_errors."""
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from nextseek_api.batch_upload.error_helpers import (
    pydantic_errors_to_api_errors,
    v1_or_v2_error,
)


def _req(version):
    r = MagicMock()
    r.version = version
    return r


class Model(BaseModel):
    name: str
    rows: list[dict]


class TestV1OrV2Error:
    def test_v1_returns_detail_shape(self):
        resp = v1_or_v2_error(
            _req("v1"),
            v1_body={"detail": "missing"},
            v1_status=400,
            v2={"title": "missing", "pointer": "/data/attributes/x"},
        )
        assert resp.status_code == 400
        assert resp.data == {"detail": "missing"}

    def test_v2_returns_errors_array_shape(self):
        resp = v1_or_v2_error(
            _req("v2"),
            v1_body={"detail": "missing"},
            v1_status=400,
            v2={"title": "missing", "pointer": "/data/attributes/x"},
        )
        assert resp.status_code == 400
        assert resp.data["errors"][0]["title"] == "missing"
        assert resp.data["errors"][0]["source"]["pointer"] == "/data/attributes/x"

    def test_unversioned_treats_as_v1(self):
        resp = v1_or_v2_error(
            _req(None),
            v1_body={"detail": "x"},
            v1_status=400,
            v2={"title": "x"},
        )
        assert resp.data == {"detail": "x"}


class TestPydanticErrorsMapping:
    def _raise(self, data):
        try:
            Model.model_validate(data)
        except ValidationError as e:
            return e

    def test_missing_field_maps_to_source_pointer(self):
        exc = self._raise({"rows": []})
        errs = pydantic_errors_to_api_errors(exc)
        titles = [e["title"] for e in errs]
        assert any("Field required" in t or "required" in t.lower() for t in titles)
        pointers = [e.get("source", {}).get("pointer") for e in errs]
        assert "/data/attributes/name" in pointers

    def test_nested_list_error_pointer_includes_index(self):
        exc = self._raise({"name": "a", "rows": ["not_a_dict"]})
        errs = pydantic_errors_to_api_errors(exc)
        pointers = [e.get("source", {}).get("pointer") for e in errs]
        assert any("/rows/0" in p for p in pointers)

    def test_every_error_has_status_422(self):
        exc = self._raise({})
        errs = pydantic_errors_to_api_errors(exc)
        for e in errs:
            assert e["status"] == "422"

    def test_type_preserved_in_meta(self):
        exc = self._raise({})
        errs = pydantic_errors_to_api_errors(exc)
        for e in errs:
            # meta.pydantic_type carries the machine type for advanced consumers
            assert "meta" in e
            assert "pydantic_type" in e["meta"]

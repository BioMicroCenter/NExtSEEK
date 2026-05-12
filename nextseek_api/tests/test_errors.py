"""Unit tests for JSON:API error response helpers."""
import pytest
from rest_framework.test import APIRequestFactory

from nextseek_api.errors import (
    JSONAPI_V2_MEDIA_TYPE,
    api_error,
    api_errors,
    build_error,
)


class TestBuildError:
    def test_minimal_fields_produce_jsonapi_shape(self):
        err = build_error(400, "bad input")
        assert err == {"status": "400", "title": "bad input"}

    def test_detail_added_when_present(self):
        err = build_error(400, "t", detail="long message")
        assert err["detail"] == "long message"

    def test_pointer_maps_to_source_pointer(self):
        err = build_error(400, "t", pointer="/data/attributes/name")
        assert err["source"] == {"pointer": "/data/attributes/name"}

    def test_parameter_maps_to_source_parameter(self):
        err = build_error(400, "t", parameter="page_size")
        assert err["source"] == {"parameter": "page_size"}

    def test_pointer_and_parameter_both_populate_source(self):
        err = build_error(400, "t", pointer="/data", parameter="q")
        assert err["source"] == {"pointer": "/data", "parameter": "q"}

    def test_valid_values_and_example_populate_meta(self):
        err = build_error(400, "t", valid_values=["a", "b"], example="a")
        assert err["meta"] == {"valid_values": ["a", "b"], "example": "a"}

    def test_status_is_stringified_per_jsonapi_spec(self):
        err = build_error(422, "t")
        assert err["status"] == "422"
        assert isinstance(err["status"], str)

    def test_omitted_optionals_produce_no_empty_keys(self):
        err = build_error(400, "t")
        assert "detail" not in err
        assert "source" not in err
        assert "meta" not in err


class TestApiError:
    def test_single_error_wrapped_in_errors_array(self):
        resp = api_error(400, "bad")
        assert resp.status_code == 400
        assert resp.data == {"errors": [{"status": "400", "title": "bad"}]}

    def test_content_type_is_jsonapi_v2(self):
        """Unit-level check — Response.__init__ stores content_type on the instance
        before finalize_response() runs. resp['Content-Type'] header requires
        finalization which only happens in a view dispatch cycle.
        """
        resp = api_error(400, "bad")
        assert resp.content_type == JSONAPI_V2_MEDIA_TYPE


class TestApiErrors:
    def test_multiple_errors_wrapped(self):
        errors = [build_error(400, "a"), build_error(400, "b")]
        resp = api_errors(400, errors)
        assert resp.data == {"errors": [
            {"status": "400", "title": "a"},
            {"status": "400", "title": "b"},
        ]}

    def test_empty_list_still_emits_errors_key(self):
        resp = api_errors(400, [])
        assert resp.data == {"errors": []}


def test_jsonapi_v2_media_type_constant():
    assert JSONAPI_V2_MEDIA_TYPE == "application/vnd.nextseek.v2+json"

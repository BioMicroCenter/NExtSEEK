from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.urls import resolve, reverse
from rest_framework.test import APIRequestFactory, force_authenticate

from nextseek_api.attributes.auth import AuthenticatedSeekPerson
from nextseek_api.attributes.scalars import ScalarInputError, parse_positive_int, parse_query_positive_int
from nextseek_api.attributes.views import AttributeViewSet


EXPECTED_ROUTES = {
    "attribute-list": ("GET", None),
    "attribute-detail": ("GET", {"pk": 4}),
    "attribute-search": ("POST", None),
    "attribute-batch-create": ("POST", None),
    "attribute-batch-patch": ("PATCH", None),
    "attribute-batch-delete": ("POST", None),
    "attribute-job": ("GET", {"job_id": "j1"}),
    "attribute-job-cancel": ("POST", {"job_id": "j1"}),
}


def _request(method, path, body=None):
    request = getattr(APIRequestFactory(), method)(path, body or {}, format="json")
    force_authenticate(
        request,
        user=MagicMock(is_authenticated=True),
        token=AuthenticatedSeekPerson(42, 84, "service-test", "session"),
    )
    return request


def test_exact_route_and_method_surface():
    observed = set()
    for name, (method, kwargs) in EXPECTED_ROUTES.items():
        path = reverse(f"nextseek_api:{name}", kwargs=kwargs)
        match = resolve(path)
        assert match.func.cls is AttributeViewSet
        assert set(match.func.actions) == {method.lower()}
        observed.add((method, path))
    assert observed == {
        ("GET", "/nextseek_api/attributes/"),
        ("GET", "/nextseek_api/attributes/4/"),
        ("POST", "/nextseek_api/attributes/search/"),
        ("POST", "/nextseek_api/attributes/batch-create/"),
        ("PATCH", "/nextseek_api/attributes/batch-patch/"),
        ("POST", "/nextseek_api/attributes/batch-delete/"),
        ("GET", "/nextseek_api/attributes/jobs/j1/"),
        ("POST", "/nextseek_api/attributes/jobs/j1/cancel/"),
    }


def test_no_single_resource_mutation_or_delete_body_route():
    assert resolve("/nextseek_api/attributes/4/").func.actions == {"get": "retrieve"}
    assert resolve("/nextseek_api/attributes/batch-delete/").func.actions == {"post": "batch_delete"}


def test_partial_outcome_cannot_return_200():
    from nextseek_api.attributes.executor import classify_mutation_http_status
    assert classify_mutation_http_status([{"status": "succeeded"}, {"status": "failed"}]) == 207


@pytest.mark.parametrize("value", [None, True, False, 0, -1, "", "  ", "true", "1.0", "-1", str(2**63)])
def test_strict_scalar_parser_rejects_malformed_overflow_boolean_nonpositive_and_multivalue(value):
    with pytest.raises(ScalarInputError):
        parse_positive_int(value, field="id")


def test_strict_scalar_parser_accepts_integer_and_ascii_decimal():
    assert parse_positive_int(7, field="id") == 7
    assert parse_positive_int("007", field="id") == 7


def test_query_scalar_rejects_repeated_value():
    query = MagicMock()
    query.__contains__.return_value = True
    query.getlist.return_value = ["1", "2"]
    with pytest.raises(ScalarInputError):
        parse_query_positive_int(query, "page", default=1, maximum=100)


@patch.object(AttributeViewSet, "get_permissions", return_value=[])
@patch("nextseek_api.attributes.views.attribute_services")
def test_view_validates_once_before_side_effect(service_factory, _permissions):
    services = service_factory.return_value
    services.mutate.return_value = ({"mode": "synchronous", "overall_status": "succeeded"}, 200)
    body = {"targets": [{"sample_type": 1, "attributes": [{"title": "X", "sample_attribute_type": 1}]}]}
    with patch("nextseek_api.attributes.views.CREATE_REQUEST_ADAPTER.validate_python", return_value=MagicMock(dry_run=False)) as validate:
        response = AttributeViewSet.as_view({"post": "batch_create"})(
            _request("post", "/nextseek_api/attributes/batch-create/", body)
        )
    assert response.status_code == 200
    validate.assert_called_once()
    services.mutate.assert_called_once()


@patch.object(AttributeViewSet, "get_permissions", return_value=[])
@patch("nextseek_api.attributes.views.attribute_services")
def test_pydantic_validation_error_is_structured_422_before_side_effect(service_factory, _permissions):
    response = AttributeViewSet.as_view({"post": "batch_create"})(
        _request("post", "/nextseek_api/attributes/batch-create/", {"targets": []})
    )
    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "request_validation_error"
    service_factory.return_value.mutate.assert_not_called()


@patch.object(AttributeViewSet, "get_permissions", return_value=[])
@patch("nextseek_api.attributes.views.attribute_services")
@pytest.mark.parametrize(("action", "method", "operation", "body"), [
    ("batch_create", "post", "create", {"targets": [{"sample_type": 1, "attributes": [{"title": "X", "sample_attribute_type": 1}]}], "dry_run": True}),
    ("batch_patch", "patch", "patch", {"targets": [{"attributes": [{"attribute": 1, "changes": {"description": None}}]}], "dry_run": True}),
    ("batch_delete", "post", "delete", {"targets": [{"attributes": [1]}], "dry_run": True}),
])
def test_all_mutations_delegate_dry_run(service_factory, _permissions, action, method, operation, body):
    service_factory.return_value.mutate.return_value = ({"mode": "dry_run", "overall_status": "succeeded"}, 200)
    response = AttributeViewSet.as_view({method: action})(_request(method, f"/{action}/", body))
    assert response.status_code == 200
    assert service_factory.return_value.mutate.call_args.kwargs["operation"] == operation
    assert service_factory.return_value.mutate.call_args.kwargs["dry_run"] is True


@patch.object(AttributeViewSet, "get_permissions", return_value=[])
@patch("nextseek_api.attributes.views.attribute_services")
def test_retrieve_404_is_structured(service_factory, _permissions):
    service_factory.return_value.retrieve.return_value = None
    response = AttributeViewSet.as_view({"get": "retrieve"})(_request("get", "/attributes/999/"), pk="999")
    assert response.status_code == 404
    assert response.data["errors"][0]["code"] == "attribute_not_found"


@patch.object(AttributeViewSet, "get_permissions", return_value=[])
@patch("nextseek_api.attributes.views.attribute_services")
def test_job_cancel_checks_object_permission_then_delegates(service_factory, _permissions):
    service = service_factory.return_value
    job = SimpleNamespace(actor_seek_person_id=42)
    service.get_job_object.return_value = job
    service.cancel_job.return_value = ({"job_id": "00000000-0000-0000-0000-000000000001"}, 202)
    with patch.object(AttributeViewSet, "check_object_permissions") as check:
        response = AttributeViewSet.as_view({"post": "job_cancel"})(
            _request("post", "/attributes/jobs/j1/cancel/"), job_id="j1"
        )
    assert response.status_code == 202
    check.assert_called_once()
    service.cancel_job.assert_called_once()

"""The attribute write routes through their real permission list (#98).

test_views.py patches get_permissions away; these tests keep it, so the superuser gate and the job-ownership check
are what answers. The service is a stub: no database, no SEEK.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from nextseek_api.attributes import auth
from nextseek_api.attributes import views

JOB = "00000000-0000-0000-0000-000000000001"
WRITES = [
    ("batch_create", "post", {"targets": [{"sample_type": 1, "attributes": [{"title": "X", "sample_attribute_type": 1}]}], "dry_run": True}),
    ("batch_patch", "patch", {"targets": [{"attributes": [{"attribute": 1, "changes": {"description": None}}]}], "dry_run": True}),
    ("batch_delete", "post", {"targets": [{"attributes": [1]}], "dry_run": True}),
]


def _call(action, method, body=None, *, superuser, person_id=42, **kwargs):
    request = getattr(APIRequestFactory(), method)("/x/", body or {}, format="json")
    force_authenticate(
        request,
        user=SimpleNamespace(is_authenticated=True, is_superuser=superuser),
        token=auth.AuthenticatedSeekPerson(person_id, 84, "svc", "session"),
    )
    return views.AttributeViewSet.as_view({method: action})(request, **kwargs)


@pytest.fixture
def service():
    with patch("nextseek_api.attributes.views.attribute_services") as factory:
        yield factory.return_value


@pytest.mark.parametrize(("action", "method", "body"), WRITES)
def test_a_non_superuser_cannot_write_attributes(service, action, method, body):
    response = _call(action, method, body, superuser=False)
    assert response.status_code == 403
    assert response.data["errors"][0]["code"] == "permission_denied"
    service.mutate.assert_not_called()


@pytest.mark.parametrize(("action", "method", "body"), WRITES)
def test_a_superuser_write_reaches_the_service(service, action, method, body):
    service.mutate.return_value = ({"mode": "dry_run", "overall_status": "succeeded"}, 200)
    response = _call(action, method, body, superuser=True)
    assert response.status_code == 200
    service.mutate.assert_called_once()


def test_a_non_superuser_cannot_cancel_a_job(service):
    # The caller created the job, so only the superuser gate can refuse.
    service.get_job_object.return_value = SimpleNamespace(actor_seek_person_id=42)
    service.cancel_job.return_value = ({"job_id": JOB}, 202)
    response = _call("job_cancel", "post", superuser=False, person_id=42, job_id=JOB)
    assert response.status_code == 403
    service.cancel_job.assert_not_called()


def test_a_superuser_who_does_not_own_the_job_cannot_cancel_it(service):
    service.get_job_object.return_value = SimpleNamespace(actor_seek_person_id=7)
    response = _call("job_cancel", "post", superuser=True, person_id=42, job_id=JOB)
    assert response.status_code == 403
    service.cancel_job.assert_not_called()


def test_the_superuser_who_created_the_job_can_cancel_it(service):
    service.get_job_object.return_value = SimpleNamespace(actor_seek_person_id=42)
    service.cancel_job.return_value = ({"job_id": JOB}, 202)
    response = _call("job_cancel", "post", superuser=True, person_id=42, job_id=JOB)
    assert response.status_code == 202
    service.cancel_job.assert_called_once()


def test_cancelling_an_unknown_job_is_404(service):
    from django.core.exceptions import ObjectDoesNotExist

    service.get_job_object.side_effect = ObjectDoesNotExist
    response = _call("job_cancel", "post", superuser=True, job_id=JOB)
    assert response.status_code == 404
    service.cancel_job.assert_not_called()

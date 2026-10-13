"""UserInParticipatingProject: an unanswered SEEK is a retryable 503, an answered "no" is a 403, neither allows."""
from unittest import mock

import orjson
import pytest
from django.core.cache import cache
from rest_framework.test import APIRequestFactory

from nextseek_api.services import assistant as svc

PROJECT = 7


def _person(ids):
    return orjson.dumps({"data": {"relationships": {"projects": {"data": [{"id": i} for i in ids]}}}})


def _check(reply=None, exc=None):
    cache.clear()
    request = APIRequestFactory().get("/x/")
    request.user = mock.Mock(is_authenticated=True, pk=1)
    client = mock.Mock()
    if exc:
        client.get_current_person.side_effect = exc
    else:
        client.get_current_person.return_value = reply
    with mock.patch.object(svc, "SeekAPIClient", return_value=client), \
            mock.patch.object(svc, "ASSISTANT_PARTICIPATING_PROJECTS", {str(PROJECT)}):
        return svc.UserInParticipatingProject().has_permission(request, None)


def test_member_is_allowed():
    assert _check((_person([str(PROJECT)]), 200, {}, None)) is True


@pytest.mark.parametrize("ids", [[], ["99"]])
def test_answered_without_the_project_is_refused_not_retryable(ids):
    assert _check((_person(ids), 200, {}, None)) is False


@pytest.mark.parametrize("reply", [(b"", 401, {}, None), (b"{}", 403, {}, None)])
def test_a_4xx_answer_is_a_refusal(reply):
    assert _check(reply) is False


@pytest.mark.parametrize("kw", [
    dict(exc=TimeoutError("slow")), dict(exc=ConnectionError("down")),
    dict(reply=(b"<html>", 500, {}, None)), dict(reply=(b"", 502, {}, None)),
    dict(reply=(b"not json", 200, {}, None)),
])
def test_an_outage_raises_the_retryable_code_and_never_allows(kw):
    with pytest.raises(svc.SeekNotAnswering) as e:
        _check(**kw)
    assert e.value.status_code == 503
    assert e.value.detail["code"] == "TRANSPORT_ERROR"
    assert "try again shortly" in e.value.detail["message"]

"""Forcing a turn onto Container-CC is a superuser's choice, on both URLs that do it.

cc-assistant/cc/query/async/ and its alias nessie/query/cc/ start an Opus agent run without the router;
force_route on query/async/ was already superuser-only, so the dedicated route must be too.
"""
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from nextseek_api.services.cc_assistant import CCAssistantViewSet
from nextseek_api.services.nessie import NessieChatViewSet

BODY = {"query": "List the files in my run directory", "mode": "standard"}
ROUTES = [
    (CCAssistantViewSet, "cc_query_async", "/nextseek_api/cc-assistant/cc/query/async/"),
    (NessieChatViewSet, "query_cc", "/nextseek_api/nessie/query/cc/"),
]


def _post(viewset, action, path, *, superuser):
    request = APIRequestFactory().post(path, BODY, format="json")
    force_authenticate(request, user=MagicMock(is_authenticated=True, is_superuser=superuser, pk=3))
    started = Response({"task_id": "t", "session_id": "s"}, status=202)
    with patch("nextseek_api.services.cc_assistant.resolve_seek_auth", return_value=(None, None)), \
         patch.object(CCAssistantViewSet, "_start_task", return_value=started) as start:
        response = viewset.as_view({"post": action})(request)
    return response, start


@pytest.mark.parametrize("viewset,action,path", ROUTES)
def test_a_member_cannot_force_container_cc(viewset, action, path):
    response, start = _post(viewset, action, path, superuser=False)
    assert response.status_code == 403
    start.assert_not_called()


@pytest.mark.parametrize("viewset,action,path", ROUTES)
def test_a_superuser_still_can(viewset, action, path):
    response, start = _post(viewset, action, path, superuser=True)
    assert response.status_code == 202
    assert start.call_args.kwargs["force_cc"] is True

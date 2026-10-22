"""Request-rate limits: the project default for every view, tight ones on the endpoints that cost money."""
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import caches
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from nextseek_api.services.cc_assistant import CCAssistantViewSet

RATES = {"anon": "3/min", "user": "1000/min", "turn_start": "3/min", "upload": "2/min"}


@pytest.fixture(autouse=True)
def _clean_counters():
    caches["throttle"].clear()
    yield
    caches["throttle"].clear()


@pytest.fixture
def rates(settings):
    def set_rates(**over):
        rf = dict(settings.REST_FRAMEWORK)
        rf["DEFAULT_THROTTLE_RATES"] = {**RATES, **over}
        settings.REST_FRAMEWORK = rf
    return set_rates


class _Open(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"ok": True})


def _user(pk=3):
    return MagicMock(is_authenticated=True, is_superuser=True, pk=pk)


def test_anonymous_requests_are_limited(rates):
    rates()
    codes = [_Open.as_view()(APIRequestFactory().get("/x/")).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_the_client_address_is_the_one_before_the_edge(rates):
    """X-Forwarded-For reaches Django as 'client, edge': one client's burst must not block another."""
    rates()
    def hit(client):
        req = APIRequestFactory().get("/x/", HTTP_X_FORWARDED_FOR=f"{client}, 203.0.113.9")
        return _Open.as_view()(req).status_code
    assert [hit("198.51.100.1") for _ in range(4)] == [200, 200, 200, 429]
    assert hit("198.51.100.2") == 200


def _post(client, path):
    started = Response({"task_id": "t", "session_id": "s"}, status=202)
    with patch("nextseek_api.services.cc_assistant.resolve_seek_auth", return_value=(None, None)), \
         patch.object(CCAssistantViewSet, "_start_task", return_value=started):
        return client.post(path, {"query": "hi", "mode": "standard"}, format="json").status_code


@pytest.fixture
def su_client(db):
    from django.contrib.auth.models import User
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(User.objects.create_user("su", password="x", is_superuser=True, is_staff=True))
    return client


# Through the URL router: the throttles named on an @action are applied by the router, not by as_view(actions).
def test_turn_starts_are_limited_per_user(rates, su_client):
    rates()
    path = "/nextseek_api/cc-assistant/cc/query/async/"
    assert [_post(su_client, path) for _ in range(4)] == [202, 202, 202, 429]


def test_the_nessie_alias_shares_the_turn_limit(rates, su_client):
    rates()
    for _ in range(3):
        assert _post(su_client, "/nextseek_api/cc-assistant/cc/query/async/") == 202
    assert _post(su_client, "/nextseek_api/nessie/query/cc/") == 429


def test_polling_is_not_limited_at_default_rates(settings):
    rf = dict(settings.REST_FRAMEWORK)
    rf["DEFAULT_THROTTLE_RATES"] = {"anon": "120/min", "user": "1200/min", "turn_start": "30/min", "upload": "10/min"}
    settings.REST_FRAMEWORK = rf
    class Poll(APIView):
        def get(self, request):
            return Response({"ok": True})
    user = _user()
    for _ in range(200):
        req = APIRequestFactory().get("/x/")
        force_authenticate(req, user=user)
        assert Poll.as_view()(req).status_code == 200


def test_the_synchronous_assistant_query_is_a_turn_start(rates, su_client):
    rates()
    from nextseek_api.services.assistant import UserInParticipatingProject
    with patch.object(UserInParticipatingProject, "has_permission", return_value=True):
        codes = [su_client.post("/nextseek_api/assistant/query/", {}, format="json").status_code for _ in range(4)]
    assert 429 not in codes[:3] and codes[3] == 429


def test_uploads_are_limited_per_user(rates, su_client):
    rates()
    codes = [su_client.post("/nextseek_api/cc-assistant/upload/", {}, format="multipart").status_code for _ in range(3)]
    assert 429 not in codes[:2] and codes[2] == 429

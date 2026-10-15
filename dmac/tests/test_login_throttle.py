"""Sign-in attempts are counted before SEEK is asked, per client address and username."""
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import caches
from django.http import HttpResponse
from django.test import RequestFactory


@pytest.fixture(autouse=True)
def _clean_counters():
    caches["throttle"].clear()
    yield
    caches["throttle"].clear()


def _attempt(views, seekdb, *, user="alice", ip="198.51.100.7"):
    request = RequestFactory().post("/login/", {"username": user, "password": "x"}, HTTP_X_FORWARDED_FOR=f"{ip}, 203.0.113.9")
    request.session = MagicMock()
    with patch.object(views, "SeekDB", return_value=seekdb), \
         patch.object(views, "render", side_effect=lambda r, t, ctx=None, **kw: HttpResponse(status=kw.get("status", 200))):
        return views.login_seek(request)


def _failing_seek():
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {
        "server": "s", "storage": "s", "storagetype": "SEEK", "username": "alice", "password": "x",
        "status": False, "err": "Error: bad login", "noexpire": "no",
    }
    return seekdb


def test_eleventh_failed_login_is_refused_without_asking_seek():
    from dmac import views
    seekdb = _failing_seek()
    assert [_attempt(views, seekdb).status_code for _ in range(10)] == [200] * 10
    assert _attempt(views, seekdb).status_code == 429
    assert seekdb.getSeekLogin.call_count == 10


def test_another_address_cannot_lock_the_user_out():
    from dmac import views
    seekdb = _failing_seek()
    for _ in range(11):
        _attempt(views, seekdb)
    assert _attempt(views, seekdb, ip="198.51.100.99").status_code == 200


def test_one_address_is_limited_across_usernames():
    from dmac import views
    seekdb = _failing_seek()
    codes = [_attempt(views, seekdb, user=f"u{i}").status_code for i in range(40)]
    assert 429 in codes


# ---- the window, on the real file cache; and Basic credentials sharing the same count ----

@pytest.fixture
def file_cache(settings, tmp_path):
    settings.CACHES = {
        **settings.CACHES,
        "throttle": {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache", "LOCATION": str(tmp_path)},
    }
    yield


def test_the_window_is_fixed_from_the_first_failure(file_cache, monkeypatch):
    """cache.incr would restart the cache timeout at each failure; the window must not slide."""
    import time
    from dmac import views
    seekdb = _failing_seek()
    t0 = time.time()
    monkeypatch.setattr(time, "time", lambda: t0)
    for _ in range(9):
        _attempt(views, seekdb)
    monkeypatch.setattr(time, "time", lambda: t0 + 6 * 60)
    _attempt(views, seekdb)  # the tenth failure, 6 minutes in: still inside the 15 minute window
    monkeypatch.setattr(time, "time", lambda: t0 + 12 * 60)
    assert _attempt(views, seekdb).status_code == 429  # a sliding 5 minute window would have forgotten them
    monkeypatch.setattr(time, "time", lambda: t0 + 16 * 60)
    assert _attempt(views, seekdb).status_code == 200


from rest_framework.authentication import BasicAuthentication  # noqa: E402
from rest_framework.permissions import IsAuthenticated  # noqa: E402
from rest_framework.response import Response  # noqa: E402
from rest_framework.test import APIRequestFactory  # noqa: E402
from rest_framework.views import APIView  # noqa: E402


class _BasicOnly(APIView):
    authentication_classes = [BasicAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = []

    def get(self, request):
        return Response({"ok": True})


def _basic(password, ip="198.51.100.7", user="alice"):
    import base64
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = APIRequestFactory().get("/x/", HTTP_AUTHORIZATION=f"Basic {token}", HTTP_X_FORWARDED_FOR=f"{ip}, 203.0.113.9")
    return _BasicOnly.as_view()(req).status_code


@pytest.mark.django_db
def test_failed_basic_credentials_are_counted_and_then_refused():
    from django.contrib.auth.models import User
    User.objects.create_user("alice", password="right")
    assert _basic("right") == 200
    assert [_basic("wrong") for _ in range(10)] == [401] * 10
    assert _basic("right") == 401  # refused without checking the password
    assert _basic("right", ip="198.51.100.99") == 200  # another address is not locked out


@pytest.mark.django_db
def test_the_login_form_and_basic_share_one_count():
    from django.contrib.auth.models import User
    from dmac import views
    User.objects.create_user("alice", password="right")
    seekdb = _failing_seek()
    for _ in range(5):
        _attempt(views, seekdb)
    for _ in range(5):
        assert _basic("wrong") == 401
    assert _basic("right") == 401


@pytest.mark.django_db
def test_loopback_failures_never_hit_the_address_cap():
    from django.contrib.auth.models import User
    User.objects.create_user("bob", password="right")
    for i in range(35):
        _basic("wrong", ip="127.0.0.1", user=f"a{i}")  # many names, so only the address count grows
    assert _basic("right", ip="127.0.0.1", user="bob") == 200
    for i in range(35):
        _basic("wrong", ip="198.51.100.50", user=f"a{i}")
    User.objects.create_user("carol", password="right")
    assert _basic("right", ip="198.51.100.50", user="carol") == 401

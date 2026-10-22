"""Session-authenticated API calls must carry Django's CSRF token."""

import base64

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

URL = "/nextseek_api/batch-upload/validate/"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("csrf_user", password="pw-for-test-only")


def test_session_post_without_token_is_refused(user):
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)
    resp = client.post(URL, {}, format="json")
    assert resp.status_code == 403
    assert "CSRF" in str(resp.json())


def test_session_post_with_token_passes_the_check(user):
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)
    token = client.get("/login/").cookies["csrftoken"].value
    resp = client.post(URL, {}, format="json", HTTP_X_CSRFTOKEN=token)
    assert "CSRF" not in resp.content.decode()


def test_basic_auth_needs_no_token(user):
    client = APIClient(enforce_csrf_checks=True)
    raw = base64.b64encode(b"csrf_user:pw-for-test-only").decode()
    resp = client.post(URL, {}, format="json", HTTP_AUTHORIZATION=f"Basic {raw}")
    assert resp.status_code != 401, "the Basic login must authenticate for this test to mean anything"
    assert "CSRF" not in resp.content.decode()

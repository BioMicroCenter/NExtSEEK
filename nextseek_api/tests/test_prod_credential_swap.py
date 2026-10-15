"""Characterization of the session owner check, and the prod-credential helper.

The six session endpoints refuse a non-owner with one fixed 403 body. The helper
tests need the swap to follow the turn-pass guard in one place.
"""
import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession

FORBIDDEN = {"errors": [{"title": "Forbidden", "detail": "You do not own this session."}]}


class OwnerCheckTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="x")
        self.other = User.objects.create_user("other", password="x")
        patcher = patch("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session = ChatSession.objects.create(user=self.owner)
        self.client = APIClient()
        self.client.force_authenticate(user=self.other)
        self.base = f"/nextseek_api/assistant/sessions/{self.session.session_id}/"

    def test_owner_check_responses_unchanged(self):
        calls = [
            self.client.get(self.base),
            self.client.patch(self.base, {"title": "x"}, format="json"),
            self.client.delete(self.base),
            self.client.get(self.base + "bundles/1/"),
            self.client.get(self.base + "bundles/1/artifacts/key/"),
            self.client.get(self.base + "download/"),
        ]
        for resp in calls:
            assert resp.status_code == 403, resp.request
            assert resp.json() == FORBIDDEN, resp.request
        assert ChatSession.objects.filter(pk=self.session.pk).exists()

    def test_unknown_session_still_404(self):
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{uuid.uuid4()}/")
        assert resp.status_code == 404

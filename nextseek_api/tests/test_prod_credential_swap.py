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

    def test_superuser_result_per_endpoint(self):
        """Strict endpoints (PATCH, DELETE) refuse even a superuser; read endpoints let one in."""
        root = User.objects.create_user("root", password="x", is_superuser=True)
        self.client.force_authenticate(user=root)
        for resp in (self.client.patch(self.base, {"title": "x"}, format="json"), self.client.delete(self.base)):
            assert resp.status_code == 403
            assert resp.json() == FORBIDDEN
        assert ChatSession.objects.filter(pk=self.session.pk).exists()
        assert self.client.get(self.base).status_code == 200

    def test_unknown_session_still_404(self):
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{uuid.uuid4()}/")
        assert resp.status_code == 404


class ProdCredentialHelperTests(TestCase):
    def _cfg(self):
        from types import SimpleNamespace
        return SimpleNamespace(API_USER="svc", API_PASS="svcpw")

    def _run(self, turn_pass, cfg, prod):
        from django.test import override_settings
        from NessieAI.ns.turn import apply_prod_credentials
        with override_settings(NEXTSEEK_CHAT_CONFIG_PROD=prod):
            return apply_prod_credentials(turn_pass, cfg, "me", "mepw")

    def test_superuser_on_prod_config_gets_the_service_login(self):
        cfg = self._cfg()
        assert self._run(False, cfg, cfg) == ("svc", "svcpw")

    def test_turn_pass_keeps_the_callers_login(self):
        cfg = self._cfg()
        assert self._run(True, cfg, cfg) == ("me", "mepw")

    def test_other_config_keeps_the_callers_login(self):
        assert self._run(False, self._cfg(), self._cfg()) == ("me", "mepw")

    def test_cc_turn_uses_the_helper(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[2] / "NessieAI/cc/turn.py").read_text()
        assert "apply_prod_credentials(" in src
        assert "chat_config.API_USER, chat_config.API_PASS" not in src

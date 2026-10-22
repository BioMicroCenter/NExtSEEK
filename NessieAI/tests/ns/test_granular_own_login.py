"""A granular op runs only as the caller's own login, never as the shared ChatConfig login (#64).

A caller that brings no login of its own (no Basic pair, and no SEEK login in its Django session) gets AUTH_FAILED on
every op, as the NS turn does. The op itself is a recorder of the login it was handed.
"""
from __future__ import annotations

import base64
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User

from django.test import Client, TestCase, override_settings
from rest_framework.authtoken.models import Token

from dmac.session_login import store_seek_password

SHARED = SimpleNamespace(API_USER="shared-login", API_PASS="shared-pw")
PLAN = '{"endpoint": "/nextseek_api/projects/", "method": "GET", "requestBody": {}, "queryParameters": {}}'
OPS = {
    "api-read": {"parser_plan": PLAN},
    "report": {"mode": "samples", "project": "p"},
    "entity": {"query": "mouse liver"},
}


@override_settings(NEXTSEEK_CHAT_CONFIG=SHARED, NEXTSEEK_CHAT_CONFIG_PROD=None)
class GranularOpsUseTheCallersOwnLogin(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("member", password="pw", is_staff=True)
        self.ran_as = []
        for target, kw in (
            ("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", {"return_value": True}),
            ("nextseek_api.services.assistant.run_op", {"side_effect": self._run_op}),
            ("nextseek_api.services.assistant._granular_outputs_dir", {"return_value": tempfile.mkdtemp()}),
        ):
            p = patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)

    def _run_op(self, op, args, *, config, **kw):
        self.ran_as.append(config.API_USER)
        return {"ok": True}

    def _post_all(self, client, **headers):
        return {op: client.post(f"/nextseek_api/assistant/{op}/", body, content_type="application/json", **headers)
                for op, body in OPS.items()}

    def _assert_refused(self, replies):
        for op, resp in replies.items():
            self.assertEqual(resp.status_code, 401, (op, resp.content))
            self.assertEqual(resp.json()["code"], "AUTH_FAILED", op)
        self.assertEqual(self.ran_as, [])

    def test_a_session_without_a_seek_login_is_refused(self):
        client = Client()
        client.force_login(self.user)
        self._assert_refused(self._post_all(client))

    def test_a_token_caller_is_refused(self):
        token = Token.objects.create(user=self.user)
        self._assert_refused(self._post_all(Client(), HTTP_AUTHORIZATION=f"Token {token.key}"))

    def test_a_basic_caller_runs_as_itself(self):
        basic = base64.b64encode(b"member:pw").decode()
        replies = self._post_all(Client(), HTTP_AUTHORIZATION=f"Basic {basic}")
        self.assertEqual({op: r.status_code for op, r in replies.items()}, {op: 200 for op in OPS})
        self.assertEqual(self.ran_as, ["member"] * len(OPS))

    def test_a_session_holding_the_seek_login_runs_as_it(self):
        client = Client()
        client.force_login(self.user)
        session = client.session
        session["username"] = "member"
        store_seek_password(session, "member", "pw")
        session.save()
        replies = self._post_all(client)
        self.assertEqual({op: r.status_code for op, r in replies.items()}, {op: 200 for op in OPS})
        self.assertEqual(self.ran_as, ["member"] * len(OPS))

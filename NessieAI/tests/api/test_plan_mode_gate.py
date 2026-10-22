"""SEC-002: mode is one of standard, plan, pipeline, and plan is for a superuser or a Container-CC turn pass.

The chat panel only sends standard; the Container-CC nextseek-plan op sends plan through its turn pass. Any other
caller asking for plan gets 403 before a task starts, and an unknown mode is a 422.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.response import Response
from rest_framework.test import APIClient

ROUTED = ("/nextseek_api/cc-assistant/query/async/", "/nextseek_api/nessie/query/")
LEGACY = ("/nextseek_api/assistant/query/async/", "/nextseek_api/assistant/query/")


class PlanModeGateTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.client = APIClient()
        for target, kwargs in (
            ("nextseek_api.services.cc_assistant.CCAssistantViewSet._start_task",
             {"return_value": Response({"ok": True}, status=202)}),
            ("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", {"return_value": True}),
            # Nothing past the gate may run a real turn, on this code or on code without the gate.
            ("NessieAI.ns.turn.run_query", {}),
            ("NessieAI.ns.turn.run_query_plan", {}),
            ("nextseek_api.services.assistant.DictSessionAdapter", {}),
        ):
            patcher = patch(target, **kwargs)
            setattr(self, target.rsplit(".", 1)[-1], patcher.start())
            self.addCleanup(patcher.stop)

    def _as(self, *, superuser):
        user = User.objects.create_user("plan-su" if superuser else "plan-member", password="pw",
                                        is_superuser=superuser)
        self.client.force_authenticate(user=user)

    def _post(self, path, mode):
        return self.client.post(path, {"query": "find mice then the female ones", "mode": mode}, format="json")

    def test_a_member_asking_for_plan_is_refused_on_every_query_route(self):
        self._as(superuser=False)
        for path in ROUTED + LEGACY:
            with self.subTest(path=path):
                self.assertEqual(self._post(path, "plan").status_code, 403)
        self._start_task.assert_not_called()
        self.run_query_plan.assert_not_called()

    def test_a_superuser_may_still_ask_for_plan(self):
        self._as(superuser=True)
        for path in ROUTED:
            with self.subTest(path=path):
                self.assertEqual(self._post(path, "plan").status_code, 202)

    def test_a_member_keeps_standard(self):
        self._as(superuser=False)
        for path in ROUTED:
            with self.subTest(path=path):
                self.assertEqual(self._post(path, "standard").status_code, 202)

    def test_an_unknown_mode_is_a_422(self):
        self._as(superuser=True)
        for path in ROUTED + LEGACY:
            with self.subTest(path=path):
                self.assertEqual(self._post(path, "bogus").status_code, 422)
        self._start_task.assert_not_called()


def test_a_turn_pass_may_ask_for_plan():
    """The Container-CC nextseek-plan op sends plan with its turn pass, for any chat owner."""
    from types import SimpleNamespace

    from nextseek_api import permissions

    member = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, is_superuser=False))
    assert permissions.plan_mode_refused(member, "plan")
    assert not permissions.plan_mode_refused(member, "standard")
    with patch.object(permissions, "is_turn_pass", return_value=True):
        assert not permissions.plan_mode_refused(member, "plan")

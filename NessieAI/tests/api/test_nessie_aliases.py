"""The nessie/ paths are aliases: same implementation, same gate, same answer.

Old paths keep working. Consumers span two container images and a committed JS
bundle that do not deploy together, so the rename lands as an alias rather than
a cut-over, and these tests pin both spellings to the same behaviour.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.response import Response
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession, QueryTask

_NO_SUCH = "2f1e0d3c-4b5a-6978-8796-a5b4c3d2e1f0"

#: (new nessie path, the cc-assistant path it forwards to)
ALIAS_PAIRS = [
    (f"/nextseek_api/nessie/tasks/{_NO_SUCH}/progress/",
     f"/nextseek_api/cc-assistant/tasks/{_NO_SUCH}/progress/"),
    (f"/nextseek_api/nessie/sessions/{_NO_SUCH}/artifacts/",
     f"/nextseek_api/cc-assistant/artifacts/{_NO_SUCH}/download/"),
    (f"/nextseek_api/nessie/sessions/{_NO_SUCH}/transcript/1/",
     f"/nextseek_api/cc-assistant/transcript/{_NO_SUCH}/1/"),
    (f"/nextseek_api/nessie/uploads/{_NO_SUCH}/",
     f"/nextseek_api/cc-assistant/upload/status/{_NO_SUCH}/"),
]


class AliasParityTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("aliasuser", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_each_alias_answers_like_the_route_it_forwards_to(self):
        for new, old in ALIAS_PAIRS:
            with self.subTest(new=new):
                self.assertEqual(self.client.get(new).status_code,
                                 self.client.get(old).status_code)

    def test_aliases_require_authentication(self):
        anon = APIClient()
        for new, _ in ALIAS_PAIRS:
            with self.subTest(new=new):
                self.assertIn(anon.get(new).status_code, (401, 403))

    def test_task_progress_alias_returns_the_same_body(self):
        session = ChatSession.objects.create(user=self.user)
        task = QueryTask.objects.create(
            session=session, user=self.user, query="q", status="completed",
        )
        new = self.client.get(f"/nextseek_api/nessie/tasks/{task.task_id}/progress/")
        old = self.client.get(f"/nextseek_api/cc-assistant/tasks/{task.task_id}/progress/")
        self.assertEqual(new.status_code, 200)
        self.assertEqual(new.json(), old.json())


class AliasQueryDispatchTests(TestCase):
    """The two query aliases must carry force_cc through unchanged."""
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("aliasuser2", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _post(self, path):
        with patch("nextseek_api.services.cc_assistant.CCAssistantViewSet._start_task",
                   return_value=Response({"ok": True}, status=202)) as started:
            self.response = self.client.post(path, {"query": "find mice", "mode": "standard"},
                                             format="json")
        return started

    def test_query_alias_does_not_force_cc(self):
        started = self._post("/nextseek_api/nessie/query/")
        self.assertEqual(started.call_args.kwargs["force_cc"], False)

    def test_query_cc_alias_forces_cc(self):
        self.user.is_superuser = True  # forcing Container-CC is superuser-only, as force_route is
        self.user.save()
        started = self._post("/nextseek_api/nessie/query/cc/")
        self.assertEqual(started.call_args.kwargs["force_cc"], True)

    def test_query_cc_alias_refuses_a_member(self):
        self.assertFalse(self.user.is_superuser)
        started = self._post("/nextseek_api/nessie/query/cc/")
        self.assertEqual(self.response.status_code, 403)
        started.assert_not_called()


class UploadsVerbTests(TestCase):
    """Both verbs answer on nessie/uploads/.

    Caught live on the dev box, not here: two @action decorators sharing one
    url_path do not combine into one route. The router emits a pattern per
    action and the first registered wins, so GET returned 405 while every unit
    test still passed. DRF's .mapping is the mechanism for a second verb.
    """
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("verbuser", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_neither_verb_is_405(self):
        for call in (self.client.get, self.client.post):
            with self.subTest(verb=call.__name__):
                self.assertNotEqual(
                    call("/nextseek_api/nessie/uploads/").status_code, 405)

    def test_the_router_maps_both_verbs_to_one_pattern(self):
        """One pattern carrying both verbs, not two patterns racing."""
        from nextseek_api.services.nessie import NessieChatViewSet
        self.assertEqual(set(NessieChatViewSet.uploads_list.mapping),
                         {"get", "post"})

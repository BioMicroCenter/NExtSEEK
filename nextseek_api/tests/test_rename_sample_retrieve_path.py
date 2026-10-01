"""``manage.py rename_sample_retrieve_path``: stored chats name the download API by its new path, nothing else moves."""
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.management.commands.rename_sample_retrieve_path import rewrite

OLD = "/nextseek_api/admin/samples/retrieve/"
NEW = "/nextseek_api/samples/retrieve/"


class RenameStoredRetrievePath(TestCase):
    databases = {"default"}

    def setUp(self):
        user = User.objects.create_user("member", password="p")
        self.hit = ChatSession.objects.create(
            user=user,
            results_history=[{"api_plan": {"endpoint": OLD, "requestBody": {"identifiers": ["NHP-1"]}}}],
            extra_state={"chat_log": [{"text": f"called {OLD}"}], "n": 3},
            last_debug={"url": "http://127.0.0.1:8000" + OLD, OLD: True},
            title="kept",
        )
        self.miss = ChatSession.objects.create(user=user, results_history=[{"endpoint": "/nextseek_api/samples/graph_search/"}])
        self.task = QueryTask.objects.create(session=self.hit, user=user, query="q",
                                             progress=[{"endpoint": OLD}], result={"endpoint": OLD})
        self.stamp = ChatSession.objects.get(pk=self.hit.pk).updated_at

    def _run(self, *args):
        out = StringIO()
        call_command("rename_sample_retrieve_path", *args, stdout=out)
        return out.getvalue()

    def test_a_dry_run_counts_and_writes_nothing(self):
        out = self._run()
        self.assertIn("assistant_chat_session: 1 rows, 4 strings would be rewritten", out)
        self.assertIn("assistant_query_task: 1 rows, 2 strings would be rewritten", out)
        self.assertEqual(ChatSession.objects.get(pk=self.hit.pk).results_history[0]["api_plan"]["endpoint"], OLD)

    def test_apply_rewrites_every_string_and_key_and_nothing_else(self):
        self._run("--apply")
        hit = ChatSession.objects.get(pk=self.hit.pk)
        self.assertEqual(hit.results_history, [{"api_plan": {"endpoint": NEW, "requestBody": {"identifiers": ["NHP-1"]}}}])
        self.assertEqual(hit.extra_state, {"chat_log": [{"text": f"called {NEW}"}], "n": 3})
        self.assertEqual(hit.last_debug, {"url": "http://127.0.0.1:8000" + NEW, NEW: True})
        self.assertEqual((hit.title, hit.updated_at), ("kept", self.stamp))
        task = QueryTask.objects.get(pk=self.task.pk)
        self.assertEqual((task.progress, task.result), ([{"endpoint": NEW}], {"endpoint": NEW}))
        self.assertEqual(ChatSession.objects.get(pk=self.miss.pk).results_history,
                         [{"endpoint": "/nextseek_api/samples/graph_search/"}])

    def test_a_second_apply_finds_nothing(self):
        self._run("--apply")
        self.assertIn("total: 0 rows, 0 strings", self._run("--apply"))


def test_rewrite_leaves_non_strings_and_other_paths_alone():
    value = {"a": [1, None, True, 2.5, "/nextseek_api/samples/advanced_search/"], "b": "admin/samples/retrieve"}
    assert rewrite(value) == ({"a": [1, None, True, 2.5, "/nextseek_api/samples/advanced_search/"],
                               "b": "samples/retrieve"}, 1)

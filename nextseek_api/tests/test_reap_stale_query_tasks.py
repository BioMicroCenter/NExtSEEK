"""The reaper ends turns whose worker died; a live turn (recently updated) is left alone."""
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from nextseek_api.assistant.models_db import ChatSession, QueryTask


class ReapStaleQueryTasksTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("owner", password="pw")
        self.session = ChatSession.objects.create(user=self.user)

    def _task(self, status, updated_ago_min, created_ago_min=None):
        t = QueryTask.objects.create(session=self.session, user=self.user, query="q", status=status)
        now = timezone.now()
        QueryTask.objects.filter(pk=t.pk).update(  # auto_now fields need a queryset update
            updated_at=now - timedelta(minutes=updated_ago_min),
            created_at=now - timedelta(minutes=created_ago_min or updated_ago_min))
        return t

    def test_once_errors_the_stale_task_and_leaves_the_fresh_one(self):
        old, fresh = self._task("running", 40), self._task("running", 1)
        call_command("reap_stale_query_tasks", "--once", stdout=StringIO())
        old.refresh_from_db(), fresh.refresh_from_db()
        assert old.status == "error"
        assert old.progress[-1]["event"] == "query_error"
        assert "ask again" in old.result["error"]
        assert "restart" not in old.result["error"]
        assert fresh.status == "running"

    def test_a_long_live_turn_is_not_reaped(self):
        """Created 40 minutes ago but updated 1 minute ago: still moving."""
        live = self._task("running", 1, created_ago_min=40)
        call_command("reap_stale_query_tasks", "--once", stdout=StringIO())
        live.refresh_from_db()
        assert live.status == "running"

    def test_finished_tasks_are_untouched(self):
        done = self._task("completed", 90)
        call_command("reap_stale_query_tasks", "--once", stdout=StringIO())
        done.refresh_from_db()
        assert done.status == "completed" and done.progress == []

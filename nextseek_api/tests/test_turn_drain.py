"""A stopping web container: turn markers, the draining 503 (nothing created), and ending cut turns."""
import threading
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant import turn_drain
from nextseek_api.assistant.models_db import ChatSession, QueryTask


class TurnDirMixin:
    def setUp(self):
        super().setUp()
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.dict("os.environ", {"NEXTSEEK_TURN_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dir = turn_drain.turn_dir()


class StartTurnThreadTests(TurnDirMixin, TestCase):
    def test_marker_exists_while_the_target_runs_and_is_gone_after(self):
        inside = threading.Event()
        release = threading.Event()
        seen = {}

        def target(x):
            seen["x"] = x
            inside.set()
            release.wait(5)

        thread = turn_drain.start_turn_thread("t-1", target, x=7)
        assert inside.wait(5)
        assert (self.dir / "t-1").exists()
        release.set()
        thread.join(5)
        assert seen == {"x": 7}
        assert not (self.dir / "t-1").exists()

    def test_marker_is_removed_when_the_target_raises(self):
        def boom():
            raise RuntimeError("turn crashed")

        with patch("threading.excepthook"):
            thread = turn_drain.start_turn_thread("t-2", boom)
            thread.join(5)
        assert not (self.dir / "t-2").exists()

    def test_draining_follows_the_flag(self):
        assert not turn_drain.draining()
        (self.dir / ".draining").touch()
        assert turn_drain.draining()


class DrainingRefusalTests(TurnDirMixin, TestCase):
    databases = {"default"}

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("asker", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        p = patch("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        (self.dir / ".draining").touch()

    def _assert_refused(self, url):
        resp = self.client.post(url, {"query": "how many mice", "mode": "standard"}, format="json")
        assert resp.status_code == 503, resp.content
        assert resp["Retry-After"]
        assert resp.json()["errors"][0]["detail"] == "NExtSEEK is restarting. Send your question again in a minute."
        assert QueryTask.objects.count() == 0
        assert ChatSession.objects.count() == 0

    def test_assistant_async_query_is_refused_before_anything_is_created(self):
        self._assert_refused("/nextseek_api/assistant/query/async/")

    def test_assistant_sse_query_is_refused(self):
        self._assert_refused("/nextseek_api/assistant/query/")

    def test_cc_assistant_async_query_is_refused(self):
        self._assert_refused("/nextseek_api/cc-assistant/query/async/")


class EndCutTurnsTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("owner", password="pw")
        self.session = ChatSession.objects.create(user=self.user)

    def _task(self, status):
        return QueryTask.objects.create(session=self.session, user=self.user, query="q", status=status)

    def test_an_open_task_gets_the_cut_message_and_a_finished_one_is_untouched(self):
        running, done = self._task("running"), self._task("completed")
        out = StringIO()
        call_command("end_cut_turns", str(running.task_id), str(done.task_id), stdout=out)
        running.refresh_from_db(), done.refresh_from_db()
        assert running.status == "error"
        assert running.progress[-1]["event"] == "query_error"
        assert running.result["error"] == turn_drain.CUT_MESSAGE
        assert done.status == "completed" and done.progress == []
        assert "1" in out.getvalue()

    def test_a_turn_that_finishes_after_its_cut_keeps_the_cut_message(self):
        """The cut turn's thread ends later: its query_complete (and a failed save's query_error) change nothing the
        user sees, and stay in progress, marked, under a name no reader takes for the turn's end."""
        from nextseek_api.assistant.pipeline_adapter import make_db_event_callback

        running = self._task("running")
        call_command("end_cut_turns", str(running.task_id), stdout=StringIO())
        send = make_db_event_callback(str(running.task_id), str(self.session.session_id))
        send("query_complete", {"reply": "late", "debug": {}})
        send("query_error", {"error": "This answer was not saved"})

        running.refresh_from_db()
        assert running.status == "error"
        assert running.result["error"] == turn_drain.CUT_MESSAGE
        assert [e["event"] for e in running.progress] == ["query_error", "after_end", "after_end"]
        assert running.progress[1]["data"]["event"] == "query_complete"
        assert running.progress[1]["data"]["data"]["reply"] == "late"
        assert running.progress[2]["data"]["event"] == "query_error"

    def test_an_unknown_id_is_ignored(self):
        call_command("end_cut_turns", "00000000-0000-4000-8000-000000000000", stdout=StringIO())


class NessieBaseUrlTests(TestCase):
    def _default(self):
        from nextseek_api.management.commands.nessie import Command
        return Command().create_parser("manage.py", "nessie").parse_args([]).base_url

    def test_base_url_follows_the_internal_front_door_when_set(self):
        with patch.dict("os.environ", {"NEXTSEEK_INTERNAL_BASE_URL": "http://nextseek_nginx"}):
            assert self._default() == "http://nextseek_nginx"

    def test_base_url_default_is_unchanged(self):
        with patch.dict("os.environ", clear=False) as env:
            env.pop("NEXTSEEK_INTERNAL_BASE_URL", None)
            assert self._default() == "http://localhost:8000"

"""A chat turn the app container's restart killed is marked interrupted at boot.

A turn runs on a thread of the app process. When the container restarts mid-turn
(an out-of-memory kill, a rebuild), that thread dies and its QueryTask stays
"running" forever: the chat panel waits on it, and the reingest upload check counts
it as a live turn, refusing every upload as ambiguous for the next hour and the
build it made as never delivered (seen live, 2026-10-20). The entrypoint runs
fail_interrupted_turns before any server starts, when no turn can be in flight.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from nextseek_api.assistant.interrupted_turns import INTERRUPTED_MESSAGE, fail_interrupted_turns
from nextseek_api.assistant.models_db import ChatSession, QueryTask


class InterruptedTurnsTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="pw")
        self.session = ChatSession.objects.create(user=self.user)

    def _task(self, status):
        return QueryTask.objects.create(session=self.session, user=self.user, query="q",
                                        status=status)

    def test_a_running_turn_is_marked_interrupted(self):
        task = self._task("running")
        self.assertEqual(fail_interrupted_turns(), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "error")
        self.assertEqual(task.result["error"], INTERRUPTED_MESSAGE)

    def test_finished_and_pending_turns_are_left_alone(self):
        done, failed, pending = self._task("completed"), self._task("error"), self._task("pending")
        self.assertEqual(fail_interrupted_turns(), 0)
        for task, status in ((done, "completed"), (failed, "error"), (pending, "pending")):
            task.refresh_from_db()
            self.assertEqual(task.status, status)

    def test_the_command_runs_it(self):
        task = self._task("running")
        call_command("fail_interrupted_turns")
        task.refresh_from_db()
        self.assertEqual(task.status, "error")

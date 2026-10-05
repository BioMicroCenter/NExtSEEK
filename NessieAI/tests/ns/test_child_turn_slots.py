"""A child NS turn a Container-CC turn starts is one of that turn's two slots (operator ruling 2026-09-30).

nextseek-query, nextseek-plan and nextseek-pipeline start a child turn on query/async with the turn pass. The view takes
a slot before it makes anything and answers BUSY when both are taken; the child's runner (NessieAI/ns/turn.py
run_async_pipeline) gives the slot back in its finally, however the child ends; the view gives it back when the thread
never starts. A browser's query/async takes no slot. No model runs: the pipeline entry point is a stand-in.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from chat_nextseek.llm_clients import LLMFatalError
from NessieAI.ns.turn import run_async_pipeline
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask
from nextseek_api.assistant.turn_pass import issue_pass, set_deadline

A = "/nextseek_api/assistant"


def _live_pass(user):
    """A running Container-CC turn and its raw pass (plan 02's issuing; see plan 03 Task 0 Step 3 item 8)."""
    chat = ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=chat, user=user, query="q", status="running")
    turn, raw = issue_pass(task=task, chat=chat, user=user, login=(user.username, "p"))
    set_deadline(turn, time.time() + 180)
    return chat, turn, raw


class _Base(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.chat, self.turn, self.raw = _live_pass(self.user)
        self.threads = []
        # "hold": the child is still running when the view answers; "run": it runs to its end inside start();
        # "fail": the thread cannot start.
        self.start_mode = "hold"
        test = self

        class _Thread:
            def __init__(self, target=None, kwargs=None, daemon=None):
                self.target, self.kwargs = target, kwargs
                test.threads.append(self)

            def start(self):
                if test.start_mode == "fail":
                    raise RuntimeError("can't start new thread")
                if test.start_mode == "run":
                    self.target(**self.kwargs)

        for patcher in (
            patch("nextseek_api.services.assistant.threading.Thread", _Thread),
            patch("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", return_value=True),
            patch("nextseek_api.services.assistant.plain_scope", return_value=None),
            patch("nextseek_api.services.assistant._select_chat_config",
                  return_value=SimpleNamespace(API_USER="", API_PASS="")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _post(self):
        return APIClient().post(f"{A}/query/async/",
                                {"query": "how many", "mode": "standard", "session_id": str(self.chat.session_id)},
                                format="json", HTTP_AUTHORIZATION=f"NextseekTurn {self.raw}")

    def _set_in_flight(self, n):
        CCTurn.objects.filter(pk=self.turn.pk).update(ops_in_flight=n)

    def in_flight(self) -> int:
        self.turn.refresh_from_db(fields=["ops_in_flight"])
        return self.turn.ops_in_flight

    def children(self):
        return QueryTask.objects.filter(parent_cc_turn=self.turn)


class ChildTurnSlotTests(_Base):
    def test_with_two_ops_running_a_child_turn_is_busy_and_nothing_is_made(self):
        self._set_in_flight(2)
        resp = self._post()
        self.assertEqual((resp.status_code, resp.json()["code"]), (429, "BUSY"))
        self.assertEqual((self.children().count(), self.threads), (0, []))
        self.assertEqual(self.in_flight(), 2)

    def test_one_op_and_one_running_child_leave_no_room_for_a_second_child(self):
        self._set_in_flight(1)
        first = self._post()  # "hold": this child is still running
        self.assertEqual(first.status_code, 202, first.content)
        self.assertEqual(self.in_flight(), 2)
        second = self._post()
        self.assertEqual((second.status_code, second.json()["code"]), (429, "BUSY"))
        self.assertEqual((self.children().count(), len(self.threads)), (1, 1))
        self.assertEqual(self.threads[0].kwargs["parent_cc_turn"].pk, self.turn.pk)

    def test_the_childs_slot_is_held_while_it_runs_and_given_back_when_it_finishes(self):
        self.start_mode = "run"
        seen = []
        with patch("NessieAI.ns.turn.run_query", lambda *a, **k: seen.append(self.in_flight())):
            resp = self._post()
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual((seen, self.in_flight()), ([1], 0))

    def test_a_child_that_fails_to_start_gives_its_slot_back(self):
        self.start_mode = "fail"
        with self.assertRaises(RuntimeError):
            self._post()
        self.assertEqual(self.in_flight(), 0)

    def test_a_browser_query_async_takes_no_slot(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        with patch("nextseek_api.services.assistant.take_op_slot") as take:
            resp = client.post(f"{A}/query/async/",
                               {"query": "q", "mode": "standard", "session_id": str(self.chat.session_id)},
                               format="json")
        self.assertEqual(resp.status_code, 202, resp.content)
        take.assert_not_called()
        self.assertIsNone(self.threads[0].kwargs["parent_cc_turn"])
        self.assertEqual(self.in_flight(), 0)


class RunnerReleaseTests(_Base):
    """The child's runner, called as the daemon thread calls it. The view took the slot (ops_in_flight 1)."""

    def _run(self, outcome=None, *, save_error=None, parent="the turn"):
        self._set_in_flight(1)
        with patch("NessieAI.ns.turn.run_query", side_effect=outcome), \
             patch("NessieAI.ns.turn._save_session_or_report", side_effect=save_error):
            run_async_pipeline(adapter=MagicMock(), chat_config=SimpleNamespace(),
                               req=SimpleNamespace(mode="standard", query="q"), send_event=MagicMock(),
                               api_user="u1", api_pass="p", chat_session=MagicMock(),
                               resolved_session_id=str(self.chat.session_id),
                               parent_cc_turn=self.turn if parent == "the turn" else parent)

    def test_a_child_that_finishes_gives_its_slot_back(self):
        self._run()
        self.assertEqual(self.in_flight(), 0)

    def test_a_child_that_errors_gives_its_slot_back(self):
        fatal = LLMFatalError("both models down", agent="parser", unavailable=True)
        for exc in (RuntimeError("boom"), fatal):
            with self.subTest(type(exc).__name__):
                self._run(exc)
                self.assertEqual(self.in_flight(), 0)

    def test_a_cancelled_or_killed_child_gives_its_slot_back(self):
        """The worker's abort (SystemExit) and an interrupt are BaseExceptions the runner does not catch; they pass
        through its finally."""
        for exc in (SystemExit(1), KeyboardInterrupt()):
            with self.subTest(type(exc).__name__):
                with self.assertRaises(type(exc)):
                    self._run(exc)
                self.assertEqual(self.in_flight(), 0)

    def test_a_save_that_raises_still_gives_the_slot_back(self):
        with self.assertRaises(RuntimeError):
            self._run(save_error=RuntimeError("save failed"))
        self.assertEqual(self.in_flight(), 0)

    def test_a_turn_of_its_own_releases_nothing(self):
        with patch("NessieAI.ns.turn.release_op_slot") as release:
            self._run(parent=None)
        release.assert_not_called()
        self.assertEqual(self.in_flight(), 1)

"""At most two ops of one Container-CC turn run at once (approach 1, piece 2; operator ruling 2026-09-28).

The count lives on the turn's row (``CCTurn.ops_in_flight``), taken with one conditional UPDATE and given back in
``run_op``'s ``finally``, so a killed op cannot keep its slot and two workers cannot both take the last one.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.db import InterfaceError, OperationalError, connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from chat_nextseek.llm_clients import LLMFatalError
from NessieAI.ns import granular
from NessieAI.ns.granular import OpBusyError, run_op
from NessieAI.ns.turn_memory import MAX_OPS_IN_FLIGHT, release_op_slot, take_op_slot
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask


def make_turn(user) -> CCTurn:
    chat = ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=chat, user=user, query="q", status="running")
    return CCTurn.objects.create(task=task, user=user, chat=chat, pass_hash="a" * 64)


def in_flight(turn) -> int:
    turn.refresh_from_db(fields=["ops_in_flight"])
    return turn.ops_in_flight


class SlotTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.turn = make_turn(User.objects.create_user("u1", password="p"))

    def test_two_slots_then_none(self):
        self.assertEqual(MAX_OPS_IN_FLIGHT, 2)
        self.assertEqual([take_op_slot(self.turn) for _ in range(3)], [True, True, False])
        self.assertEqual(in_flight(self.turn), 2)

    def test_a_released_slot_can_be_taken_again(self):
        take_op_slot(self.turn)
        take_op_slot(self.turn)
        release_op_slot(self.turn)
        self.assertTrue(take_op_slot(self.turn))
        self.assertEqual(in_flight(self.turn), 2)

    def test_release_never_goes_below_zero(self):
        release_op_slot(self.turn)
        self.assertEqual(in_flight(self.turn), 0)

    def test_taking_a_slot_is_one_conditional_update(self):
        """No read-then-write: two workers cannot both see a free slot and both take it."""
        with CaptureQueriesContext(connection) as ctx:
            take_op_slot(self.turn)
        [query] = ctx.captured_queries
        sql = query["sql"].upper()
        self.assertTrue(sql.startswith("UPDATE"), sql)
        self.assertRegex(sql, r'WHERE .*OPS_IN_FLIGHT["`]? < 2')


class RunOpSlotTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.turn = make_turn(User.objects.create_user("u1", password="p"))

    def _run(self, handler, turn="the turn"):
        with patch.dict(granular._HANDLERS, {"entity": handler}):
            return run_op("entity", {"query": "q"}, config=SimpleNamespace(), session=None,
                          write_gate=MagicMock(), turn=self.turn if turn == "the turn" else turn)

    def test_the_op_holds_a_slot_while_it_runs_and_gives_it_back(self):
        seen = []

        def handler(*args, **op_ctx):
            seen.append(in_flight(self.turn))
            return {}

        self._run(handler)
        self.assertEqual(seen, [1])
        self.assertEqual(in_flight(self.turn), 0)

    def test_with_both_slots_taken_the_op_is_busy_and_never_runs(self):
        take_op_slot(self.turn)
        take_op_slot(self.turn)
        handler = MagicMock(return_value={})
        with self.assertRaises(OpBusyError):
            self._run(handler)
        handler.assert_not_called()
        self.assertEqual(in_flight(self.turn), 2)

    def test_without_a_turn_no_slot_is_taken(self):
        with patch("NessieAI.ns.turn_memory.take_op_slot") as take:
            self._run(MagicMock(return_value={}), turn=None)
        take.assert_not_called()

    def test_a_killed_op_gives_its_slot_back(self):
        """Review focus 1: the worker's abort (SystemExit), an interrupt, a model fatal (a BaseException) and a plain
        failure all pass through the finally that gives the slot back."""
        fatal = LLMFatalError("both models down", agent="entity", unavailable=True)
        for exc in (SystemExit(1), KeyboardInterrupt(), fatal, RuntimeError("boom")):
            with self.subTest(type(exc).__name__):
                def handler(*args, _exc=exc, **op_ctx):
                    raise _exc

                with self.assertRaises(type(exc)):
                    self._run(handler)
                self.assertEqual(in_flight(self.turn), 0)


class ReleaseRetryTests(SimpleTestCase):
    def test_a_dropped_connection_is_retried_once(self):
        manager = MagicMock()
        manager.filter.return_value.update.side_effect = [OperationalError("gone away"), 1]
        with patch.object(CCTurn, "objects", manager), patch("django.db.connection") as conn:
            release_op_slot(SimpleNamespace(pk=7))
        self.assertEqual(manager.filter.return_value.update.call_count, 2)
        conn.close.assert_called_once_with()

    def test_a_release_that_fails_twice_is_logged_never_raised(self):
        manager = MagicMock()
        manager.filter.return_value.update.side_effect = OperationalError("gone away")
        with patch.object(CCTurn, "objects", manager), patch("django.db.connection"), \
             self.assertLogs("NessieAI.ns.turn_memory", level="WARNING"):
            release_op_slot(SimpleNamespace(pk=7))

    def test_an_interface_error_and_a_failing_close_are_logged_never_raised(self):
        """InterfaceError is not a DatabaseError, and the retry's close can fail too; neither may leave the finally."""
        manager = MagicMock()
        manager.filter.return_value.update.side_effect = InterfaceError("(0, '')")
        with patch.object(CCTurn, "objects", manager), patch("django.db.connection") as conn, \
             self.assertLogs("NessieAI.ns.turn_memory", level="WARNING") as logs:
            conn.close.side_effect = InterfaceError("(0, '')")
            release_op_slot(SimpleNamespace(pk=7))
        self.assertEqual(len(logs.records), 1)

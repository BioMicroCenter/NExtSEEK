"""A turn that cannot be saved must say so, not vanish.

Production, session c0062000, 2026-09-07: an NS turn completed successfully at
63.83s, streamed a correct reply, and persisted nothing. results_history and
chat_log were both empty and updated_at still equalled created_at. Reloading the
page showed an empty chat.

Two defects combined:

* ``save()`` swallowed the locked write with a bare ``except: pass`` and logged
  nothing, then the unlocked retry hit the same (2006, 'Server has gone away')
  and raised out of a background thread -- "Exception in thread Thread-2 (_run)".
* the caller runs ``adapter.save()`` in a ``finally:`` OUTSIDE its own
  ``try/except``, so nothing turned that into a visible error, and
  ``_auto_title_if_unset`` never ran either, which is why the chat had no title.
"""

import logging
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import OperationalError
from django.test import TestCase

from nextseek_api.assistant.models_db import ChatSession
from nextseek_api.assistant.session_adapter import DictSessionAdapter, SessionSaveError


class SaveFailureIsLoudTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("saveuser", password="x")
        self.session = ChatSession.objects.create(user=self.user)
        self.adapter = DictSessionAdapter(self.session)
        self.adapter["results_history"] = [{"id": 1, "user_query": "q"}]

    def test_a_save_that_cannot_persist_raises_a_named_error(self):
        """Not a bare OperationalError out of a thread nobody is watching."""
        with patch.object(ChatSession, "save",
                          side_effect=OperationalError(2006, "Server has gone away")):
            with self.assertRaises(SessionSaveError):
                self.adapter.save()

    def test_the_error_says_which_session_and_how_big_the_write_was(self):
        """13.5 MB of api_result_full was the cause. A message that does not say
        so leaves the next person guessing."""
        with patch.object(ChatSession, "save",
                          side_effect=OperationalError(2006, "Server has gone away")):
            with self.assertRaises(SessionSaveError) as caught:
                self.adapter.save()

        msg = str(caught.exception)
        self.assertIn(str(self.session.session_id), msg)
        self.assertIn("results_history=", msg)
        self.assertRegex(msg, r"results_history=[\d,]+B")

    def test_the_swallowed_locked_write_is_logged_not_silent(self):
        """The locked path legitimately falls back on a backend without row
        locking, but a bare pass meant a real failure left no trace at all."""
        # transaction is imported inside save(), so patch it at the source.
        with patch("django.db.transaction.atomic",
                   side_effect=OperationalError(2006, "gone")):
            with self.assertLogs("nextseek_api.assistant.session_adapter",
                                 level=logging.WARNING) as logs:
                self.adapter.save()

        self.assertTrue(
            any("locked" in line.lower() for line in logs.output),
            f"expected the locked-path failure to be logged, got {logs.output}",
        )

    def test_a_successful_save_still_returns_quietly(self):
        self.adapter.save()

        self.session.refresh_from_db()
        self.assertEqual(self.session.results_history, [{"id": 1, "user_query": "q"}])


class LockedSaveIsTakenTests(TestCase):
    """The row-locked read-merge-write is the normal path, not a fallback's fallback.

    ``ChatSession`` was imported only under ``TYPE_CHECKING``, so the locked path
    raised NameError on every save, the except logged "locked save failed", and the
    turn was written unlocked and UNMERGED: a concurrent turn's bundle in the same
    session was overwritten, which is exactly what the lock and the merge exist to stop.
    """

    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("lockuser", password="x")
        self.session = ChatSession.objects.create(user=self.user)
        self.adapter = DictSessionAdapter(self.session)
        self.adapter["results_history"] = [{"id": 1, "user_query": "this turn"}]

    def test_a_save_takes_the_locked_path_and_logs_no_fallback(self):
        real = ChatSession.objects.select_for_update
        with patch.object(ChatSession.objects, "select_for_update", wraps=real) as locked:
            with self.assertNoLogs("nextseek_api.assistant.session_adapter",
                                   level=logging.WARNING):
                self.adapter.save()

        locked.assert_called_once_with()

    def test_a_concurrent_turns_bundle_survives_the_save(self):
        """Only the locked path merges; the unlocked fallback writes this turn's copy."""
        ChatSession.objects.filter(pk=self.session.pk).update(
            results_history=[{"id": 2, "user_query": "the other turn"}]
        )

        self.adapter.save()

        self.session.refresh_from_db()
        self.assertEqual([b["id"] for b in self.session.results_history], [2, 1])


class TurnSurfacesTheFailureTests(TestCase):
    """The caller must turn a failed save into something the user can see."""

    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("surfaceuser", password="x")
        self.session = ChatSession.objects.create(user=self.user)

    def test_a_failed_save_emits_an_error_event_instead_of_killing_the_thread(self):
        from NessieAI.ns.turn import _save_session_or_report

        events = []
        adapter = DictSessionAdapter(self.session)

        with patch.object(DictSessionAdapter, "save",
                          side_effect=SessionSaveError("too big")):
            _save_session_or_report(
                adapter, self.session,
                lambda name, data: events.append((name, data)),
                str(self.session.session_id),
            )

        self.assertEqual([n for n, _ in events], ["query_error"])
        self.assertIn("not saved", events[0][1]["error"].lower())

    def test_a_successful_save_emits_nothing_and_titles_the_chat(self):
        from NessieAI.ns.turn import _save_session_or_report

        events = []
        adapter = DictSessionAdapter(self.session)
        adapter["results_history"] = [{"id": 1, "user_query": "find me monkeys"}]

        _save_session_or_report(
            adapter, self.session,
            lambda name, data: events.append((name, data)),
            str(self.session.session_id),
        )

        self.assertEqual(events, [])
        self.session.refresh_from_db()
        self.assertTrue(self.session.title)

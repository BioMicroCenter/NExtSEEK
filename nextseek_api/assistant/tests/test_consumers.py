"""Tests for the TaskProgressConsumer WebSocket consumer.

Covers: connect, disconnect, origin validation, task lookup,
ownership checks, polling loop, and terminal state handling.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase, override_settings


# ---------------------------------------------------------------------------
# _is_allowed_origin (static method, no DB needed)
# ---------------------------------------------------------------------------

class IsAllowedOriginTests(SimpleTestCase):
    """Tests for TaskProgressConsumer._is_allowed_origin."""

    def _call(self, origin):
        from nextseek_api.assistant.consumers import TaskProgressConsumer
        return TaskProgressConsumer._is_allowed_origin(origin)

    def test_none_origin_allowed(self):
        """None means same-origin or non-browser client — should be allowed."""
        self.assertTrue(self._call(None))

    @override_settings(
        CORS_ALLOWED_ORIGINS=["https://example.com"],
        CSRF_TRUSTED_ORIGINS=["https://trusted.com"],
    )
    def test_cors_origin_allowed(self):
        self.assertTrue(self._call("https://example.com"))

    @override_settings(
        CORS_ALLOWED_ORIGINS=[],
        CSRF_TRUSTED_ORIGINS=["https://trusted.com"],
    )
    def test_csrf_trusted_origin_allowed(self):
        self.assertTrue(self._call("https://trusted.com"))

    @override_settings(
        CORS_ALLOWED_ORIGINS=["https://example.com"],
        CSRF_TRUSTED_ORIGINS=["https://trusted.com"],
    )
    def test_unknown_origin_rejected(self):
        self.assertFalse(self._call("https://evil.com"))

    @override_settings(CORS_ALLOWED_ORIGINS=[], CSRF_TRUSTED_ORIGINS=[])
    def test_empty_allowed_lists_rejects_all(self):
        self.assertFalse(self._call("https://anything.com"))


# ---------------------------------------------------------------------------
# Async consumer tests — mock DB layer and transport
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _authed_user(pk=42):
    """A stand-in for a logged-in Django user."""
    user = MagicMock()
    user.is_authenticated = True
    user.pk = pk
    return user


def _anonymous_user():
    """The real AnonymousUser channels' AuthMiddlewareStack puts in scope
    when no session cookie identifies a user."""
    from django.contrib.auth.models import AnonymousUser

    return AnonymousUser()


def _make_consumer(task_id="00000000-0000-0000-0000-000000000001",
                   headers=None, user=None):
    """Build a TaskProgressConsumer with mocked transport.

    Returns (consumer, sent_messages_list).
    """
    from nextseek_api.assistant.consumers import TaskProgressConsumer

    consumer = TaskProgressConsumer()
    sent: list[str] = []

    # Simulate scope
    consumer.scope = {
        "type": "websocket",
        "url_route": {"kwargs": {"task_id": task_id}},
        "headers": headers or [],
    }
    if user is not None:
        consumer.scope["user"] = user

    # Mock transport methods
    consumer.accept = AsyncMock()
    consumer.close = AsyncMock()
    consumer.send = AsyncMock(side_effect=lambda text_data: sent.append(text_data))

    return consumer, sent


class ConnectOriginRejectionTests(SimpleTestCase):
    """connect() should close when origin is not allowed."""

    @override_settings(CORS_ALLOWED_ORIGINS=[], CSRF_TRUSTED_ORIGINS=[])
    def test_bad_origin_closes_immediately(self):
        consumer, sent = _make_consumer(
            headers=[(b"origin", b"https://evil.com")],
        )
        _run(consumer.connect())
        consumer.close.assert_awaited_once()
        consumer.accept.assert_not_awaited()

    @override_settings(
        CORS_ALLOWED_ORIGINS=["https://good.com"],
        CSRF_TRUSTED_ORIGINS=[],
    )
    def test_good_origin_proceeds_to_task_lookup(self):
        """With a valid origin, connect proceeds past origin check."""
        consumer, _ = _make_consumer(
            headers=[(b"origin", b"https://good.com")],
        )
        # Task lookup returns None -> closes, but after accept was NOT called
        consumer._get_task_info = AsyncMock(return_value=None)
        _run(consumer.connect())
        # It should have tried to get task info (past the origin check)
        consumer._get_task_info.assert_awaited_once()
        consumer.close.assert_awaited_once()


class ConnectTaskLookupTests(SimpleTestCase):
    """connect() task lookup and ownership."""

    def test_task_not_found_closes(self):
        consumer, _ = _make_consumer()
        consumer._get_task_info = AsyncMock(return_value=None)
        _run(consumer.connect())
        consumer.close.assert_awaited_once()
        consumer.accept.assert_not_awaited()

    def test_task_found_accepts_and_starts_polling(self):
        consumer, _ = _make_consumer()
        consumer._get_task_info = AsyncMock(
            return_value={"status": "running", "user_id": 1}
        )
        # Stop poll loop immediately
        consumer._poll_loop = AsyncMock()
        _run(consumer.connect())
        consumer.accept.assert_awaited_once()
        self.assertTrue(consumer._polling)


class ConnectRequiresAuthenticatedOwnerTests(SimpleTestCase):
    """End-to-end connect() behaviour with the real _get_task_info.

    The task_id UUID is not a capability token: holding a valid one is not
    enough, the connection must carry an authenticated user who owns the task.
    Only the ORM is mocked here, so these exercise the actual auth branch.
    """

    OWNER_PK = 42

    def _connect(self, consumer, task_status="running", task_user_id=OWNER_PK):
        mock_task = MagicMock()
        mock_task.status = task_status
        mock_task.user_id = task_user_id
        consumer._poll_loop = AsyncMock()
        with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
            mock_qs.get.return_value = mock_task
            _run(consumer.connect())

    def test_no_user_in_scope_is_rejected(self):
        consumer, _ = _make_consumer()
        self._connect(consumer)
        consumer.accept.assert_not_awaited()
        consumer.close.assert_awaited_once()

    def test_user_none_in_scope_is_rejected(self):
        consumer, _ = _make_consumer()
        consumer.scope["user"] = None
        self._connect(consumer)
        consumer.accept.assert_not_awaited()
        consumer.close.assert_awaited_once()

    def test_anonymous_user_is_rejected(self):
        consumer, _ = _make_consumer(user=_anonymous_user())
        self._connect(consumer)
        consumer.accept.assert_not_awaited()
        consumer.close.assert_awaited_once()

    def test_authenticated_non_owner_is_rejected(self):
        consumer, _ = _make_consumer(user=_authed_user(pk=99))
        self._connect(consumer, task_user_id=self.OWNER_PK)
        consumer.accept.assert_not_awaited()
        consumer.close.assert_awaited_once()

    def test_authenticated_owner_is_accepted(self):
        consumer, _ = _make_consumer(user=_authed_user(pk=self.OWNER_PK))
        self._connect(consumer, task_user_id=self.OWNER_PK)
        consumer.accept.assert_awaited_once()
        consumer.close.assert_not_awaited()
        self.assertTrue(consumer._polling)

    def test_authenticated_owner_still_receives_the_stream(self):
        """The legitimate path must keep working, not merely be accepted:
        the owner gets the progress events and the final done frame."""
        consumer, sent = _make_consumer(user=_authed_user(pk=self.OWNER_PK))
        consumer.POLL_INTERVAL = 0  # don't sleep 300ms in a unit test

        mock_task = MagicMock()
        mock_task.status = "running"
        mock_task.user_id = self.OWNER_PK

        call_count = 0

        async def fake_get_progress():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ("running", [{"event": "step", "step": "search"}], None)
            return (
                "completed",
                [{"event": "step", "step": "search"}],
                {"answer": "42"},
            )

        consumer._get_progress_snapshot = AsyncMock(side_effect=fake_get_progress)

        async def scenario():
            with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
                mock_qs.get.return_value = mock_task
                await consumer.connect()
            # connect() fire-and-forgets the poll loop; drain it.
            pending = [
                t for t in asyncio.all_tasks() if t is not asyncio.current_task()
            ]
            await asyncio.gather(*pending)

        _run(scenario())

        consumer.accept.assert_awaited_once()
        messages = [json.loads(m) for m in sent]
        step_msgs = [m for m in messages if m.get("event") == "step"]
        done_msgs = [m for m in messages if m.get("event") == "done"]
        self.assertEqual(len(step_msgs), 1)
        self.assertEqual(step_msgs[0]["step"], "search")
        self.assertEqual(len(done_msgs), 1)
        self.assertEqual(done_msgs[0]["status"], "completed")
        self.assertEqual(done_msgs[0]["result"], {"answer": "42"})


class DisconnectTests(SimpleTestCase):
    """disconnect() should stop the polling flag."""

    def test_disconnect_stops_polling(self):
        consumer, _ = _make_consumer()
        consumer._polling = True
        _run(consumer.disconnect(1000))
        self.assertFalse(consumer._polling)


class PollLoopTests(SimpleTestCase):
    """_poll_loop sends progress events and handles terminal states."""

    def test_sends_new_progress_events(self):
        consumer, sent = _make_consumer()
        consumer._polling = True

        call_count = 0

        async def fake_get_progress():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ("running", [{"event": "step", "step": "search"}], None)
            # Terminal on second call
            return ("completed", [{"event": "step", "step": "search"}], {"answer": "42"})

        consumer._get_progress_snapshot = AsyncMock(side_effect=fake_get_progress)

        _run(consumer._poll_loop())

        # Should have sent the step event + done event
        messages = [json.loads(m) for m in sent]
        step_msgs = [m for m in messages if m.get("event") == "step"]
        done_msgs = [m for m in messages if m.get("event") == "done"]
        self.assertEqual(len(step_msgs), 1)
        self.assertEqual(len(done_msgs), 1)
        self.assertEqual(done_msgs[0]["status"], "completed")
        self.assertEqual(done_msgs[0]["result"], {"answer": "42"})
        consumer.close.assert_awaited_once()

    def test_terminal_error_sends_done_with_error_status(self):
        consumer, sent = _make_consumer()
        consumer._polling = True
        consumer._get_progress_snapshot = AsyncMock(
            return_value=("error", [], None),
        )
        _run(consumer._poll_loop())
        messages = [json.loads(m) for m in sent]
        self.assertEqual(messages[0]["event"], "done")
        self.assertEqual(messages[0]["status"], "error")

    def test_cancelled_error_is_handled(self):
        """CancelledError during poll should be caught gracefully."""
        consumer, sent = _make_consumer()
        consumer._polling = True
        consumer.task_id = "test-task-id"

        consumer._get_progress_snapshot = AsyncMock(
            side_effect=asyncio.CancelledError()
        )
        # Should not raise
        _run(consumer._poll_loop())
        self.assertEqual(len(sent), 0)

    def test_incremental_progress_delivery(self):
        """Only new events since last poll are sent."""
        consumer, sent = _make_consumer()
        consumer._polling = True
        call_count = 0

        async def fake_get_progress():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ("running", [{"event": "step", "n": 1}], None)
            if call_count == 2:
                return ("running", [{"event": "step", "n": 1}, {"event": "step", "n": 2}], None)
            return ("completed", [{"event": "step", "n": 1}, {"event": "step", "n": 2}], {"ok": True})

        consumer._get_progress_snapshot = AsyncMock(side_effect=fake_get_progress)
        _run(consumer._poll_loop())

        messages = [json.loads(m) for m in sent]
        step_msgs = [m for m in messages if m.get("event") == "step"]
        # Should be 2 step messages (n=1 from first poll, n=2 from second poll),
        # NOT 3 (n=1 should not be re-sent)
        self.assertEqual(len(step_msgs), 2)
        self.assertEqual(step_msgs[0]["n"], 1)
        self.assertEqual(step_msgs[1]["n"], 2)


# ---------------------------------------------------------------------------
# _get_task_info and _get_progress_snapshot
#
# These methods are wrapped with @database_sync_to_async at class definition
# time, so we need to call them as async coroutines. We mock the ORM layer
# and use _run() to await the results.
# ---------------------------------------------------------------------------

class GetTaskInfoTests(SimpleTestCase):
    """_get_task_info DB helper — tested with mocked ORM."""

    def test_task_not_found_returns_none(self):
        # Authenticated so the lookup is actually reached: this covers the
        # DoesNotExist branch, not the unauthenticated short-circuit.
        consumer, _ = _make_consumer(user=_authed_user(pk=42))
        consumer.task_id = str(uuid.uuid4())

        with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
            from nextseek_api.assistant.models_db import QueryTask
            mock_qs.get.side_effect = QueryTask.DoesNotExist
            result = _run(consumer._get_task_info())
        self.assertIsNone(result)

    def test_task_found_but_user_is_none_returns_none(self):
        """A valid task_id with no authenticated user must NOT be served.

        This asserted the opposite before the UUID stopped being treated as a
        capability token: the task was found, no user was in scope, and the
        consumer handed the stream over anyway.
        """
        consumer, _ = _make_consumer()
        consumer.task_id = str(uuid.uuid4())
        consumer.scope["user"] = None

        mock_task = MagicMock()
        mock_task.status = "running"
        mock_task.user_id = 42

        with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
            mock_qs.get.return_value = mock_task
            result = _run(consumer._get_task_info())
        self.assertIsNone(result)

    def test_no_user_key_in_scope_returns_none(self):
        """No auth middleware in the stack at all -> no "user" key. Reject,
        and do not raise."""
        consumer, _ = _make_consumer()
        consumer.task_id = str(uuid.uuid4())
        self.assertNotIn("user", consumer.scope)

        mock_task = MagicMock()
        mock_task.status = "running"
        mock_task.user_id = 42

        with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
            mock_qs.get.return_value = mock_task
            result = _run(consumer._get_task_info())
        self.assertIsNone(result)

    def test_anonymous_user_returns_none(self):
        """AnonymousUser is what AuthMiddlewareStack supplies without a
        session cookie — it must be rejected and must not raise."""
        consumer, _ = _make_consumer(user=_anonymous_user())
        consumer.task_id = str(uuid.uuid4())

        mock_task = MagicMock()
        mock_task.status = "running"
        mock_task.user_id = 42

        with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
            mock_qs.get.return_value = mock_task
            result = _run(consumer._get_task_info())
        self.assertIsNone(result)

    def test_user_without_is_authenticated_attribute_returns_none(self):
        """A scope "user" that is not a Django user at all must be rejected
        rather than blowing up in the consumer."""
        consumer, _ = _make_consumer(user=object())
        consumer.task_id = str(uuid.uuid4())

        mock_task = MagicMock()
        mock_task.status = "running"
        mock_task.user_id = 42

        with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
            mock_qs.get.return_value = mock_task
            result = _run(consumer._get_task_info())
        self.assertIsNone(result)

    def test_authenticated_user_wrong_owner_returns_none(self):
        user = MagicMock()
        user.is_authenticated = True
        user.pk = 99

        consumer, _ = _make_consumer(user=user)
        consumer.task_id = str(uuid.uuid4())

        mock_task = MagicMock()
        mock_task.status = "running"
        mock_task.user_id = 42  # different from user.pk

        with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
            mock_qs.get.return_value = mock_task
            result = _run(consumer._get_task_info())
        self.assertIsNone(result)

    def test_authenticated_user_correct_owner_returns_dict(self):
        user = MagicMock()
        user.is_authenticated = True
        user.pk = 42

        consumer, _ = _make_consumer(user=user)
        consumer.task_id = str(uuid.uuid4())

        mock_task = MagicMock()
        mock_task.status = "completed"
        mock_task.user_id = 42

        with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
            mock_qs.get.return_value = mock_task
            result = _run(consumer._get_task_info())
        self.assertEqual(result, {"status": "completed", "user_id": 42})


class GetProgressSnapshotTests(SimpleTestCase):
    """_get_progress_snapshot returns (status, progress, result)."""

    def test_task_found(self):
        consumer, _ = _make_consumer()
        consumer.task_id = str(uuid.uuid4())

        mock_task = MagicMock()
        mock_task.status = "running"
        mock_task.progress = [{"event": "step", "step": "search"}]
        mock_task.result = None

        with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
            mock_qs.get.return_value = mock_task
            status, progress, result = _run(consumer._get_progress_snapshot())
        self.assertEqual(status, "running")
        self.assertEqual(len(progress), 1)
        self.assertIsNone(result)

    def test_task_not_found_returns_error(self):
        consumer, _ = _make_consumer()
        consumer.task_id = str(uuid.uuid4())

        with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
            from nextseek_api.assistant.models_db import QueryTask
            mock_qs.get.side_effect = QueryTask.DoesNotExist
            status, progress, result = _run(consumer._get_progress_snapshot())
        self.assertEqual(status, "error")
        self.assertEqual(progress, [])
        self.assertIsNone(result)

    def test_task_with_none_progress_returns_empty_list(self):
        consumer, _ = _make_consumer()
        consumer.task_id = str(uuid.uuid4())

        mock_task = MagicMock()
        mock_task.status = "pending"
        mock_task.progress = None
        mock_task.result = None

        with patch("nextseek_api.assistant.models_db.QueryTask.objects") as mock_qs:
            mock_qs.get.return_value = mock_task
            status, progress, result = _run(consumer._get_progress_snapshot())
        self.assertEqual(status, "pending")
        self.assertEqual(progress, [])

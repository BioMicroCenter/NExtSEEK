"""Unit tests for WebSocket origin validation in TaskProgressConsumer."""

from django.test import TestCase

from nextseek_api.assistant.consumers import TaskProgressConsumer


class WebSocketOriginCheckTest(TestCase):
    """Verify _is_allowed_origin correctly filters origins."""

    def test_vite_localhost_allowed(self):
        self.assertTrue(
            TaskProgressConsumer._is_allowed_origin("http://localhost:5173")
        )

    def test_server_origin_allowed(self):
        self.assertTrue(
            TaskProgressConsumer._is_allowed_origin("https://nextseek-dev.mit.edu")
        )

    def test_evil_origin_rejected(self):
        self.assertFalse(
            TaskProgressConsumer._is_allowed_origin("http://evil.com")
        )

    def test_none_origin_allowed(self):
        # No Origin header = same-origin or non-browser client
        self.assertTrue(
            TaskProgressConsumer._is_allowed_origin(None)
        )

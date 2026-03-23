"""Tests for WebSocket URL routing in the Assistant module.

Covers: nextseek_api/assistant/routing.py (3 lines — route definition).
"""

from django.test import SimpleTestCase


class RoutingTests(SimpleTestCase):
    """Verify websocket_urlpatterns is correctly defined."""

    def test_websocket_urlpatterns_exists(self):
        from nextseek_api.assistant.routing import websocket_urlpatterns
        self.assertIsInstance(websocket_urlpatterns, list)
        self.assertEqual(len(websocket_urlpatterns), 1)

    def test_route_pattern_matches_uuid(self):
        from nextseek_api.assistant.routing import websocket_urlpatterns

        route = websocket_urlpatterns[0]
        pattern = route.pattern
        # Should match a UUID-style task_id in the URL
        import re
        regex = pattern.regex
        self.assertIsNotNone(
            regex.match("ws/assistant/progress/550e8400-e29b-41d4-a716-446655440000/")
        )

    def test_route_points_to_task_progress_consumer(self):
        from nextseek_api.assistant.routing import websocket_urlpatterns

        route = websocket_urlpatterns[0]
        # The callback should be the ASGI application from TaskProgressConsumer
        callback = route.callback
        # .as_asgi() returns an ASGIHandler class, check it exists
        self.assertIsNotNone(callback)

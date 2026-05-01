"""WebSocket URL routing for the Assistant module."""

from django.urls import re_path

from nextseek_api.assistant.consumers import TaskProgressConsumer

websocket_urlpatterns = [
    re_path(
        r"ws/assistant/progress/(?P<task_id>[0-9a-f-]+)/$",
        TaskProgressConsumer.as_asgi(),
    ),
]

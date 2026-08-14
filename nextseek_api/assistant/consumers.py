"""WebSocket consumers for the Assistant module.

Provides real-time progress updates for async query tasks.
"""

from __future__ import annotations

import asyncio
import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class TaskProgressConsumer(AsyncWebsocketConsumer):
    """Push query-pipeline progress events over WebSocket.

    Connect to: ws://host/ws/assistant/progress/{task_id}/

    The consumer polls the QueryTask table every 300 ms and pushes
    new progress events to the client.  When the task reaches a
    terminal status (completed / error) it sends a final ``done``
    frame and closes the socket.

    Auth model (authenticated owner only):
        Both an authenticated user AND ownership of the task are
        required.  The task_id UUID is NOT a capability token: a
        connection holding a valid UUID but carrying no authenticated
        user is rejected.  This matches the HTTP endpoint serving the
        same data, GET /assistant/tasks/{task_id}/progress/, which
        checks auth and then loads the task filtered by request.user.

        Browser WebSocket API cannot send custom Authorization headers,
        so the authenticated user comes from the Django session cookie
        via channels' AuthMiddlewareStack (see dmac/asgi.py).  A client
        that authenticates only by Basic/Token header is anonymous here
        and must fall back to polling the HTTP progress endpoint, which
        the chat frontend already does when the socket fails to open.
    """

    POLL_INTERVAL = 0.3  # seconds

    @staticmethod
    def _is_allowed_origin(origin: str | None) -> bool:
        """Check if the WebSocket Origin header is allowed.

        Reads from settings.CORS_ALLOWED_ORIGINS plus the server's own
        CSRF_TRUSTED_ORIGINS.  None origin (no header) is allowed since
        that means same-origin or a non-browser client.
        """
        if origin is None:
            return True
        from django.conf import settings

        allowed = set(getattr(settings, "CORS_ALLOWED_ORIGINS", []))
        allowed.update(getattr(settings, "CSRF_TRUSTED_ORIGINS", []))
        return origin in allowed

    async def connect(self):
        self.task_id = self.scope["url_route"]["kwargs"]["task_id"]
        self._polling = False

        # Validate Origin header (defense-in-depth)
        origin = None
        for header_name, header_value in self.scope.get("headers", []):
            if header_name == b"origin":
                origin = header_value.decode("utf-8")
                break
        if not self._is_allowed_origin(origin):
            await self.close()
            return

        # Verify the caller is an authenticated user who owns this task.
        # None also covers "task does not exist" — the two are deliberately
        # indistinguishable to the client, as on the HTTP progress endpoint.
        task_info = await self._get_task_info()
        if task_info is None:
            await self.close()
            return

        await self.accept()
        self._polling = True
        asyncio.ensure_future(self._poll_loop())

    async def disconnect(self, close_code):
        self._polling = False

    # ------------------------------------------------------------------
    # Database helpers (run in thread via database_sync_to_async)
    # ------------------------------------------------------------------
    @database_sync_to_async
    def _get_task_info(self):
        """Return {"status", "user_id"}, or None if the caller may not have it.

        None means "no stream for you" and covers all three refusals:
        unauthenticated, task not found, and task owned by someone else.
        """
        from nextseek_api.assistant.models_db import QueryTask

        # Authentication is mandatory — holding the task_id is not enough.
        # "user" is missing from the scope when no auth middleware is installed
        # and is AnonymousUser when the connection carries no session; getattr
        # keeps a non-Django object in that slot from raising here.
        user = self.scope.get("user")
        if user is None or not getattr(user, "is_authenticated", False):
            return None

        try:
            task = QueryTask.objects.get(task_id=self.task_id)
        except QueryTask.DoesNotExist:
            return None

        # Ownership is mandatory too.
        if task.user_id != user.pk:
            return None

        return {"status": task.status, "user_id": task.user_id}

    @database_sync_to_async
    def _get_progress_snapshot(self):
        """Return (status, progress_list, result_or_none)."""
        from nextseek_api.assistant.models_db import QueryTask

        try:
            task = QueryTask.objects.get(task_id=self.task_id)
            return task.status, task.progress or [], task.result
        except QueryTask.DoesNotExist:
            return "error", [], None

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------
    async def _poll_loop(self):
        last_count = 0
        try:
            while self._polling:
                status, progress, result = await self._get_progress_snapshot()

                # Send any new events since last poll
                if len(progress) > last_count:
                    for event in progress[last_count:]:
                        await self.send(text_data=json.dumps(event))
                    last_count = len(progress)

                # Terminal state → send final frame and close
                if status in ("completed", "error"):
                    await self.send(text_data=json.dumps({
                        "event": "done",
                        "status": status,
                        "result": result,
                    }))
                    await self.close()
                    return

                await asyncio.sleep(self.POLL_INTERVAL)
        except asyncio.CancelledError:
            logger.debug("TaskProgressConsumer poll cancelled for %s", self.task_id)

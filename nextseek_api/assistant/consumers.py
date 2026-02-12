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

    Auth model (UUID-as-capability):
        Browser WebSocket API cannot send custom Authorization headers.
        Instead, the task_id UUID itself acts as a capability token:
        only the authenticated caller of POST /query/async/ receives
        the UUID, and UUIDv4 is cryptographically random. If a Django
        session cookie is present, ownership is still enforced.
    """

    POLL_INTERVAL = 0.3  # seconds

    async def connect(self):
        self.task_id = self.scope["url_route"]["kwargs"]["task_id"]
        self._polling = False

        # Verify task exists (UUID-as-capability; ownership checked
        # only when a session cookie provides an authenticated user)
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
        """Return (status, user_id) or None if task not found."""
        from nextseek_api.assistant.models_db import QueryTask

        try:
            task = QueryTask.objects.get(task_id=self.task_id)
        except QueryTask.DoesNotExist:
            return None

        # Check ownership if user is authenticated
        user = self.scope.get("user")
        if user and user.is_authenticated and task.user_id != user.pk:
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

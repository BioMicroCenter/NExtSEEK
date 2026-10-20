"""Mark the chat turns a restart of the app container killed.

A turn runs on a thread of the app process and its ``QueryTask`` turns
``completed`` or ``error`` only when that thread finishes. When the container
restarts mid-turn (an out-of-memory kill, a rebuild) the thread dies with it and
the task stays ``running`` for good: the chat panel waits on it, and the reingest
upload check counts it as a turn still in flight.

``docker/scripts/entrypoint.sh`` calls this after ``migrate`` and before it starts
any server, when no turn of this instance can be running, so every task still
``running`` then was cut off. One app container serves each instance's database.
"""
from __future__ import annotations

from nextseek_api.assistant.models_db import QueryTask

INTERRUPTED_MESSAGE = "interrupted: the server restarted during this turn"


def fail_interrupted_turns() -> int:
    """Mark every ``running`` task as ``error``; return how many there were."""
    return QueryTask.objects.filter(status="running").update(
        status="error", result={"error": INTERRUPTED_MESSAGE, "agent": None})

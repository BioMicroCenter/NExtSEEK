"""The HTTP side of the chat-turn drain: the 503 a turn-start view answers while its container drains.

The markers, the thread starter and the flag are engine code in ``NessieAI/turn_drain.py``; they are re-exported here
for the API callers.
"""
from __future__ import annotations

from rest_framework import status

from NessieAI.turn_drain import (  # noqa: F401  (re-exported for the API callers)
    CUT_MESSAGE,
    DRAINING_DETAIL,
    DRAINING_FLAG,
    draining,
    start_turn_thread,
    turn_dir,
)


def draining_response():
    """The 503 a turn-start view answers while draining, in the NExtSEEK error envelope."""
    from nextseek_api.authentication import _error_response

    response = _error_response("Service restarting", DRAINING_DETAIL, status.HTTP_503_SERVICE_UNAVAILABLE)
    response["Retry-After"] = "60"
    return response

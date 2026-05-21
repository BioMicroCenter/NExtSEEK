"""Read-only MySQL fetcher for assistant_chat_session.extra_state.chat_log.

Used by the trio assertion to compare the latest assistant_reply persisted
to the DB against what was rendered in the UI and written to console.txt.
"""
from __future__ import annotations

import json
from typing import Any


def fetch_chat_session_row(config: Any, session_id: str, *, env: str = "dev") -> list[dict]:
    """Return the chat_log list from assistant_chat_session.extra_state.

    Returns [] when the session row is missing, extra_state has no chat_log key,
    or the DB connection cannot be established. Never raises on missing data.

    Args:
        config: ChatConfig-like object exposing _connect_db(env=...).
        session_id: 32-char UUID (hyphens-stripped) from the WebSocket / POST response.
        env: 'dev' (default) or 'prod' — matches the docker container's DB target.
    """
    conn = config._connect_db(env=env)
    if conn is None:
        return []

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT extra_state FROM assistant_chat_session WHERE session_id = %s",
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            return []

        raw = row[0]
        if isinstance(raw, dict):
            extra_state = raw
        elif isinstance(raw, (str, bytes)):
            extra_state = json.loads(raw)
        else:
            return []

        return list(extra_state.get("chat_log") or [])
    finally:
        try:
            conn.close()
        except Exception:
            pass

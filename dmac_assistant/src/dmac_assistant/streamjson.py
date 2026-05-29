"""Claude Code stream-json parsing for the DMAC bridge."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StreamJsonErrorDetail(BaseModel):
    """Details about one malformed stream-json line."""

    model_config = ConfigDict(frozen=True)

    line: bytes
    reason: str


class StreamEvent(BaseModel):
    """One parser result item."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["event", "error"]
    payload: dict | None = None
    error: StreamJsonErrorDetail | None = None


class StreamJsonParser:
    """Incremental newline-delimited JSON parser for Claude stream-json output."""

    __slots__ = ("session_id", "_buffer")

    def __init__(self) -> None:
        self.session_id: str | None = None
        self._buffer = b""

    def __repr__(self) -> str:
        return f"StreamJsonParser(session_id={self.session_id!r}, buffered_bytes=<hidden>)"

    def feed(self, chunk: bytes) -> list[StreamEvent]:
        """Consume a chunk of bytes and return every complete parsed line eagerly."""
        if not isinstance(chunk, bytes):
            raise TypeError("chunk must be bytes")
        if not chunk:
            return []

        self._buffer += chunk
        return self._consume_complete_lines()

    def flush(self) -> list[StreamEvent]:
        """Return any remaining buffered output, surfacing a trailing partial as an error."""
        events = self._consume_complete_lines()
        if not self._buffer:
            return events

        trailing = self._buffer
        self._buffer = b""
        if trailing.strip():
            events.append(
                StreamEvent(
                    kind="error",
                    error=StreamJsonErrorDetail(
                        line=trailing,
                        reason="unterminated trailing stream-json line",
                    ),
                )
            )
        return events

    def _consume_complete_lines(self) -> list[StreamEvent]:
        if b"\n" not in self._buffer:
            return []

        lines = self._buffer.split(b"\n")
        self._buffer = lines.pop()
        events: list[StreamEvent] = []
        for raw_line in lines:
            if not raw_line.strip():
                continue
            events.append(self._line_to_event(raw_line))
        return events

    def _line_to_event(self, raw_line: bytes) -> StreamEvent:
        try:
            payload = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return StreamEvent(
                kind="error",
                error=StreamJsonErrorDetail(line=raw_line, reason=str(exc)),
            )

        if not isinstance(payload, dict):
            return StreamEvent(
                kind="error",
                error=StreamJsonErrorDetail(
                    line=raw_line,
                    reason="stream-json line must decode to an object",
                ),
            )

        if payload.get("type") == "system" and payload.get("subtype") == "init":
            session_id = payload.get("session_id")
            if isinstance(session_id, str):
                self.session_id = session_id

        return StreamEvent(kind="event", payload=payload)

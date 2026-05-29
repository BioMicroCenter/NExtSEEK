"""Docker attach-socket reader for the Container-CC engine.

``BridgeAttachSocket`` is copied verbatim (with attribution) from
``dmac_assistant/src/dmac_assistant/containers.py``. We copy rather than import
it because dmac's ``containers`` module imports ``dmac_assistant.auth`` (FastAPI),
which the vendored package intentionally does not depend on. This class is
self-contained (only ``struct`` + ``logging``) and is the proven, edge-case-hardened
demuxer for Docker's stdcopy 8-byte-header framing plus stdin relay — exactly
the part that is easy to get wrong, so we reuse it as-is.

Upstream source: tavjo/dmac-assistant containers.py::BridgeAttachSocket.
"""
from __future__ import annotations

import logging
import struct
from typing import Any, BinaryIO

log = logging.getLogger(__name__)


class BridgeAttachSocket:
    """Thin wrapper over docker-py's hijacked attach socket.

    Demuxes Docker's stdcopy 8-byte-header framing so callers read plain
    stdout/stderr payload bytes, and exposes stdin-side helpers for relaying
    the user message. When ``stdout_stream`` is provided (a
    ``container.logs(stream=True, follow=True, stdout=True, stderr=False)``
    generator), reads come from that already-separated stdout stream and the
    stdcopy header parsing is bypassed.
    """

    def __init__(
        self,
        raw_socket: Any,
        *,
        stdout_stream: Any | None = None,
        stderr_sink: BinaryIO | None = None,
    ) -> None:
        self._raw = raw_socket
        self._stdout_stream = stdout_stream
        self._line_buffer: bytearray = bytearray()
        self.stderr_sink: BinaryIO | None = stderr_sink

    # ------------------------------------------------------------------ read
    def read_frame(self) -> tuple[str, bytes] | None:
        """Read one Docker stdcopy frame; ("stdout"|"stderr", payload) or None on EOF."""
        if self._stdout_stream is not None:
            return self._read_log_chunk()
        header = self._recv_exact(8)
        if header is None:
            return None
        stream_id = header[0]
        size = struct.unpack(">I", header[4:8])[0]
        payload = b"" if size == 0 else (self._recv_exact(size) or b"")
        stream_name = "stdout" if stream_id == 1 else "stderr"
        return stream_name, payload

    # ----------------------------------------------------------------- write
    def send_stdin(self, data: bytes) -> None:
        self._transport().sendall(data)

    def close_stdin(self) -> None:
        self._transport().shutdown(1)  # SHUT_WR

    def close(self) -> None:
        if self._stdout_stream is not None and hasattr(self._stdout_stream, "close"):
            self._stdout_stream.close()
        self._raw.close()

    # ---------------------------------------------------------------- helpers
    def _read_log_chunk(self) -> tuple[str, bytes] | None:
        try:
            payload = next(self._stdout_stream)
        except StopIteration:
            return None
        if not isinstance(payload, bytes):
            payload = bytes(payload)
        return "stdout", payload

    def _recv_exact(self, size: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < size:
            chunk = self._read_chunk(size - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _read_chunk(self, size: int) -> bytes:
        if hasattr(self._raw, "read"):
            return self._raw.read(size)
        return self._transport().recv(size)

    def _transport(self) -> Any:
        return getattr(self._raw, "_sock", self._raw)

    def read_event_line(self) -> str | None:
        """Read one UTF-8 line of stdout, demuxing frames internally.

        Returns None only on EOF with an empty buffer. stderr frames are logged
        (truncated) but not returned as lines.
        """
        while True:
            newline_idx = self._line_buffer.find(b"\n")
            if newline_idx >= 0:
                line_bytes = bytes(self._line_buffer[:newline_idx])
                del self._line_buffer[: newline_idx + 1]
                return line_bytes.decode("utf-8", errors="replace")

            frame = self.read_frame()
            if frame is None:
                if self._line_buffer:
                    residual = bytes(self._line_buffer)
                    self._line_buffer.clear()
                    return residual.decode("utf-8", errors="replace")
                return None

            stream_name, payload = frame
            if stream_name == "stderr":
                if self.stderr_sink is not None:
                    try:
                        self.stderr_sink.write(bytes(payload))
                        self.stderr_sink.flush()
                    except (OSError, ValueError):
                        log.warning("CC stderr sink write failed; dropping payload")
                log.info("CC container stderr (truncated 512B): %r", bytes(payload[:512]))
                continue

            if not payload:
                continue

            self._line_buffer.extend(payload)

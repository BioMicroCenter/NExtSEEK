"""Hermetic tests for Docker stdcopy attach demux (package-attributed)."""
from __future__ import annotations

import io
import struct

import pytest

from nextseek_api.cc_assistant.attach import BridgeAttachSocket


class _Raw:
    def __init__(self, chunks: list[bytes]):
        self._buf = bytearray(b"".join(chunks))
        self.sent = []
        self.shutdowns = []
        self.closed = False

    def read(self, size: int) -> bytes:
        if not self._buf:
            return b""
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def shutdown(self, how: int) -> None:
        self.shutdowns.append(how)

    def close(self) -> None:
        self.closed = True


def _frame(stream_id: int, payload: bytes) -> bytes:
    return bytes([stream_id, 0, 0, 0]) + struct.pack(">I", len(payload)) + payload


def test_read_frame_stdout_and_stderr_and_eof():
    raw = _Raw([_frame(1, b"hello"), _frame(2, b"err"), b""])
    sock = BridgeAttachSocket(raw)
    assert sock.read_frame() == ("stdout", b"hello")
    assert sock.read_frame() == ("stderr", b"err")
    # truncated header -> EOF
    assert sock.read_frame() is None


def test_read_frame_zero_size_payload():
    raw = _Raw([_frame(1, b"")])
    sock = BridgeAttachSocket(raw)
    assert sock.read_frame() == ("stdout", b"")


def test_stdout_stream_bypasses_stdcopy_and_stopiteration():
    def gen():
        yield b"chunk"
        yield bytearray(b"more")

    sock = BridgeAttachSocket(_Raw([]), stdout_stream=gen())
    assert sock.read_frame() == ("stdout", b"chunk")
    assert sock.read_frame() == ("stdout", b"more")
    assert sock.read_frame() is None


def test_send_close_and_transport_fallback():
    class SockOnly:
        def __init__(self):
            self.sent = []
            self.shutdowns = []
            self.closed = False

        def recv(self, size: int) -> bytes:
            return b""

        def sendall(self, data: bytes) -> None:
            self.sent.append(data)

        def shutdown(self, how: int) -> None:
            self.shutdowns.append(how)

        def close(self) -> None:
            self.closed = True

    inner = SockOnly()

    class Wrapper:
        def __init__(self, sock):
            self._sock = sock

        def close(self) -> None:
            self._sock.close()

    wrap = Wrapper(inner)
    sock = BridgeAttachSocket(wrap)
    sock.send_stdin(b"hi")
    sock.close_stdin()
    sock.close()
    assert inner.sent == [b"hi"]
    assert inner.shutdowns == [1]
    assert inner.closed is True


def test_read_event_line_demuxes_stderr_to_sink_and_logs(tmp_path, caplog):
    sink = io.BytesIO()

    class BoomSink:
        def write(self, data: bytes) -> None:
            raise OSError("full")

        def flush(self) -> None:
            pass

    raw = _Raw([
        _frame(2, b"stderr-bytes"),
        _frame(1, b"line-one\npartial"),
        _frame(1, b"-two\n"),
    ])
    sock = BridgeAttachSocket(raw, stderr_sink=sink)
    with caplog.at_level("INFO"):
        assert sock.read_event_line() == "line-one"
        assert sock.read_event_line() == "partial-two"
    assert sink.getvalue() == b"stderr-bytes"

    raw2 = _Raw([_frame(2, b"x"), _frame(1, b"rest-no-nl")])
    sock2 = BridgeAttachSocket(raw2, stderr_sink=BoomSink())
    assert sock2.read_event_line() == "rest-no-nl"
    assert sock2.read_event_line() is None


def test_read_event_line_skips_empty_stdout_payload():
    raw = _Raw([_frame(1, b""), _frame(1, b"ok\n")])
    sock = BridgeAttachSocket(raw)
    assert sock.read_event_line() == "ok"


def test_close_closes_stdout_stream():
    class Stream:
        def __init__(self):
            self.closed = False

        def close(self) -> None:
            self.closed = True

        def __next__(self):
            raise StopIteration

    stream = Stream()
    raw = _Raw([])
    sock = BridgeAttachSocket(raw, stdout_stream=stream)
    sock.close()
    assert stream.closed is True
    assert raw.closed is True

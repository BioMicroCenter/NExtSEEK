"""Step 3 — durable full-transcript store (zstd).

The complete Claude Code session ``.jsonl`` is compressed with zstd and held in
the ``CCSessionTranscript`` table so a turn's transcript is recoverable even if
the on-disk ``cc-state`` dir is wiped (SPEC-3 §7, decision E7). Pure byte I/O;
no Django import so it stays hermetically testable.
"""
from __future__ import annotations

import zstandard


class TranscriptTooLarge(Exception):
    """Decompressed output would exceed the configured bound (bomb guard)."""


def compress(jsonl: bytes, *, level: int = 10) -> bytes:
    """zstd-compress raw jsonl bytes. ``level`` 10 is a fast/good default."""
    return zstandard.ZstdCompressor(level=level).compress(jsonl)


def decompress(blob: bytes, *, max_bytes: int = 256 * 1024 * 1024) -> bytes:
    """Inverse of ``compress``. Streams with a hard output cap so a corrupted or
    hostile row cannot exhaust memory (SPEC-3 §10)."""
    dctx = zstandard.ZstdDecompressor()
    out = bytearray()
    with dctx.stream_reader(blob) as reader:
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            out.extend(chunk)
            if len(out) > max_bytes:
                raise TranscriptTooLarge(f"transcript exceeds {max_bytes} bytes")
    return bytes(out)

"""Hermetic tests for the zstd transcript store. No DB, no network."""
import pytest

from nextseek_api.cc_assistant.cc_transcript_store import (
    compress, decompress, TranscriptTooLarge,
)


def test_round_trip_is_byte_identical():
    raw = b'{"type":"user"}\n{"type":"assistant"}\n' * 1000
    blob = compress(raw)
    assert isinstance(blob, bytes)
    assert decompress(blob) == raw


def test_compression_actually_shrinks_repetitive_jsonl():
    raw = b'{"type":"assistant","message":{"role":"assistant"}}\n' * 5000
    assert len(compress(raw)) < len(raw)


def test_empty_round_trips():
    assert decompress(compress(b"")) == b""


def test_decompress_bomb_is_bounded():
    raw = b"A" * (2 * 1024 * 1024)          # compresses tiny, expands large
    blob = compress(raw)
    with pytest.raises(TranscriptTooLarge):
        decompress(blob, max_bytes=1024)    # cap below the real size -> reject

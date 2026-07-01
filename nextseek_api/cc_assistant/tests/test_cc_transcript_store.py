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


def test_ccsessiontranscript_model_shape():
    """Field set + db_table guard — does not touch the DB (no migrate/connect)."""
    import django
    from django.conf import settings
    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=[], DATABASES={}, USE_TZ=True,
        )
        django.setup()
    from nextseek_api.assistant.models_db import CCSessionTranscript
    names = {f.name for f in CCSessionTranscript._meta.get_fields()}
    assert {"chat_session", "cc_session_id", "turn_id", "blob",
            "uncompressed_size", "created_at"} <= names
    assert CCSessionTranscript._meta.db_table == "assistant_cc_transcript"

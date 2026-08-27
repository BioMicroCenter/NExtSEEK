"""Step 3 — durable full-transcript store (zstd).

The complete Claude Code session ``.jsonl`` is compressed with zstd and held in
the ``CCSessionTranscript`` table so a turn's transcript is recoverable even if
the on-disk ``cc-state`` dir is wiped (SPEC-3 §7, decision E7).

``compress``/``decompress`` are pure byte I/O. ``store_transcript`` is the one
function here that touches the DB, and it lives in this module because this
module's stated job *is* that table: the compression policy (level, bomb cap)
and the row it produces belong together, so an engine caller never has to know
zstd. Its ``CCSessionTranscript`` import is deliberately **lazy and
function-local** — no Django import happens at module import time, so this
module stays hermetically importable (and testable) with Django unconfigured.
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


def store_transcript(*, chat_session, cc_session_id: str, turn_id: str,
                     raw_jsonl: bytes) -> None:
    """Upsert one ``CCSessionTranscript`` row for a single CC turn.

    Keyed on ``(chat_session, cc_session_id, turn_id)`` — the table's
    ``unique_together`` — so re-persisting the same turn overwrites rather than
    duplicating. Same row shape as the success-path upsert in
    ``nextseek_api/services/cc_assistant.py`` (``_append_cc_turn_complete``),
    which still writes its own copy; the duplication is known and accepted.

    Called for turns that **FAILED** as well as turns that succeeded (#68): a
    ``query_error``, a watchdog timeout, or an exception out of ``run_cc_turn``
    each used to leave no durable record at all, which made exactly the turns
    worth triaging the ones with nothing to triage.

    ``raw_jsonl`` is stored as given, minus zstd. **The caller is responsible
    for having scrubbed the bytes first** (#72) — this function does no
    redaction, so anything secret still in ``raw_jsonl`` lands in the DB.

    Args are keyword-only. Returns ``None``; exceptions from the ORM propagate,
    so a caller on an error path must decide for itself whether to swallow them.
    """
    # Lazy, function-local on purpose: importing Django models at module scope
    # would break this module's hermetic import (see the module docstring).
    from nextseek_api.assistant.models_db import CCSessionTranscript

    CCSessionTranscript.objects.update_or_create(
        chat_session=chat_session,
        cc_session_id=cc_session_id or "",
        turn_id=turn_id,
        defaults={
            "blob": compress(raw_jsonl),
            "uncompressed_size": len(raw_jsonl),
        },
    )

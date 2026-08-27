"""Hermetic tests for the zstd transcript store. No DB, no network."""
import ast
import inspect
from pathlib import Path

import pytest

from nextseek_api.cc_assistant import cc_transcript_store
from nextseek_api.cc_assistant.cc_transcript_store import (
    compress, decompress, store_transcript, TranscriptTooLarge,
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


_CONTAINER_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")


def _module_level_imported_modules(tree: ast.Module) -> set[str]:
    """Modules imported at module scope, i.e. NOT inside any def/class.

    Recurses through ``if``/``try``/``with``/``match`` so a *guarded* top-level
    import still counts as top-level, but stops at every function and class
    boundary. ``cases`` is in ``_CONTAINER_FIELDS`` for that last one: without
    it, a module-scope ``match``/``case`` holding an import would be invisible
    to this walk and would evade the guard entirely.
    """
    found: set[str] = set()
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue                      # anything below here is lazy, not top-level
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
        for field in _CONTAINER_FIELDS:
            for child in getattr(node, field, None) or []:
                if isinstance(child, ast.AST):
                    stack.append(child)
    return found


def test_store_transcript_signature_is_keyword_only():
    """Task 4 calls this by keyword from run_cc_turn's finally — pin the shape."""
    sig = inspect.signature(store_transcript)
    assert list(sig.parameters) == [
        "chat_session", "cc_session_id", "turn_id", "raw_jsonl",
    ]
    for name, param in sig.parameters.items():
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert param.default is inspect.Parameter.empty, name
    # `from __future__ import annotations` stringifies these.
    assert sig.parameters["cc_session_id"].annotation in ("str", str)
    assert sig.parameters["turn_id"].annotation in ("str", str)
    assert sig.parameters["raw_jsonl"].annotation in ("bytes", bytes)
    assert sig.return_annotation in ("None", None, type(None))


def test_store_transcript_rejects_positional_args():
    """Binding fails before any ORM/DB touch, so this stays hermetic."""
    with pytest.raises(TypeError):
        store_transcript(object(), "sess", "turn", b"")


def test_django_import_stays_lazy():
    """Structural guard: no models_db / django import at module scope.

    THE TWO ASSERTIONS BELOW ARE THE GUARANTEE. Nothing about *running* this
    file proves the module is importable with Django unconfigured, because the
    mandated lane never presents that state: `pytest-django` reads
    `DJANGO_SETTINGS_MODULE=dmac.test_settings` and configures settings at
    plugin init, before collection. So the `if not settings.configured` fallback
    in `test_ccsessiontranscript_model_shape` is defensive scaffolding for a
    bare-pytest run, not evidence — under the real lane that branch is never
    taken.

    The AST check is what actually holds the line: it reads the source, so it
    fails on a module-level `from nextseek_api.assistant.models_db import ...`
    (or any `django.*` import) whether or not the environment running it happens
    to have settings configured.
    """
    tree = ast.parse(Path(cc_transcript_store.__file__).read_text())
    top_level = _module_level_imported_modules(tree)
    assert not [m for m in top_level if "models_db" in m], top_level
    assert not [m for m in top_level if m.split(".")[0] == "django"], top_level


def test_store_transcript_body_does_import_models_db():
    """The other half of the guard: lazy, but actually there."""
    tree = ast.parse(Path(cc_transcript_store.__file__).read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "store_transcript")
    imported = {n.module for n in ast.walk(fn) if isinstance(n, ast.ImportFrom)}
    assert "nextseek_api.assistant.models_db" in imported


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

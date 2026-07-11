"""Text extraction wrapper for batch-upload source files.

MarkItDown is the default extractor. Pure-Python fallbacks are intentionally
curator-selected only; automatic fallback substitution can hide parse failures
and produce a misleading batch payload.
"""
from __future__ import annotations

import pathlib
import sys
from collections.abc import Callable
from typing import Any

_FALLBACKS: dict[str, tuple[str, str, str]] = {
    "pdfplumber": ("pdfplumber", "PDF", "pdf"),
    "python-docx": ("docx", "DOCX", "docx"),
}


def extract_text(path: str, *, fallback: str | None = None) -> str:
    if fallback is not None:
        return _run_explicit_fallback(path, fallback)

    try:
        from markitdown import MarkItDown
    except ImportError:
        _escalate("markitdown_import_error", path)

    try:
        result = MarkItDown().convert(path)
    except Exception as exc:  # noqa: BLE001 - failure is escalated, not hidden.
        _escalate(f"markitdown_failed:{type(exc).__name__}", path)

    text = str(getattr(result, "text_content", "") or "")
    if not text.strip():
        _escalate("markitdown_empty_text", path)
    return text


def _escalate(reason: str, path: str) -> None:
    fallback = _suggested_fallback(path)
    sys.stderr.write(
        "nextseek-error: EXTRACT_FALLBACK_REQUIRED — "
        f"{reason}; ask the curator before using named fallback {fallback}\n"
    )
    raise SystemExit(2)


def _suggested_fallback(path: str) -> str:
    suffix = pathlib.Path(path).suffix.lower()
    if suffix == ".pdf":
        return "pdfplumber"
    if suffix == ".docx":
        return "python-docx"
    return "pdfplumber/python-docx"


def _run_explicit_fallback(path: str, fallback: str) -> str:
    if fallback not in _FALLBACKS:
        raise ValueError(f"unknown fallback: {fallback}")
    module_name, label, kind = _FALLBACKS[fallback]
    try:
        module = __import__(module_name)
    except ImportError:
        sys.stderr.write(
            f"nextseek-error: CONFIG_MISSING — explicit {fallback} fallback not installed\n"
        )
        raise SystemExit(2)
    text = _fallback_reader(kind, module)(path)
    if not text.strip():
        sys.stderr.write(
            f"nextseek-error: EXTRACT_EMPTY — explicit {label} fallback returned no text\n"
        )
        raise SystemExit(2)
    return text


def _fallback_reader(kind: str, module: Any) -> Callable[[str], str]:
    if kind == "pdf":
        return lambda path: "\n".join(
            page.extract_text() or "" for page in module.open(path).pages
        )
    if kind == "docx":
        return lambda path: "\n".join(
            paragraph.text for paragraph in module.Document(path).paragraphs
        )
    raise ValueError(f"unsupported fallback kind: {kind}")

"""Where NS report artifacts are written, and the containment check that serves them.

``_granular_outputs_dir`` makes a fresh run-root for an op that writes files;
``_safe_artifact_path`` resolves a stored path and serves it only when it is really
inside ``<BASE_DIR>/outputs`` or ``NEXTSEEK_OUTPUTS_DIR`` (``_artifact_roots``);
``_resolve_saved_path`` picks the one path to serve from a ``saved_files`` value.
The granular-op and artifact-download endpoints in
``nextseek_api/services/assistant.py`` call them.

Moved verbatim from ``nextseek_api/services/assistant.py`` (Phase B of the
NessieAI consolidation), which imports them back. Nothing here imports
``nextseek_api``.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from django.conf import settings


def _granular_outputs_dir() -> str:
    """A fresh writable run-root for a report op's saved_files."""
    base = getattr(settings, "BASE_DIR", None)
    root = os.path.join(str(base), "outputs", "granular") if base else "outputs/granular"
    out = os.path.join(root, uuid.uuid4().hex)
    os.makedirs(out, exist_ok=True)
    return out


def _discard_empty_dir(path: str | None) -> None:
    """Remove a run-root an op was refused before writing to. rmdir removes only an empty folder, so a partly
    written op keeps its files for the log."""
    if path:
        try:
            os.rmdir(path)
        except OSError:
            pass


def _resolve_saved_path(value):
    """A saved_files value is either a string path or a list of paths (multi-file
    keys like geo_seq_workbooks / sra_*). Serve the first concrete path."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        return first if isinstance(first, str) else None
    return None


def _artifact_roots() -> list[Path]:
    """The narrowly-scoped directories report artifacts are written under. Both the
    granular report op (``_granular_outputs_dir``) and the query pipeline
    (``_ensure_query_log_dir``) write beneath ``<BASE_DIR>/outputs`` /
    ``NEXTSEEK_OUTPUTS_DIR``. The whole BASE_DIR (contains source) and home (holds
    secrets) are deliberately excluded."""
    roots: list[Path] = []
    base = getattr(settings, "BASE_DIR", None)
    if base:
        roots.append(Path(base, "outputs").resolve())
    nod = os.environ.get("NEXTSEEK_OUTPUTS_DIR")
    if nod:
        try:
            roots.append(Path(nod).resolve())
        except (OSError, ValueError, RuntimeError):
            pass
    return roots


def _safe_artifact_path(src) -> Path | None:
    """Resolve ``src`` and require it to be *really contained* within an allowed
    artifact root. Uses ``Path.relative_to`` (no string-prefix bypass like
    ``/app-evil`` matching ``/app``) and ``Path.resolve`` (canonicalizes symlinks,
    so a symlink that escapes the root is rejected). Returns the resolved Path when
    safe, else None."""
    if not isinstance(src, str) or not src:
        return None
    try:
        filepath = Path(src).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    for root in _artifact_roots():
        try:
            filepath.relative_to(root)
            return filepath
        except ValueError:
            continue
    return None

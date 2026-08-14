"""Safe path resolution under a repository root."""
from __future__ import annotations

import os
from pathlib import Path


class PathEscapeError(ValueError):
    """Raised when a path escapes the repository root or follows an unsafe symlink."""


def resolve_under_root(repo_root: Path, relative: str | Path) -> Path:
    """Resolve a repository-relative path, rejecting traversal and unsafe symlinks."""
    root = repo_root.resolve()
    rel = Path(relative)
    if rel.is_absolute():
        raise PathEscapeError(f"absolute path not allowed under {root}: {relative}")
    if ".." in rel.parts:
        raise PathEscapeError(f"path traversal rejected: {relative}")

    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PathEscapeError(f"path escapes repository root: {relative}") from exc

    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            link_target = current.resolve()
            try:
                link_target.relative_to(root)
            except ValueError as exc:
                raise PathEscapeError(
                    f"symlink target escapes repository root: {current}"
                ) from exc

    if not candidate.exists() and not candidate.parent.exists():
        candidate.parent.mkdir(parents=True, exist_ok=True)

    return candidate

"""SHA-256 content hashing and stored-hash I/O."""
from __future__ import annotations

import hashlib
from pathlib import Path


def compute_content_hash(text: str) -> str:
    """Return the hex-encoded SHA-256 digest of text encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_stored_hash(path: Path) -> str | None:
    """Read and strip the stored hash file, or return None if missing."""
    if not path.exists():
        return None
    return path.read_text().strip()


def write_stored_hash(path: Path, digest: str) -> None:
    """Write digest plus trailing newline to path, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(digest + "\n")

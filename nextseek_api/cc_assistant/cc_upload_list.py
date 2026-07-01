"""Step 3 — CC upload list helper (SPEC-3 §4)."""
from __future__ import annotations

from pathlib import Path


def list_input_files(input_mnt: str) -> list[str]:
    """Return sorted basenames of regular files directly under input_mnt."""
    root = Path(input_mnt)
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )

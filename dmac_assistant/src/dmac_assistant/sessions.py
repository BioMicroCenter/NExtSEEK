"""Claude session discovery helpers for the DMAC bridge."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class SessionRecord:
    """One discovered Claude session file."""

    session_id: str
    cwd: str
    encoded_cwd: str
    path: Path
    mtime: float


def encode_cwd(cwd: str) -> str:
    """Encode a cwd the same way Claude stores it under ``.claude/projects``."""
    return "".join(char if char.isalnum() else "-" for char in cwd)


def session_dir(claude_root: Path, cwd: str) -> Path:
    """Return the directory containing session files for ``cwd``."""
    return claude_root / "projects" / encode_cwd(cwd)


def list_sessions(claude_root: Path, cwd: str) -> list[SessionRecord]:
    """Discover valid session files newest-first for the given Claude root."""
    target_dir = session_dir(claude_root, cwd)
    if not target_dir.exists():
        return []

    encoded = encode_cwd(cwd)
    records: list[SessionRecord] = []
    for candidate in target_dir.glob("*.jsonl"):
        try:
            UUID(candidate.stem)
        except ValueError:
            continue

        try:
            stat_result = candidate.stat()
        except OSError:
            continue

        records.append(
            SessionRecord(
                session_id=candidate.stem,
                cwd=cwd,
                encoded_cwd=encoded,
                path=candidate.resolve(),
                mtime=stat_result.st_mtime,
            )
        )

    return sorted(records, key=lambda record: (-record.mtime, record.session_id))


def most_recent_session(claude_root: Path, cwd: str) -> SessionRecord | None:
    """Return the newest known session for ``cwd`` or ``None`` when absent."""
    sessions = list_sessions(claude_root, cwd)
    return sessions[0] if sessions else None

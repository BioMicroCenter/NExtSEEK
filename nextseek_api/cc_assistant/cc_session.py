"""Django-free helpers for Container-CC multi-turn resume (Step 1b).

Kept import-light (pathlib + typing only) so the hermetic test suite can import
it without a configured Django. The only Django touch — saving the resume id —
lives in the service layer's ``on_session_id`` callback, injected here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

SendEvent = Callable[[str, dict[str, Any]], None]


def resume_id_from_state(extra_state: Mapping[str, Any] | None) -> str | None:
    """Return the stored claude resume id (``cc_session_id``) from a
    ChatSession's ``extra_state``, or ``None`` if absent/blank/wrong-type."""
    if not isinstance(extra_state, Mapping):
        return None
    sid = extra_state.get("cc_session_id")
    return sid if isinstance(sid, str) and sid else None


def make_session_sniffer(
    inner: SendEvent, on_session_id: Callable[[str], None]
) -> SendEvent:
    """Wrap a ``send_event`` callback: whenever an emitted event's data carries a
    truthy ``cc_session_id`` that differs from the last seen, invoke
    ``on_session_id`` with it (last-wins, deduped) BEFORE forwarding to ``inner``.

    All persistence side effects live in ``on_session_id``; this wrapper stays
    pure and Django-free.
    """
    last: dict[str, str | None] = {"id": None}

    def _wrapped(event: str, data: dict[str, Any]) -> None:
        sid = data.get("cc_session_id") if isinstance(data, dict) else None
        if isinstance(sid, str) and sid and sid != last["id"]:
            last["id"] = sid
            on_session_id(sid)
        inner(event, data)

    return _wrapped


def store_has_transcripts(store_dir: Path | str) -> bool:
    """True if the per-session ``.claude`` store already holds at least one
    transcript (``projects/**/*.jsonl``). Used to skip ``--resume`` when the
    store is empty (turn 1 or a wiped store) so claude never tries to resume a
    session whose transcript is gone.
    """
    projects = Path(store_dir) / "projects"
    if not projects.is_dir():
        return False
    return any(projects.rglob("*.jsonl"))

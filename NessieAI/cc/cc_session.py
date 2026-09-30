"""Django-free helpers for Container-CC multi-turn resume (Step 1b).

Kept import-light (pathlib, typing and the stdlib-only ``safe_fs``) so the hermetic test suite can import
it without a configured Django. The only Django touch — saving the resume id —
lives in the service layer's ``on_session_id`` callback, injected here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from NessieAI.cc import safe_fs

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


def _store_dirname() -> str:
    # The one store-name constant lives in cc_engine (which imports this module), so it is read lazily.
    from NessieAI.cc.cc_engine import _TRANSCRIPT_STORE_DIRNAME
    return _TRANSCRIPT_STORE_DIRNAME


def store_has_transcripts(store_dir: Path | str) -> bool:
    """True if the per-session ``.claude`` store already holds at least one
    transcript (``projects/**/*.jsonl``). Used to skip ``--resume`` when the
    store is empty (turn 1 or a wiped store) so claude never tries to resume a
    session whose transcript is gone. The store is the agent's folder, so it is
    listed without following a link: a ``projects`` that is a link, or not a
    folder, reads as no store and the turn starts fresh.
    """
    # The session folder is the trusted root; projects/ is the agent's and is walked in rel_parts.
    files = safe_fs.iter_files(Path(store_dir), (_store_dirname(),), suffix=".jsonl")
    try:
        return next(files, None) is not None
    except OSError:
        return False
    finally:
        files.close()


def split_store_path(transcript_path: str | Path) -> tuple[Path, str] | None:
    """``(session folder, path below it)`` for a transcript in a session store, else None.

    The session folder (``cc-state/<session>``, the agent's mount root) is the trusted root ``safe_fs`` reads
    from, and the path below it, which starts with ``projects/``, is walked without following a link. The
    session folder is the registered agent root that holds the path when this process registered one
    (``build_user_dirs`` registers every session folder it builds, and every transcript path Django records
    comes from there), else the parent of the OUTERMOST ``projects`` folder on the path: an agent can make
    folders named ``projects`` only below its own store, never above it (the rule
    ``cc_engine.transcript_is_verified_scrubbed`` uses). Raises ``safe_fs.UnsafePath`` for a path that is not
    absolute or holds an empty, ``.`` or ``..`` step.
    """
    name = _store_dirname()
    path = Path(transcript_path)
    registered = safe_fs.agent_root_of(path)
    if registered is not None:
        rel = path.relative_to(registered).as_posix()
        return (registered, rel) if rel.startswith(f"{name}/") else None
    stores = [parent for parent in path.parents if parent.name == name]
    if not stores:
        return None
    root = stores[-1].parent
    return root, path.relative_to(root).as_posix()


def read_store_transcript(transcript_path: str | Path) -> bytes:
    """The bytes of a session transcript, read from its store down without following a link.

    Raises ``OSError``: ``safe_fs.UnsafePath`` for a link or a non-regular file on the way, or for a path that
    is under no store; ``FileNotFoundError`` when it is gone.
    """
    split = split_store_path(transcript_path)
    if split is None:
        raise safe_fs.UnsafePath(f"not under a transcript store: {transcript_path}")
    root, rel = split
    return safe_fs.read_file(root, rel)

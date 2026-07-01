from pathlib import Path

from nextseek_api.cc_assistant.cc_engine import _safe_relpath


def resolve_artifact_path(artifacts_root: str, key: str) -> Path:
    if not key or not _safe_relpath(key):
        raise ValueError("bad key")
    root = Path(artifacts_root).resolve()
    target = (root / key).resolve()
    if not target.is_relative_to(root):
        raise ValueError("traversal")
    return target


def session_owned_by_user(user_id: int, session_id: str) -> bool:
    from nextseek_api.assistant.models_db import ChatSession
    return ChatSession.objects.filter(user_id=user_id, session_id=session_id).exists()

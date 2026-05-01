"""Adapts Django ChatSession to the dict-like interface chat_nextseek agents expect."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models_db import ChatSession


class DictSessionAdapter:
    """Wraps a Django ChatSession instance as a dict-like session state.

    ``chat_nextseek`` agents use ``session.get(key)``, ``session[key]``,
    and ``session[key] = value`` — this adapter provides those operations
    against an in-memory cache backed by the Django ORM model.

    Call :meth:`save` after the pipeline finishes to persist changes.
    """

    def __init__(self, chat_session: ChatSession) -> None:
        self._session = chat_session
        self._cache: dict[str, Any] = {
            "results_history": list(chat_session.results_history),
            "last_debug": dict(chat_session.last_debug),
        }

    # --- dict-like interface ---

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._cache[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._cache[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._cache

    # --- persistence ---

    def save(self) -> None:
        """Persist the cache back to the Django ChatSession model."""
        self._session.results_history = self._cache.get("results_history", [])
        self._session.last_debug = self._cache.get("last_debug", {})
        self._session.save(update_fields=["results_history", "last_debug", "updated_at"])

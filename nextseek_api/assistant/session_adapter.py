"""Adapts Django ChatSession to the dict-like interface chat_nextseek agents expect."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models_db import ChatSession


# Keys that have dedicated columns on ChatSession. Everything else the
# pipeline writes (nfcore_wizard, chat_log, log paths, etc.) lands in
# the catch-all ``extra_state`` JSON column.
_TYPED_KEYS = ("results_history", "last_debug")


class DictSessionAdapter:
    """Wraps a Django ChatSession instance as a dict-like session state.

    ``chat_nextseek`` agents use ``session.get(key)``, ``session[key]``,
    and ``session[key] = value`` — this adapter provides those operations
    against an in-memory cache backed by the Django ORM model.

    The model has dedicated columns for ``results_history`` and
    ``last_debug``; every other key chat_nextseek writes (notably
    ``nfcore_wizard`` for the wizard state machine and ``chat_log`` for
    rolling per-turn memory) is persisted via the ``extra_state`` JSON
    column. Without this, wizard state and chat memory get dropped between
    requests because chat_nextseek treats them as ordinary session keys.

    Call :meth:`save` after the pipeline finishes to persist changes.
    """

    def __init__(self, chat_session: ChatSession) -> None:
        self._session = chat_session
        self._cache: dict[str, Any] = {
            "results_history": list(chat_session.results_history),
            "last_debug": dict(chat_session.last_debug),
            **(getattr(chat_session, "extra_state", None) or {}),
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
        self._session.extra_state = {
            k: v for k, v in self._cache.items() if k not in _TYPED_KEYS
        }
        self._session.save(update_fields=[
            "results_history", "last_debug", "extra_state", "updated_at",
        ])

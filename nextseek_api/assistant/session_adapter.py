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

    @staticmethod
    def _merge_history(stored: list, cached: list) -> list:
        """Union of what is already persisted and what this turn produced.

        A turn reads the whole ``results_history`` JSON column into memory at
        start and writes the whole thing back at the end. Two turns running
        concurrently (gunicorn runs several sync workers, and each turn executes
        in a daemon thread) both read the same snapshot, and the second write
        erases the first turn's bundle. The symptom is subtle and bad: recall
        then resolves a follow-up question against whichever bundle survived.

        Merging by bundle id instead of overwriting keeps both. Bundles from
        this turn win on conflict, since they are the fresher version of the
        same id.
        """
        merged: list = []
        index: dict = {}
        for bundle in list(stored) + list(cached):
            if not isinstance(bundle, dict):
                continue
            bundle_id = bundle.get("id")
            if bundle_id is None:
                merged.append(bundle)
                continue
            if bundle_id in index:
                merged[index[bundle_id]] = bundle
            else:
                index[bundle_id] = len(merged)
                merged.append(bundle)
        return merged

    def save(self) -> None:
        """Persist the cache back to the Django ChatSession model.

        Done under a row lock so the read-merge-write cycle cannot interleave
        with another turn in the same session.
        """
        from django.db import transaction  # local: keeps import cost off module load

        cached_history = self._cache.get("results_history", [])
        last_debug = self._cache.get("last_debug", {})
        extra_state = {k: v for k, v in self._cache.items() if k not in _TYPED_KEYS}
        fields = ["results_history", "last_debug", "extra_state", "updated_at"]

        try:
            with transaction.atomic():
                locked = (
                    ChatSession.objects.select_for_update()
                    .get(pk=self._session.pk)
                )
                locked.results_history = self._merge_history(
                    locked.results_history or [], cached_history
                )
                locked.last_debug = last_debug
                locked.extra_state = extra_state
                locked.save(update_fields=fields)
                self._session.results_history = locked.results_history
                self._session.last_debug = locked.last_debug
                self._session.extra_state = locked.extra_state
                self._cache["results_history"] = locked.results_history
                return
        except Exception:
            # Backends without row locking (or a session row that vanished) must
            # still persist the turn rather than lose it outright.
            pass

        self._session.results_history = cached_history
        self._session.last_debug = last_debug
        self._session.extra_state = extra_state
        self._session.save(update_fields=fields)

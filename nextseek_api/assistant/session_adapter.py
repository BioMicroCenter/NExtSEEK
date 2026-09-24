"""Adapts Django ChatSession to the dict-like interface chat_nextseek agents expect."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import json
import logging


logger = logging.getLogger(__name__)


class SessionSaveError(RuntimeError):
    """A turn ran to completion but could not be written back to its session.

    Raised so the caller can tell the user the turn was not saved. Previously
    this escaped as a bare OperationalError out of a background thread, which
    killed the thread and left the chat silently empty.
    """


def _approx_size(value) -> int:
    """Serialized size of a payload, for saying how big the failed write was."""
    try:
        return len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return -1


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

    def reload(self) -> None:
        """Re-read the row and rebuild the cache: a turn must see the turn before it."""
        self._session.refresh_from_db(fields=["results_history", "last_debug", "extra_state"])
        self._cache = {
            "results_history": list(self._session.results_history),
            "last_debug": dict(self._session.last_debug),
            **(getattr(self._session, "extra_state", None) or {}),
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

        # The module-level import is TYPE_CHECKING only, so the model must be imported
        # here too. Without it the locked path raised NameError on every save, and every
        # turn fell through to the unlocked, unmerged write below.
        from .models_db import ChatSession

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
            # still persist the turn rather than lose it outright. That fallback
            # is legitimate, but a bare `pass` also hid REAL failures: on
            # production a (2006, 'Server has gone away') here left no trace at
            # all, and the unlocked retry below then raised out of a background
            # thread, losing a turn that had already succeeded.
            logger.warning(
                "session %s: locked save failed, falling back to an unlocked write",
                getattr(self._session, "session_id", "?"), exc_info=True,
            )

        self._session.results_history = cached_history
        self._session.last_debug = last_debug
        self._session.extra_state = extra_state
        try:
            self._session.save(update_fields=fields)
        except Exception as exc:
            # Both paths are gone, so this turn is not going to be persisted.
            # Say so with the sizes attached: the production cause was a single
            # UPDATE carrying results_history AND last_debug, ~40 MB between them
            # because a 13.5 MB api_result_full was inlined into both.
            sizes = ", ".join(
                f"{name}={_approx_size(value):,}B"
                for name, value in (
                    ("results_history", cached_history),
                    ("last_debug", last_debug),
                    ("extra_state", extra_state),
                )
            )
            msg = (
                f"session {getattr(self._session, 'session_id', '?')}: could not "
                f"persist the turn ({sizes}) -- {type(exc).__name__}: {exc}"
            )
            logger.error(msg, exc_info=True)
            raise SessionSaveError(msg) from exc

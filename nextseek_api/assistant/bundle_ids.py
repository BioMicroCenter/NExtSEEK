"""One allocator for the bundle ids of a chat (approach 1, piece 2).

Two writers add bundles to a ChatSession's ``results_history``: an NS turn (the orchestrator numbers its bundle
through ``DictSessionAdapter.allocate_bundle_id``) and the Container-CC artifact ops (``_register_artifact_bundle``).
Both take the next id here, on the row they hold with ``select_for_update``, and record it in
``extra_state["bundle_seq"]`` in the same write, so no two writers hand out one id and the adapter's merge keeps both
bundles. The counter only moves forward (``DictSessionAdapter.save`` never writes an older one back).
"""
from __future__ import annotations

from typing import Any

from django.db import transaction

#: chat_nextseek.orchestrator.BUNDLE_SEQ_KEY; NessieAI/tests/ns/test_bundle_allocator.py pins the two together.
BUNDLE_SEQ_KEY = "bundle_seq"


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def next_bundle_id_locked(locked) -> int:
    """The next id for ``locked``, a ChatSession row the caller holds with ``select_for_update`` inside
    ``transaction.atomic``. Sets ``extra_state[bundle_seq]`` on it; the caller saves ``extra_state``."""
    history = locked.results_history or []
    highest = max((as_int(b.get("id")) for b in history if isinstance(b, dict)), default=0)
    state = dict(locked.extra_state or {})
    nxt = max(as_int(state.get(BUNDLE_SEQ_KEY)), highest, len(history)) + 1
    state[BUNDLE_SEQ_KEY] = nxt
    locked.extra_state = state
    return nxt


def allocate_bundle_id(session_pk) -> int:
    """Take the chat's next bundle id in its own transaction (the NS turn's path)."""
    from nextseek_api.assistant.models_db import ChatSession

    with transaction.atomic():
        locked = (ChatSession.objects.select_for_update()
                  .only("session_id", "results_history", "extra_state", "updated_at")
                  .get(pk=session_pk))
        nxt = next_bundle_id_locked(locked)
        locked.save(update_fields=["extra_state", "updated_at"])
    return nxt

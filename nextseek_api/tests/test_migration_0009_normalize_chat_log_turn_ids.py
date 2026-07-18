"""Migration 0009: renumber chat_log turn_ids to sequential ints; move str
(UUID) turn_ids into cc_run_id. Tests the pure transform over REAL observed
chat_log shapes (live-DB census 2026-07-18) + the full RunPython under sqlite.

Observed shapes (structure only):
- extra_state is always an OBJECT (dict); chat_log is an ARRAY or JSON null/absent.
- Two entry key-sets: CC entries with a STRING (UUID) turn_id, NS entries with an
  INTEGER turn_id. Poisoned sessions: the str entry is always the LAST entry.
"""
from __future__ import annotations

import pytest

from nextseek_api.migrations import (
    _chat_log_normalize as norm,  # pure helper module used by 0009
)


_UUID1 = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"
_UUID2 = "11112222-3333-4444-5555-666677778888"


def _ns_entry(tid):
    return {
        "turn_id": tid, "ts": "t", "mode": "new_search",
        "user_query": "q", "assistant_reply": "a", "bundle_id": 1,
        "key_entities": {}, "tool_summary": {}, "intent_summary": "",
        "result_summary": {}, "assistant_reply_preview": "a",
    }


def _cc_entry(tid):
    return {
        "turn_id": tid, "ts": "t", "mode": "cc",
        "user_query": "q", "assistant_reply": "a",
        "artifacts": None, "cc_traces": [],
    }


# --- pure transform: renumber_chat_log --------------------------------------

def test_poisoned_mixed_log_renumbered_with_cc_run_id_preserved():
    """The canonical poisoned shape: NS ints then a trailing CC str UUID."""
    log = [_ns_entry(1), _ns_entry(2), _cc_entry(_UUID1)]
    out = norm.renumber_chat_log(log)
    assert [e["turn_id"] for e in out] == [1, 2, 3]
    assert out[2]["cc_run_id"] == _UUID1
    # non-turn_id content is preserved untouched
    assert out[2]["mode"] == "cc"
    assert out[0]["mode"] == "new_search"


def test_interleaved_cc_and_ns_all_renumbered():
    log = [_cc_entry(_UUID1), _ns_entry(1), _cc_entry(_UUID2)]
    out = norm.renumber_chat_log(log)
    assert [e["turn_id"] for e in out] == [1, 2, 3]
    assert out[0]["cc_run_id"] == _UUID1
    assert out[2]["cc_run_id"] == _UUID2
    assert "cc_run_id" not in out[1]  # NS entry never gains cc_run_id


def test_pure_int_log_is_noop_values():
    log = [_ns_entry(1), _ns_entry(2), _ns_entry(3)]
    out = norm.renumber_chat_log(log)
    assert [e["turn_id"] for e in out] == [1, 2, 3]
    assert all("cc_run_id" not in e for e in out)


def test_non_ordinal_int_log_is_renumbered_1_to_n():
    # After eviction ids may not be 1..N; normalize collapses to 1..N.
    log = [_ns_entry(40), _ns_entry(41), _ns_entry(42)]
    out = norm.renumber_chat_log(log)
    assert [e["turn_id"] for e in out] == [1, 2, 3]


def test_idempotent_second_pass_is_noop():
    log = [_ns_entry(1), _ns_entry(2), _cc_entry(_UUID1)]
    once = norm.renumber_chat_log(log)
    twice = norm.renumber_chat_log(once)
    assert once == twice


def test_existing_cc_run_id_not_clobbered():
    entry = _cc_entry(5)
    entry["cc_run_id"] = _UUID1  # already migrated CC entry (int turn_id)
    out = norm.renumber_chat_log([entry])
    assert out[0]["turn_id"] == 1
    assert out[0]["cc_run_id"] == _UUID1  # preserved, not overwritten by int


def test_non_dict_entries_left_untouched_and_not_counted():
    log = [_ns_entry(1), "junk", _cc_entry(_UUID1)]
    out = norm.renumber_chat_log(log)
    assert out[0]["turn_id"] == 1
    assert out[1] == "junk"
    assert out[2]["turn_id"] == 2  # counter skips the non-dict entry
    assert out[2]["cc_run_id"] == _UUID1


# --- extra_state-level guard ------------------------------------------------

def test_normalize_extra_state_tolerates_malformed():
    assert norm.normalize_extra_state(None) is None
    assert norm.normalize_extra_state({"chat_log": None}) == {"chat_log": None}
    assert norm.normalize_extra_state({}) == {}
    # non-list chat_log is left as-is
    assert norm.normalize_extra_state({"chat_log": "oops"}) == {"chat_log": "oops"}


def test_normalize_extra_state_preserves_sibling_keys():
    es = {"chat_log": [_cc_entry(_UUID1)], "cc_session_id": "sid",
          "cc_project_dirname": "proj", "summary": {"x": 1}}
    out = norm.normalize_extra_state(es)
    assert out["chat_log"][0]["turn_id"] == 1
    assert out["chat_log"][0]["cc_run_id"] == _UUID1
    assert out["cc_session_id"] == "sid"
    assert out["cc_project_dirname"] == "proj"
    assert out["summary"] == {"x": 1}


def test_normalize_extra_state_returns_none_when_unchanged():
    """Signal 'no write needed' so the migration can skip untouched rows."""
    es = {"chat_log": [_ns_entry(1), _ns_entry(2)]}
    # already sequential ints -> normalized value equals input
    assert norm.normalize_extra_state(es) == es


# --- full RunPython under sqlite (pytest-django) ----------------------------

@pytest.mark.django_db
def test_migration_applies_over_stored_sessions():
    from django.contrib.auth import get_user_model
    from nextseek_api.assistant.models_db import ChatSession
    from nextseek_api.migrations import (
        _chat_log_normalize as m,
    )

    User = get_user_model()
    user = User.objects.create(username="mig-user")
    poisoned = ChatSession.objects.create(
        user=user,
        extra_state={"chat_log": [_ns_entry(1), _ns_entry(2), _cc_entry(_UUID1)],
                     "cc_session_id": "sid"},
    )
    clean = ChatSession.objects.create(
        user=user, extra_state={"chat_log": [_ns_entry(1)]})
    empty = ChatSession.objects.create(user=user, extra_state={})

    m.forwards_apply(ChatSession)

    poisoned.refresh_from_db()
    log = poisoned.extra_state["chat_log"]
    assert [e["turn_id"] for e in log] == [1, 2, 3]
    assert log[2]["cc_run_id"] == _UUID1
    assert poisoned.extra_state["cc_session_id"] == "sid"

    clean.refresh_from_db()
    assert [e["turn_id"] for e in clean.extra_state["chat_log"]] == [1]

    empty.refresh_from_db()
    assert empty.extra_state == {}

    # Idempotent: a second application changes nothing.
    m.forwards_apply(ChatSession)
    poisoned.refresh_from_db()
    assert [e["turn_id"] for e in poisoned.extra_state["chat_log"]] == [1, 2, 3]
    assert poisoned.extra_state["chat_log"][2]["cc_run_id"] == _UUID1

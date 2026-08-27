"""Bundle ids must be unique within a session.

Bundle ids index ``results_history``, and follow-up recall resolves a question
against the bundle carrying a given id. Observed in the 2026-07-24 run: three
different searches ("Find all NHP samples", "Find me mice associated with ndma",
"Find mice treated with NDMA") were all issued bundle id 13, because the id was
``len(results_history) + 1`` and the appends were not all surviving. A follow-up
then answered from whichever bundle happened to own that id.
"""
from __future__ import annotations

from chat_nextseek.orchestrator import BUNDLE_SEQ_KEY, _next_bundle_id


def test_ids_are_unique_on_a_fresh_session():
    session: dict = {}
    assert [_next_bundle_id(session) for _ in range(4)] == [1, 2, 3, 4]


def test_ids_continue_past_existing_history():
    session = {"results_history": [{"id": 1}, {"id": 2}]}
    assert _next_bundle_id(session) == 3
    assert _next_bundle_id(session) == 4  # counter advances even before the append lands


def test_a_lost_append_cannot_cause_a_collision():
    """The exact 2026-07-24 failure mode.

    A bundle was allocated and the history write did not survive. Deriving the
    next id from len(history) hands out the SAME id again; a counter kept
    separately from the history does not.
    """
    session = {"results_history": [{"id": 7}], BUNDLE_SEQ_KEY: 7}
    session["results_history"] = []  # the append is lost
    assert _next_bundle_id(session) == 8


def test_ids_recover_from_a_history_whose_counter_is_missing():
    # e.g. a session created before the counter existed
    session = {"results_history": [{"id": 1}, {"id": 2}, {"id": 9}]}
    assert _next_bundle_id(session) == 10


def test_a_corrupt_counter_does_not_crash_the_turn():
    session = {"results_history": [], BUNDLE_SEQ_KEY: "not-a-number"}
    assert _next_bundle_id(session) == 1

"""The sync schedule (`graph_sync/schedule.py`; sync design 12, R9).

Pure: no database, no Neo4j, no clock. Every `now` is passed in, and every time in this module is UTC. The one
import from the rest of graph_sync is `state`, and only to prove that the keys the schedule builds are keys the
outbox accepts and the drain can parse.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest

from nextseek_api.graph_sync import schedule, state

UTC = dt_timezone.utc


def dt(*parts: int) -> datetime:
    """An aware UTC datetime, written as its parts."""
    return datetime(*parts, tzinfo=UTC)


# 2026-09-13 and 2026-09-20 are Sundays; 2026-09-15 is a Tuesday of ISO week 2026-W38, which began Monday the 14th.
RECONCILE = schedule.Cadence("reconcile", None, 2, 0)
DRIFT = schedule.Cadence("drift", None, 2, 30)
FULL = schedule.Cadence("full", schedule.SUNDAY, 3, 0)


# --- the cadences ------------------------------------------------------------------------------------------------

def test_the_default_cadences_are_the_schedule_the_spec_set():
    assert schedule.DEFAULT_CADENCES == (RECONCILE, DRIFT, FULL)


def test_every_default_cadence_is_an_outbox_slot_kind_and_no_kind_is_scheduled_twice():
    kinds = [cadence.kind for cadence in schedule.DEFAULT_CADENCES]
    assert sorted(kinds) == sorted(state.SLOT_KINDS)
    assert len(set(kinds)) == len(kinds)


@pytest.mark.parametrize("bad", [
    dict(kind="", weekday=None, hour=2, minute=0),
    dict(kind=None, weekday=None, hour=2, minute=0),
    dict(kind="full", weekday=7, hour=3, minute=0),
    dict(kind="full", weekday=-1, hour=3, minute=0),
    dict(kind="reconcile", weekday=None, hour=24, minute=0),
    dict(kind="reconcile", weekday=None, hour=-1, minute=0),
    dict(kind="reconcile", weekday=None, hour=2, minute=60),
    dict(kind="reconcile", weekday=None, hour=2, minute=-1),
])
def test_a_malformed_cadence_is_refused(bad):
    with pytest.raises(ValueError):
        schedule.Cadence(**bad)


# --- the boundaries ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("now, boundary", [
    (dt(2026, 9, 15, 1, 59, 59), dt(2026, 9, 14, 2, 0)),      # a minute short of the hour: yesterday's slot
    (dt(2026, 9, 15, 2, 0), dt(2026, 9, 15, 2, 0)),           # exactly on it
    (dt(2026, 9, 15, 2, 0, 1), dt(2026, 9, 15, 2, 0)),
    (dt(2026, 9, 15, 23, 59, 59), dt(2026, 9, 15, 2, 0)),     # the rest of the day belongs to it
])
def test_a_daily_boundary_is_the_last_time_of_day_that_has_passed(now, boundary):
    assert schedule.last_boundary(RECONCILE, now) == boundary


@pytest.mark.parametrize("now, boundary", [
    (dt(2026, 9, 15, 2, 29, 59), dt(2026, 9, 14, 2, 30)),
    (dt(2026, 9, 15, 2, 30), dt(2026, 9, 15, 2, 30)),
])
def test_a_daily_boundary_keeps_its_minutes(now, boundary):
    assert schedule.last_boundary(DRIFT, now) == boundary


@pytest.mark.parametrize("now, boundary", [
    (dt(2026, 9, 15, 0, 0), dt(2026, 9, 15, 0, 0)),           # midnight is its own boundary
    (dt(2026, 9, 14, 23, 59, 59), dt(2026, 9, 14, 0, 0)),     # the last second before it
    (dt(2026, 9, 15, 0, 0, 1), dt(2026, 9, 15, 0, 0)),
])
def test_a_midnight_cadence_turns_over_with_the_date(now, boundary):
    assert schedule.last_boundary(schedule.Cadence("reconcile", None, 0, 0), now) == boundary


@pytest.mark.parametrize("now, boundary", [
    (dt(2026, 9, 20, 2, 59, 59), dt(2026, 9, 13, 3, 0)),      # Sunday, before the hour: last Sunday's slot
    (dt(2026, 9, 20, 3, 0), dt(2026, 9, 20, 3, 0)),
    (dt(2026, 9, 21, 0, 0), dt(2026, 9, 20, 3, 0)),           # the Monday after
    (dt(2026, 9, 26, 23, 59), dt(2026, 9, 20, 3, 0)),         # the Saturday after: still the same slot
])
def test_a_weekly_boundary_is_the_last_time_on_its_weekday_that_has_passed(now, boundary):
    assert schedule.last_boundary(FULL, now) == boundary


def test_a_boundary_is_utc():
    boundary = schedule.last_boundary(RECONCILE, dt(2026, 9, 15, 5, 0))
    assert boundary.tzinfo is not None and boundary.utcoffset() == timedelta(0)


def test_a_naive_now_is_read_as_utc():
    assert schedule.last_boundary(RECONCILE, datetime(2026, 9, 15, 1, 59)) == dt(2026, 9, 14, 2, 0)


def test_an_offset_now_is_converted_before_its_boundary_is_taken():
    now = datetime(2026, 9, 14, 21, 30, tzinfo=dt_timezone(timedelta(hours=-5)))   # 2026-09-15 02:30 UTC
    boundary = schedule.last_boundary(RECONCILE, now)
    assert boundary == dt(2026, 9, 15, 2, 0)
    assert schedule.slot_key(RECONCILE, boundary) == "slot:2026-09-15"


# --- the slot keys -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("boundary, key", [
    (dt(2026, 9, 15, 2, 0), "slot:2026-09-15"),
    (dt(2026, 9, 15, 23, 30), "slot:2026-09-15"),
    (dt(2027, 1, 1, 2, 0), "slot:2027-01-01"),                # a daily key is a calendar date, not an ISO one
])
def test_a_daily_slot_key_is_the_date_of_its_boundary(boundary, key):
    assert schedule.slot_key(RECONCILE, boundary) == key


@pytest.mark.parametrize("boundary, key", [
    (dt(2026, 9, 20, 3, 0), "slot:2026-W38"),                 # the week that began Monday the 14th
    (dt(2026, 9, 13, 3, 0), "slot:2026-W37"),
    (dt(2027, 1, 3, 3, 0), "slot:2026-W53"),                  # the Sunday that closes ISO 2026
    (dt(2027, 1, 10, 3, 0), "slot:2027-W01"),                 # the Sunday that closes ISO 2027's first week
    (dt(2026, 1, 4, 3, 0), "slot:2026-W01"),
])
def test_a_weekly_slot_key_is_the_iso_week_of_its_boundary_and_follows_the_iso_year(boundary, key):
    assert schedule.slot_key(FULL, boundary) == key


def test_every_key_the_schedule_builds_is_one_the_outbox_accepts():
    """`state.check_item` raises on a key the drain could not parse, so a slot could never sit in the outbox dead."""
    for cadence in schedule.DEFAULT_CADENCES:
        key = schedule.slot_key(cadence, schedule.last_boundary(cadence, dt(2026, 9, 20, 4, 0)))
        state.check_item(cadence.kind, key)
        assert len(key) <= state.KEY_CHARS


# --- what is due -------------------------------------------------------------------------------------------------

def kinds_and_keys(due) -> list[tuple[str, str]]:
    return [(slot.kind, slot.key) for slot in due]


def test_a_slot_no_run_has_satisfied_is_due_oldest_boundary_first():
    due = schedule.due_slots(schedule.DEFAULT_CADENCES, dt(2026, 9, 15, 4, 0), {})
    assert isinstance(due, tuple)
    assert kinds_and_keys(due) == [
        ("full", "slot:2026-W37"),            # Sunday the 13th, the oldest boundary still unmet
        ("reconcile", "slot:2026-09-15"),
        ("drift", "slot:2026-09-15"),
    ]


def test_a_slot_carries_its_boundary_as_well_as_its_key():
    (slot,) = schedule.due_slots((FULL,), dt(2026, 9, 20, 4, 0), {})
    assert (slot.kind, slot.key, slot.boundary) == ("full", "slot:2026-W38", dt(2026, 9, 20, 3, 0))


def test_a_run_of_the_kind_at_or_after_the_boundary_satisfies_the_slot():
    last_ok = {"reconcile": dt(2026, 9, 15, 2, 0), "drift": dt(2026, 9, 15, 3, 0), "full": dt(2026, 9, 13, 3, 0)}
    assert schedule.due_slots(schedule.DEFAULT_CADENCES, dt(2026, 9, 15, 4, 0), last_ok) == ()


def test_a_run_that_started_before_the_boundary_leaves_the_slot_due():
    last_ok = {"reconcile": dt(2026, 9, 15, 1, 59, 59)}
    assert kinds_and_keys(schedule.due_slots((RECONCILE,), dt(2026, 9, 15, 4, 0), last_ok)) == [
        ("reconcile", "slot:2026-09-15")]


def test_a_full_sync_after_the_boundary_satisfies_the_reconcile():
    """Sunday: the reconcile's 02:00 slot went unrun, then the full sync at 03:00 did everything a reconcile does."""
    last_ok = {"full": dt(2026, 9, 20, 3, 0), "drift": dt(2026, 9, 20, 2, 30)}
    assert schedule.due_slots(schedule.DEFAULT_CADENCES, dt(2026, 9, 20, 4, 0), last_ok) == ()


def test_a_full_sync_before_the_boundary_leaves_the_reconcile_due():
    last_ok = {"full": dt(2026, 9, 20, 3, 0), "drift": dt(2026, 9, 21, 2, 30)}
    assert kinds_and_keys(schedule.due_slots(schedule.DEFAULT_CADENCES, dt(2026, 9, 21, 4, 0), last_ok)) == [
        ("reconcile", "slot:2026-09-21")]


def test_a_reconcile_does_not_satisfy_the_weekly_full_sync():
    last_ok = {"reconcile": dt(2026, 9, 20, 3, 30), "drift": dt(2026, 9, 20, 3, 30)}
    assert kinds_and_keys(schedule.due_slots(schedule.DEFAULT_CADENCES, dt(2026, 9, 20, 4, 0), last_ok)) == [
        ("full", "slot:2026-W38")]


def test_a_full_sync_does_not_satisfy_the_drift_check():
    """A sync writes; the drift check reads and reports. One is never evidence for the other."""
    last_ok = {"full": dt(2026, 9, 20, 3, 0)}
    assert kinds_and_keys(schedule.due_slots(schedule.DEFAULT_CADENCES, dt(2026, 9, 20, 4, 0), last_ok)) == [
        ("drift", "slot:2026-09-20")]


def test_only_a_full_sync_stands_in_for_another_kind():
    assert schedule.satisfied_by("reconcile") == ("reconcile", "full")
    assert schedule.satisfied_by("full") == ("full",)
    assert schedule.satisfied_by("drift") == ("drift",)
    assert schedule.satisfied_by("samples") == ("samples",)


def test_a_run_of_a_kind_the_cadence_does_not_name_satisfies_nothing():
    last_ok = {"samples": dt(2026, 9, 15, 3, 0), "catalog": dt(2026, 9, 15, 3, 0)}
    assert kinds_and_keys(schedule.due_slots((RECONCILE,), dt(2026, 9, 15, 4, 0), last_ok)) == [
        ("reconcile", "slot:2026-09-15")]


def test_a_kind_recorded_as_never_run_is_due():
    assert kinds_and_keys(schedule.due_slots((RECONCILE,), dt(2026, 9, 15, 4, 0), {"reconcile": None})) == [
        ("reconcile", "slot:2026-09-15")]


def test_a_missed_slot_is_due_at_the_next_pass():
    """The loop was down for two days. The days in between are gone: the current slot is the one that is due, and
    the sync it asks for reads everything that changed while the loop was down."""
    last_ok = {"reconcile": dt(2026, 9, 13, 2, 0)}
    assert kinds_and_keys(schedule.due_slots((RECONCILE,), dt(2026, 9, 15, 9, 0), last_ok)) == [
        ("reconcile", "slot:2026-09-15")]


def test_a_pass_before_the_day_s_boundary_asks_for_nothing_new():
    last_ok = {"reconcile": dt(2026, 9, 14, 2, 5)}
    assert schedule.due_slots((RECONCILE,), dt(2026, 9, 15, 1, 0), last_ok) == ()


def test_a_last_run_with_an_offset_is_compared_in_utc():
    last_ok = {"reconcile": datetime(2026, 9, 14, 22, 5, tzinfo=dt_timezone(timedelta(hours=-5)))}   # 03:05 UTC
    assert schedule.due_slots((RECONCILE,), dt(2026, 9, 15, 4, 0), last_ok) == ()


def test_slots_with_the_same_boundary_keep_the_order_they_were_given():
    early, late = schedule.Cadence("drift", None, 2, 0), schedule.Cadence("reconcile", None, 2, 0)
    assert [slot.kind for slot in schedule.due_slots((early, late), dt(2026, 9, 15, 4, 0), {})] == [
        "drift", "reconcile"]
    assert [slot.kind for slot in schedule.due_slots((late, early), dt(2026, 9, 15, 4, 0), {})] == [
        "reconcile", "drift"]


def test_no_cadence_is_due_when_none_is_scheduled():
    assert schedule.due_slots((), dt(2026, 9, 15, 4, 0), {}) == ()

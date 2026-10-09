"""The sync schedule (`graph_sync/schedule.py`; sync design 12, R9).

Pure: no database, no Neo4j, no clock. Every `now` is passed in. Instants are UTC; a cadence's hour is US Eastern,
written in these tests with `et(...)`. The one
import from the rest of graph_sync is `state`, and only to prove that the keys the schedule builds are keys the
outbox accepts and the drain can parse.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest

from nextseek_api.graph_sync import schedule, state

UTC = dt_timezone.utc


def dt(*parts: int) -> datetime:
    """An aware UTC datetime, written as its parts."""
    return datetime(*parts, tzinfo=UTC)


def et(*parts: int) -> datetime:
    """The UTC instant of a US Eastern wall-clock time written as its parts (the tests' September days are EDT)."""
    return datetime(*parts, tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)


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
    (et(2026, 9, 15, 1, 59, 59), et(2026, 9, 14, 2, 0)),      # a minute short of the hour: yesterday's slot
    (et(2026, 9, 15, 2, 0), et(2026, 9, 15, 2, 0)),           # exactly on it
    (et(2026, 9, 15, 2, 0, 1), et(2026, 9, 15, 2, 0)),
    (et(2026, 9, 15, 23, 59, 59), et(2026, 9, 15, 2, 0)),     # the rest of the day belongs to it
])
def test_a_daily_boundary_is_the_last_time_of_day_that_has_passed(now, boundary):
    assert schedule.last_boundary(RECONCILE, now) == boundary


@pytest.mark.parametrize("now, boundary", [
    (et(2026, 9, 15, 2, 29, 59), et(2026, 9, 14, 2, 30)),
    (et(2026, 9, 15, 2, 30), et(2026, 9, 15, 2, 30)),
])
def test_a_daily_boundary_keeps_its_minutes(now, boundary):
    assert schedule.last_boundary(DRIFT, now) == boundary


@pytest.mark.parametrize("now, boundary", [
    (et(2026, 9, 15, 0, 0), et(2026, 9, 15, 0, 0)),           # midnight is its own boundary
    (et(2026, 9, 14, 23, 59, 59), et(2026, 9, 14, 0, 0)),     # the last second before it
    (et(2026, 9, 15, 0, 0, 1), et(2026, 9, 15, 0, 0)),
])
def test_a_midnight_cadence_turns_over_with_the_date(now, boundary):
    assert schedule.last_boundary(schedule.Cadence("reconcile", None, 0, 0), now) == boundary


@pytest.mark.parametrize("now, boundary", [
    (et(2026, 9, 20, 2, 59, 59), et(2026, 9, 13, 3, 0)),      # Sunday, before the hour: last Sunday's slot
    (et(2026, 9, 20, 3, 0), et(2026, 9, 20, 3, 0)),
    (et(2026, 9, 21, 0, 0), et(2026, 9, 20, 3, 0)),           # the Monday after
    (et(2026, 9, 26, 23, 59), et(2026, 9, 20, 3, 0)),         # the Saturday after: still the same slot
])
def test_a_weekly_boundary_is_the_last_time_on_its_weekday_that_has_passed(now, boundary):
    assert schedule.last_boundary(FULL, now) == boundary


def test_a_boundary_is_utc():
    boundary = schedule.last_boundary(RECONCILE, et(2026, 9, 15, 5, 0))
    assert boundary.tzinfo is not None and boundary.utcoffset() == timedelta(0)


def test_a_naive_now_is_read_as_utc():
    assert schedule.last_boundary(RECONCILE, datetime(2026, 9, 15, 5, 59)) == et(2026, 9, 14, 2, 0)   # 01:59 EDT


def test_an_offset_now_is_converted_before_its_boundary_is_taken():
    now = datetime(2026, 9, 15, 7, 30, tzinfo=dt_timezone(timedelta(hours=1)))   # 2026-09-15 06:30 UTC = 02:30 EDT
    boundary = schedule.last_boundary(RECONCILE, now)
    assert boundary == et(2026, 9, 15, 2, 0)
    assert schedule.slot_key(RECONCILE, boundary) == "slot:2026-09-15"


# --- the slot keys -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("boundary, key", [
    (et(2026, 9, 15, 2, 0), "slot:2026-09-15"),
    (et(2026, 9, 15, 23, 30), "slot:2026-09-15"),
    (et(2027, 1, 1, 2, 0), "slot:2027-01-01"),                # a daily key is a calendar date, not an ISO one
])
def test_a_daily_slot_key_is_the_date_of_its_boundary(boundary, key):
    assert schedule.slot_key(RECONCILE, boundary) == key


@pytest.mark.parametrize("boundary, key", [
    (et(2026, 9, 20, 3, 0), "slot:2026-W38"),                 # the week that began Monday the 14th
    (et(2026, 9, 13, 3, 0), "slot:2026-W37"),
    (et(2027, 1, 3, 3, 0), "slot:2026-W53"),                  # the Sunday that closes ISO 2026
    (et(2027, 1, 10, 3, 0), "slot:2027-W01"),                 # the Sunday that closes ISO 2027's first week
    (et(2026, 1, 4, 3, 0), "slot:2026-W01"),
])
def test_a_weekly_slot_key_is_the_iso_week_of_its_boundary_and_follows_the_iso_year(boundary, key):
    assert schedule.slot_key(FULL, boundary) == key


def test_every_key_the_schedule_builds_is_one_the_outbox_accepts():
    """`state.check_item` raises on a key the drain could not parse, so a slot could never sit in the outbox dead."""
    for cadence in schedule.DEFAULT_CADENCES:
        key = schedule.slot_key(cadence, schedule.last_boundary(cadence, et(2026, 9, 20, 4, 0)))
        state.check_item(cadence.kind, key)
        assert len(key) <= state.KEY_CHARS


# --- what is due -------------------------------------------------------------------------------------------------

def kinds_and_keys(due) -> list[tuple[str, str]]:
    return [(slot.kind, slot.key) for slot in due]


def test_a_slot_no_run_has_satisfied_is_due_oldest_boundary_first():
    due = schedule.due_slots(schedule.DEFAULT_CADENCES, et(2026, 9, 15, 4, 0), {})
    assert isinstance(due, tuple)
    assert kinds_and_keys(due) == [
        ("full", "slot:2026-W37"),            # Sunday the 13th, the oldest boundary still unmet
        ("reconcile", "slot:2026-09-15"),
        ("drift", "slot:2026-09-15"),
    ]


def test_a_slot_carries_its_boundary_as_well_as_its_key():
    (slot,) = schedule.due_slots((FULL,), et(2026, 9, 20, 4, 0), {})
    assert (slot.kind, slot.key, slot.boundary) == ("full", "slot:2026-W38", et(2026, 9, 20, 3, 0))


def test_a_run_of_the_kind_at_or_after_the_boundary_satisfies_the_slot():
    last_ok = {"reconcile": et(2026, 9, 15, 2, 0), "drift": et(2026, 9, 15, 3, 0), "full": et(2026, 9, 13, 3, 0)}
    assert schedule.due_slots(schedule.DEFAULT_CADENCES, et(2026, 9, 15, 4, 0), last_ok) == ()


def test_a_run_that_started_before_the_boundary_leaves_the_slot_due():
    last_ok = {"reconcile": et(2026, 9, 15, 1, 59, 59)}
    assert kinds_and_keys(schedule.due_slots((RECONCILE,), et(2026, 9, 15, 4, 0), last_ok)) == [
        ("reconcile", "slot:2026-09-15")]


def test_a_hand_run_the_evening_before_does_not_cancel_the_night_s_run():
    last_ok = {"reconcile": et(2026, 9, 14, 22, 0)}
    assert kinds_and_keys(schedule.due_slots((RECONCILE,), et(2026, 9, 15, 2, 0), last_ok)) == [
        ("reconcile", "slot:2026-09-15")]


def test_a_full_sync_after_the_boundary_satisfies_the_reconcile():
    """Sunday: the reconcile's 02:00 slot went unrun, then the full sync at 03:00 did everything a reconcile does."""
    last_ok = {"full": et(2026, 9, 20, 3, 0), "drift": et(2026, 9, 20, 2, 30)}
    assert schedule.due_slots(schedule.DEFAULT_CADENCES, et(2026, 9, 20, 4, 0), last_ok) == ()


def test_a_full_sync_before_the_boundary_leaves_the_reconcile_due():
    last_ok = {"full": et(2026, 9, 20, 3, 0), "drift": et(2026, 9, 21, 2, 30)}
    assert kinds_and_keys(schedule.due_slots(schedule.DEFAULT_CADENCES, et(2026, 9, 21, 4, 0), last_ok)) == [
        ("reconcile", "slot:2026-09-21")]


def test_a_reconcile_does_not_satisfy_the_weekly_full_sync():
    last_ok = {"reconcile": et(2026, 9, 20, 3, 30), "drift": et(2026, 9, 20, 3, 30)}
    assert kinds_and_keys(schedule.due_slots(schedule.DEFAULT_CADENCES, et(2026, 9, 20, 4, 0), last_ok)) == [
        ("full", "slot:2026-W38")]


def test_a_full_sync_does_not_satisfy_the_drift_check():
    """A sync writes; the drift check reads and reports. One is never evidence for the other."""
    last_ok = {"full": et(2026, 9, 20, 3, 0)}
    assert kinds_and_keys(schedule.due_slots(schedule.DEFAULT_CADENCES, et(2026, 9, 20, 4, 0), last_ok)) == [
        ("drift", "slot:2026-09-20")]


def test_only_a_full_sync_stands_in_for_another_kind():
    assert schedule.satisfied_by("reconcile") == ("reconcile", "full")
    assert schedule.satisfied_by("full") == ("full",)
    assert schedule.satisfied_by("drift") == ("drift",)
    assert schedule.satisfied_by("samples") == ("samples",)


def test_a_run_of_a_kind_the_cadence_does_not_name_satisfies_nothing():
    last_ok = {"samples": et(2026, 9, 15, 3, 0), "catalog": et(2026, 9, 15, 3, 0)}
    assert kinds_and_keys(schedule.due_slots((RECONCILE,), et(2026, 9, 15, 4, 0), last_ok)) == [
        ("reconcile", "slot:2026-09-15")]


def test_a_kind_recorded_as_never_run_is_due():
    assert kinds_and_keys(schedule.due_slots((RECONCILE,), et(2026, 9, 15, 4, 0), {"reconcile": None})) == [
        ("reconcile", "slot:2026-09-15")]


def test_a_missed_slot_is_due_at_the_next_pass():
    """The loop was down for two days. The days in between are gone: the current slot is the one that is due, and
    the sync it asks for reads everything that changed while the loop was down."""
    last_ok = {"reconcile": et(2026, 9, 13, 2, 0)}
    assert kinds_and_keys(schedule.due_slots((RECONCILE,), et(2026, 9, 15, 9, 0), last_ok)) == [
        ("reconcile", "slot:2026-09-15")]


def test_a_pass_before_the_day_s_boundary_asks_for_nothing_new():
    last_ok = {"reconcile": et(2026, 9, 14, 2, 5)}
    assert schedule.due_slots((RECONCILE,), et(2026, 9, 15, 1, 0), last_ok) == ()


def test_a_last_run_with_an_offset_is_compared_in_utc():
    last_ok = {"reconcile": datetime(2026, 9, 15, 6, 5, tzinfo=UTC).astimezone(dt_timezone(timedelta(hours=-5)))}   # 02:05 EDT
    assert schedule.due_slots((RECONCILE,), et(2026, 9, 15, 4, 0), last_ok) == ()


def test_slots_with_the_same_boundary_keep_the_order_they_were_given():
    early, late = schedule.Cadence("drift", None, 2, 0), schedule.Cadence("reconcile", None, 2, 0)
    assert [slot.kind for slot in schedule.due_slots((early, late), et(2026, 9, 15, 4, 0), {})] == [
        "drift", "reconcile"]
    assert [slot.kind for slot in schedule.due_slots((late, early), et(2026, 9, 15, 4, 0), {})] == [
        "reconcile", "drift"]


def test_no_cadence_is_due_when_none_is_scheduled():
    assert schedule.due_slots((), et(2026, 9, 15, 4, 0), {}) == ()


# --- US Eastern time, daylight saving, and the move from UTC ------------------------------------------------------

@pytest.mark.parametrize("now, boundary", [
    (dt(2026, 7, 15, 12, 0), dt(2026, 7, 15, 6, 0)),          # summer: 02:00 EDT is 06:00Z
    (dt(2026, 7, 15, 5, 59), dt(2026, 7, 14, 6, 0)),
    (dt(2026, 1, 15, 12, 0), dt(2026, 1, 15, 7, 0)),          # winter: 02:00 EST is 07:00Z
    (dt(2026, 1, 15, 6, 59), dt(2026, 1, 14, 7, 0)),
])
def test_the_reconcile_is_2am_eastern_on_an_ordinary_summer_and_winter_day(now, boundary):
    assert schedule.last_boundary(RECONCILE, now) == boundary


def test_the_utc_date_and_the_eastern_date_can_differ_and_the_key_follows_eastern():
    """Winter drift at 02:30 EST is 07:30Z; a pass at 23:30 EST on the 14th is already 04:30Z on the 15th."""
    boundary = schedule.last_boundary(DRIFT, dt(2026, 1, 15, 4, 30))
    assert boundary == dt(2026, 1, 14, 7, 30)
    assert schedule.slot_key(DRIFT, boundary) == "slot:2026-01-14"


@pytest.mark.parametrize("day_start, day_end, boundary, key", [
    # 2026-03-08 spring forward: 02:00 does not exist. One slot, read with the old offset, so 03:00 EDT = 07:00Z.
    (dt(2026, 3, 8, 5, 0), dt(2026, 3, 9, 3, 59), dt(2026, 3, 8, 7, 0), "slot:2026-03-08"),
    # 2026-11-01 fall back: the clocks repeat 01:00-02:00, so 02:00 is the first EST instant and comes once (07:00Z).
    (dt(2026, 11, 1, 4, 0), dt(2026, 11, 2, 6, 59), dt(2026, 11, 1, 7, 0), "slot:2026-11-01"),
    # 2027-03-14 and 2027-11-07, the next year's transitions.
    (dt(2027, 3, 14, 5, 0), dt(2027, 3, 15, 5, 59), dt(2027, 3, 14, 7, 0), "slot:2027-03-14"),
    (dt(2027, 11, 7, 4, 0), dt(2027, 11, 8, 6, 59), dt(2027, 11, 7, 7, 0), "slot:2027-11-07"),
])
def test_a_dst_transition_day_gives_exactly_one_reconcile_slot(day_start, day_end, boundary, key):
    seen = set()
    now = day_start
    while now <= day_end:       # a pass every 15 minutes across the whole Eastern day
        b = schedule.last_boundary(RECONCILE, now)
        seen.add((b, schedule.slot_key(RECONCILE, b)))
        now += timedelta(minutes=15)
    assert (boundary, key) in seen
    assert len([k for _, k in seen if k == key]) == 1
    assert len(seen) == 2       # this day's slot and the previous one that the early passes still belong to


def test_the_weekly_full_sync_is_sunday_3am_eastern_across_the_changes():
    assert schedule.last_boundary(FULL, dt(2026, 3, 8, 12, 0)) == dt(2026, 3, 8, 7, 0)     # Sunday 03:00 EDT, just after
    assert schedule.last_boundary(FULL, dt(2026, 11, 1, 12, 0)) == dt(2026, 11, 1, 8, 0)   # 03:00 EST
    assert schedule.last_boundary(FULL, dt(2026, 11, 1, 7, 59)) == dt(2026, 10, 25, 7, 0)  # still before it
    assert schedule.slot_key(FULL, dt(2026, 11, 1, 8, 0)) == "slot:2026-W44"


@pytest.mark.parametrize("now, old_keys", [
    (dt(2026, 9, 20, 12, 0), {"reconcile": "slot:2026-09-20", "drift": "slot:2026-09-20", "full": "slot:2026-W38"}),
    (dt(2027, 1, 10, 12, 0), {"reconcile": "slot:2027-01-10", "drift": "slot:2027-01-10", "full": "slot:2027-W01"}),
])
def test_a_day_s_eastern_slot_has_the_key_the_utc_schedule_gave_that_day(now, old_keys):
    """The outbox keeps a done row's key, so the old 02:00Z run's row stops the same day's Eastern slot from running
    again the day the change deploys (the loop test proves it on real rows)."""
    for cadence in schedule.DEFAULT_CADENCES:
        assert schedule.slot_key(cadence, schedule.last_boundary(cadence, now)) == old_keys[cadence.kind]

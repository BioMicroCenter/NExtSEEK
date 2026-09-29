"""The sync schedule: which scheduled runs are owed right now (the sync design, section 12; R9).

Pure: no database, no Neo4j, no clock. The loop passes ``now`` and what it read from the run records, and gets back
the slots to put in the outbox; nothing here writes anything.

A cadence is a time of day, on one weekday or on every day. ``last_boundary`` is the last time that cadence came
round, and ``slot_key`` names it: a daily slot is its date (``slot:2026-09-15``), a weekly slot the ISO week of its
boundary (``slot:2026-W38``), which is what the outbox's ``(kind, key)`` uniqueness coalesces on, so a slot is
enqueued once however many passes see it.

A slot is due when no successful run that satisfies it started at or after its boundary. Only the run's start
counts, the moment it began reading MySQL. A full sync satisfies a reconcile, because it does everything a reconcile
does and more; nothing satisfies a drift check but a drift check, which reads and reports rather than writes.

**A missed slot runs at the next pass, and only once.** A loop that was down for two days finds the current
boundary unmet and asks for one run, not one per day it missed: the sync that run performs reads everything that
changed while the loop was down.

Every time is UTC. A naive datetime is read as UTC, an aware one is converted, so a caller cannot shift the schedule
by handing it a local clock.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

UTC = timezone.utc

MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY = range(7)   # as ``datetime.weekday()`` numbers them


@dataclass(frozen=True)
class Cadence:
    """When one kind of run comes round: ``weekday`` None for every day, else the day it runs on."""

    kind: str
    weekday: int | None
    hour: int
    minute: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError(f"not a graph_sync run kind: {self.kind!r}")
        if self.weekday is not None and not (isinstance(self.weekday, int) and MONDAY <= self.weekday <= SUNDAY):
            raise ValueError(f"not a weekday, Monday 0 to Sunday 6, or None for daily: {self.weekday!r}")
        if not (isinstance(self.hour, int) and 0 <= self.hour <= 23):
            raise ValueError(f"not an hour of the day: {self.hour!r}")
        if not (isinstance(self.minute, int) and 0 <= self.minute <= 59):
            raise ValueError(f"not a minute of the hour: {self.minute!r}")


# The spec's schedule (R9): the nightly reconcile, the drift check half an hour behind it, the weekly full sync.
DEFAULT_CADENCES: tuple[Cadence, ...] = (
    Cadence("reconcile", None, 2, 0),
    Cadence("drift", None, 2, 30),
    Cadence("full", SUNDAY, 3, 0),
)

# The kinds of run that satisfy a slot, its own always first. A full sync stands in for a reconcile; nothing stands
# in for a drift check.
_SATISFIED_BY: Mapping[str, tuple[str, ...]] = {"reconcile": ("reconcile", "full")}


@dataclass(frozen=True)
class Slot:
    """One scheduled run that is owed: the outbox row to write (``kind``, ``key``) and the boundary it stands for."""

    kind: str
    key: str
    boundary: datetime


def satisfied_by(kind: str) -> tuple[str, ...]:
    """The kinds of successful run that satisfy a slot of ``kind``, its own included."""
    return _SATISFIED_BY.get(kind, (kind,))


def _utc(when: datetime) -> datetime:
    """``when`` in UTC. A naive datetime is read as UTC: the loop, the run records and this schedule are all UTC."""
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


def last_boundary(cadence: Cadence, now: datetime) -> datetime:
    """The last time ``cadence`` came round at or before ``now``, in UTC.

    The whole span from one boundary to the next belongs to the earlier one, so a pass at any hour of the day asks
    for the slot that opened at the cadence's time.
    """
    now = _utc(now)
    at = now.replace(hour=cadence.hour, minute=cadence.minute, second=0, microsecond=0)
    if cadence.weekday is not None:
        at -= timedelta(days=(at.weekday() - cadence.weekday) % 7)
    if at > now:
        at -= timedelta(days=1 if cadence.weekday is None else 7)
    return at


def slot_key(cadence: Cadence, boundary: datetime) -> str:
    """The outbox key of that boundary's slot: ``slot:<date>`` daily, ``slot:<ISO year>-W<ISO week>`` weekly.

    The ISO week is the boundary's own, so a Sunday belongs to the week that began the Monday before it, and the
    turn of the ISO year is not the turn of the calendar year.
    """
    boundary = _utc(boundary)
    if cadence.weekday is None:
        return f"slot:{boundary.date().isoformat()}"
    iso_year, iso_week, _ = boundary.isocalendar()
    return f"slot:{iso_year:04d}-W{iso_week:02d}"


def _is_satisfied(kind: str, boundary: datetime, last_ok_started: Mapping[str, datetime | None]) -> bool:
    for other in satisfied_by(kind):
        started = last_ok_started.get(other)
        if started is not None and _utc(started) >= boundary:
            return True
    return False


def due_slots(cadences: Sequence[Cadence], now: datetime,
              last_ok_started: Mapping[str, datetime | None]) -> tuple[Slot, ...]:
    """The slots owed at ``now``, oldest boundary first, ties in the order the cadences were given.

    ``last_ok_started`` maps a run kind to the start of its last successful run, None or absent for a kind that has
    never finished one. A kind it names that no cadence is satisfied by is ignored.
    """
    now = _utc(now)
    owed = []
    for order, cadence in enumerate(cadences):
        boundary = last_boundary(cadence, now)
        if _is_satisfied(cadence.kind, boundary, last_ok_started):
            continue
        owed.append((boundary, order, Slot(cadence.kind, slot_key(cadence, boundary), boundary)))
    return tuple(slot for _, _, slot in sorted(owed, key=lambda owing: owing[:2]))

"""The graph_sync state: the outbox, the run records and the graph-write lock (the spec's sections 7.4 and 12).

Two dmac tables (``models_db.py``) and one MySQL named lock, all on the dmac (``default``) connection.

**The outbox.** One row per unit of work: ``kind`` says what to do, ``key`` what to do it to, ``payload`` a batch's
sample ids. ``(kind, key)`` is unique, so repeated hook writes coalesce and a scheduled slot is inserted once.

- ``enqueue`` inserts the row or resets it to pending (not done, no attempts, the new payload) and always moves
  ``enqueued_at`` forward, by a microsecond when the clock has not. It leaves a live claim and a back-off in place.
  ``delay_s`` holds the row back from any worker for that long, as a back-off would.
- ``claim_next`` is a compare-and-set over the row as it was read: two workers never hold one row, and the claim counts
  an attempt. A row at ``MAX_ATTEMPTS`` is dead: no worker claims it until a new write resets it.
- ``finish_done`` marks the row done only if ``enqueued_at`` is unchanged since the claim. A row re-enqueued meanwhile
  carries a write the worker may not have read, so it goes back to pending instead.
- ``finish_failed`` releases the claim with a back-off (``backoff_s``: 6 h for a full sync, 1 h otherwise).
- ``mark_done_before`` closes every row enqueued before a successful full sync started: that sync read them all. A
  row whose delay had not run out when the sync started is left open, since the sync may have read before its write.

``lease_expires_at`` is the time before which no worker may claim the row: a live claim's lease while ``claimed_by`` is
set, a failure's back-off after ``finish_failed`` clears it. An expired lease is claimable again, so a worker that dies
holding a row delays it by one lease and loses nothing.

**The run records.** ``start_run`` writes a ``running`` row and returns a handle whose ``finish`` records the outcome.
Both are best-effort: a missing table (production has no migration 0021) or any other database error logs a warning,
and the run goes on unrecorded. ``reap_abandoned`` ends the runs whose process died. The readers (``last_runs``,
``freshness``, ``outbox_summary``) raise instead, so the status endpoint can answer 503.

**The graph-write lock.** Every graph_sync write unit holds ``GET_LOCK('nextseek_graph_write', timeout)`` on the dmac
connection's MySQL session. MySQL releases it at ``RELEASE_LOCK`` or when the session ends, and counts nested
acquisitions by one session, so a full sync that calls the catalog sync holds it throughout. On SQLite (the unit-test
lane) the lock is a no-op behind the same function.

Every function that takes ``now`` uses the clock when it is omitted; the tests pass it.
"""
from __future__ import annotations

import logging
import math
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping

from django.conf import settings
from django.db import DatabaseError, IntegrityError, connections, transaction
from django.db.models import Count, F, Max, Q
from django.utils import timezone

from nextseek_api.graph_sync.models_db import GraphSyncOutbox, GraphSyncRun

log = logging.getLogger(__name__)

LOCK_NAME = "nextseek_graph_write"

# The outbox kinds and the key shapes each takes (the spec, section 12). A key the drain could not parse would sit in
# the outbox as a dead row, so enqueue refuses it instead.
_ONE_SAMPLE = re.compile(r"sample:\d+")
_BATCH = re.compile(r"batch:\S+")          # batch:<job>:<n>, batch:backfill:<n>; the ids are the payload
_TYPE = re.compile(r"type:\d+")
_ALL = re.compile(r"\*")
_SLOT = re.compile(r"slot:\S+")            # slot:<date> or slot:<ISO week>
KEY_RULES: Mapping[str, tuple[re.Pattern, ...]] = MappingProxyType({
    "samples": (_ONE_SAMPLE, _BATCH),
    "samples_of_type": (_TYPE,),
    "retire": (_ONE_SAMPLE,),
    "catalog": (_ALL,),
    "assay_map": (_ALL,),
    "protocol_map": (_ALL,),
    "isa": (_ALL,),
    "membership": (_ALL,),
    "reconcile": (_SLOT,),
    "full": (_SLOT,),
    "drift": (_SLOT,),
})
KINDS = tuple(KEY_RULES)
SLOT_KINDS = ("reconcile", "full", "drift")
KEY_CHARS = 191           # graph_sync_outbox.key
WORKER_CHARS = 255        # graph_sync_outbox.claimed_by
RUN_KIND_CHARS = 32       # graph_sync_run.kind

MAX_ATTEMPTS = 8          # claims before a row is dead
ERROR_CHARS = 4_000       # longest last_error kept
CLAIM_CANDIDATES = 20     # rows read per claim; a claim lost to another worker tries the next
_TICK = timedelta(microseconds=1)

# A claim's lease: long enough for the kind's work, so a live worker is never overtaken. The heavy kinds run as child
# processes of the loop, whose own timeout is shorter than these.
_LEASE_S = MappingProxyType({"full": 6 * 3600, "reconcile": 3 * 3600, "drift": 2 * 3600})
DEFAULT_LEASE_S = 30 * 60
_BACKOFF_S = MappingProxyType({"full": 6 * 3600})
DEFAULT_BACKOFF_S = 3600
# A run still ``running`` this long after its start lost its process.
_RUN_MAX_S = MappingProxyType({"full": 12 * 3600, "reconcile": 6 * 3600, "drift": 3 * 3600})
DEFAULT_RUN_MAX_S = 2 * 3600

RUN_STATUSES = ("ok", "failed", "refused", "abandoned", "drift")    # the outcomes ``finish`` records
# The spec's freshness rules, in seconds: a full sync within 8 days, a reconcile (or a full sync) within 26 hours,
# the oldest waiting outbox row within 1 hour.
DEFAULT_THRESHOLDS: Mapping[str, int] = MappingProxyType({"full": 8 * 86400, "reconcile": 26 * 3600, "outbox": 3600})


def lease_s(kind: str) -> int:
    return _LEASE_S.get(kind, DEFAULT_LEASE_S)


def backoff_s(kind: str) -> int:
    """The spec's back-off after a failure: 6 h for a full sync, 1 h otherwise."""
    return _BACKOFF_S.get(kind, DEFAULT_BACKOFF_S)


def run_max_s(kind: str) -> int:
    return _RUN_MAX_S.get(kind, DEFAULT_RUN_MAX_S)


def _db() -> str:
    return getattr(settings, "NEXTSEEK_DATABASE", "default")


def _outbox():
    return GraphSyncOutbox.objects.using(_db())


def _runs():
    return GraphSyncRun.objects.using(_db())


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _age_s(now: datetime, then: datetime) -> float:
    return round((now - then).total_seconds(), 3)


# --- the outbox ----------------------------------------------------------------------------------

def check_item(kind: str, key: str, payload: Any = None) -> None:
    """Raise ValueError unless ``kind`` is an outbox kind, ``key`` has one of its shapes and ``payload`` fits the key:
    a ``batch:`` key carries its sample ids as a list of ints, every other key carries none."""
    if not isinstance(kind, str) or kind not in KEY_RULES:
        raise ValueError(f"not a graph_sync outbox kind: {kind!r}")
    if not isinstance(key, str) or len(key) > KEY_CHARS:
        raise ValueError(f"not a graph_sync outbox key: {key!r}")
    if not any(rule.fullmatch(key) for rule in KEY_RULES[kind]):
        raise ValueError(f"key {key!r} does not fit kind {kind!r}")
    if key.startswith("batch:"):
        if not isinstance(payload, list) or any(type(i) is not int for i in payload):
            raise ValueError(f"{key!r} needs its sample ids as a list of ints, not {payload!r}")
    elif payload is not None:
        raise ValueError(f"{key!r} carries no payload, not {payload!r}")


def _reopen(kind: str, key: str, payload: Any, now: datetime, not_before: datetime | None) -> bool:
    """Reset an existing row to pending, under its row lock. False when there is no such row.

    ``not_before`` pushes the row's back-off out to that time unless a longer one is already running, and is ignored
    for a row under a live claim: ``finish_done`` finds its claim by the lease, so moving it would take the row from
    its worker."""
    found = (_outbox().select_for_update().filter(kind=kind, key=key)
             .values("id", "enqueued_at", "claimed_by", "lease_expires_at").first())
    if found is None:
        return False
    stamp = max(now, found["enqueued_at"] + _TICK)
    fields = {"enqueued_at": stamp, "done_at": None, "attempts": 0, "payload": payload}
    lease = found["lease_expires_at"]
    live_claim = found["claimed_by"] is not None and lease is not None and lease > now
    if not_before is not None and not live_claim and (lease is None or lease < not_before):
        fields["lease_expires_at"] = not_before
    _outbox().filter(pk=found["id"]).update(**fields)
    return True


def enqueue(kind: str, key: str, payload: Any = None, *, now: datetime | None = None, delay_s: float = 0) -> None:
    """Insert the row, or reset it to pending with ``payload``. Raises ValueError on a malformed item and a database
    error as it comes; ``hooks.enqueue`` is the one that never raises.

    ``delay_s`` keeps the row from being claimed for that many seconds, through the same ``lease_expires_at`` a
    back-off uses: for work that must not run before the write it follows has landed. The row counts as pending
    meanwhile. A longer back-off already on the row stands, and so does a live claim.

    Inside a caller's transaction on the dmac database the row commits with it, and a failure rolls back to this
    function's own savepoint. The existing row is read first, so two writers of an existing key queue on its row lock
    instead of both failing an insert."""
    check_item(kind, key, payload)
    now = now or timezone.now()
    not_before = now + timedelta(seconds=delay_s) if delay_s and delay_s > 0 else None
    db = _db()
    with transaction.atomic(using=db):
        if _reopen(kind, key, payload, now, not_before):
            return
        try:
            with transaction.atomic(using=db):
                _outbox().create(kind=kind, key=key, payload=payload, enqueued_at=now, lease_expires_at=not_before)
            return
        except IntegrityError:
            # Another writer inserted it since the read above.
            if not _reopen(kind, key, payload, now, not_before):
                raise


def ensure_slot(kind: str, key: str, *, now: datetime | None = None) -> bool:
    """Insert a scheduled slot once. True when this call inserted it; an existing row, done or not, is left alone."""
    check_item(kind, key, None)
    # The loop asks on every pass; a read is cheaper than a failed insert, which on MySQL also locks the row.
    if _outbox().filter(kind=kind, key=key).exists():
        return False
    try:
        with transaction.atomic(using=_db()):
            _outbox().create(kind=kind, key=key, enqueued_at=now or timezone.now())
        return True
    except IntegrityError:
        return False


@dataclass(frozen=True)
class Claim:
    """A row as its worker claimed it. ``worker_id`` and ``lease_expires_at`` identify the claim; ``enqueued_at`` is
    what ``finish_done`` compares."""

    id: int
    kind: str
    key: str
    payload: Any
    enqueued_at: datetime
    attempts: int
    worker_id: str
    lease_expires_at: datetime


def _candidates(now: datetime, kinds: Iterable[str] | None, limit: int) -> list[dict]:
    """Claimable rows, oldest first: not done, below the attempt limit, and no lease or back-off still running."""
    qs = (_outbox().filter(done_at__isnull=True, attempts__lt=MAX_ATTEMPTS)
          .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now)))
    if kinds is not None:
        qs = qs.filter(kind__in=list(kinds))
    return list(qs.order_by("enqueued_at", "id")
                .values("id", "kind", "claimed_by", "lease_expires_at", "attempts")[:limit])


def claim_next(worker_id: str, *, now: datetime | None = None, kinds: Iterable[str] | None = None) -> Claim | None:
    """Claim the oldest claimable row (of ``kinds``, when given) for ``worker_id``, or return None.

    The write is a compare-and-set on the row as it was read (its claim, lease and attempts), so a row another worker
    took in between is skipped. The claim is read back after the write: a re-enqueue that lands before the write is
    in the Claim, and one that lands after it moves ``enqueued_at`` and fails ``finish_done``."""
    if not worker_id or len(worker_id) > WORKER_CHARS:
        raise ValueError(f"not a worker id: {worker_id!r}")
    now = now or timezone.now()
    for c in _candidates(now, kinds, CLAIM_CANDIDATES):
        lease = now + timedelta(seconds=lease_s(c["kind"]))
        won = _outbox().filter(
            pk=c["id"], done_at__isnull=True, attempts=c["attempts"],
            claimed_by=c["claimed_by"], lease_expires_at=c["lease_expires_at"],
        ).update(claimed_by=worker_id, lease_expires_at=lease, attempts=F("attempts") + 1)
        if won != 1:
            continue
        got = (_outbox().filter(pk=c["id"], claimed_by=worker_id, lease_expires_at=lease)
               .values("id", "kind", "key", "payload", "enqueued_at", "attempts").first())
        if got is not None:
            return Claim(worker_id=worker_id, lease_expires_at=lease, **got)
    return None


def _held(claim: Claim):
    return _outbox().filter(pk=claim.id, claimed_by=claim.worker_id, lease_expires_at=claim.lease_expires_at)


def finish_done(claim: Claim, *, now: datetime | None = None) -> bool:
    """Mark the row done if this worker still holds it and nothing re-enqueued it since the claim. Otherwise release
    the claim, so a re-enqueued row goes back to pending, and return False."""
    done = _held(claim).filter(enqueued_at=claim.enqueued_at, done_at__isnull=True).update(
        done_at=now or timezone.now(), claimed_by=None, lease_expires_at=None, last_error=None)
    if done == 1:
        return True
    _held(claim).update(claimed_by=None, lease_expires_at=None)
    return False


def _error_text(error: BaseException | str) -> str:
    text = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
    return text[:ERROR_CHARS]


def finish_failed(claim: Claim, error: BaseException | str, backoff_s: float, *,
                  now: datetime | None = None) -> bool:
    """Release the claim with ``error`` recorded; no worker claims the row again for ``backoff_s`` seconds. The
    back-off applies even when the row was re-enqueued meanwhile: what failed will likely fail again. False when this
    worker no longer holds the row."""
    until = (now or timezone.now()) + timedelta(seconds=max(0.0, backoff_s))
    return _held(claim).update(claimed_by=None, lease_expires_at=until, last_error=_error_text(error)) == 1


def mark_done_before(ts: datetime, *, kinds: Iterable[str] | None = None, now: datetime | None = None) -> int:
    """Mark done every open row enqueued before ``ts`` (of ``kinds``, when given), claimed or not, and return how many.
    A successful full sync passes its start: it read everything those rows ask for. A worker holding one of them then
    finds it done, and ``finish_done`` leaves it so.

    Except a row still held back by its ``delay_s`` at ``ts``: its writer could not tell whether its write had landed,
    and the sync may have read MySQL before it did. A delay is stored as a back-off is, in ``lease_expires_at`` with
    no claim, and told apart by the attempts: none since the row was enqueued, where a failure's back-off follows a
    claim that counted one."""
    qs = _outbox().filter(done_at__isnull=True, enqueued_at__lt=ts)
    qs = qs.filter(Q(claimed_by__isnull=False) | Q(attempts__gt=0) | Q(lease_expires_at__isnull=True)
                   | Q(lease_expires_at__lte=ts))
    if kinds is not None:
        qs = qs.filter(kind__in=list(kinds))
    return qs.update(done_at=now or timezone.now(), claimed_by=None, lease_expires_at=None, last_error=None)


# --- run records ---------------------------------------------------------------------------------

class RunHandle:
    """A run's record. ``id`` is None when the run could not be recorded; ``finish`` is then a no-op."""

    def __init__(self, run_id: int | None, kind: str, trigger: str, started_at: datetime):
        self.id = run_id
        self.kind = kind
        self.trigger = trigger
        self.started_at = started_at

    def finish(self, status: str, counts: dict | None = None, drift: dict | None = None,
               watermark_from: Any = None, watermark_to: Any = None, *, now: datetime | None = None) -> bool:
        """Record the outcome; True when it was written. ``counts`` is stored with the run's ``trigger`` added. A run
        ``reap_abandoned`` marked still records its real outcome here. Raises ValueError on a status that is not an
        outcome; any failure to write is logged and swallowed."""
        if status not in RUN_STATUSES:
            raise ValueError(f"not a graph_sync run outcome: {status!r}; one of {', '.join(RUN_STATUSES)}")
        if self.id is None:
            return False
        fields = {
            "status": status,
            "finished_at": now or timezone.now(),
            "counts_json": {**(counts or {}), "trigger": self.trigger},
            "drift_json": drift,
            "watermark_from": None if watermark_from is None else str(watermark_from),
            "watermark_to": None if watermark_to is None else str(watermark_to),
        }
        try:
            with transaction.atomic(using=_db()):
                _runs().filter(pk=self.id).update(**fields)
            return True
        except (DatabaseError, TypeError, ValueError) as exc:
            # TypeError and ValueError: counts or drift that JSON cannot hold. The run's own outcome stands.
            log.warning("graph_sync_run: could not record the end of %s run %s: %s", self.kind, self.id, exc)
            return False


def start_run(kind: str, *, trigger: str, now: datetime | None = None) -> RunHandle:
    """Record a ``running`` row for a run of ``kind`` (``full``, ``catalog``, ``reconcile``, ``drift``, ``samples``)
    started by ``trigger`` (the command, the loop, batch upload). Best-effort: a missing table or any other database
    error logs a warning and returns a handle that records nothing."""
    if not isinstance(kind, str) or not kind or len(kind) > RUN_KIND_CHARS:
        raise ValueError(f"not a graph_sync run kind: {kind!r}")
    now = now or timezone.now()
    try:
        with transaction.atomic(using=_db()):
            run = _runs().create(kind=kind, started_at=now, status="running", counts_json={"trigger": trigger})
    except DatabaseError as exc:
        log.warning("graph_sync_run is not available, so this %s run is not recorded: %s", kind, exc)
        return RunHandle(None, kind, trigger, now)
    return RunHandle(run.pk, kind, trigger, now)


def reap_abandoned(*, now: datetime | None = None) -> int:
    """Mark ``abandoned`` every run still ``running`` longer than its kind allows (``run_max_s``): its process died
    before it could finish. Returns how many."""
    now = now or timezone.now()
    marked = 0
    for kind in set(_runs().filter(status="running").values_list("kind", flat=True)):
        cutoff = now - timedelta(seconds=run_max_s(kind))
        marked += _runs().filter(kind=kind, status="running", started_at__lt=cutoff).update(
            status="abandoned", finished_at=now)
    return marked


def _run_dict(run: GraphSyncRun) -> dict:
    return {
        "id": run.pk,
        "kind": run.kind,
        "status": run.status,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "watermark_from": run.watermark_from,
        "watermark_to": run.watermark_to,
        "counts": run.counts_json,
        "drift": run.drift_json,
    }


def last_runs() -> dict[str, dict]:
    """The latest run of each kind, whatever its status, keyed by kind."""
    latest = _runs().order_by().values("kind").annotate(last=Max("id")).values_list("last", flat=True)
    return {run.kind: _run_dict(run) for run in _runs().filter(pk__in=list(latest))}


# --- freshness and the outbox summary ------------------------------------------------------------

def _last_ok(kinds: tuple[str, ...]) -> dict | None:
    return (_runs().filter(kind__in=kinds, status="ok").order_by("-started_at", "-id")
            .values("kind", "started_at", "finished_at").first())


def _run_freshness(run: dict | None, now: datetime, threshold: int) -> dict:
    if run is None:
        return {"status": "never", "satisfied_by": None, "last_ok_started_at": None, "last_ok_finished_at": None,
                "age_s": None, "threshold_s": threshold}
    age = _age_s(now, run["started_at"])
    return {"status": "ok" if age <= threshold else "stale", "satisfied_by": run["kind"],
            "last_ok_started_at": _iso(run["started_at"]), "last_ok_finished_at": _iso(run["finished_at"]),
            "age_s": age, "threshold_s": threshold}


def freshness(*, now: datetime | None = None, thresholds: Mapping[str, int] = DEFAULT_THRESHOLDS) -> dict[str, dict]:
    """Each job's freshness: ``full`` and ``reconcile`` (``ok``, ``stale`` or ``never``) and ``outbox`` (``ok`` or
    ``stale``). A run counts from its start, the moment it began reading MySQL, and only when it ended ``ok``. A full
    sync counts for the reconcile: it does everything a reconcile does. ``thresholds`` overrides some of
    ``DEFAULT_THRESHOLDS``."""
    now = now or timezone.now()
    limits = {**DEFAULT_THRESHOLDS, **thresholds}
    oldest = outbox_summary(now=now)["oldest_pending"]
    age = None if oldest is None else oldest["age_s"]
    return {
        "full": _run_freshness(_last_ok(("full",)), now, limits["full"]),
        "reconcile": _run_freshness(_last_ok(("reconcile", "full")), now, limits["reconcile"]),
        "outbox": {"status": "ok" if age is None or age <= limits["outbox"] else "stale",
                   "oldest_enqueued_at": None if oldest is None else oldest["enqueued_at"],
                   "age_s": age, "threshold_s": limits["outbox"]},
    }


def _by_kind(qs) -> dict[str, int]:
    return dict(qs.order_by().values_list("kind").annotate(n=Count("id")))


def outbox_summary(*, now: datetime | None = None) -> dict:
    """The open rows by kind: ``pending`` (claimable now or later, those in a worker's hands included), ``dead`` (at
    the attempt limit and not in a worker's hands) and ``claimed`` (under a live lease); and ``oldest_pending``, the
    oldest open row no worker holds, dead rows included: a dead row's work never happened."""
    now = now or timezone.now()
    live = Q(claimed_by__isnull=False, lease_expires_at__gt=now)
    not_live = Q(claimed_by__isnull=True) | Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now)
    open_rows = _outbox().filter(done_at__isnull=True)
    oldest = (open_rows.filter(not_live).order_by("enqueued_at", "id")
              .values("kind", "key", "enqueued_at").first())
    return {
        "pending": _by_kind(open_rows.filter(live | Q(attempts__lt=MAX_ATTEMPTS))),
        "dead": _by_kind(open_rows.filter(not_live, attempts__gte=MAX_ATTEMPTS)),
        "claimed": _by_kind(open_rows.filter(live)),
        "oldest_pending": None if oldest is None else {
            "kind": oldest["kind"], "key": oldest["key"], "enqueued_at": _iso(oldest["enqueued_at"]),
            "age_s": _age_s(now, oldest["enqueued_at"])},
        "max_attempts": MAX_ATTEMPTS,
    }


# --- the graph-write lock ------------------------------------------------------------------------

@contextmanager
def graph_write_lock(timeout_s: float) -> Iterator[bool]:
    """Hold the graph-write lock for the ``with`` block; yields whether it was acquired within ``timeout_s``.

    MySQL: ``GET_LOCK`` on the dmac connection, waiting whole seconds (rounded up, never negative: MySQL reads a
    negative timeout as wait forever), and ``RELEASE_LOCK`` on exit when it was acquired, the block raising or not.
    On a timeout (``GET_LOCK`` answers 0, or NULL on an error) the block runs with False and must not write. SQLite
    yields True and issues nothing."""
    conn = connections[_db()]
    if conn.vendor != "mysql":
        yield True
        return
    wait = max(0, math.ceil(timeout_s))
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, %s)", [LOCK_NAME, wait])
        answer = cur.fetchone()
    if not answer or answer[0] != 1:
        log.info("graph-write lock %s not acquired within %s s", LOCK_NAME, wait)
        yield False
        return
    try:
        yield True
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", [LOCK_NAME])
                cur.fetchone()
        except DatabaseError as exc:
            log.warning("could not release the graph-write lock %s (MySQL releases it when the session ends): %s",
                        LOCK_NAME, exc)

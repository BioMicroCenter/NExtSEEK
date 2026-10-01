"""The outbox, run records and graph-write lock (nextseek_api/graph_sync/state.py; the spec's sections 7.4 and 12).

Runs on the SQLite test settings: the rows go to the in-memory ``default`` database. Every time is passed in, so no
test depends on the clock. The MySQL half of the lock is checked on a stub connection that records its statements.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext

from nextseek_api.graph_sync import loop as sync_loop, run as sync_run, state, writer
from nextseek_api.graph_sync.models_db import GraphSyncOutbox, GraphSyncRun

T0 = datetime(2026, 9, 15, 2, 0, tzinfo=dt_timezone.utc)


def at(**delta) -> datetime:
    return T0 + timedelta(**delta)


def row(kind: str, key: str) -> GraphSyncOutbox:
    return GraphSyncOutbox.objects.get(kind=kind, key=key)


def drop_table(name: str) -> None:
    """Drop a table inside the test's transaction; the rollback at the end of the test restores it."""
    with connection.cursor() as cur:
        cur.execute(f'DROP TABLE "{name}"')


# --- enqueue and its key rules ---------------------------------------------------------------------

@pytest.mark.django_db
def test_enqueue_inserts_a_pending_row():
    state.enqueue("samples", "sample:7", now=T0)
    r = row("samples", "sample:7")
    assert (r.enqueued_at, r.done_at, r.claimed_by, r.attempts, r.payload) == (T0, None, None, 0, None)


@pytest.mark.django_db
def test_enqueue_coalesces_repeated_writes_into_one_row():
    state.enqueue("catalog", "*", now=T0)
    state.enqueue("catalog", "*", now=at(seconds=5))
    state.enqueue("catalog", "*", now=at(seconds=9))
    assert GraphSyncOutbox.objects.filter(kind="catalog").count() == 1
    assert row("catalog", "*").enqueued_at == at(seconds=9)


@pytest.mark.django_db
def test_enqueue_resets_a_done_row_to_pending():
    state.enqueue("samples_of_type", "type:3", now=T0)
    claim = state.claim_next("w1", now=at(seconds=1))
    assert state.finish_done(claim, now=at(seconds=2))
    assert row("samples_of_type", "type:3").done_at is not None

    state.enqueue("samples_of_type", "type:3", now=at(minutes=5))
    r = row("samples_of_type", "type:3")
    assert (r.done_at, r.attempts, r.enqueued_at) == (None, 0, at(minutes=5))
    assert state.claim_next("w1", now=at(minutes=6)).key == "type:3"


@pytest.mark.django_db
def test_enqueue_replaces_the_payload_of_a_batch_row():
    state.enqueue("samples", "batch:job-1:0", [3, 1, 2], now=T0)
    state.enqueue("samples", "batch:job-1:0", [3, 1, 2, 9], now=at(seconds=1))
    assert row("samples", "batch:job-1:0").payload == [3, 1, 2, 9]


@pytest.mark.django_db
def test_enqueue_moves_enqueued_at_forward_even_when_the_clock_does_not():
    """finish_done tells a re-enqueued row by its changed enqueued_at, so the stamp must move even when two writes
    read the same clock value (or a clock behind the stored one)."""
    state.enqueue("isa", "*", now=T0)
    state.enqueue("isa", "*", now=T0)
    assert row("isa", "*").enqueued_at > T0
    state.enqueue("isa", "*", now=at(seconds=-30))
    assert row("isa", "*").enqueued_at > T0


# --- enqueue with a delay: work that must not run before the write it follows has landed -----------

@pytest.mark.django_db
def test_a_delayed_row_is_pending_but_not_claimable_until_its_delay_passes():
    state.enqueue("retire", "sample:7", now=T0, delay_s=300)
    r = row("retire", "sample:7")
    assert (r.done_at, r.claimed_by, r.attempts, r.lease_expires_at) == (None, None, 0, at(seconds=300))
    assert state.claim_next("w1", now=at(seconds=299)) is None
    claim = state.claim_next("w1", now=at(seconds=300))
    assert claim is not None and claim.key == "sample:7" and claim.attempts == 1


@pytest.mark.django_db
def test_a_delayed_row_counts_as_pending_in_the_outbox_summary():
    """The lane's wait_for_drain reads ``pending``: a delayed row must keep it waiting, not look drained."""
    state.enqueue("retire", "sample:7", now=T0, delay_s=300)
    assert state.outbox_summary(now=at(seconds=1))["pending"] == {"retire": 1}


@pytest.mark.django_db
def test_a_delay_reopens_a_done_row_behind_the_delay():
    state.enqueue("retire", "sample:7", now=T0)
    assert state.finish_done(state.claim_next("w1", now=at(seconds=1)), now=at(seconds=2))

    state.enqueue("retire", "sample:7", now=at(minutes=5), delay_s=300)
    r = row("retire", "sample:7")
    assert (r.done_at, r.attempts, r.lease_expires_at) == (None, 0, at(minutes=10))
    assert state.claim_next("w1", now=at(minutes=9)) is None
    assert state.claim_next("w1", now=at(minutes=10)).key == "sample:7"


@pytest.mark.django_db
def test_a_delay_never_shortens_a_longer_back_off():
    state.enqueue("retire", "sample:7", now=T0)
    claim = state.claim_next("w1", now=at(seconds=1))
    state.finish_failed(claim, "neo4j unavailable", 3600, now=at(seconds=2))

    state.enqueue("retire", "sample:7", now=at(seconds=3), delay_s=300)
    assert row("retire", "sample:7").lease_expires_at == at(seconds=2, hours=1)


@pytest.mark.django_db
def test_a_delay_leaves_a_live_claim_alone():
    """Moving the lease of a claimed row would take the row from its worker (``finish_done`` matches the lease)."""
    state.enqueue("retire", "sample:7", now=T0)
    claim = state.claim_next("w1", now=at(seconds=1))

    state.enqueue("retire", "sample:7", now=at(seconds=2), delay_s=300)
    r = row("retire", "sample:7")
    assert (r.claimed_by, r.lease_expires_at) == ("w1", claim.lease_expires_at)


@pytest.mark.django_db
def test_no_delay_changes_nothing_about_when_a_row_is_claimable():
    state.enqueue("retire", "sample:7", now=T0, delay_s=0)
    assert row("retire", "sample:7").lease_expires_at is None
    assert state.claim_next("w1", now=T0).key == "sample:7"


@pytest.mark.parametrize("kind, key, payload", [
    ("samples", "sample:7", None),
    ("samples", "batch:job-1:0", [1, 2]),
    ("samples", "batch:backfill:3", []),
    ("samples_of_type", "type:12", None),
    ("retire", "sample:7", None),
    ("catalog", "*", None),
    ("assay_map", "*", None),
    ("protocol_map", "*", None),
    ("isa", "*", None),
    ("membership", "*", None),
    ("reconcile", "slot:2026-09-15", None),
    ("full", "slot:2026-W38", None),
    ("drift", "slot:2026-09-15", None),
])
def test_every_kind_and_key_of_the_spec_is_accepted(kind, key, payload):
    state.check_item(kind, key, payload)


@pytest.mark.parametrize("kind, key, payload", [
    ("sample", "sample:7", None),               # not a kind
    ("samples", "sample:None", None),           # a missing id
    ("samples", "sample:7", [7]),               # a payload on a single-sample key
    ("samples", "batch:job-1:0", None),         # a batch key without its ids
    ("samples", "batch:job-1:0", [1, "2"]),     # an id that is not an int
    ("samples", "batch:job-1:0", [True]),       # a bool is not an id
    ("samples", "batch:", [1]),                 # a batch key without a name
    ("samples_of_type", "type:x", None),
    ("retire", "batch:job-1:0", [1]),           # retire takes one sample at a time
    ("catalog", "all", None),
    ("full", "2026-W38", None),
    ("samples", "sample:" + "9" * 200, None),   # longer than the column
])
def test_a_malformed_item_is_refused(kind, key, payload):
    with pytest.raises(ValueError):
        state.check_item(kind, key, payload)


@pytest.mark.django_db
def test_enqueue_raises_on_a_malformed_item_and_writes_nothing():
    with pytest.raises(ValueError):
        state.enqueue("samples", "sample:None")
    assert GraphSyncOutbox.objects.count() == 0


@pytest.mark.django_db
def test_enqueue_raises_a_database_error():
    """state.enqueue raises; hooks.enqueue is the one that swallows (test_graph_sync_hooks.py)."""
    drop_table("graph_sync_outbox")
    with pytest.raises(DatabaseError):
        state.enqueue("catalog", "*")


# --- slots -----------------------------------------------------------------------------------------

@pytest.mark.django_db
def test_a_slot_inserts_once():
    assert state.ensure_slot("reconcile", "slot:2026-09-15", now=T0) is True
    assert state.ensure_slot("reconcile", "slot:2026-09-15", now=at(minutes=1)) is False
    assert GraphSyncOutbox.objects.filter(kind="reconcile").count() == 1
    assert row("reconcile", "slot:2026-09-15").enqueued_at == T0


@pytest.mark.django_db
def test_a_done_slot_is_not_reopened():
    state.ensure_slot("full", "slot:2026-W38", now=T0)
    claim = state.claim_next("loop", now=at(seconds=1))
    state.finish_done(claim, now=at(minutes=20))
    assert state.ensure_slot("full", "slot:2026-W38", now=at(hours=1)) is False
    assert row("full", "slot:2026-W38").done_at == at(minutes=20)


@pytest.mark.django_db
def test_an_existing_slot_costs_a_read_and_no_insert():
    """The loop asks for every due slot on every pass, so an existing slot must not cost a failed insert."""
    state.ensure_slot("drift", "slot:2026-09-15", now=T0)
    with CaptureQueriesContext(connection) as queries:
        assert state.ensure_slot("drift", "slot:2026-09-15", now=at(seconds=5)) is False
    sql = [q["sql"].upper() for q in queries.captured_queries]
    assert len(sql) == 1 and sql[0].startswith("SELECT")


def test_ensure_slot_refuses_a_malformed_key():
    with pytest.raises(ValueError):
        state.ensure_slot("full", "2026-W38")


# --- claims ----------------------------------------------------------------------------------------

@pytest.mark.django_db
def test_a_claim_is_exclusive_and_counts_an_attempt():
    state.enqueue("samples", "sample:7", now=T0)
    first = state.claim_next("w1", now=at(seconds=1))
    assert first is not None
    assert (first.kind, first.key, first.worker_id, first.attempts) == ("samples", "sample:7", "w1", 1)
    assert first.enqueued_at == T0
    assert state.claim_next("w2", now=at(seconds=2)) is None
    r = row("samples", "sample:7")
    assert (r.claimed_by, r.attempts, r.lease_expires_at) == ("w1", 1, first.lease_expires_at)
    assert first.lease_expires_at > at(seconds=1)


@pytest.mark.django_db
def test_a_claim_carries_the_payload():
    state.enqueue("samples", "batch:job-1:0", [5, 6], now=T0)
    assert state.claim_next("w1", now=at(seconds=1)).payload == [5, 6]


@pytest.mark.django_db
def test_claims_come_oldest_first():
    state.enqueue("catalog", "*", now=at(seconds=2))
    state.enqueue("samples", "sample:1", now=T0)
    state.enqueue("isa", "*", now=at(seconds=1))
    keys = [state.claim_next("w", now=at(seconds=10)).kind for _ in range(3)]
    assert keys == ["samples", "isa", "catalog"]
    assert state.claim_next("w", now=at(seconds=10)) is None


@pytest.mark.django_db
def test_a_claim_can_be_limited_to_some_kinds():
    state.enqueue("full", "slot:2026-W38", now=T0)
    state.enqueue("samples", "sample:1", now=at(seconds=1))
    claim = state.claim_next("w", now=at(seconds=5), kinds=["samples", "retire"])
    assert claim.key == "sample:1"
    assert state.claim_next("w", now=at(seconds=5), kinds=["samples", "retire"]) is None


@pytest.mark.django_db
def test_a_claim_loses_to_a_row_taken_between_its_read_and_its_write(monkeypatch):
    """The claim is a compare-and-set: a second worker that read the same pending row cannot take it too."""
    state.enqueue("samples", "sample:1", now=T0)
    state.enqueue("samples", "sample:2", now=at(seconds=1))
    real = state._candidates

    def raced(*args, **kwargs):
        found = real(*args, **kwargs)
        # Another worker claims the oldest row after this worker read it.
        GraphSyncOutbox.objects.filter(key="sample:1").update(
            claimed_by="other", lease_expires_at=at(hours=1), attempts=1)
        return found

    monkeypatch.setattr(state, "_candidates", raced)
    claim = state.claim_next("w1", now=at(seconds=5))
    assert claim.key == "sample:2"
    assert row("samples", "sample:1").claimed_by == "other"


@pytest.mark.django_db
def test_an_expired_lease_is_claimable_again():
    state.enqueue("samples", "sample:7", now=T0)
    first = state.claim_next("w1", now=T0)
    assert state.claim_next("w2", now=first.lease_expires_at - timedelta(seconds=1)) is None
    second = state.claim_next("w2", now=first.lease_expires_at)
    assert second is not None and second.worker_id == "w2" and second.attempts == 2
    # The first worker lost its lease: it can finish the row neither way.
    assert state.finish_done(first, now=first.lease_expires_at) is False
    assert state.finish_failed(first, "late", 60, now=first.lease_expires_at) is False
    assert row("samples", "sample:7").claimed_by == "w2"


@pytest.mark.django_db
def test_heavy_kinds_hold_a_longer_lease():
    state.enqueue("samples", "sample:7", now=T0)
    state.ensure_slot("full", "slot:2026-W38", now=T0)
    light = state.claim_next("w", now=at(seconds=1), kinds=["samples"])
    heavy = state.claim_next("w", now=at(seconds=1), kinds=["full"])
    assert heavy.lease_expires_at - at(seconds=1) == timedelta(seconds=state.lease_s("full"))
    assert light.lease_expires_at - at(seconds=1) == timedelta(seconds=state.lease_s("samples"))
    assert state.lease_s("full") > state.lease_s("samples")


# --- finishing -------------------------------------------------------------------------------------

@pytest.mark.django_db
def test_finish_done_marks_the_row_done_and_clears_the_claim():
    state.enqueue("samples", "sample:7", now=T0)
    claim = state.claim_next("w1", now=at(seconds=1))
    assert state.finish_done(claim, now=at(seconds=3)) is True
    r = row("samples", "sample:7")
    assert (r.done_at, r.claimed_by, r.lease_expires_at, r.last_error) == (at(seconds=3), None, None, None)
    assert state.claim_next("w1", now=at(seconds=4)) is None


@pytest.mark.django_db
def test_a_failure_backs_off():
    state.enqueue("assay_map", "*", now=T0)
    claim = state.claim_next("w1", now=at(seconds=1))
    assert state.finish_failed(claim, RuntimeError("neo4j unavailable"), 3600, now=at(seconds=2)) is True
    r = row("assay_map", "*")
    assert (r.done_at, r.claimed_by, r.attempts) == (None, None, 1)
    assert "neo4j unavailable" in r.last_error
    assert state.claim_next("w1", now=at(seconds=2, minutes=59)) is None
    again = state.claim_next("w1", now=at(seconds=2, hours=1))
    assert again is not None and again.attempts == 2


def test_the_default_back_off_is_six_hours_for_a_full_sync_and_one_hour_otherwise():
    assert state.backoff_s("full") == 6 * 3600
    assert state.backoff_s("reconcile") == 3600
    assert state.backoff_s("samples") == 3600


@pytest.mark.django_db
def test_a_long_error_is_truncated():
    state.enqueue("catalog", "*", now=T0)
    claim = state.claim_next("w1", now=T0)
    state.finish_failed(claim, "x" * 100_000, 60, now=T0)
    assert len(row("catalog", "*").last_error) <= state.ERROR_CHARS


@pytest.mark.django_db
def test_a_row_re_enqueued_while_claimed_stays_pending_after_finish_done():
    """The worker read its sources before the new write: marking the row done would lose that write."""
    state.enqueue("samples", "sample:7", now=T0)
    claim = state.claim_next("w1", now=at(seconds=1))
    state.enqueue("samples", "sample:7", now=at(seconds=2))
    # No other worker takes the row while the first still holds its lease.
    assert state.claim_next("w2", now=at(seconds=3)) is None

    assert state.finish_done(claim, now=at(seconds=4)) is False
    r = row("samples", "sample:7")
    assert (r.done_at, r.claimed_by, r.lease_expires_at) == (None, None, None)
    again = state.claim_next("w2", now=at(seconds=5))
    assert again is not None and again.enqueued_at == at(seconds=2)
    assert state.finish_done(again, now=at(seconds=6)) is True


@pytest.mark.django_db
def test_a_row_at_the_attempt_limit_is_not_claimed():
    state.enqueue("catalog", "*", now=T0)
    now = T0
    for _ in range(state.MAX_ATTEMPTS):
        claim = state.claim_next("w1", now=now)
        assert claim is not None
        state.finish_failed(claim, "boom", 60, now=now)
        now += timedelta(seconds=61)
    assert row("catalog", "*").attempts == state.MAX_ATTEMPTS
    assert state.claim_next("w1", now=now + timedelta(days=30)) is None
    # A new write gives the row a new budget.
    state.enqueue("catalog", "*", now=now)
    assert state.claim_next("w1", now=now + timedelta(days=30)) is not None


# --- mark_done_before ------------------------------------------------------------------------------

@pytest.mark.django_db
def test_mark_done_before_leaves_later_rows_alone():
    state.enqueue("samples", "sample:1", now=T0)
    state.enqueue("catalog", "*", now=at(minutes=1))
    claimed = state.claim_next("w1", now=at(minutes=2), kinds=["catalog"])
    state.enqueue("samples", "sample:2", now=at(minutes=10))

    assert state.mark_done_before(at(minutes=5)) == 2
    assert row("samples", "sample:1").done_at is not None
    held = row("catalog", "*")
    assert (held.done_at is not None, held.claimed_by, held.lease_expires_at) == (True, None, None)
    assert row("samples", "sample:2").done_at is None
    # The worker that held the catalog row finds it done by the full sync and does not reopen it.
    assert state.finish_done(claimed, now=at(minutes=11)) is False
    assert row("catalog", "*").done_at is not None


@pytest.mark.django_db
def test_mark_done_before_can_be_limited_to_some_kinds():
    state.enqueue("samples", "sample:1", now=T0)
    state.enqueue("drift", "slot:2026-09-15", now=T0)
    assert state.mark_done_before(at(minutes=5), kinds=["samples"]) == 1
    assert row("drift", "slot:2026-09-15").done_at is None


@pytest.mark.django_db
def test_mark_done_before_spares_a_row_still_inside_its_delay_when_the_sync_started():
    """A writer that cannot tell whether its write has landed enqueues with a delay (the samples proxy's retire after
    SEEK gave no answer to a delete). A full sync that started before that delay ran out may have read MySQL before
    the write landed, so it did not cover the row: closing it left the deleted sample's node in the graph."""
    state.enqueue("retire", "sample:5", now=T0, delay_s=300)

    assert state.mark_done_before(at(seconds=10), now=at(minutes=30)) == 0
    r = row("retire", "sample:5")
    assert r.done_at is None and r.lease_expires_at == at(seconds=300)
    assert state.claim_next("w1", now=at(minutes=30)).key == "sample:5"


@pytest.mark.django_db
def test_mark_done_before_closes_a_delayed_row_whose_delay_ran_out_before_the_sync_started():
    state.enqueue("retire", "sample:5", now=T0, delay_s=300)

    assert state.mark_done_before(at(minutes=10), now=at(minutes=30)) == 1
    assert row("retire", "sample:5").done_at == at(minutes=30)


@pytest.mark.django_db
def test_mark_done_before_still_closes_a_row_backing_off_after_a_failure():
    """A back-off is stored where a delay is, but it waits on nothing: the sync read what the row asks for."""
    state.enqueue("samples", "sample:1", now=T0)
    state.finish_failed(state.claim_next("w1", now=at(seconds=1)), "boom", 3600, now=at(seconds=2))

    assert state.mark_done_before(at(minutes=5), now=at(minutes=30)) == 1
    assert row("samples", "sample:1").done_at == at(minutes=30)


@pytest.mark.django_db
def test_mark_done_before_does_not_touch_a_row_already_done():
    state.enqueue("samples", "sample:1", now=T0)
    state.finish_done(state.claim_next("w", now=at(seconds=1)), now=at(seconds=2))
    assert state.mark_done_before(at(minutes=5)) == 0
    assert row("samples", "sample:1").done_at == at(seconds=2)


# --- failing_since: how long a row has been failing (SPEC-ci-health D1, D2) -----------------------

@pytest.mark.django_db
def test_a_failure_records_when_the_row_started_failing():
    state.enqueue("catalog", "*", now=T0)
    claim = state.claim_next("w1", now=at(seconds=1))
    state.finish_failed(claim, "boom", 3600, now=at(seconds=2))
    assert row("catalog", "*").failing_since == at(seconds=2)


@pytest.mark.django_db
def test_a_later_failure_keeps_the_first_failure_time():
    state.enqueue("catalog", "*", now=T0)
    state.finish_failed(state.claim_next("w1", now=at(seconds=1)), "boom", 60, now=at(seconds=2))
    state.finish_failed(state.claim_next("w1", now=at(minutes=5)), "boom again", 60, now=at(minutes=5, seconds=1))
    r = row("catalog", "*")
    assert r.failing_since == at(seconds=2)
    assert r.last_error == "boom again"


@pytest.mark.django_db
def test_a_re_enqueue_keeps_the_failure_time():
    """A hot key is re-enqueued by every write; that must not hide that it keeps failing."""
    state.enqueue("catalog", "*", now=T0)
    state.finish_failed(state.claim_next("w1", now=at(seconds=1)), "boom", 3600, now=at(seconds=2))
    state.enqueue("catalog", "*", now=at(minutes=10))
    r = row("catalog", "*")
    assert r.failing_since == at(seconds=2)
    assert r.attempts == 0


@pytest.mark.django_db
def test_success_clears_the_failure_time():
    state.enqueue("catalog", "*", now=T0)
    state.finish_failed(state.claim_next("w1", now=at(seconds=1)), "boom", 60, now=at(seconds=2))
    assert state.finish_done(state.claim_next("w1", now=at(minutes=5)), now=at(minutes=5, seconds=1))
    r = row("catalog", "*")
    assert r.failing_since is None
    assert r.last_error is None


@pytest.mark.django_db
def test_a_success_on_a_row_re_enqueued_meanwhile_still_clears_the_failure_time():
    """The retry worked; the newer write only sends the row round again, so the row is not failing (D1)."""
    state.enqueue("catalog", "*", now=T0)
    state.finish_failed(state.claim_next("w1", now=at(seconds=1)), "boom", 60, now=at(seconds=2))
    claim = state.claim_next("w1", now=at(minutes=5))
    state.enqueue("catalog", "*", now=at(minutes=5, seconds=1))
    assert state.finish_done(claim, now=at(minutes=5, seconds=2)) is False
    r = row("catalog", "*")
    assert r.done_at is None
    assert r.failing_since is None


@pytest.mark.django_db
def test_a_full_sync_closing_a_failing_row_clears_the_failure_time():
    state.enqueue("samples", "sample:7", now=T0)
    state.finish_failed(state.claim_next("w1", now=at(seconds=1)), "boom", 3600, now=at(seconds=2))
    assert state.mark_done_before(at(minutes=5), now=at(minutes=30)) == 1
    assert row("samples", "sample:7").failing_since is None


@pytest.mark.django_db
def test_a_deferral_records_no_failure_time():
    state.enqueue("catalog", "*", now=T0)
    state.finish_failed(state.claim_next("w1", now=at(seconds=1)), "lock_timeout", 60, now=at(seconds=2),
                        failure=False)
    r = row("catalog", "*")
    assert r.failing_since is None
    assert r.last_error == "lock_timeout"


@pytest.mark.django_db
def test_a_deferral_keeps_an_earlier_failure_time():
    state.enqueue("catalog", "*", now=T0)
    state.finish_failed(state.claim_next("w1", now=at(seconds=1)), "boom", 60, now=at(seconds=2))
    state.finish_failed(state.claim_next("w1", now=at(minutes=5)), "lock_timeout", 60,
                        now=at(minutes=5, seconds=1), failure=False)
    assert row("catalog", "*").failing_since == at(seconds=2)


# --- run records -----------------------------------------------------------------------------------

@pytest.mark.django_db
def test_start_run_records_a_running_row_and_finish_records_the_outcome():
    handle = state.start_run("full", trigger="command", now=T0)
    r = GraphSyncRun.objects.get(pk=handle.id)
    assert (r.kind, r.status, r.started_at, r.finished_at) == ("full", "running", T0, None)
    assert r.counts_json == {"trigger": "command"}

    handle.finish("ok", counts={"samples_written": 12}, drift={"checks": []}, watermark_from=None,
                  watermark_to=1084754, now=at(minutes=9))
    r.refresh_from_db()
    assert (r.status, r.finished_at, r.watermark_to) == ("ok", at(minutes=9), "1084754")
    assert r.counts_json == {"samples_written": 12, "trigger": "command"}
    assert r.drift_json == {"checks": []}


@pytest.mark.django_db
def test_finish_refuses_an_unknown_status():
    handle = state.start_run("catalog", trigger="loop", now=T0)
    with pytest.raises(ValueError):
        handle.finish("done")
    with pytest.raises(ValueError):
        handle.finish("running")


@pytest.mark.django_db
def test_start_run_tolerates_a_missing_table(caplog):
    drop_table("graph_sync_run")
    handle = state.start_run("full", trigger="command", now=T0)
    assert handle.id is None
    assert "graph_sync_run" in caplog.text
    handle.finish("ok", counts={"n": 1})     # a no-op, and no exception
    with pytest.raises(ValueError):
        handle.finish("done")


@pytest.mark.django_db
def test_start_run_inside_a_transaction_leaves_the_transaction_usable():
    """A run record that cannot be written must not break the caller's own transaction."""
    drop_table("graph_sync_run")
    with transaction.atomic():
        state.start_run("samples", trigger="batch_upload", now=T0)
        state.enqueue("catalog", "*", now=T0)
    assert GraphSyncOutbox.objects.filter(kind="catalog").exists()


@pytest.mark.django_db
def test_finish_tolerates_a_table_that_went_away(caplog):
    handle = state.start_run("reconcile", trigger="loop", now=T0)
    drop_table("graph_sync_run")
    handle.finish("ok", now=at(minutes=2))
    assert "graph_sync_run" in caplog.text


@pytest.mark.django_db
def test_abandoned_runs_are_marked():
    old_full = state.start_run("full", trigger="loop", now=at(hours=-13))
    live_full = state.start_run("full", trigger="loop", now=at(hours=-1))
    old_catalog = state.start_run("catalog", trigger="loop", now=at(hours=-3))
    done = state.start_run("drift", trigger="loop", now=at(days=-2))
    done.finish("drift", now=at(days=-2, minutes=5))

    assert state.reap_abandoned(now=T0) == 2
    statuses = dict(GraphSyncRun.objects.values_list("id", "status"))
    assert statuses == {old_full.id: "abandoned", live_full.id: "running", old_catalog.id: "abandoned",
                        done.id: "drift"}
    assert GraphSyncRun.objects.get(pk=old_full.id).finished_at == T0
    assert state.reap_abandoned(now=T0) == 0


@pytest.mark.django_db
def test_a_run_marked_abandoned_still_records_its_real_outcome():
    handle = state.start_run("full", trigger="loop", now=at(hours=-13))
    state.reap_abandoned(now=T0)
    handle.finish("ok", now=at(minutes=1))
    assert GraphSyncRun.objects.get(pk=handle.id).status == "ok"


@pytest.mark.django_db
def test_last_runs_gives_the_latest_run_of_each_kind():
    state.start_run("full", trigger="loop", now=at(days=-8)).finish("ok", now=at(days=-8, minutes=9))
    latest_full = state.start_run("full", trigger="loop", now=at(days=-1))
    latest_full.finish("failed", counts={"error": "boom"}, now=at(days=-1, minutes=1))
    state.start_run("reconcile", trigger="loop", now=at(hours=-1))

    runs = state.last_runs()
    assert set(runs) == {"full", "reconcile"}
    assert runs["full"]["id"] == latest_full.id
    assert runs["full"]["status"] == "failed"
    assert runs["full"]["counts"] == {"error": "boom", "trigger": "loop"}
    assert runs["full"]["started_at"] == at(days=-1).isoformat()
    assert runs["full"]["finished_at"] == at(days=-1, minutes=1).isoformat()
    assert runs["reconcile"]["status"] == "running"
    assert runs["reconcile"]["finished_at"] is None


@pytest.mark.django_db
def test_last_runs_is_empty_with_no_run():
    assert state.last_runs() == {}


# --- freshness and the outbox summary --------------------------------------------------------------

@pytest.mark.django_db
def test_freshness_reports_never_with_no_run():
    f = state.freshness(now=T0)
    assert f["full"]["status"] == "never"
    assert f["full"]["age_s"] is None
    assert f["reconcile"]["status"] == "never"
    assert f["outbox"]["status"] == "ok"
    assert f["outbox"]["age_s"] is None


@pytest.mark.django_db
def test_freshness_counts_a_full_sync_for_the_reconcile():
    state.start_run("full", trigger="loop", now=at(hours=-2)).finish("ok", now=at(hours=-1))
    f = state.freshness(now=T0)
    assert f["full"]["status"] == "ok"
    assert f["reconcile"]["status"] == "ok"
    assert f["reconcile"]["satisfied_by"] == "full"
    assert f["reconcile"]["age_s"] == 7200


@pytest.mark.django_db
def test_freshness_takes_the_newer_of_a_reconcile_and_a_full_sync():
    state.start_run("full", trigger="loop", now=at(days=-3)).finish("ok", now=at(days=-3, minutes=9))
    state.start_run("reconcile", trigger="loop", now=at(hours=-5)).finish("ok", now=at(hours=-5, minutes=2))
    f = state.freshness(now=T0)
    assert (f["reconcile"]["satisfied_by"], f["reconcile"]["age_s"]) == ("reconcile", 5 * 3600)
    assert (f["full"]["status"], f["full"]["age_s"]) == ("ok", 3 * 86400)


@pytest.mark.django_db
def test_freshness_is_stale_past_each_threshold():
    state.start_run("full", trigger="loop", now=at(days=-9)).finish("ok", now=at(days=-9, minutes=9))
    state.start_run("reconcile", trigger="loop", now=at(hours=-27)).finish("ok", now=at(hours=-27, minutes=1))
    state.enqueue("samples", "sample:1", now=at(hours=-2))
    f = state.freshness(now=T0)
    assert (f["full"]["status"], f["reconcile"]["status"], f["outbox"]["status"]) == ("stale", "stale", "stale")
    assert f["full"]["threshold_s"] == 8 * 86400
    assert f["reconcile"]["threshold_s"] == 26 * 3600
    assert f["outbox"]["threshold_s"] == 3600


@pytest.mark.django_db
def test_freshness_counts_only_successful_runs():
    state.start_run("full", trigger="loop", now=at(hours=-1)).finish("failed", now=at(minutes=-50))
    state.start_run("reconcile", trigger="loop", now=at(hours=-1)).finish("refused", now=at(minutes=-59))
    f = state.freshness(now=T0)
    assert (f["full"]["status"], f["reconcile"]["status"]) == ("never", "never")


@pytest.mark.django_db
def test_freshness_takes_thresholds():
    state.start_run("full", trigger="loop", now=at(hours=-2)).finish("ok", now=at(hours=-1))
    f = state.freshness(now=T0, thresholds={"full": 3600})
    assert f["full"]["status"] == "stale"
    assert f["reconcile"]["threshold_s"] == 26 * 3600


@pytest.mark.django_db
def test_outbox_summary_counts_pending_dead_and_claimed_rows_by_kind():
    state.enqueue("samples", "sample:1", now=at(minutes=-30))
    state.enqueue("samples", "sample:2", now=at(minutes=-20))
    state.enqueue("catalog", "*", now=at(minutes=-90))
    state.enqueue("isa", "*", now=at(minutes=-5))
    state.enqueue("retire", "sample:9", now=at(minutes=-1))
    state.finish_done(state.claim_next("w", now=at(minutes=-1), kinds=["retire"]), now=at(minutes=-1))
    GraphSyncOutbox.objects.filter(kind="isa").update(attempts=state.MAX_ATTEMPTS)
    # The oldest row is in a worker's hands, under a live lease.
    state.claim_next("w", now=at(minutes=-2), kinds=["catalog"])

    s = state.outbox_summary(now=T0)
    assert s["pending"] == {"samples": 2, "catalog": 1}
    assert s["dead"] == {"isa": 1}
    assert s["claimed"] == {"catalog": 1}
    assert s["max_attempts"] == state.MAX_ATTEMPTS
    # The oldest row waiting for a worker: the claimed catalog row is being worked on, the dead one still waits.
    assert s["oldest_pending"] == {"kind": "samples", "key": "sample:1",
                                   "enqueued_at": at(minutes=-30).isoformat(), "age_s": 1800}


@pytest.mark.django_db
def test_a_dead_row_counts_as_waiting_in_the_outbox_age():
    state.enqueue("isa", "*", now=at(hours=-3))
    GraphSyncOutbox.objects.filter(kind="isa").update(attempts=state.MAX_ATTEMPTS)
    s = state.outbox_summary(now=T0)
    assert s["oldest_pending"]["key"] == "*"
    assert state.freshness(now=T0)["outbox"]["status"] == "stale"


@pytest.mark.django_db
def test_outbox_summary_of_an_empty_outbox():
    s = state.outbox_summary(now=T0)
    assert (s["pending"], s["dead"], s["claimed"], s["oldest_pending"]) == ({}, {}, {}, None)


@pytest.mark.django_db
def test_the_readers_raise_when_the_tables_are_missing():
    """The status endpoint turns this into its 503; only start_run and finish are best-effort."""
    drop_table("graph_sync_outbox")
    drop_table("graph_sync_run")
    for read in (state.last_runs, state.outbox_summary, state.freshness):
        with pytest.raises(DatabaseError):
            read()


# --- the graph-write lock --------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_lock_is_a_no_op_on_sqlite():
    assert connection.vendor == "sqlite"
    with CaptureQueriesContext(connection) as queries:
        with state.graph_write_lock(60) as got:
            assert got is True
    assert queries.captured_queries == []


class StubCursor:
    def __init__(self, log: list, results: dict):
        self.log, self.results, self.last = log, results, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, list(params or [])))
        self.last = sql

    def fetchone(self):
        for fn, value in self.results.items():
            if fn in self.last:
                return (value,)
        return (None,)


class StubMySQL:
    vendor = "mysql"

    def __init__(self, get_lock=1):
        self.log: list = []
        self.results = {"GET_LOCK": get_lock, "RELEASE_LOCK": 1}

    def cursor(self):
        return StubCursor(self.log, self.results)


@pytest.fixture
def stub_mysql(monkeypatch):
    def install(get_lock=1):
        conn = StubMySQL(get_lock)
        monkeypatch.setattr(state, "connections", {"default": conn})
        return conn
    return install


def test_the_lock_issues_get_lock_and_release_lock_on_mysql(stub_mysql):
    conn = stub_mysql(get_lock=1)
    with state.graph_write_lock(60) as got:
        assert got is True
        assert [sql for sql, _ in conn.log] == ["SELECT GET_LOCK(%s, %s)"]
    assert conn.log == [("SELECT GET_LOCK(%s, %s)", ["nextseek_graph_write", 60]),
                        ("SELECT RELEASE_LOCK(%s)", ["nextseek_graph_write"])]


def test_the_lock_yields_false_on_a_timeout_and_releases_nothing(stub_mysql):
    conn = stub_mysql(get_lock=0)
    with state.graph_write_lock(5) as got:
        assert got is False
    assert [sql for sql, _ in conn.log] == ["SELECT GET_LOCK(%s, %s)"]


def test_the_lock_yields_false_when_mysql_answers_null(stub_mysql):
    stub_mysql(get_lock=None)
    with state.graph_write_lock(5) as got:
        assert got is False


def test_the_lock_is_released_when_the_body_raises(stub_mysql):
    conn = stub_mysql(get_lock=1)
    with pytest.raises(RuntimeError):
        with state.graph_write_lock(60):
            raise RuntimeError("write failed")
    assert conn.log[-1] == ("SELECT RELEASE_LOCK(%s)", ["nextseek_graph_write"])


@pytest.mark.parametrize("given, sent", [(0, 0), (0.2, 1), (59.5, 60), (-1, 0)])
def test_the_lock_timeout_is_whole_seconds_and_never_infinite(stub_mysql, given, sent):
    """MySQL reads a negative timeout as wait forever; the lock never asks for that."""
    conn = stub_mysql(get_lock=1)
    with state.graph_write_lock(given):
        pass
    assert conn.log[0] == ("SELECT GET_LOCK(%s, %s)", ["nextseek_graph_write", sent])


# --- failing rows and failed runs (SPEC-ci-health D4 to D8) --------------------------------------

LOST = "OperationalError: (2006, 'Server has gone away')"


def failing(kind: str, key: str, when: datetime, error: str = LOST) -> None:
    """Enqueue a row and fail it once at ``when``."""
    state.enqueue(kind, key, now=when - timedelta(seconds=2))
    claim = state.claim_next("w1", now=when - timedelta(seconds=1), kinds=[kind])
    assert claim is not None and claim.key == key
    state.finish_failed(claim, error, state.backoff_s(kind), now=when)


def test_the_failing_threshold_is_the_back_off_plus_half_an_hour():
    assert state.failing_threshold_s("catalog") == 3600 + 1800
    assert state.failing_threshold_s("full") == 6 * 3600 + 1800


@pytest.mark.parametrize("raw, expected", [
    (None, None),
    ("", None),
    (LOST, LOST),
    ("\n  Traceback (most recent call last):\n  File \"x.py\", line 1", "Traceback (most recent call last):"),
    ("ServiceUnavailable: Couldn't connect to bolt://neo4j.example:7687 (10.0.0.5:7687)",
     "ServiceUnavailable: Couldn't connect to <url> (<ip>)"),
])
def test_an_error_excerpt_is_its_first_line_with_urls_and_addresses_replaced(raw, expected):
    assert state.error_excerpt(raw) == expected


def test_a_long_error_excerpt_is_cut():
    excerpt = state.error_excerpt("x" * 10_000)
    assert len(excerpt) == state.ERROR_EXCERPT_CHARS
    assert excerpt.endswith("...")


@pytest.mark.django_db
def test_failing_rows_lists_a_failing_row_with_its_excerpt_and_next_retry():
    failing("catalog", "*", T0)

    out = state.failing_rows(now=at(minutes=10))

    assert (out["total"], out["overdue"], out["limit"]) == (1, 0, state.FAILING_ROWS_SHOWN)
    assert out["rows"] == [{
        "kind": "catalog", "key": "*", "attempts": 1, "dead": False,
        "failing_since": T0.isoformat(), "age_s": 600.0, "threshold_s": 5400, "overdue": False,
        "next_retry_at": at(hours=1).isoformat(), "error": LOST,
    }]


@pytest.mark.django_db
def test_a_row_failing_past_its_back_off_and_grace_is_overdue():
    failing("catalog", "*", T0)

    assert state.failing_rows(now=at(minutes=90))["overdue"] == 0
    out = state.failing_rows(now=at(minutes=90, seconds=1))
    assert out["overdue"] == 1
    assert out["rows"][0]["overdue"] is True


@pytest.mark.django_db
def test_a_full_sync_gets_its_six_hour_back_off_before_it_is_overdue():
    failing("full", "slot:2026-W37", T0)

    assert state.failing_rows(now=at(hours=6))["overdue"] == 0
    assert state.failing_rows(now=at(hours=7))["overdue"] == 1


@pytest.mark.django_db
def test_a_re_enqueued_hot_row_stays_overdue():
    failing("catalog", "*", T0)
    state.enqueue("catalog", "*", now=at(hours=2))

    out = state.failing_rows(now=at(hours=2, seconds=1))

    assert out["overdue"] == 1
    assert out["rows"][0]["attempts"] == 0


@pytest.mark.django_db
def test_only_open_failing_rows_outside_a_live_claim_are_listed():
    failing("samples", "sample:2", T0)                                   # fails, then succeeds
    state.enqueue("samples", "sample:1", now=at(hours=1))                # never fails
    assert state.finish_done(state.claim_next("w1", now=at(hours=2), kinds=["samples"]), now=at(hours=2))
    state.enqueue("retire", "sample:3", now=at(hours=2))                 # deferred
    state.finish_failed(state.claim_next("w1", now=at(hours=2), kinds=["retire"]), "lock_timeout", 60,
                        now=at(hours=2), failure=False)
    failing("catalog", "*", at(hours=2))                                 # failing, then claimed for its retry
    assert state.claim_next("w2", now=at(hours=3, seconds=1), kinds=["catalog"]) is not None
    failing("isa", "*", at(hours=2))                                     # failing and waiting

    out = state.failing_rows(now=at(hours=3, seconds=1))

    assert [(r["kind"], r["key"]) for r in out["rows"]] == [("isa", "*")]
    assert out["total"] == 1


@pytest.mark.django_db
def test_a_dead_row_is_listed_as_dead_with_no_next_retry():
    state.enqueue("catalog", "*", now=T0)
    for n in range(state.MAX_ATTEMPTS):
        when = at(hours=2 * n, seconds=1)
        state.finish_failed(state.claim_next("w1", now=when), "boom", 3600, now=when)

    (r,) = state.failing_rows(now=at(days=2))["rows"]

    assert r["dead"] is True
    assert r["attempts"] == state.MAX_ATTEMPTS
    assert r["next_retry_at"] is None
    assert r["failing_since"] == at(seconds=1).isoformat()


@pytest.mark.django_db
def test_the_list_is_capped_oldest_first_and_the_counts_cover_every_row():
    for n in range(state.FAILING_ROWS_SHOWN + 5):
        failing("samples", f"sample:{n}", at(minutes=n))

    out = state.failing_rows(now=at(hours=5))

    assert len(out["rows"]) == state.FAILING_ROWS_SHOWN
    assert out["total"] == out["overdue"] == state.FAILING_ROWS_SHOWN + 5
    assert out["rows"][0]["key"] == "sample:0"


@pytest.mark.django_db
def test_the_latest_failed_run_of_a_kind_is_reported_and_other_outcomes_are_not():
    handle = state.start_run("catalog", trigger="loop", now=T0)
    handle.finish("failed", counts={"error": LOST}, now=at(seconds=1))
    state.start_run("drift", trigger="loop", now=T0).finish("drift", now=at(minutes=2))
    state.start_run("reconcile", trigger="loop", now=T0).finish("refused", now=at(minutes=1))

    assert state.failed_runs(state.last_runs(), now=at(minutes=31)) == [{
        "id": handle.id, "kind": "catalog", "status": "failed", "trigger": "loop",
        "finished_at": at(seconds=1).isoformat(), "age_s": 1859.0, "threshold_s": 5400, "overdue": False,
        "error": LOST,
    }]


@pytest.mark.django_db
def test_a_later_successful_run_clears_the_failed_run():
    state.start_run("catalog", trigger="loop", now=T0).finish("failed", counts={"error": LOST}, now=T0)
    state.start_run("catalog", trigger="loop", now=at(hours=1)).finish("ok", now=at(hours=1))

    assert state.failed_runs(state.last_runs(), now=at(hours=2)) == []


@pytest.mark.django_db
def test_a_failed_run_turns_overdue_past_its_back_off_and_grace():
    state.start_run("catalog", trigger="loop", now=T0).finish("failed", counts={"error": LOST}, now=T0)

    assert state.failed_runs(state.last_runs(), now=at(minutes=90))[0]["overdue"] is False
    assert state.failed_runs(state.last_runs(), now=at(minutes=91))[0]["overdue"] is True


@pytest.mark.django_db
def test_an_abandoned_run_is_a_failed_run():
    state.start_run("full", trigger="loop", now=T0)
    assert state.reap_abandoned(now=at(hours=13)) == 1

    (run,) = state.failed_runs(state.last_runs(), now=at(hours=13))

    assert (run["kind"], run["status"], run["error"]) == ("full", "abandoned", None)
    assert (run["age_s"], run["threshold_s"], run["overdue"]) == (0.0, 6 * 3600 + 1800, False)


@pytest.mark.django_db
def test_a_failed_run_of_a_kind_the_sync_never_reruns_is_not_reported():
    """A hand --samples run, or another tool's run kind, has no retry to wait for: it would hold CI red until someone
    reran it (SPEC-ci-health D6)."""
    state.start_run("samples", trigger="command", now=T0).finish("failed", counts={"error": LOST}, now=T0)
    state.start_run("merge_studies", trigger="command", now=T0).finish("failed", counts={"error": LOST}, now=T0)

    assert state.failed_runs(state.last_runs(), now=at(days=1)) == []


@pytest.mark.django_db
def test_a_batch_row_closed_by_the_orchestrator_is_not_failing_when_the_key_is_written_again():
    """Stage 6 of a batch upload closes its rows itself (orchestrator._mark_outbox_done); that close must clear the
    failure time too, or a later write to the same key reopens a row that looks like it has failed for days."""
    from nextseek_api.batch_upload import orchestrator

    key = "batch:job7:0"
    state.enqueue("samples", key, [7], now=T0)
    state.finish_failed(state.claim_next("w1", now=at(seconds=1)), "boom", 3600, now=at(seconds=2))
    assert orchestrator._mark_outbox_done("job7", at(minutes=1)) == 1
    assert row("samples", key).failing_since is None

    state.enqueue("samples", key, [7], now=at(days=3))

    assert state.failing_rows(now=at(days=3, seconds=5))["total"] == 0


# --- requeue_dead: dead rows back to pending by hand (PLAN-ci-health Task 7a) ----------------------

def dead(kind: str, key: str, *, error: str = LOST, payload=None) -> None:
    """Enqueue a row and fail it MAX_ATTEMPTS times, each claim an hour after the last failure."""
    state.enqueue(kind, key, payload, now=T0)
    for n in range(state.MAX_ATTEMPTS):
        when = at(hours=2 * n, seconds=1)
        claim = state.claim_next("w1", now=when, kinds=[kind])
        assert claim is not None and claim.key == key
        state.finish_failed(claim, error, 3600, now=when)


@pytest.mark.django_db
def test_requeue_dead_puts_a_dead_row_back_to_pending_and_claimable_at_once():
    dead("catalog", "*")
    assert state.outbox_summary(now=at(days=2))["dead"] == {"catalog": 1}
    now = at(hours=2 * (state.MAX_ATTEMPTS - 1), minutes=5)       # inside the last failure's hour of back-off

    out = state.requeue_dead(now=now)

    assert out == [{"kind": "catalog", "key": "*", "attempts": state.MAX_ATTEMPTS, "error": LOST}]
    r = row("catalog", "*")
    assert (r.attempts, r.done_at, r.claimed_by, r.lease_expires_at, r.failing_since) == (0, None, None, None, None)
    assert r.enqueued_at == now
    assert r.last_error == LOST
    summary = state.outbox_summary(now=now)
    assert summary["dead"] == {} and summary["pending"] == {"catalog": 1}
    assert state.failing_rows(now=now)["total"] == 0
    assert state.claim_next("w2", now=now) is not None


@pytest.mark.django_db
def test_requeue_dead_keeps_a_batch_rows_sample_ids():
    dead("samples", "batch:job7:0", payload=[1001, 1002])

    state.requeue_dead(now=at(days=2))

    assert row("samples", "batch:job7:0").payload == [1001, 1002]


@pytest.mark.django_db
def test_requeue_dead_leaves_a_row_at_the_limit_under_a_live_claim():
    """The claim that reached the limit is still running: its worker decides the row, not the operator."""
    state.enqueue("catalog", "*", now=T0)
    for n in range(state.MAX_ATTEMPTS - 1):
        when = at(hours=2 * n, seconds=1)
        state.finish_failed(state.claim_next("w1", now=when), LOST, 3600, now=when)
    last = state.claim_next("w1", now=at(days=1))
    assert last.attempts == state.MAX_ATTEMPTS

    assert state.requeue_dead(now=at(days=1, minutes=1)) == []
    assert state.finish_done(last, now=at(days=1, minutes=2)) is True


@pytest.mark.django_db
def test_requeue_dead_never_touches_a_done_or_a_live_row():
    dead("catalog", "*")
    state.mark_done_before(at(days=3), now=at(days=3))                  # done while dead
    state.enqueue("samples", "sample:7", now=at(days=3))                # pending, never failed

    assert state.requeue_dead(now=at(days=3, minutes=1)) == []
    assert row("catalog", "*").done_at is not None
    assert row("samples", "sample:7").attempts == 0


@pytest.mark.django_db
def test_requeue_dead_of_one_kind_leaves_the_others_dead():
    dead("catalog", "*")
    dead("samples_of_type", "type:3")

    out = state.requeue_dead("samples_of_type", now=at(days=2))

    assert [(r["kind"], r["key"]) for r in out] == [("samples_of_type", "type:3")]
    assert state.outbox_summary(now=at(days=2))["dead"] == {"catalog": 1}


@pytest.mark.django_db
def test_requeue_dead_dry_run_lists_and_writes_nothing():
    dead("catalog", "*")
    before = row("catalog", "*")

    with CaptureQueriesContext(connection) as queries:
        out = state.requeue_dead(dry_run=True, now=at(days=2))

    assert [(r["kind"], r["key"]) for r in out] == [("catalog", "*")]
    assert [q["sql"] for q in queries.captured_queries if q["sql"].lstrip().upper().startswith("UPDATE")] == []
    after = row("catalog", "*")
    assert (after.attempts, after.lease_expires_at) == (before.attempts, before.lease_expires_at)


@pytest.mark.django_db
def test_requeue_dead_publishes_the_error_as_an_excerpt():
    dead("catalog", "*", error="ServiceUnavailable: bolt://neo4j.example:7687\nTraceback ...")

    (out,) = state.requeue_dead(dry_run=True, now=at(days=2))

    assert out["error"] == "ServiceUnavailable: <url>"


def test_requeue_dead_refuses_a_kind_that_is_not_an_outbox_kind():
    with pytest.raises(ValueError, match="not a graph_sync outbox kind"):
        state.requeue_dead("merge_studies")


@pytest.mark.django_db
def test_a_requeued_row_that_fails_again_starts_a_new_failure_time():
    dead("catalog", "*")
    now = at(days=2)
    state.requeue_dead(now=now)

    state.finish_failed(state.claim_next("w1", now=now), LOST, 3600, now=now + timedelta(seconds=1))

    assert row("catalog", "*").failing_since == now + timedelta(seconds=1)


# --- a full sync or reconcile its data refused is a failed run (PLAN-ci-health Task 7b) ------------

TITLE_CONFLICT = "1 SampleType titles are held under other ids in the graph (sample_type_title_conflicts)"


def refused(kind: str, counts: dict | None = None, *, when: datetime = T0) -> state.RunHandle:
    handle = state.start_run(kind, trigger="loop", now=when)
    handle.finish("refused", counts=counts, now=when)
    return handle


@pytest.mark.django_db
def test_a_reconcile_its_data_refused_is_a_failed_run_named_by_its_first_problem():
    handle = refused("reconcile", {"status": "refused", "stopped_at": "catalog", "problems": [TITLE_CONFLICT]})

    assert state.failed_runs(state.last_runs(), now=at(minutes=91)) == [{
        "id": handle.id, "kind": "reconcile", "status": "refused", "trigger": "loop",
        "finished_at": T0.isoformat(), "age_s": 5460.0, "threshold_s": 5400, "overdue": True,
        "error": TITLE_CONFLICT,
    }]


@pytest.mark.django_db
def test_a_full_sync_whose_catalog_does_not_build_is_a_failed_run():
    """run._build_or_refuse records catalog_error and no problems."""
    refused("full", {"status": "refused", "catalog_error": "label collision: T_A_B"})

    (failed,) = state.failed_runs(state.last_runs(), now=at(hours=7))

    assert (failed["kind"], failed["status"], failed["overdue"]) == ("full", "refused", True)
    assert failed["error"] == "the catalog does not build: label collision: T_A_B"


@pytest.mark.django_db
@pytest.mark.parametrize("kind, counts", [
    ("reconcile", {"status": "lock_timeout", "stopped_at": "catalog", "problems": [sync_run._lock_problem(600)]}),
    ("reconcile", {"status": "refused", "stopped_at": "catalog", "problems": [
        f"the graph is at schema '1.1', not {writer.SCHEMA_VERSION!r}; run a full sync first, which brings it there"]}),
    ("reconcile", {"status": "not_at_version", "schema_version": "1.1"}),
    ("reconcile", {"status": "guard_tripped"}),
    ("full", {"status": "refused"}),                            # the lock, or no run directory: nothing recorded
    ("catalog", {"status": "refused", "problems": [TITLE_CONFLICT]}),   # its drain row fails instead (loop._refused)
], ids=["lock", "version", "not at version", "guard", "full, no reason", "catalog kind"])
def test_a_refusal_that_is_not_the_datas_fault_is_not_a_failed_run(kind, counts):
    refused(kind, counts)

    assert state.failed_runs(state.last_runs(), now=at(days=1)) == []


@pytest.mark.django_db
def test_a_later_successful_run_clears_a_data_refusal():
    refused("reconcile", {"status": "refused", "problems": [TITLE_CONFLICT]})
    state.start_run("reconcile", trigger="command", now=at(hours=3)).finish("ok", now=at(hours=3))

    assert state.failed_runs(state.last_runs(), now=at(hours=4)) == []


def test_the_refusal_texts_are_the_ones_the_sync_writes():
    """state cannot import loop or run (both import state), so it keeps the two texts; these pin them."""
    assert state.LOCK_REFUSAL_TEXT == sync_loop.LOCK_REFUSAL
    assert state.LOCK_REFUSAL_TEXT in sync_run._lock_problem(600)
    assert state.VERSION_REFUSAL_TEXT in inspect.getsource(sync_run.catalog_sync)


# --- the drift check's own freshness (PLAN-ci-health Task 7d) --------------------------------------

@pytest.mark.django_db
def test_the_drift_check_has_a_freshness_of_its_own():
    f = state.freshness(now=T0)
    assert (f["drift"]["status"], f["drift"]["age_s"], f["drift"]["threshold_s"]) == ("never", None, 26 * 3600)


@pytest.mark.django_db
@pytest.mark.parametrize("status, hours_ago, expected", [("ok", 27, "stale"), ("ok", 25, "ok"), ("drift", 1, "ok")])
def test_a_drift_run_that_compared_the_graph_is_a_fresh_check(status, hours_ago, expected):
    """A run that found drift did its job: it reports and does not repair (schedule._SATISFIED_BY, loop)."""
    state.start_run("drift", trigger="loop", now=at(hours=-hours_ago)).finish(status, now=at(hours=-hours_ago,
                                                                                               minutes=20))
    f = state.freshness(now=T0)
    assert (f["drift"]["status"], f["drift"]["satisfied_by"], f["drift"]["age_s"]) == (
        expected, "drift", hours_ago * 3600)


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["failed", "refused", "abandoned"])
def test_a_drift_run_that_did_not_compare_the_graph_is_not_a_fresh_check(status):
    state.start_run("drift", trigger="loop", now=at(hours=-1)).finish(status, now=at(minutes=-50))
    assert state.freshness(now=T0)["drift"]["status"] == "never"


@pytest.mark.django_db
def test_a_full_sync_does_not_stand_in_for_a_drift_check():
    state.start_run("full", trigger="loop", now=at(hours=-1)).finish("ok", now=at(minutes=-50))
    assert state.freshness(now=T0)["drift"]["status"] == "never"


@pytest.mark.django_db
def test_claim_more_takes_claimable_rows_of_one_kind_and_key_prefix_oldest_first():
    for n, (kind, key) in enumerate([("samples", "sample:1"), ("samples", "batch:x:0"), ("retire", "sample:2"),
                                     ("samples", "sample:3"), ("samples", "sample:4"), ("samples", "sample:5")]):
        state.enqueue(kind, key, [9] if key.startswith("batch") else None, now=at(seconds=n))
    first = state.claim_next("w1", now=at(minutes=1))
    assert first.key == "sample:1"

    more = state.claim_more("w1", "samples", "sample:", 2, now=at(minutes=1))

    assert [c.key for c in more] == ["sample:3", "sample:4"]
    assert all(c.attempts == 1 and c.worker_id == "w1" for c in more)
    assert [c.key for c in state.claim_more("w2", "samples", "sample:", 10, now=at(minutes=1))] == ["sample:5"]
    assert state.claim_more("w2", "samples", "sample:", 0, now=at(minutes=1)) == []


# --- a claimed row of many samples hands its failing ones on as rows of their own ------------------------------------

def _claimed_batch(*, attempts: int = 0, failing_since=None):
    state.enqueue("samples", "batch:reg:1:0", [11, 12, 13], now=T0)
    GraphSyncOutbox.objects.filter(key="batch:reg:1:0").update(attempts=attempts, failing_since=failing_since)
    claim = state.claim_next("w1", now=at(minutes=1), kinds=["samples"])
    assert claim.key == "batch:reg:1:0"
    return claim


@pytest.mark.django_db
def test_hand_on_failed_writes_each_key_as_a_row_that_failed_as_the_claimed_row_did():
    claim = _claimed_batch()

    assert state.hand_on_failed(claim, "samples", {"sample:12": "gap in 12"}, 3600, now=at(minutes=1)) == 1

    r = row("samples", "sample:12")
    assert (r.done_at, r.claimed_by, r.attempts, r.payload, r.last_error) == (None, None, 1, None, "gap in 12")
    assert (r.failing_since, r.lease_expires_at) == (at(minutes=1), at(minutes=61))
    assert row("samples", "batch:reg:1:0").claimed_by == "w1"          # the claimed row is the caller's to close
    assert state.claim_more("w2", "samples", "sample:", 10, now=at(minutes=60)) == []
    assert [c.key for c in state.claim_more("w2", "samples", "sample:", 10, now=at(minutes=61))] == ["sample:12"]


@pytest.mark.django_db
def test_a_handed_on_row_keeps_the_attempts_and_the_failing_clock_of_the_claimed_row():
    claim = _claimed_batch(attempts=2, failing_since=at(hours=-5))
    state.hand_on_failed(claim, "samples", {"sample:12": "gap"}, 3600, now=at(minutes=1))
    r = row("samples", "sample:12")
    assert (r.attempts, r.failing_since) == (3, at(hours=-5))


@pytest.mark.django_db
def test_a_handed_on_failure_folds_into_the_row_already_there_except_a_live_claim_or_a_later_write():
    state.enqueue("samples", "sample:14", now=at(minutes=-5))
    assert state.claim_next("w2", now=at(minutes=-4)).key == "sample:14"          # another worker syncs 14 now
    state.enqueue("samples", "sample:11", now=at(minutes=-3))                      # done since
    state.finish_done(state.claim_next("w3", now=at(minutes=-2), kinds=["samples"]), now=at(minutes=-2))
    state.enqueue("samples", "sample:12", now=at(minutes=-1))                      # failing since the night
    GraphSyncOutbox.objects.filter(key="sample:12").update(
        attempts=5, failing_since=at(hours=-9), lease_expires_at=at(hours=3), last_error="old")
    claim = _claimed_batch()
    state.enqueue("samples", "sample:13", now=at(minutes=2))                       # a write after the claim
    before_14 = row("samples", "sample:14")

    written = state.hand_on_failed(claim, "samples", {f"sample:{i}": f"gap {i}" for i in (11, 12, 13, 14)}, 3600,
                                   now=at(minutes=1))

    assert written == 2
    done = row("samples", "sample:11")
    assert (done.done_at, done.attempts, done.failing_since, done.last_error) == (None, 1, at(minutes=1), "gap 11")
    failing = row("samples", "sample:12")
    assert (failing.attempts, failing.failing_since, failing.lease_expires_at, failing.last_error) == (
        5, at(hours=-9), at(hours=3), "gap 12")
    later = row("samples", "sample:13")
    assert (later.attempts, later.failing_since, later.last_error, later.lease_expires_at) == (0, None, None, None)
    live = row("samples", "sample:14")
    assert (live.claimed_by, live.lease_expires_at, live.attempts, live.last_error) == (
        before_14.claimed_by, before_14.lease_expires_at, before_14.attempts, None)

"""The outbox, run records and graph-write lock (nextseek_api/graph_sync/state.py; the spec's sections 7.4 and 12).

Runs on the SQLite test settings: the rows go to the in-memory ``default`` database. Every time is passed in, so no
test depends on the clock. The MySQL half of the lock is checked on a stub connection that records its statements.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext

from nextseek_api.graph_sync import state
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

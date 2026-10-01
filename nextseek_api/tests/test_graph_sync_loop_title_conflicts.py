"""The drain while SampleType titles are held under other ids (nextseek_api/graph_sync/loop.py).

A type deleted in SEEK with its samples and recreated under its old title leaves the old SampleType node holding the
title until the nightly reconcile retires those samples. Until then a by-id sync leaves out the samples of a type it
cannot write a node for and names them (``catalog_waiting_samples``); the loop defers only the rows holding them.

Real loop and outbox code on SQLite, and the real status body; only ``targeted.sync_samples`` is replaced. A fake
clock advances by the cost of each sync, a fresh single-sample write arrives once a minute, and the status body is read
once a minute. Whatever the number of waiting rows, a fresh write must drain within a minute, every waiting row must
stay open without an attempt, and the health line must stay green.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.utils import timezone as djtz

from nextseek_api.graph_sync import health, loop, state, targeted, writer
from nextseek_api.graph_sync.models_db import GraphSyncOutbox
from nextseek_api.services.graph_sync_status import build_status

T0 = datetime(2026, 10, 6, 9, 0, tzinfo=dt_timezone.utc)    # a Tuesday morning, far from the nightly
WHY = "sample type 9 has no current SampleType node ('Tissue' is held by type 3 in the graph)"
FRESH = 100_000                                               # the first id of a fresh write


def simulate(monkeypatch, tmp_path, *, waiting: int, riders: int, cost_s: float, hours: float) -> dict:
    clock = [T0]
    monkeypatch.setattr(djtz, "now", lambda: clock[0])
    monkeypatch.setattr(writer, "graphmeta", lambda driver, db: {"schema_version": writer.SCHEMA_VERSION})
    bad = set(range(1, waiting + 1))
    for i in sorted(bad) + list(range(10_001, 10_001 + riders)):
        state.enqueue("samples", f"sample:{i}", now=T0 - timedelta(seconds=1))
    out = {"fresh": {}, "bodies": [], "syncs_with_waiting": 0}
    next_fresh, next_body = [T0 + timedelta(minutes=1)], [T0]

    def arrivals():
        while next_fresh[0] <= clock[0]:
            sid = FRESH + len(out["fresh"])
            state.enqueue("samples", f"sample:{sid}", now=next_fresh[0])
            out["fresh"][sid] = next_fresh[0]
            next_fresh[0] += timedelta(minutes=1)
        while next_body[0] <= clock[0]:
            out["bodies"].append(build_status(now=clock[0]))
            next_body[0] += timedelta(minutes=1)

    def sync(driver, db, ids, **kwargs):
        clock[0] += timedelta(seconds=cost_s)
        left_out = {i: WHY for i in ids if i in bad}
        out["syncs_with_waiting"] += bool(left_out)
        arrivals()
        return {"status": targeted.OK, "catalog_waiting_samples": left_out} if left_out else {"status": targeted.OK}

    monkeypatch.setattr(targeted, "sync_samples", sync)
    opts = loop.Options(run_root=str(tmp_path), cadences=())
    end = T0 + timedelta(hours=hours)
    while clock[0] < end:
        loop.run_pass(object(), "neo4j", "w1", opts=opts, launch=lambda argv, t: 0)
        clock[0] += timedelta(seconds=loop.DEFAULT_INTERVAL_S)
        arrivals()
    out["end"] = clock[0]
    return out


@pytest.mark.django_db
@pytest.mark.parametrize("waiting, riders, cost_s, hours", [
    (1000, 0, 5.5, 2),      # 5,500 s of syncs if each waiting row drained alone: over the 30-minute back-off
    (1000, 0, 1.0, 2),      # 1,000 s: under it
    (300, 0, 5.5, 2),       # 1,650 s: just under it
    (1, 999, 5.5, 2),       # one waiting row among 999 healthy ones claimed with it
])
def test_rows_waiting_for_the_catalog_never_hold_back_a_fresh_write(monkeypatch, tmp_path, waiting, riders, cost_s,
                                                                       hours):
    out = simulate(monkeypatch, tmp_path, waiting=waiting, riders=riders, cost_s=cost_s, hours=hours)

    rows = {int(r.key.split(":")[1]): r for r in GraphSyncOutbox.objects.filter(kind="samples")}
    # Every fresh write drained within a minute of its arrival.
    waits = [(rows[i].done_at - t) if rows[i].done_at else (out["end"] - t) for i, t in out["fresh"].items()]
    assert max(waits) <= timedelta(minutes=1), max(waits)
    # Every waiting row is still open, never counted an attempt and never showed as failing; every rider is done.
    assert {(rows[i].done_at, rows[i].attempts, rows[i].failing_since) for i in range(1, waiting + 1)} == {
        (None, 0, None)}
    assert all(rows[i].last_error.startswith(loop.TITLE_CONFLICT_DEFERRAL) for i in range(1, waiting + 1))
    assert all(rows[i].done_at is not None for i in range(10_001, 10_001 + riders))
    # The waiting rows drain together, once per back-off, not one sync each.
    assert out["syncs_with_waiting"] <= hours * 3600 / loop.TITLE_CONFLICT_BACKOFF_S + 1
    # The health line stayed green all along.
    assert [health.problems(body) for body in out["bodies"] if health.problems(body)] == []
    assert max(body["failing"]["total"] for body in out["bodies"]) == 0

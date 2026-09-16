"""The publication backfill command queues its updated samples for a graph sync (spec 5 E2, writer WR-16; task H7).

``backfill_publication_attributes`` rewrites ``samples.json_metadata`` through the SEEK connection and bumps no
timestamp, so nothing else would ever see the change. After ``--apply`` has committed it enqueues the ids it wrote,
in batches of 5,000: kind ``samples``, key ``batch:backfill:<n>``, the ids as the payload. Nothing is written to any
graph here; the drain applies the row.

No MySQL: the SEEK connection is a small fixed world (``FakeSeek``), and the outbox is the dmac ``default``
connection of the SQLite test settings.
"""
from __future__ import annotations

import json
from importlib import import_module
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import OperationalError, connection

from nextseek_api.graph_sync import state
from nextseek_api.graph_sync.models_db import GraphSyncOutbox

cmd = import_module("nextseek_api.management.commands.backfill_publication_attributes")

COMMAND = "backfill_publication_attributes"


class FakeSeek:
    """The SEEK ``samples`` table the command reads and updates, and the cursor it reads it through."""

    def __init__(self, rows: dict[int, dict]):
        self.metadata = {sample_id: json.dumps(md) for sample_id, md in rows.items()}
        self.updated: list[int] = []
        self._result: list[tuple] = []

    # the cursor: ``_cursor()`` returns it, and the command uses it as a context manager
    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=None):
        params = list(params or [])
        if sql.lstrip().startswith("SELECT"):
            self._result = [(sid, self.metadata[sid]) for sid in params if sid in self.metadata]
        elif sql.lstrip().startswith("UPDATE"):
            new, sample_id = params
            self.metadata[sample_id] = new
            self.updated.append(sample_id)
        else:  # pragma: no cover - the command sends no other statement
            raise AssertionError(f"unexpected statement: {sql}")

    def fetchall(self):
        return self._result

    def stored(self, sample_id: int) -> dict:
        return json.loads(self.metadata[sample_id])


@pytest.fixture
def seek(monkeypatch):
    world = FakeSeek({11: {"Title": "a"}, 12: {"Title": "b"}, 13: {"Title": "c"}})
    monkeypatch.setattr(cmd, "_cursor", world.cursor)
    return world


def run(tmp_path, rows, *args) -> str:
    path = tmp_path / "pairs.tsv"
    path.write_text("".join(f"{sid}\t{doi}\t{pmid}\n" for sid, doi, pmid in rows), encoding="utf-8")
    out = StringIO()
    call_command(COMMAND, "--from-file", str(path), *args, stdout=out)
    return out.getvalue()


def queued() -> list[tuple[str, str, list]]:
    return [(r.kind, r.key, r.payload) for r in GraphSyncOutbox.objects.order_by("key")]


@pytest.mark.django_db
def test_apply_queues_the_ids_it_wrote(seek, tmp_path):
    run(tmp_path, [(11, "10.1/a", "999"), (12, "10.1/a", "999")], "--apply")

    assert queued() == [("samples", "batch:backfill:0", [11, 12])]
    row = GraphSyncOutbox.objects.get(key="batch:backfill:0")
    assert (row.done_at, row.attempts) == (None, 0)


@pytest.mark.django_db
def test_a_dry_run_queues_nothing_and_writes_nothing(seek, tmp_path):
    out = run(tmp_path, [(11, "10.1/a", "999")])

    assert queued() == []
    assert seek.updated == [] and seek.stored(11) == {"Title": "a"}
    assert "1 sample(s) would change" in out


@pytest.mark.django_db
def test_only_the_samples_whose_metadata_changed_are_queued(seek, tmp_path):
    seek.metadata[11] = json.dumps({"Title": "a", "DOI": "10.1/a", "PMID": "999"})

    run(tmp_path, [(11, "10.1/a", "999"), (12, "10.1/a", "999")], "--apply")

    assert queued() == [("samples", "batch:backfill:0", [12])]


@pytest.mark.django_db
def test_an_id_the_source_names_and_mysql_lacks_is_not_queued(seek, tmp_path):
    out = run(tmp_path, [(11, "10.1/a", "999"), (404, "10.1/a", "999")], "--apply")

    assert queued() == [("samples", "batch:backfill:0", [11])]
    assert "1 sample id(s) in the source do not exist here" in out


@pytest.mark.django_db
def test_nothing_is_queued_when_no_sample_changed(seek, tmp_path):
    seek.metadata[11] = json.dumps({"Title": "a", "DOI": "10.1/a", "PMID": "999"})

    run(tmp_path, [(11, "10.1/a", "999")], "--apply")

    assert queued() == []


@pytest.mark.django_db
def test_the_ids_are_queued_in_batches(seek, tmp_path, monkeypatch):
    monkeypatch.setattr(cmd, "GRAPH_SYNC_BATCH", 2)

    run(tmp_path, [(sid, "10.1/a", "999") for sid in (11, 12, 13)], "--apply")

    assert queued() == [("samples", "batch:backfill:0", [11, 12]),
                        ("samples", "batch:backfill:1", [13])]


def test_the_batch_size_is_five_thousand():
    assert cmd.GRAPH_SYNC_BATCH == 5_000


@pytest.mark.django_db
def test_enqueue_graph_sync_chunks_and_sorts_the_ids():
    assert cmd.enqueue_graph_sync([13, 11, 12], batch=2) == 3
    assert queued() == [("samples", "batch:backfill:0", [11, 12]),
                        ("samples", "batch:backfill:1", [13])]


@pytest.mark.django_db
def test_enqueue_graph_sync_queues_nothing_for_no_ids():
    assert cmd.enqueue_graph_sync([]) == 0
    assert queued() == []


@pytest.mark.django_db
def test_an_enqueue_failure_does_not_fail_the_command(seek, tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OperationalError("(2006, 'MySQL server has gone away')")

    monkeypatch.setattr(state, "enqueue", broken)

    out = run(tmp_path, [(11, "10.1/a", "999")], "--apply")

    assert "1 sample(s) updated" in out
    assert seek.stored(11)["DOI"] == "10.1/a"
    assert queued() == []
    assert "could not be queued" in out


@pytest.mark.django_db
def test_a_missing_outbox_table_does_not_fail_the_command(seek, tmp_path):
    with connection.cursor() as cur:
        cur.execute('DROP TABLE "graph_sync_outbox"')

    out = run(tmp_path, [(11, "10.1/a", "999")], "--apply")

    assert "1 sample(s) updated" in out
    assert seek.stored(11)["DOI"] == "10.1/a"


@pytest.mark.django_db
def test_the_ids_are_queued_after_their_rows_are_written(seek, tmp_path, monkeypatch):
    """The hook goes after the command's own write: what the drain will read is already in MySQL."""
    seen: list[dict] = []
    real = state.enqueue

    def recording(kind, key, payload=None, **kwargs):
        seen.append({"key": key, "payload": payload,
                     "stored": [seek.stored(sample_id) for sample_id in payload]})
        return real(kind, key, payload, **kwargs)

    monkeypatch.setattr(state, "enqueue", recording)

    run(tmp_path, [(11, "10.1/a", "999"), (12, "10.1/a", "999")], "--apply")

    assert seen[0]["payload"] == [11, 12]
    assert seen[0]["stored"] == [{"Title": "a", "DOI": "10.1/a", "PMID": "999"},
                                 {"Title": "b", "DOI": "10.1/a", "PMID": "999"}]

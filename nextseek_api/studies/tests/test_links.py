"""One link unit (tool spec 7.2) and its undo (7.7), on SQLite."""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from nextseek_api.studies import links
from nextseek_api.studies import planner as p
from nextseek_api.studies.journal import JOURNAL_FILE, Journal, journal_state, read_journal
from nextseek_api.studies.models import AssociationSet, StudyTarget
from nextseek_api.studies.tests.conftest import (FakeReader, links_of, outbox_of, rows_of, seed,
                                                 sqlite_connection)

ORIGINAL = [(101, 1, 1), (101, 2, 2), (101, 3, 2), (102, 1, 1), (102, 4, 2), (301, 6, 1)]


@pytest.fixture
def unit(alpha, seek_db):
    seed(seek_db, alpha)
    aset = AssociationSet(source="replay", source_ref="t", created_at="t",
                          targets=[StudyTarget(key="k", investigation_id=7, title="Paper One", sample_ids=[3])])
    return p.plan_study_moves(aset, FakeReader(alpha), run_id="run-1", now="t").units[0]


CLONES = {("k", 101): 302}


def _run(seek_db, unit, journal):
    with sqlite_connection(seek_db) as conn:
        return links.run_link_unit(conn, unit, journal, CLONES, run_id="run-1")


def _events(tmp_path):
    return [(l["step"], l["event"]) for l in read_journal(tmp_path / JOURNAL_FILE)[0]]


def test_a_unit_moves_its_links_reads_them_back_and_writes_its_outbox_row(tmp_path, seek_db, unit):
    journal = Journal(tmp_path / JOURNAL_FILE, run_id="run-1")
    result = _run(seek_db, unit, journal)
    assert links_of(seek_db) == [(101, 1, 1), (101, 2, 2), (102, 1, 1), (102, 4, 2), (301, 6, 1),
                                 (302, 3, 2), (302, 2, 1)]
    assert result.inserted == [[8, 302, 2], [7, 302, 3]] and result.outbox_in_transaction
    assert outbox_of(seek_db) == [("samples", "batch:studies:run-1:1", [2, 3])]
    lines, _ = read_journal(tmp_path / JOURNAL_FILE)
    assert [(l["step"], l["event"]) for l in lines] == [("links", "intent"), ("links", "prepared")]
    [deleted] = lines[0]["deleted_rows"]
    assert (deleted["id"], deleted["assay_id"], deleted["asset_id"], deleted["direction"],
            deleted["asset_type"]) == (3, 101, 3, 2, "Sample")
    assert lines[1]["outbox"] == "in_transaction" and lines[1]["inserted"] == [[8, 302, 2], [7, 302, 3]]


def test_a_changed_source_assay_stops_the_unit_before_any_write(tmp_path, seek_db, unit):
    with seek_db.begin() as conn:
        conn.execute(text("INSERT INTO assay_assets (assay_id, asset_id, asset_type, direction) "
                          "VALUES (101, 5, 'Sample', 1)"))
    with pytest.raises(links.LinkRefused) as exc:
        _run(seek_db, unit, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"))
    assert exc.value.reason == "digest_mismatch"
    assert _events(tmp_path) == [] and len(links_of(seek_db)) == len(ORIGINAL) + 1


def test_a_read_back_that_misses_a_pair_rolls_the_unit_back(tmp_path, seek_db, unit, monkeypatch):
    monkeypatch.setattr(links, "batch_insert_assay_assets", lambda records, conn: 0)
    with pytest.raises(links.LinkRefused) as exc:
        _run(seek_db, unit, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"))
    assert exc.value.reason == "readback_missing"
    assert links_of(seek_db) == ORIGINAL and outbox_of(seek_db) == []


def test_a_refused_outbox_row_goes_in_after_the_commit(tmp_path, seek_db, unit):
    with seek_db.begin() as conn:
        conn.execute(text("INSERT INTO dmac.graph_sync_outbox (kind, key, payload, attempts) "
                          "VALUES ('samples', 'batch:studies:run-1:1', '[]', 0)"))
    result = _run(seek_db, unit, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"))
    assert not result.outbox_in_transaction and result.sample_ids == [2, 3]
    assert read_journal(tmp_path / JOURNAL_FILE)[0][-1]["outbox"] == "after_commit"


def test_rows_are_locked_for_update_only_on_mysql():
    for dialect, locked in (("mysql", True), ("sqlite", False)):
        conn = MagicMock()
        conn.dialect.name = dialect
        conn.execute.return_value.fetchall.return_value = []
        links.current_digest(conn, [101], lock=True)
        sent = str(conn.execute.call_args_list[0].args[0])
        assert sent.rstrip().endswith("FOR UPDATE") is locked


def _committed_state(tmp_path, seek_db, unit):
    _run(seek_db, unit, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"))
    return journal_state(read_journal(tmp_path / JOURNAL_FILE)[0]).units[unit.unit]


def test_undo_puts_every_row_back_with_its_own_id(tmp_path, seek_db, unit):
    state = _committed_state(tmp_path, seek_db, unit)
    with sqlite_connection(seek_db) as conn:
        report = links.undo_link_unit(conn, unit.unit, state, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"),
                                      run_id="run-1")
    assert (report["deleted"], report["reinserted"], report["not_deleted_changed"], report["not_reinserted"]) == (
        2, 1, [], [])
    assert sorted(links_of(seek_db)) == sorted(ORIGINAL)
    assert (3, 101, 3) in rows_of(seek_db)
    assert outbox_of(seek_db)[-1] == ("samples", "batch:studies:run-1:undo:1", [2, 3])


def test_undo_reports_rows_changed_since_and_leaves_them(tmp_path, seek_db, unit):
    state = _committed_state(tmp_path, seek_db, unit)
    with seek_db.begin() as conn:
        conn.execute(text("UPDATE assay_assets SET asset_id = 9 WHERE id = 7"))
        conn.execute(text("INSERT INTO assay_assets (assay_id, asset_id, asset_type, direction) "
                          "VALUES (101, 3, 'Sample', 2)"))
    with sqlite_connection(seek_db) as conn:
        report = links.undo_link_unit(conn, unit.unit, state, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"),
                                      run_id="run-1")
    assert report["not_deleted_changed"] == [7] and report["deleted"] == 1
    assert report["not_reinserted"] == [3] and report["reinserted"] == 0


def test_a_unit_writes_one_outbox_row_per_chunk_the_first_under_the_unit_key(tmp_path, seek_db, unit, monkeypatch):
    monkeypatch.setattr(links, "SAMPLE_CHUNK", 1)
    result = _run(seek_db, unit, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"))
    assert result.outbox_in_transaction
    assert outbox_of(seek_db) == [("samples", "batch:studies:run-1:1", [2]),
                                  ("samples", "batch:studies:run-1:1:1", [3])]
    assert links.outbox_rows("batch:studies:run-1:1", [3, 2, 3]) == [("batch:studies:run-1:1", [2]),
                                                                      ("batch:studies:run-1:1:1", [3])]

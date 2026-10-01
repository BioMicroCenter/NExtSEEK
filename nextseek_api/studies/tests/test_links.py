"""One link unit (tool spec 7.2) and its undo (7.7), on SQLite."""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from nextseek_api.studies import links
from nextseek_api.studies import planner as p
from nextseek_api.studies.journal import JOURNAL_FILE, Journal, journal_state, read_journal
from nextseek_api.studies.models import AssociationSet, ProjectInsert, StudyTarget
from nextseek_api.studies.tests.conftest import (FakeReader, links_of, outbox_of, projects_of, rows_of, seed,
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


# --- a share's unit: project rows (tool spec 16.2 step 4, T36) ------------------------------------------------------

SHARE_CLONES = {("share:40", 101): 402}


def _share_plan(world):
    from nextseek_api.studies import share as sh
    from nextseek_api.studies.models import ShareInput

    inp = ShareInput(sample_uids=[world.samples[2]["uuid"]], source_project_id=3, destination_project_id=5,
                     destination_study_id=40, created_at="t")
    return sh.plan_share(inp, FakeReader(world), run_id="share-1", now="t")


def _run_share(seek_db, unit, tmp_path):
    with sqlite_connection(seek_db) as conn:
        return links.run_link_unit(conn, unit, Journal(tmp_path / JOURNAL_FILE, run_id="share-1"), SHARE_CLONES,
                                   run_id="share-1", share_project_id=5)


@pytest.fixture
def share_unit(share, seek_db):
    seed(seek_db, share)
    return _share_plan(share).units[0]


def test_a_share_unit_adds_links_project_rows_and_one_outbox_row(tmp_path, seek_db, share_unit):
    result = _run_share(seek_db, share_unit, tmp_path)
    assert {(a, s, d) for a, s, d in links_of(seek_db) if a == 402} == {(402, 2, 2), (402, 1, 1)}
    assert {(5, 1), (5, 2)} <= set(projects_of(seek_db))
    assert outbox_of(seek_db) == [("samples", "batch:studies:share-1:1", [1, 2])] and result.outbox_in_transaction
    lines = read_journal(tmp_path / JOURNAL_FILE)[0]
    assert lines[0]["project_inserts"] == [[5, 1], [5, 2]] and lines[1]["project_pairs_inserted"] == [[5, 1], [5, 2]]


def test_a_project_pair_present_before_the_unit_is_neither_inserted_nor_journaled(tmp_path, seek_db, share):
    share.sample_projects[1] |= {5}
    seed(seek_db, share)
    unit = _share_plan(share).units[0]
    assert [(r.project_id, r.sample_id) for r in unit.project_inserts] == [(5, 2)]
    unit = unit.model_copy(update={"project_inserts": [*unit.project_inserts,
                                                       ProjectInsert(project_id=5, sample_id=1, role="parent")]})
    _run_share(seek_db, unit, tmp_path)
    assert read_journal(tmp_path / JOURNAL_FILE)[0][-1]["project_pairs_inserted"] == [[5, 2]]
    assert projects_of(seek_db).count((5, 1)) == 1


def test_a_failure_after_the_project_insert_rolls_both_back(tmp_path, seek_db, share_unit, monkeypatch):
    before = projects_of(seek_db)
    monkeypatch.setattr(links, "existing_membership_ids", lambda pairs, conn: {})
    with pytest.raises(links.LinkRefused):
        _run_share(seek_db, share_unit, tmp_path)
    assert projects_of(seek_db) == before and not any(a == 402 for a, _s, _d in links_of(seek_db))
    assert outbox_of(seek_db) == []


def test_the_read_back_refuses_when_a_project_pair_is_missing(tmp_path, seek_db, share_unit, monkeypatch):
    monkeypatch.setattr(links, "batch_insert_projects_samples", lambda project_id, ids, conn: 0)
    with pytest.raises(links.LinkRefused) as exc:
        _run_share(seek_db, share_unit, tmp_path)
    assert exc.value.reason == "readback_missing" and "project" in exc.value.detail


def test_a_share_digest_ignores_unplanned_samples_and_sees_planned_ones(tmp_path, seek_db, share_unit):
    with seek_db.begin() as conn:
        conn.execute(text("INSERT INTO assay_assets (assay_id, asset_id, asset_type, direction) "
                          "VALUES (101, 5, 'Sample', 1)"))
    _run_share(seek_db, share_unit, tmp_path)
    with seek_db.begin() as conn:
        conn.exec_driver_sql("DELETE FROM assay_assets WHERE assay_id = 402")
        conn.exec_driver_sql("DELETE FROM projects_samples WHERE project_id = 5")
        conn.exec_driver_sql("DELETE FROM dmac.graph_sync_outbox")
        conn.execute(text("INSERT INTO projects_samples (project_id, sample_id) VALUES (5, 2)"))
    with pytest.raises(links.LinkRefused) as exc:
        _run_share(seek_db, share_unit, tmp_path)
    assert exc.value.reason == "digest_mismatch"


def test_a_share_undo_deletes_only_the_journaled_pairs_and_reports_one_gone(tmp_path, seek_db, share_unit):
    _run_share(seek_db, share_unit, tmp_path)
    with seek_db.begin() as conn:
        conn.exec_driver_sql("DELETE FROM projects_samples WHERE project_id = 5 AND sample_id = 2")
    state = journal_state(read_journal(tmp_path / JOURNAL_FILE)[0]).units[1]
    with sqlite_connection(seek_db) as conn:
        report = links.undo_link_unit(conn, 1, state, Journal(tmp_path / JOURNAL_FILE, run_id="share-1"),
                                      run_id="share-1")
    assert (report["project_pairs_deleted"], report["project_pairs_gone"]) == (1, [[5, 2]])
    assert not any(p == 5 for p, _s in projects_of(seek_db)) and (3, 1) in projects_of(seek_db)
    assert outbox_of(seek_db)[-1] == ("samples", "batch:studies:share-1:undo:1", [1, 2])


# --- what the unit checks and reports, each rule pinned by a test ----------------------------------------------------

def test_a_planned_pair_already_in_its_target_assay_refuses_the_unit_before_any_write(tmp_path, seek_db, unit):
    with seek_db.begin() as conn:     # another writer links sample 3 to the clone after the plan
        conn.execute(text("INSERT INTO assay_assets (assay_id, asset_id, asset_type, direction) "
                          "VALUES (302, 3, 'Sample', 1)"))
    before = rows_of(seek_db)
    with pytest.raises(links.LinkRefused) as exc:
        _run(seek_db, unit, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"))
    assert exc.value.reason == "clone_changed"
    assert _events(tmp_path) == [] and rows_of(seek_db) == before and outbox_of(seek_db) == []


def test_a_removal_pair_left_after_the_delete_rolls_the_unit_back(tmp_path, seek_db, unit, monkeypatch):
    monkeypatch.setattr(links, "delete_assay_links", lambda removals, conn: 0)
    with pytest.raises(links.LinkRefused) as exc:
        _run(seek_db, unit, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"))
    assert exc.value.reason == "readback_removal_left"
    assert links_of(seek_db) == ORIGINAL and outbox_of(seek_db) == []


def test_the_unit_locks_its_source_rows_its_members_and_its_clone_rows_on_mysql(unit):
    sent = []

    def execute(statement, params=None):
        sql = str(statement)
        sent.append(sql)
        result = MagicMock()
        if "FROM assay_assets" in sql and ":a0" in sql and params.get("a0") == 101:
            result.fetchall.return_value = [(3, 101, 3, 1, None, None, None, "Sample", 2)]
        elif "FROM samples" in sql:
            result.fetchall.return_value = [(3, "{}")]
        else:
            result.fetchall.return_value = []
        return result

    conn = MagicMock()
    conn.dialect.name = "mysql"
    conn.execute.side_effect = execute
    with pytest.raises(links.LinkRefused):
        links.run_link_unit(conn, unit, MagicMock(), CLONES, run_id="run-1")
    reads = [s for s in sent if s.lstrip().startswith("SELECT")]
    assert any("FROM assay_assets" in s and s.rstrip().endswith("FOR UPDATE") and "101" not in s for s in reads)
    assert sum(1 for s in reads if "FROM assay_assets" in s and s.rstrip().endswith("FOR UPDATE")) == 2
    assert any("FROM samples" in s and s.rstrip().endswith("FOR UPDATE") for s in reads)


def test_undo_restores_a_deleted_row_with_every_column_it_had(tmp_path, seek_db, unit):
    with seek_db.begin() as conn:
        conn.execute(text("UPDATE assay_assets SET version = 4, created_at = '2025-02-03 04:05:06', "
                          "updated_at = '2025-03-04 05:06:07', relationship_type_id = 9 WHERE id = 3"))
    with seek_db.connect() as conn:
        before = conn.execute(text("SELECT * FROM assay_assets WHERE id = 3")).fetchone()
    state = _committed_state(tmp_path, seek_db, unit)
    with sqlite_connection(seek_db) as conn:
        links.undo_link_unit(conn, unit.unit, state, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"),
                             run_id="run-1")
    with seek_db.connect() as conn:
        after = conn.execute(text("SELECT * FROM assay_assets WHERE id = 3")).fetchone()
    assert tuple(after) == tuple(before)


def test_a_refused_later_chunk_row_sends_every_row_after_the_commit(tmp_path, seek_db, unit, monkeypatch):
    monkeypatch.setattr(links, "SAMPLE_CHUNK", 1)
    with seek_db.begin() as conn:
        conn.execute(text("INSERT INTO dmac.graph_sync_outbox (kind, key, payload, attempts) "
                          "VALUES ('samples', 'batch:studies:run-1:1:1', '[]', 0)"))
    result = _run(seek_db, unit, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"))
    assert not result.outbox_in_transaction


def test_the_outbox_row_is_written_before_the_read_back(tmp_path, seek_db, unit, monkeypatch):
    order = []
    real_enqueue, real_read = links._enqueue, links.existing_membership_ids
    monkeypatch.setattr(links, "_enqueue", lambda *a: order.append("outbox") or real_enqueue(*a))
    monkeypatch.setattr(links, "existing_membership_ids", lambda *a: order.append("read") or real_read(*a))
    _run(seek_db, unit, Journal(tmp_path / JOURNAL_FILE, run_id="run-1"))
    assert order[-1] == "read" and "outbox" in order


def test_an_undo_run_twice_reports_its_own_rows_as_gone_not_changed(tmp_path, seek_db, unit):
    state = _committed_state(tmp_path, seek_db, unit)
    journal = Journal(tmp_path / JOURNAL_FILE, run_id="run-1")
    with sqlite_connection(seek_db) as conn:
        links.undo_link_unit(conn, unit.unit, state, journal, run_id="run-1")
    with sqlite_connection(seek_db) as conn:
        again = links.undo_link_unit(conn, unit.unit, state, journal, run_id="run-1")
    assert again["not_deleted_changed"] == [] and sorted(again["not_deleted_gone"]) == [7, 8]
    assert again["deleted"] == 0 and again["reinserted"] == 0

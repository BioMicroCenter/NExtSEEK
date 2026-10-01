"""The snapshot reader (tool spec 5, 6.1): each read's parse, against scripted rows."""
import pytest

from nextseek_api.graph_sync import sources, writer
from nextseek_api.studies import snapshot
from nextseek_api.studies.tests.conftest import FakeDriver


class Rows:
    """``snapshot._rows`` scripted by a marker in the SQL; records every statement."""

    def __init__(self, table):
        self.table = table
        self.sent = []

    def __call__(self, alias, sql, params=None):
        self.sent.append((alias, sql, list(params or [])))
        for marker, rows in self.table:
            if marker in sql:
                if isinstance(rows, Exception):
                    raise rows
                return list(rows)
        raise AssertionError(f"unexpected SQL {sql}")


@pytest.fixture
def reader(monkeypatch):
    def make(table, driver=None, session=None):
        rows = Rows(table)
        monkeypatch.setattr(snapshot, "_rows", rows)
        return snapshot.SnapshotReader(session, driver, "neo4j"), rows
    return make


def test_studies_and_buckets(reader):
    r, _ = reader([("FROM studies", [(20, 7, "Alpha Unpublished", None), (21, 7, "Paper", "d")])])
    assert r.studies()[1] == snapshot.StudyRow(21, 7, "Paper", "d")
    assert dict(r.buckets().by_investigation) == {7: 20}


def test_memberships_keep_a_pairs_first_row(reader):
    r, rows = reader([("FROM assay_assets", [(3, 101, 2), (3, 101, 1), (3, 102, None)])])
    assert r.memberships([3]) == {3: {101: 2, 102: None}}
    assert rows.sent[0][2] == ["Sample", 3]


def test_assay_rows_keep_every_row_in_order(reader):
    r, _ = reader([("FROM assay_assets", [(101, 1, 1), (101, 1, 1), (101, 2, 2)])])
    assert r.assay_rows([101]) == {101: [(1, 1), (1, 1), (2, 2)]}


def test_mapping_rows_keep_duplicates_and_drop_nulls(reader):
    r, rows = reader([("FROM assays_internal_assays", [(101, 900), (101, 900), (102, 901)])])
    assert r.mapping_rows([101, 102, 103]) == {101: [900, 900], 102: [901], 103: []}
    assert "internal_assay_id IS NOT NULL" in rows.sent[0][1]


def test_next_study_id_reads_auto_increment_and_tolerates_no_expiry_setting(reader):
    from django.db import DatabaseError

    r, rows = reader([("information_schema_stats_expiry", DatabaseError("unknown variable")),
                      ("AUTO_INCREMENT", [(64,)])])
    assert r.next_study_id() == 64
    r, _ = reader([("information_schema_stats_expiry", []), ("AUTO_INCREMENT", [(None,)]),
                   ("MAX(id)", [(57,)])])
    assert r.next_study_id() == 57


def test_graph_reads_are_read_only(reader, monkeypatch):
    driver = FakeDriver({snapshot.GRAPH_MAX_STUDY_ID: lambda p: [{"n": 63}]})
    r, _ = reader([], driver=driver)
    assert r.graph_max_study_id() == 63
    assert all(c.read for c in driver.calls)
    monkeypatch.setattr(writer, "edges_incident", lambda d, db, ids: [{"ids": list(ids)}])
    assert r.stored_edges({3, 2}) == [{"ids": [2, 3]}]


def test_the_seek_gets_go_through_the_session(reader):
    class Session:
        def get_assay(self, assay_id):
            return {"assay": assay_id}

        def get_study(self, study_id):
            return {"study": study_id}

    r, _ = reader([], session=Session())
    assert r.assay_representation(101) == {"assay": 101} and r.study_representation(20) == {"study": 20}


def test_sample_rows_and_the_uid_index_come_from_graph_syncs_readers(reader, monkeypatch):
    monkeypatch.setattr(sources, "samples_by_ids", lambda ids: [{"id": i, "uuid": f"u{i}", "json_metadata": "{}",
                                                                   "title": "", "sample_type_id": 1} for i in ids])
    monkeypatch.setattr(sources, "uuid_to_ids_for", lambda tokens: {t: [1] for t in tokens})
    r, _ = reader([])
    assert r.sample_rows([4])[4]["uuid"] == "u4"
    assert r.uuid_index(["TIS-260101AAA-1"]) == {"TIS-260101AAA-1": [1]}


def test_the_share_reads_projects_and_a_projects_investigations(reader):
    r, rows = reader([("FROM projects WHERE", [(3,), (5,)]),
                      ("FROM investigations_projects WHERE project_id", [(7,), (9,)])])
    assert r.project_ids_present([3, 5, 404]) == {3, 5}
    assert r.project_investigations(5) == {7, 9}
    assert rows.sent[1][2] == [5]


def test_a_studys_policy_is_read_from_seeks_tables_in_the_apis_form(reader):
    r, rows = reader([("JOIN policies", [(12, 0)]),
                      ("FROM permissions", [("Project", 3, 4), ("Person", 55, 2), ("WorkGroup", 8, 1)])])
    assert r.study_policy(40) == {"access": "no_access", "permissions": [
        {"resource": {"id": "3", "type": "projects"}, "access": "manage"},
        {"resource": {"id": "55", "type": "people"}, "access": "download"},
        {"resource": {"id": "8", "type": "work_groups"}, "access": "view"}]}
    assert rows.sent[0][2] == [40] and rows.sent[1][2] == [12] and "ORDER BY created_at, id" in rows.sent[1][1]


@pytest.mark.parametrize("policy, permissions", [([], []), ([(12, 9)], []), ([(12, 1)], [("Martian", 3, 4)]),
                                                 ([(12, 1)], [("Project", 3, -1)]), ([(12, None)], [])])
def test_a_policy_the_api_form_cannot_carry_reads_as_none(reader, policy, permissions):
    r, _ = reader([("JOIN policies", policy), ("FROM permissions", permissions)])
    assert r.study_policy(40) is None

"""Unit tests for nextseek_api.graph_sync.sources (the v1.1 MySQL readers).

No database: `sources.connections` is replaced by fake connections whose cursors record every
`execute` call and hand back canned rows. The ORM read of `sample_types_context` is replaced at
its seam, `_context_rows`, and the field names it selects are pinned against the model so the
capital-T `Tags` db_column cannot come back as a silently empty catalog.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from nextseek_api.graph_sync import sources


class FakeCursor:
    """Records (sql, params) per execute and serves one canned result per execute, in order."""

    def __init__(self, results):
        self.results = list(results)
        self.executed = []
        self._rows = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), list(params) if params is not None else None))
        self._rows = list(self.results.pop(0)) if self.results else []

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchmany(self, size=1):
        rows, self._rows = self._rows[:size], self._rows[size:]
        return rows

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Introspection:
    def __init__(self, tables):
        self.tables = list(tables)

    def table_names(self, cursor=None, include_views=False):
        return list(self.tables)


class FakeConnection:
    def __init__(self, results=(), tables=()):
        self.cursor_obj = FakeCursor(results)
        self.introspection = _Introspection(tables)

    def cursor(self):
        return self.cursor_obj


@pytest.fixture
def fake_db(monkeypatch):
    """Install fake `seek` and `default` connections; returns a setter taking results and tables."""
    conns = {}

    def install(seek_results=(), dmac_results=(), seek_tables=(), dmac_tables=()):
        conns["seek"] = FakeConnection(seek_results, seek_tables)
        conns["default"] = FakeConnection(dmac_results, dmac_tables)
        monkeypatch.setattr(sources, "connections", conns)
        return conns["seek"].cursor_obj, conns["default"].cursor_obj

    return install


# --- iter_samples ------------------------------------------------------------------------------

def _srow(i, meta=None):
    return (i, f"TIS-220119FLY-{i}", f"t{i}", 26, json.dumps(meta or {}))


def test_iter_samples_pages_by_keyset_and_advances_after_id(fake_db):
    seek, _ = fake_db(seek_results=[[_srow(3), _srow(8)], [_srow(12)]])
    pages = list(sources.iter_samples(chunk=2))
    assert [[r["id"] for r in page] for page in pages] == [[3, 8], [12]]
    assert len(seek.executed) == 2
    for sql, _params in seek.executed:
        assert "FROM samples" in sql
        assert "WHERE id > %s ORDER BY id LIMIT %s" in sql
    assert seek.executed[0][1] == [0, 2]
    assert seek.executed[1][1] == [8, 2]


def test_iter_samples_rows_carry_the_five_columns(fake_db):
    fake_db(seek_results=[[_srow(3, {"Organ": "Lung"})]])
    (page,) = list(sources.iter_samples(chunk=10))
    assert page == [{"id": 3, "uuid": "TIS-220119FLY-3", "title": "t3", "sample_type_id": 26,
                     "json_metadata": '{"Organ": "Lung"}'}]


def test_iter_samples_starts_after_the_given_id_and_stops_on_an_empty_page(fake_db):
    seek, _ = fake_db(seek_results=[[_srow(101), _srow(102)], []])
    pages = list(sources.iter_samples(chunk=2, after_id=100))
    assert [[r["id"] for r in page] for page in pages] == [[101, 102]]
    assert [params for _sql, params in seek.executed] == [[100, 2], [102, 2]]


def test_iter_samples_decodes_bytes_metadata(fake_db):
    fake_db(seek_results=[[(1, "U", "t", 26, b'{"A": "\xc3\xa9"}')]])
    (page,) = list(sources.iter_samples(chunk=5))
    assert page[0]["json_metadata"] == '{"A": "é"}'


def test_iter_samples_rejects_a_non_positive_chunk():
    with pytest.raises(ValueError):
        next(sources.iter_samples(chunk=0))


# --- sample_projects ---------------------------------------------------------------------------

def test_sample_projects_deduplicates_and_sorts(fake_db):
    seek, _ = fake_db(seek_results=[[(5, 6), (5, 2), (5, 2), (7, 1), (5, 6)]])
    assert sources.sample_projects() == {5: [2, 6], 7: [1]}
    assert "FROM projects_samples" in seek.executed[0][0]


# --- catalog sources ---------------------------------------------------------------------------

def test_sample_types_rows(fake_db):
    fake_db(seek_results=[[(10, "MUS", "uuid-mus", "Mouse Sample")]])
    assert sources.sample_types() == [
        {"id": 10, "title": "MUS", "uuid": "uuid-mus", "description": "Mouse Sample"}]


def test_sample_attributes_rows_have_booleans(fake_db):
    fake_db(seek_results=[[(7, 26, "Manufacturer ", 3, 1, 0, 7, "d"), (8, 26, "UID", 1, None, 1, 7, None)]])
    assert sources.sample_attributes() == [
        {"id": 7, "sample_type_id": 26, "title": "Manufacturer ", "pos": 3, "required": True,
         "is_title": False, "sample_attribute_type_id": 7, "description": "d"},
        {"id": 8, "sample_type_id": 26, "title": "UID", "pos": 1, "required": False,
         "is_title": True, "sample_attribute_type_id": 7, "description": None},
    ]


def test_sample_attribute_types_by_id(fake_db):
    fake_db(seek_results=[[(3, "Real number", "Float", ".*"), (7, "Text", "Text", ".*")]])
    assert sources.sample_attribute_types() == {
        3: {"id": 3, "title": "Real number", "base_type": "Float", "regexp": ".*"},
        7: {"id": 7, "title": "Text", "base_type": "Text", "regexp": ".*"},
    }


def test_type_context_is_empty_when_the_table_is_absent(fake_db, monkeypatch):
    fake_db(dmac_tables=["sample_attributes_unique", "clades"])

    def boom():
        raise AssertionError("the ORM must not be queried when the table is absent")

    monkeypatch.setattr(sources, "_context_rows", boom)
    assert sources.type_context() == {}


def test_type_context_keys_are_byte_exact_titles_and_first_row_wins(fake_db, monkeypatch):
    fake_db(dmac_tables=["sample_types_context"])
    rows = [
        {"sample_type": "TIS", "sampletype_id": 26, "name": "Tissue", "description": "d",
         "tags": "tissue, biopsy", "parent_sampletypes": "MUS or PAV", "child_sampletypes": "DNA",
         "clade": "Processed"},
        {"sample_type": "tis", "sampletype_id": 99, "name": "lower", "description": None,
         "tags": None, "parent_sampletypes": None, "child_sampletypes": None, "clade": None},
        {"sample_type": "TIS", "sampletype_id": 26, "name": "second", "description": None,
         "tags": None, "parent_sampletypes": None, "child_sampletypes": None, "clade": None},
        {"sample_type": None, "sampletype_id": 1, "name": "no code", "description": None,
         "tags": None, "parent_sampletypes": None, "child_sampletypes": None, "clade": None},
    ]
    monkeypatch.setattr(sources, "_context_rows", lambda: [dict(r) for r in rows])
    context = sources.type_context()
    assert sorted(context) == ["TIS", "tis"]
    assert context["TIS"]["name"] == "Tissue"
    assert context["TIS"]["tags"] == "tissue, biopsy"
    assert context["tis"]["name"] == "lower"


def test_context_fields_are_real_model_fields_and_use_the_orm_name_tags():
    """The db_column is capital-T Tags; selecting it through the ORM raises FieldError."""
    from seek.models import Sample_types_context

    field_names = {f.name for f in Sample_types_context._meta.get_fields()}
    assert set(sources.CONTEXT_FIELDS) <= field_names, sorted(set(sources.CONTEXT_FIELDS) - field_names)
    assert "tags" in sources.CONTEXT_FIELDS and "Tags" not in sources.CONTEXT_FIELDS
    for key in ("name", "description", "tags", "parent_sampletypes", "child_sampletypes"):
        assert key in sources.CONTEXT_FIELDS


def test_attribute_meanings_are_global_rows_with_byte_exact_keys(fake_db):
    _, dmac = fake_db(dmac_tables=["sample_attributes_unique"], dmac_results=[[
        ("Organ", "", "The organ."),
        ("organ", "", "lowercase twin"),
        ("Organ", "TIS", "a scoped override"),
        ("Sex", " ", "a padded scope is not the global scope"),
        ("Blank", "", None),
        (b"Bead_Catalog#", b"", b"bytes decode"),
    ]])
    assert sources.attribute_meanings() == {
        "Organ": "The organ.", "organ": "lowercase twin", "Bead_Catalog#": "bytes decode"}
    assert "FROM sample_attributes_unique" in dmac.executed[0][0]


def test_attribute_meanings_empty_when_the_table_is_absent(fake_db):
    _, dmac = fake_db(dmac_tables=[])
    assert sources.attribute_meanings() == {}
    assert dmac.executed == []


def test_type_clades_first_row_wins_and_null_clades_are_skipped(fake_db):
    fake_db(dmac_tables=["sample_types_clades", "clades"],
            dmac_results=[[(10, "Source"), (26, None), (10, "Raw"), (33, "Raw")]])
    assert sources.type_clades() == {10: "Source", 33: "Raw"}


def test_type_clades_empty_when_a_table_is_absent(fake_db):
    _, dmac = fake_db(dmac_tables=["clades"])
    assert sources.type_clades() == {}
    assert dmac.executed == []


def test_deprecated_titles_use_the_template_catalog_rule(fake_db):
    fake_db(seek_results=[[(12, "IMG", "u1", "Depreciated"), (13, "A.SEQ", "u2", "Depcreciated"),
                           (10, "MUS", "u3", "Mouse Sample"), (14, "X", "u4", None)]])
    assert sources.deprecated_titles() == {"IMG", "A.SEQ"}


# --- projects, people, investigations, studies -------------------------------------------------

def test_projects_rows(fake_db):
    fake_db(seek_results=[[(2, "IMPACT"), (16, "TCGA")]])
    assert sources.projects() == [{"id": 2, "title": "IMPACT"}, {"id": 16, "title": "TCGA"}]


def test_memberships_one_row_per_person_and_project(fake_db):
    left1, left2 = datetime(2024, 1, 1), datetime(2025, 6, 1)
    seek, _ = fake_db(seek_results=[[
        (1, 2, 0, None), (1, 2, 1, left1),          # still active through one work group
        (3, 2, 1, left1), (3, 2, 1, left2),         # left through every work group
        (4, 5, None, None),                         # NULL has_left means a member
    ]])
    assert sources.memberships() == [
        {"person_id": 1, "project_id": 2, "has_left": False, "time_left_at": None},
        {"person_id": 3, "project_id": 2, "has_left": True, "time_left_at": left2},
        {"person_id": 4, "project_id": 5, "has_left": False, "time_left_at": None},
    ]
    sql = seek.executed[0][0]
    assert "FROM group_memberships" in sql and "JOIN work_groups" in sql


def test_investigation_projects_rows(fake_db):
    fake_db(seek_results=[[(1, 2), (1, 2), (9, 9)]])
    assert sources.investigation_projects() == [
        {"investigation_id": 1, "project_id": 2}, {"investigation_id": 9, "project_id": 9}]


def test_investigations_rows(fake_db):
    fake_db(seek_results=[[(1, "Impact", "desc")]])
    assert sources.investigations() == [{"id": 1, "title": "Impact", "description": "desc"}]


def test_seek_study_links_bind_the_asset_type(fake_db):
    seek, _ = fake_db(seek_results=[[(100, 7, "Study A", 3), (101, 7, "Study A", 3)]])
    links = sources.seek_study_links()
    assert links == [
        {"sample_id": 100, "study_id": 7, "study_title": "Study A", "investigation_id": 3},
        {"sample_id": 101, "study_id": 7, "study_title": "Study A", "investigation_id": 3},
    ]
    sql, params = seek.executed[0]
    assert "FROM assay_assets" in sql and "JOIN assays" in sql and "asset_type = %s" in sql
    assert params == ["Sample"]


# --- lineage -----------------------------------------------------------------------------------

def test_uuid_to_ids_groups_duplicates_and_skips_blanks(fake_db):
    fake_db(seek_results=[[("A-1", 1), ("A-1", 2), ("B-1", 3), (None, 4), ("", 5)]])
    assert sources.uuid_to_ids() == {"A-1": [1, 2], "B-1": [3]}


def _lrow(sid, uuid, meta):
    return {"id": sid, "uuid": uuid, "title": "t", "sample_type_id": 26,
            "json_metadata": meta if isinstance(meta, str) or meta is None else json.dumps(meta)}


def test_declared_lineage_yields_known_uid_tokens_one_pair_per_parent_id():
    index = {"MUS-220119FLY-1": [10], "MUS-220119FLY-2": [11, 12], "notauid": [99],
             "TIS-220119FLY-7": [20]}
    rows = [_lrow(20, "TIS-220119FLY-7", {
        "Parent": "MUS-220119FLY-1; MUS-220119FLY-2;notauid;MUS-220119FLY-404",
        "OtherParent": "MUS-220119FLY-1",
        "Organ": "MUS-220119FLY-2",
    })]
    assert sorted(sources.declared_lineage(rows, index)) == [(20, 10), (20, 11), (20, 12)]


def test_declared_lineage_skips_a_sample_naming_itself():
    index = {"TIS-220119FLY-7": [20, 21], "MUS-220119FLY-1": [10]}
    rows = [_lrow(20, "TIS-220119FLY-7", {"Parent": "TIS-220119FLY-7;MUS-220119FLY-1"}),
            _lrow(30, "TIS-220119FLY-9", {"Parent": "TIS-220119FLY-9"})]
    assert list(sources.declared_lineage(rows, {**index, "TIS-220119FLY-9": [30]})) == [(20, 10)]


def test_declared_lineage_tolerates_unreadable_metadata():
    index = {"MUS-220119FLY-1": [10]}
    rows = [_lrow(1, "U-1", "{not json"), _lrow(2, "U-2", None), _lrow(3, "U-3", "[1, 2]"),
            _lrow(4, "U-4", {"Parent": "MUS-220119FLY-1"})]
    assert list(sources.declared_lineage(rows, index)) == [(4, 10)]


# --- table_exists ------------------------------------------------------------------------------

def test_table_exists_checks_the_named_connection(fake_db):
    fake_db(seek_tables=["samples"], dmac_tables=["sample_types_context"])
    assert sources.table_exists("seek", "samples") is True
    assert sources.table_exists("seek", "sample_types_context") is False
    assert sources.table_exists("default", "sample_types_context") is True


# --- by-id readers (spec 7.2) ------------------------------------------------------------------

def _in_params(executed):
    """The bound ids of each execute, without any leading non-id parameter."""
    return [[p for p in params if isinstance(p, int)] for _sql, params in executed]


def test_samples_by_ids_reads_the_five_columns_in_id_order(fake_db):
    seek, _ = fake_db(seek_results=[[_srow(3, {"Organ": "Lung"}), _srow(8)]])
    rows = sources.samples_by_ids([8, 3, 8, "3"])
    assert [r["id"] for r in rows] == [3, 8]
    assert rows[0] == {"id": 3, "uuid": "TIS-220119FLY-3", "title": "t3", "sample_type_id": 26,
                       "json_metadata": '{"Organ": "Lung"}'}
    sql, params = seek.executed[0]
    assert "FROM samples WHERE id IN (%s, %s)" in sql
    assert params == [3, 8]


def test_samples_by_ids_chunks_the_in_list_at_1000(fake_db):
    seek, _ = fake_db(seek_results=[[], [], []])
    assert sources.samples_by_ids(range(1, 2501)) == []
    assert [len(p) for p in _in_params(seek.executed)] == [1000, 1000, 500]
    assert _in_params(seek.executed)[1][0] == 1001
    assert all("%s" in sql and "2500" not in sql for sql, _ in seek.executed)


def test_samples_by_ids_runs_no_query_for_no_ids(fake_db):
    seek, _ = fake_db()
    assert sources.samples_by_ids([]) == []
    assert seek.executed == []


def test_sample_projects_for_deduplicates_sorts_and_skips_unlinked_ids(fake_db):
    seek, _ = fake_db(seek_results=[[(5, 6), (5, 2), (5, 2), (7, 1)]])
    assert sources.sample_projects_for([5, 7, 9]) == {5: [2, 6], 7: [1]}
    sql, params = seek.executed[0]
    assert "FROM projects_samples" in sql and "sample_id IN (%s, %s, %s)" in sql
    assert params == [5, 7, 9]


def test_sample_assay_ids_for_binds_the_asset_type_and_chunks(fake_db):
    seek, _ = fake_db(seek_results=[[(5, 40), (5, 30), (5, 40)], [(1500, 7)]])
    assert sources.sample_assay_ids_for(range(1, 1501)) == {5: [30, 40], 1500: [7]}
    assert len(seek.executed) == 2
    for sql, params in seek.executed:
        assert "FROM assay_assets" in sql and "asset_type = %s" in sql
        assert params[0] == "Sample"
    assert [len(p) for p in _in_params(seek.executed)] == [1000, 500]


def test_uuid_to_ids_for_keeps_only_byte_exact_uuids_and_chunks(fake_db):
    tokens = [f"MUS-220119FLY-{i}" for i in range(1, 1201)] + ["", None, "MUS-220119FLY-1"]
    # MySQL's collation also returns a case twin and a padded twin; each chunk may return a row
    # whose stored uuid is exact for a token of another chunk, which must not be counted twice.
    seek, _ = fake_db(seek_results=[
        [("MUS-220119FLY-1", 10), ("mus-220119fly-1", 11), ("MUS-220119FLY-1 ", 12),
         ("MUS-220119FLY-2", 20), ("MUS-220119FLY-2", 21)],
        [("MUS-220119FLY-1200", 30), ("MUS-220119FLY-1", 10)],
    ])
    assert sources.uuid_to_ids_for(tokens) == {
        "MUS-220119FLY-1": [10], "MUS-220119FLY-2": [20, 21], "MUS-220119FLY-1200": [30]}
    assert [len(params) for _sql, params in seek.executed] == [1000, 200]
    assert "FROM samples WHERE uuid IN" in seek.executed[0][0]


def test_uuid_to_ids_for_runs_no_query_for_blank_tokens(fake_db):
    seek, _ = fake_db()
    assert sources.uuid_to_ids_for(["", None]) == {}
    assert seek.executed == []


def test_parent_identities_use_the_batch_upload_identity_rule(fake_db):
    from nextseek_api.batch_upload.identity import extract_identity

    meta = {"Name": "Mouse 1", "Organ": "Lung"}
    seek, _ = fake_db(seek_results=[[
        ("MUS-220119FLY-1", json.dumps(meta)),
        ("MUS-220119FLY-2", "{not json"),
        ("MUS-220119FLY-3", "[1, 2]"),
        ("mus-220119fly-4", json.dumps({"Name": "case twin"})),
        ("MUS-220119FLY-5", json.dumps({"Name": "older"})),
        ("MUS-220119FLY-5", json.dumps({"Name": "newer"})),
    ]])
    got = sources.parent_identities(
        ["MUS-220119FLY-1", "MUS-220119FLY-2", "MUS-220119FLY-3", "MUS-220119FLY-4",
         "MUS-220119FLY-5", "MUS-220119FLY-404"])
    assert got == {
        "MUS-220119FLY-1": extract_identity(meta, uid="MUS-220119FLY-1"),
        "MUS-220119FLY-2": None,
        "MUS-220119FLY-3": None,
        "MUS-220119FLY-5": "newer",
    }
    assert got["MUS-220119FLY-1"] == "Mouse 1"
    sql, _params = seek.executed[0]
    assert "SELECT uuid, json_metadata FROM samples WHERE uuid IN" in sql and "ORDER BY id" in sql


def test_seek_study_links_for_restricts_to_the_ids(fake_db):
    seek, _ = fake_db(seek_results=[[(100, 7, b"Study A", 3), (101, 8, "Study B", None)]])
    assert sources.seek_study_links_for([101, 100]) == [
        {"sample_id": 100, "study_id": 7, "study_title": "Study A", "investigation_id": 3},
        {"sample_id": 101, "study_id": 8, "study_title": "Study B", "investigation_id": None},
    ]
    sql, params = seek.executed[0]
    assert "FROM assay_assets" in sql and "JOIN studies" in sql and "asset_type = %s" in sql
    assert "aa.asset_id IN (%s, %s)" in sql
    assert params == ["Sample", 100, 101]


def test_seek_study_links_for_runs_no_query_for_no_ids(fake_db):
    seek, _ = fake_db()
    assert sources.seek_study_links_for([]) == []
    assert seek.executed == []


# --- keyed streams -----------------------------------------------------------------------------

def test_ids_of_type_pages_by_keyset_within_the_type(fake_db):
    seek, _ = fake_db(seek_results=[[(4,), (9,)], [(15,)]])
    assert list(sources.ids_of_type(26, chunk=2)) == [[4, 9], [15]]
    for sql, _params in seek.executed:
        assert "FROM samples WHERE sample_type_id = %s AND id > %s ORDER BY id LIMIT %s" in sql
    assert [params for _sql, params in seek.executed] == [[26, 0, 2], [26, 9, 2]]


def test_ids_of_type_rejects_a_non_positive_chunk():
    with pytest.raises(ValueError):
        next(sources.ids_of_type(26, chunk=0))


def test_samples_naming_finds_old_rows_that_name_the_uuids(fake_db):
    seek, _ = fake_db(seek_results=[
        [(1, "TIS-220119FLY-1", json.dumps({"Parent": "MUS-220119FLY-9; MUS-220119FLY-2"})),
         (2, "TIS-220119FLY-2", json.dumps({"Organ": "MUS-220119FLY-9"})),
         (3, "MUS-220119FLY-9", json.dumps({"Parent": "MUS-220119FLY-9"}))],
        [(4, "TIS-220119FLY-4", json.dumps({"OtherParent": "mus-220119fly-9"})),
         (5, "TIS-220119FLY-5", "{not json"),
         (6, "TIS-220119FLY-6", json.dumps({"Sample Parent": "MUS-220119FLY-8"}))],
        [],
    ])
    assert sources.samples_naming({"MUS-220119FLY-9", "MUS-220119FLY-8"}, chunk=3) == [1, 6]
    assert [params for _sql, params in seek.executed] == [[0, 3], [3, 3], [6, 3]]
    assert "SELECT id, uuid, json_metadata FROM samples WHERE id > %s" in seek.executed[0][0]


def test_samples_naming_runs_no_query_for_no_uuids(fake_db):
    seek, _ = fake_db()
    assert sources.samples_naming([], chunk=10) == []
    assert seek.executed == []


# --- the digest stream (spec 10.3) -------------------------------------------------------------

class _RoutedCursor(FakeCursor):
    """A cursor of its own per `cursor()` call, serving the next canned result of its statement.

    The digest stream keeps two link statements open while it pages samples, as Django's
    buffered MySQL cursors allow, so one shared cursor cannot model it.
    """

    def __init__(self, routes, log):
        super().__init__(())
        self.routes, self.log = routes, log

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.log.append((flat, list(params) if params is not None else None))
        for marker in ("FROM assay_assets", "FROM projects_samples", "FROM samples"):
            if marker in flat:
                queue = self.routes[marker]
                self._rows = list(queue.pop(0)) if queue else []
                return
        raise AssertionError(f"unexpected statement: {flat}")


class _RoutedConnection:
    def __init__(self, routes):
        self.routes, self.executed, self.opened = routes, [], 0

    def cursor(self):
        self.opened += 1
        return _RoutedCursor(self.routes, self.executed)


@pytest.fixture
def routed_seek(monkeypatch):
    def install(pages, projects, assays):
        conn = _RoutedConnection({"FROM samples": list(pages), "FROM projects_samples": [projects],
                                  "FROM assay_assets": [assays]})
        monkeypatch.setattr(sources, "connections", {"seek": conn, "default": FakeConnection()})
        return conn

    return install


def _drow(i, updated="2026-09-01 00:00:00"):
    return (i, f"TIS-220119FLY-{i}", f"t{i}", 26, "{}", updated)


def test_iter_digest_rows_merges_the_link_streams_across_page_boundaries(routed_seek):
    conn = routed_seek(
        pages=[[_drow(2), _drow(5)], [_drow(7), _drow(9)], [_drow(12)]],
        # A link to a sample id no longer in `samples` (3, 8, 20) is dropped, never carried to the
        # next row; 7 has neither link; 8 sits inside the second page's id range.
        projects=[(2, 6), (2, 1), (2, 6), (3, 4), (5, 2), (8, 5), (9, 3), (12, 2), (20, 1)],
        assays=[(2, 40), (5, 41), (5, 40), (9, 44), (12, 45), (12, 45), (20, 46)],
    )
    pages = list(sources.iter_digest_rows(chunk=2))
    got = {r["id"]: (r["project_ids"], r["assay_ids"]) for page in pages for r in page}
    assert [[r["id"] for r in page] for page in pages] == [[2, 5], [7, 9], [12]]
    assert got == {2: ([1, 6], [40]), 5: ([2], [40, 41]), 7: ([], []), 9: ([3], [44]),
                   12: ([2], [45])}
    first = pages[0][0]
    assert {k: first[k] for k in ("uuid", "title", "sample_type_id", "json_metadata", "updated_at")} == {
        "uuid": "TIS-220119FLY-2", "title": "t2", "sample_type_id": 26, "json_metadata": "{}",
        "updated_at": "2026-09-01 00:00:00"}
    statements = [sql for sql, _ in conn.executed]
    assert sum("FROM projects_samples" in s for s in statements) == 1
    assert sum("FROM assay_assets" in s for s in statements) == 1
    assert any("ORDER BY sample_id" in s for s in statements)
    assert any("asset_type = %s" in s and "ORDER BY asset_id" in s for s in statements)
    page_params = [p for s, p in conn.executed if "FROM projects_samples" not in s
                   and "FROM assay_assets" not in s]
    assert page_params == [[0, 2], [5, 2], [9, 2]]


def test_iter_digest_rows_handles_empty_link_tables_and_no_samples(routed_seek):
    routed_seek(pages=[[_drow(1)]], projects=[], assays=[])
    assert [[(r["id"], r["project_ids"], r["assay_ids"]) for r in page]
            for page in sources.iter_digest_rows(chunk=5)] == [[(1, [], [])]]
    routed_seek(pages=[[]], projects=[(1, 2)], assays=[(1, 3)])
    assert list(sources.iter_digest_rows(chunk=5)) == []


def test_iter_digest_rows_refuses_a_link_stream_out_of_order(routed_seek):
    routed_seek(pages=[[_drow(2), _drow(5)]], projects=[(5, 1), (2, 1)], assays=[])
    with pytest.raises(RuntimeError, match="projects_samples"):
        list(sources.iter_digest_rows(chunk=5))


def test_iter_digest_rows_rejects_a_non_positive_chunk():
    with pytest.raises(ValueError):
        next(sources.iter_digest_rows(chunk=0))


# --- the label maps and the small tables -------------------------------------------------------

def test_resolved_assay_map_keeps_the_smallest_internal_id_and_falls_back_to_the_seek_assay(fake_db):
    seek, dmac = fake_db(
        seek_results=[[(10, "Seek Ten"), (20, "Seek Twenty"), (30, None), (40, b"Seek Forty ")]],
        dmac_tables=["assays_internal_assays", "internal_assays"],
        # 50 is neither the first nor the last of assay 10's rows, so only "smallest wins" passes.
        dmac_results=[[(200, 10, "IA 200"), (50, 10, "IA 50"), (120, 10, "IA 120"), (60, 20, None),
                       (70, 99, "IA 70")]],
    )
    assert sources.resolved_assay_map() == {
        10: (50, "IA 50"),         # 1:N keeps the smallest internal id
        20: (60, None),            # an internal title is kept raw, None included
        30: (None, ""),            # the fallback: the SEEK assay's title, "" for none
        40: (None, "Seek Forty "),  # byte-exact, trailing space kept
        99: (70, "IA 70"),         # mapped although SEEK has no such assay row, as batch upload
    }
    assert "FROM assays" in seek.executed[0][0]
    sql = dmac.executed[0][0]
    assert "FROM assays_internal_assays" in sql and "JOIN internal_assays" in sql


def test_internal_assay_links_empty_when_a_dmac_table_is_absent(fake_db):
    _, dmac = fake_db(dmac_tables=["internal_assays"])
    assert sources.internal_assay_links() == {}
    assert dmac.executed == []


def test_resolved_assay_map_falls_back_everywhere_without_the_dmac_tables(fake_db):
    _, dmac = fake_db(seek_results=[[(10, "Seek Ten"), (30, None)]], dmac_tables=[])
    assert sources.resolved_assay_map() == {10: (None, "Seek Ten"), 30: (None, "")}
    assert dmac.executed == []


def test_resolved_assay_map_equals_batch_upload_resolution(fake_db, monkeypatch):
    """The same rows through batch upload's resolver give the same (internal id, title)."""
    from unittest.mock import MagicMock

    from nextseek_api.batch_upload.neo4j_sync import _resolve_internal_assays

    junction = [(200, 10, "IA 200"), (50, 10, "IA 50"), (60, 20, "IA 60")]
    fake_db(seek_results=[[(10, "Seek Ten"), (20, "Seek Twenty"), (30, "Seek Thirty")]],
            dmac_tables=["assays_internal_assays", "internal_assays"], dmac_results=[junction])
    ours = sources.resolved_assay_map()
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = junction
    theirs = _resolve_internal_assays({10, 20, 30}, conn)
    assert {a: ours[a] for a in theirs} == theirs
    assert ours[30] == (None, "Seek Thirty")


def test_sops_map_by_id(fake_db):
    seek, _ = fake_db(seek_results=[[(3, "Tissue dissociation"), (4, b"RNA extraction "), (5, None)]])
    assert sources.sops_map() == {3: "Tissue dissociation", 4: "RNA extraction ", 5: None}
    assert "FROM sops" in seek.executed[0][0]


def test_studies_rows(fake_db):
    fake_db(seek_results=[[(7, "Study A", 3), (8, b"Study B", None)]])
    assert sources.studies() == [{"id": 7, "title": "Study A", "investigation_id": 3},
                                 {"id": 8, "title": "Study B", "investigation_id": None}]

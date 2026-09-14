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

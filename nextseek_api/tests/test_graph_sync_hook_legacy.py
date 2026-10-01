"""The legacy sample pages enqueue a graph sync after they write (spec 5 and 9; plan H3).

The legacy sheet upload, the two legacy update paths and the legacy delete used to write Neo4j themselves, in a
second step the uploader's request waited on and a bare ``except`` hid. They now write MySQL only and enqueue one
outbox row each, which the graph sync loop drains through ``targeted.sync_samples`` and ``targeted.retire_samples``:

* ``_storeSample``, ``_batchUpdateSample`` and ``_batchUpdateSampleAssociation`` enqueue ``samples sample:<id>``
  (E1, E2, E5, E7, E8);
* ``_deleteOneSample`` enqueues ``retire sample:<id>`` after its transaction (E17, the deletion rule of section 9);
* ``sampleAttributeSave`` and ``sampleAttributeDelete`` enqueue ``catalog *`` and ``samples_of_type type:<id>``
  (E2, E11).

Three rules per site: the row is there after the write, there is none when the write failed, and an enqueue failure
never reaches the caller. Two more, over the source: the hook comes after the write, and no Neo4j driver is left in
``seek/sample/upload.py`` or ``seek/sample/table.py``.

Hermetic: ``DBtable_sample`` is built with ``__new__`` because its ``__init__`` opens a cursor, every database
collaborator of the paths under test is a stand-in, and the outbox is the SQLite test database.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.db import OperationalError
from django.test import RequestFactory

import seek.sample.table as table_module
import seek.sample.upload as upload_module
import seek.views.samples as samples_views
from nextseek_api.graph_sync import hooks, state
from nextseek_api.graph_sync.models_db import GraphSyncOutbox

UPLOAD_SOURCE = Path(upload_module.__file__).read_text(encoding="utf-8")
TABLE_SOURCE = Path(table_module.__file__).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def fresh_counts():
    hooks.reset_failure_counts()
    yield
    hooks.reset_failure_counts()


def _sample_table():
    """A ``DBtable_sample`` that never touched a database: ``__init__`` opens a cursor at construction."""
    from seek.dbtable_sample import DBtable_sample

    return DBtable_sample.__new__(DBtable_sample)


def _rows(kind=None):
    qs = GraphSyncOutbox.objects.all() if kind is None else GraphSyncOutbox.objects.filter(kind=kind)
    return sorted(qs.values_list("kind", "key"))


def _break_enqueue(monkeypatch):
    """What a hook meets when the outbox cannot be written."""
    def broken(*args, **kwargs):
        raise OperationalError("(2006, 'MySQL server has gone away')")

    monkeypatch.setattr(state, "enqueue", broken)


def _record_enqueues(monkeypatch, events):
    """Append ``"enqueue"`` to ``events`` as each row is written, so a test can assert the order of the two."""
    real = state.enqueue

    def recorded(kind, key, payload=None, **kwargs):
        events.append("enqueue")
        return real(kind, key, payload, **kwargs)

    monkeypatch.setattr(state, "enqueue", recorded)


# --------------------------------------------------------------------------- #
# The sheet upload: _storeSample
# --------------------------------------------------------------------------- #

def _store_sample_table(*, stored=True, new_sample=True, sample_id=42, events=None):
    table = _sample_table()
    record_new = {"uuid": "MOU-260101MIT-1", "json_metadata": '{"Parent": "NHP-260225MIT-1"}'}
    table._notEmptyLine = lambda record: True
    table._verifyRequiredFields = lambda record, headers: ("", True)
    table._getRecord = lambda *a, **k: (record_new, new_sample)

    def storeOneRecord(username, record):
        if events is not None:
            events.append("write")
        return ("Info: stored", 1 if stored else 0, sample_id if stored else -1)

    table.storeOneRecord = storeOneRecord
    table._updateSampleProject = lambda *a, **k: None
    table._updateSampleAssetsCreators = lambda *a, **k: None
    table._setSampleDatafileAssociation = lambda *a, **k: ("", True)
    return table


def _call_store_sample(table):
    return table._storeSample(
        {"username": "bob", "user_id": 3},
        "Mouse",
        {"UID": "MOU-260101MIT-1", "Name": "m1"},
        {"headers_required": [], "sampleType_id": 1, "headers": []},
        [],
        {"user_id": 3, "projectid": 2},
    )


@pytest.mark.django_db
def test_a_stored_sample_enqueues_its_own_sync():
    _call_store_sample(_store_sample_table())
    assert _rows() == [("samples", "sample:42")]


@pytest.mark.django_db
def test_an_updated_existing_sample_enqueues_too():
    """The row was rewritten, so its metadata, projects and lineage in the graph are behind it."""
    _call_store_sample(_store_sample_table(new_sample=False, sample_id=7))
    assert _rows() == [("samples", "sample:7")]


@pytest.mark.django_db
def test_a_sample_that_was_not_stored_enqueues_nothing():
    _call_store_sample(_store_sample_table(stored=False))
    assert _rows() == []


@pytest.mark.django_db
def test_store_sample_writes_before_it_enqueues(monkeypatch):
    events = []
    _record_enqueues(monkeypatch, events)
    _call_store_sample(_store_sample_table(events=events))
    assert events == ["write", "enqueue"]


@pytest.mark.django_db
def test_an_enqueue_failure_does_not_reach_the_uploader(monkeypatch):
    _break_enqueue(monkeypatch)
    msg, status, uid = _call_store_sample(_store_sample_table())
    assert status == 1 and uid == "MOU-260101MIT-1"
    assert hooks.failure_counts() == {"samples": 1}


# --------------------------------------------------------------------------- #
# The sheet update paths
# --------------------------------------------------------------------------- #

def _update_table(*, updated=True, sample_id=11):
    table = _sample_table()
    table._verifyUpdateSample = lambda sheetData, feedbackfile: ("okay", 1, ["UID", "Name"])
    table.updateSingleSample = lambda dici, username=None, attributes=None: (
        ("Info: updated", 1) if updated else ("Error: not updated", 0))
    table.getSampleID = lambda uid: sample_id
    table._outputUploadFeedback_V2 = lambda *a, **k: None
    return table


def _sheet(rows, headers=None):
    return {"headers": list(headers or ["UID", "Name"]), "diclist": rows}


@pytest.mark.django_db
def test_a_batch_update_enqueues_each_updated_sample():
    table = _update_table()
    table.getSampleID = lambda uid: {"MOU-1": 11, "MOU-2": 12}[uid]
    table._batchUpdateSample(_sheet([{"UID": "MOU-1"}, {"UID": "MOU-2"}]), "out.xls", {"username": "bob"})
    assert _rows() == [("samples", "sample:11"), ("samples", "sample:12")]


@pytest.mark.django_db
def test_a_batch_update_row_that_failed_enqueues_nothing():
    table = _update_table(updated=False)
    table._batchUpdateSample(_sheet([{"UID": "MOU-1"}]), "out.xls", {"username": "bob"})
    assert _rows() == []


@pytest.mark.django_db
def test_a_batch_update_of_a_uid_that_does_not_resolve_enqueues_nothing():
    """``getSampleID`` answers None for a UID it cannot find; there is no id to sync."""
    table = _update_table()
    table.getSampleID = lambda uid: None
    table._batchUpdateSample(_sheet([{"UID": "MOU-1"}]), "out.xls", {"username": "bob"})
    assert _rows() == []


@pytest.mark.django_db
def test_a_batch_update_enqueue_failure_does_not_reach_the_uploader(monkeypatch):
    _break_enqueue(monkeypatch)
    msg, status = _update_table()._batchUpdateSample(
        _sheet([{"UID": "MOU-1"}]), "out.xls", {"username": "bob"})
    assert status == 1
    assert hooks.failure_counts() == {"samples": 1}


ASSOCIATION_HEADERS = ["Sample UID", "Current Assay ID", "Current Assay Direction",
                       "New Assay ID", "New Assay Direction"]


def _association_table(sample_id=11):
    table = _sample_table()
    table.getSampleID = lambda uid: sample_id
    table._outputUploadFeedback_V2 = lambda *a, **k: None
    return table


def _call_association(table, monkeypatch, *, updated=True):
    assay_assets = MagicMock()
    assay_assets.updateSample_assay_asset.return_value = (
        ("Info: updated", 1) if updated else ("Error: not updated", 0))
    monkeypatch.setattr(upload_module, "DBtable_assay_assets", MagicMock(return_value=assay_assets))
    sheet = _sheet([dict.fromkeys(ASSOCIATION_HEADERS, "1") | {"Sample UID": "MOU-1"}], ASSOCIATION_HEADERS)
    return table._batchUpdateSampleAssociation(sheet, "out.xls", {"username": "bob"})


@pytest.mark.django_db
def test_an_assay_association_update_enqueues_its_sample(monkeypatch):
    """The sample's assay links changed, so every edge it touches is relabelled (E8)."""
    _call_association(_association_table(), monkeypatch)
    assert _rows() == [("samples", "sample:11")]


@pytest.mark.django_db
def test_a_failed_assay_association_update_enqueues_nothing(monkeypatch):
    _call_association(_association_table(), monkeypatch, updated=False)
    assert _rows() == []


@pytest.mark.django_db
def test_an_assay_association_uid_that_does_not_resolve_enqueues_nothing(monkeypatch):
    table = _association_table(sample_id=0)
    _call_association(table, monkeypatch)
    assert _rows() == []


# --------------------------------------------------------------------------- #
# The legacy delete
# --------------------------------------------------------------------------- #

def _delete_table(*, committed=True, events=None):
    table = _sample_table()
    table.db = MagicMock()

    def run_custom_transaction(sqlqueries, db_alias):
        if events is not None:
            events.append("write")
        return committed

    table.db.run_custom_transaction = run_custom_transaction
    return table


@pytest.mark.django_db
def test_a_deleted_sample_is_enqueued_for_retirement():
    msg, status = _delete_table()._deleteOneSample(5, 3)
    assert status is True
    assert _rows() == [("retire", "sample:5")]


@pytest.mark.django_db
def test_a_deletion_that_did_not_commit_enqueues_nothing():
    _delete_table(committed=False)._deleteOneSample(5, 3)
    assert _rows() == []


@pytest.mark.django_db
def test_the_deletion_commits_before_the_hook_runs(monkeypatch):
    events = []
    _record_enqueues(monkeypatch, events)
    _delete_table(events=events)._deleteOneSample(5, 3)
    assert events == ["write", "enqueue"]


@pytest.mark.django_db
def test_a_delete_enqueue_failure_does_not_reach_the_caller(monkeypatch):
    _break_enqueue(monkeypatch)
    msg, status = _delete_table()._deleteOneSample(5, 3)
    assert status is True
    assert hooks.failure_counts() == {"retire": 1}


# --------------------------------------------------------------------------- #
# The legacy attribute editor
# --------------------------------------------------------------------------- #

def _seekdb():
    """What ``seek.decorators._login`` builds: a SEEK login that succeeded."""
    db = MagicMock()
    db.getSeekLogin.return_value = {"status": True, "username": "admin"}
    return db


def _attribute_request(params):
    request = RequestFactory().get("/seek/attribute/save/", params)
    request.user = MagicMock(is_authenticated=True, is_superuser=True)
    return request


def _attribute_views(monkeypatch, *, saved=True, events=None):
    sampleattr = MagicMock()

    def processRecords(request, user_seek, operation):
        if events is not None:
            events.append("write")
        return json.dumps({"status": 1 if saved else 0, "msg": "okay"})

    sampleattr.processRecords = processRecords
    sampleattr.getAttributesRenamed.return_value = {}
    monkeypatch.setattr(samples_views, "DBtable_sampleattribute", MagicMock(return_value=sampleattr))
    dbsample = MagicMock()
    dbsample.updateSampleType.return_value = json.dumps({"status": 1, "rows": []})
    monkeypatch.setattr(samples_views, "DBtable_sample", MagicMock(return_value=dbsample))
    monkeypatch.setattr("seek.decorators.SeekDB", MagicMock(return_value=_seekdb()))
    return sampleattr, dbsample


RECORDS = [{"id": 8, "title": "Tissue", "sample_type_id": 26}]


@pytest.mark.django_db
def test_saving_an_attribute_enqueues_the_catalog_and_its_samples(monkeypatch):
    _attribute_views(monkeypatch)
    resp = samples_views.sampleAttributeSave(
        _attribute_request({"sampletype_id": "26", "records": json.dumps(RECORDS)}))
    assert resp.status_code == 200
    assert _rows() == [("catalog", "*"), ("samples_of_type", "type:26")]


@pytest.mark.django_db
def test_saving_an_attribute_writes_before_it_enqueues(monkeypatch):
    events = []
    _attribute_views(monkeypatch, events=events)
    _record_enqueues(monkeypatch, events)
    samples_views.sampleAttributeSave(
        _attribute_request({"sampletype_id": "26", "records": json.dumps(RECORDS)}))
    assert events == ["write", "enqueue", "enqueue"]


@pytest.mark.django_db
def test_deleting_an_attribute_enqueues_the_catalog_and_the_types_of_its_records(monkeypatch):
    _attribute_views(monkeypatch)
    records = RECORDS + [{"id": 9, "title": "Organ", "sample_type_id": 31}]
    resp = samples_views.sampleAttributeDelete(_attribute_request({"records": json.dumps(records)}))
    assert resp.status_code == 200
    assert _rows() == [("catalog", "*"), ("samples_of_type", "type:26"), ("samples_of_type", "type:31")]


@pytest.mark.django_db
def test_a_delete_request_carrying_no_records_enqueues_nothing(monkeypatch):
    """``processRecords`` writes only when the request carried records."""
    _attribute_views(monkeypatch)
    samples_views.sampleAttributeDelete(_attribute_request({}))
    assert _rows() == []


@pytest.mark.django_db
def test_an_attribute_editor_enqueue_failure_does_not_reach_the_caller(monkeypatch):
    _attribute_views(monkeypatch)
    _break_enqueue(monkeypatch)
    resp = samples_views.sampleAttributeSave(
        _attribute_request({"sampletype_id": "26", "records": json.dumps(RECORDS)}))
    assert resp.status_code == 200
    assert hooks.failure_counts() == {"catalog": 1, "samples_of_type": 1}


@pytest.mark.django_db
def test_an_attribute_request_that_names_no_type_still_enqueues_the_catalog(monkeypatch):
    """A record with no ``sample_type_id`` still changed the Attribute catalog (E11)."""
    _attribute_views(monkeypatch)
    samples_views.sampleAttributeDelete(_attribute_request({"records": json.dumps([{"id": 8}])}))
    assert _rows() == [("catalog", "*")]


# --------------------------------------------------------------------------- #
# What is gone: the graph writes these pages used to make
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source", [UPLOAD_SOURCE, TABLE_SOURCE], ids=["upload", "table"])
def test_no_neo4j_driver_is_imported(source):
    """The page writes MySQL and enqueues; the graph is the loop's to write."""
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "neo4j" not in imported


@pytest.mark.parametrize("source", [UPLOAD_SOURCE, TABLE_SOURCE], ids=["upload", "table"])
def test_no_neo4j_connection_is_opened(source):
    assert "GraphDatabase" not in source
    assert "NEO4J_DATABASE" not in source


@pytest.mark.parametrize("name", ["storeSampleNeo4j", "_storeSampleNeo4jGuarded", "deleteSampleNeo4j"])
def test_the_graph_writing_methods_are_gone(name):
    from seek.dbtable_sample import DBtable_sample

    assert not hasattr(DBtable_sample, name)

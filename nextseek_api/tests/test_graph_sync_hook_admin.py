"""The clade and internal-assay admin views enqueue a graph sync after they write (spec 5, E8 and E10; plan H5).

The clade views change what a ``SampleType`` node carries (E10), so each enqueues ``catalog *``; the internal-assay
views change the map that labels ``DERIVED_FROM`` edges (E8), so each enqueues ``assay_map *``. The drain applies the
row later: no admin request waits on Neo4j.

Three rules per view, one test each: the row is there after a successful write, there is no row when the write
failed, and an enqueue failure never reaches the caller. A fourth, over the source, pins the call to the end of the
view, after its write and outside no transaction of its own.

Hermetic: the table classes are fakes, the SEEK login and the supervisor check see a logged-in superuser, and the
outbox is the SQLite test database.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.db import OperationalError
from django.test import RequestFactory

import seek.views.admin as admin
from nextseek_api.graph_sync import hooks, state
from nextseek_api.graph_sync.models_db import GraphSyncOutbox

# view -> the table class it builds, the one method its write calls, its GET records, the kind it enqueues.
# Every case writes exactly once, which is what lets the success test assert the write and the hook in order.
CASES = {
    "cladeSave": ("DBtable_clades", "new", [{"title": "Tissue", "color": "#ffffff", "order": 1}], "catalog"),
    "cladeDelete": ("DBtable_clades", "delete", [{"id": 3}], "catalog"),
    "cladeSampleTypesSave": ("DBtable_stc", "update", [{"sample_type_id": 26, "clade_title": 3}], "catalog"),
    "cladesSyncSampleTypes": ("DBtable_stc", "syncSampleTypes", None, "catalog"),
    "internalAssaySave": ("DBtable_internalassays", "new", [{"internal_assay_title": "RNAseq"}], "assay_map"),
    "internalAssayDelete": ("DBtable_internalassays", "delete", [{"id": 4}], "assay_map"),
    "assayAssociationSave": ("DBtable_assaysinternalassays", "update",
                             [{"assay_id": 7, "internal_assay_id": 9}], "assay_map"),
    "syncInternalAssays": ("DBtable_assaysinternalassays", "syncAssays", None, "assay_map"),
}

ADMIN_SOURCE = Path(admin.__file__).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def fresh_counts():
    hooks.reset_failure_counts()
    yield
    hooks.reset_failure_counts()


def _seekdb():
    """What ``seek.decorators._login`` builds: a SEEK login that succeeded."""
    db = MagicMock()
    db.getSeekLogin.return_value = {"status": True, "username": "admin"}
    return db


def _request(records):
    params = {} if records is None else {"records": json.dumps(records)}
    request = RequestFactory().get("/seek/admin/", params)
    request.user = MagicMock(is_authenticated=True, is_superuser=True)
    return request


def _call(view_name, monkeypatch, *, write_fails=False, events=None):
    table, method, records, _kind = CASES[view_name]
    db = MagicMock()
    if write_fails:
        getattr(db, method).side_effect = RuntimeError("the table layer failed")
    elif events is not None:
        getattr(db, method).side_effect = lambda *a, **k: events.append("write")
    monkeypatch.setattr(admin, table, MagicMock(return_value=db))
    monkeypatch.setattr("seek.decorators.SeekDB", MagicMock(return_value=_seekdb()))
    return getattr(admin, view_name)(_request(records)), db


def _record_enqueues(monkeypatch, events):
    real = state.enqueue

    def recording(*args, **kwargs):
        events.append("enqueue")
        return real(*args, **kwargs)

    monkeypatch.setattr(state, "enqueue", recording)


def _view(view_name):
    tree = ast.parse(ADMIN_SOURCE)
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == view_name)


@pytest.mark.django_db
@pytest.mark.parametrize("view_name", list(CASES))
def test_the_row_is_written_after_the_view_wrote(view_name, monkeypatch):
    kind = CASES[view_name][3]
    events = []
    _record_enqueues(monkeypatch, events)

    response, db = _call(view_name, monkeypatch, events=events)

    assert response.status_code == 200
    getattr(db, CASES[view_name][1]).assert_called_once()
    assert events == ["write", "enqueue"]
    row = GraphSyncOutbox.objects.get(kind=kind, key="*")
    assert row.done_at is None
    assert row.payload is None


@pytest.mark.django_db
@pytest.mark.parametrize("view_name", list(CASES))
def test_no_row_when_the_write_failed(view_name, monkeypatch):
    with pytest.raises(RuntimeError):
        _call(view_name, monkeypatch, write_fails=True)

    assert not GraphSyncOutbox.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("view_name", list(CASES))
def test_an_enqueue_failure_never_reaches_the_caller(view_name, monkeypatch):
    def broken(*args, **kwargs):
        raise OperationalError("(2006, 'MySQL server has gone away')")

    monkeypatch.setattr(state, "enqueue", broken)

    response, db = _call(view_name, monkeypatch)

    assert response.status_code == 200
    getattr(db, CASES[view_name][1]).assert_called_once()
    assert not GraphSyncOutbox.objects.exists()
    assert hooks.failure_counts() == {CASES[view_name][3]: 1}


@pytest.mark.parametrize("view_name", list(CASES))
def test_the_hook_is_the_last_statement_before_the_response(view_name):
    """After the write, and never in the middle of the loop that does it."""
    body = _view(view_name).body
    assert isinstance(body[-1], ast.Return)
    call = body[-2].value
    assert isinstance(call, ast.Call)
    assert ast.unparse(call.func) == "hooks.enqueue"
    assert [ast.literal_eval(arg) for arg in call.args] == [CASES[view_name][3], "*"]
    assert not call.keywords


def test_no_other_view_in_the_module_enqueues():
    """The read-only pages (the two renders, the retrieval download) write nothing, so they enqueue nothing."""
    enqueuing = {node.name for node in ast.walk(ast.parse(ADMIN_SOURCE))
                 if isinstance(node, ast.FunctionDef) and "hooks.enqueue" in ast.unparse(node)}
    assert enqueuing == set(CASES)

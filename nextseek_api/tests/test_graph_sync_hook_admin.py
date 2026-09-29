"""The clade and internal-assay admin views enqueue a graph sync after they write (spec 5, E8 and E10; plan H5).

The clade views change what a ``SampleType`` node carries (E10), so each enqueues ``catalog *``; the internal-assay
views change the map that labels ``DERIVED_FROM`` edges (E8), so each enqueues ``assay_map *``. The drain applies the
row later: no admin request waits on Neo4j.

Three rules per view, one test each: the row is there after a successful write, there is no row when the write
failed, and an enqueue failure never reaches the caller. A fourth, over the source, pins the call to the end of the
view, after its write and outside no transaction of its own.

Hermetic: the table classes are fakes, the SEEK login and the supervisor check see a logged-in superuser, and the
outbox is the SQLite test database.

Rewritten 2026-09-22 for the association workbench (origin/dev), which changed the contract these views answer on:
six of the eight now take a JSON POST body and report per-row errors instead of reading GET params and failing the
whole request. The three guarantees are unchanged and are what the shapes below preserve. What moved is HOW the hook
is attached: a workbench view ends in ``_wb_batch(...)``, so it hands its kind over as ``on_change``, which the helper
runs once after the loop and only when a record committed. The two sync views were not rewritten and still carry the
bare call as the last statement. "No row when the write failed" therefore splits: a sync view still raises, and a
workbench view reports the row and enqueues nothing because nothing committed.
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

# view -> the table class it builds, the one method its write calls, the records it is given, the kind it
# enqueues, and its shape ("workbench" = JSON POST body and per-row errors, "sync" = no records, raises).
# Every case writes exactly once, which is what lets the success test assert the write and the hook in order.
CASES = {
    "cladeSave": ("DBtable_clades", "new", [{"title": "Tissue", "color": "#ffffff", "order": 1}], "catalog",
                  "workbench"),
    "cladeDelete": ("DBtable_clades", "delete", [{"id": 3}], "catalog", "workbench"),
    # clade_title became clade_id in the workbench rewrite, and the JS sends the new name.
    "cladeSampleTypesSave": ("DBtable_stc", "update", [{"sample_type_id": 26, "clade_id": 3}], "catalog",
                             "workbench"),
    "cladesSyncSampleTypes": ("DBtable_stc", "syncSampleTypes", None, "catalog", "sync"),
    "internalAssaySave": ("DBtable_internalassays", "new", [{"internal_assay_title": "RNAseq"}],
                          "assay_map", "workbench"),
    "internalAssayDelete": ("DBtable_internalassays", "delete", [{"id": 4}], "assay_map", "workbench"),
    "assayAssociationSave": ("DBtable_assaysinternalassays", "update",
                             [{"assay_id": 7, "internal_assay_id": 9}], "assay_map", "workbench"),
    "syncInternalAssays": ("DBtable_assaysinternalassays", "syncAssays", None, "assay_map", "sync"),
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
    """A sync view reads no records and answers on GET; a workbench view takes a JSON POST body."""
    if records is None:
        request = RequestFactory().get("/seek/admin/")
    else:
        request = RequestFactory().post("/seek/admin/", data=json.dumps({"records": records}),
                                        content_type="application/json")
    request.user = MagicMock(is_authenticated=True, is_superuser=True)
    return request


def _call(view_name, monkeypatch, *, write_fails=False, events=None):
    table, method, records, _kind, _shape = CASES[view_name]
    db = MagicMock()
    if write_fails:
        getattr(db, method).side_effect = RuntimeError("the table layer failed")
    elif events is not None:
        getattr(db, method).side_effect = lambda *a, **k: events.append("write")
    monkeypatch.setattr(admin, table, MagicMock(return_value=db))
    monkeypatch.setattr("seek.decorators.SeekDB", MagicMock(return_value=_seekdb()))
    # `_wb_guard` is a deliberate second copy of the decorators (see its comment in
    # admin.py), so the workbench views need the module's own names patched too.
    monkeypatch.setattr(admin, "SeekDB", MagicMock(return_value=_seekdb()))
    monkeypatch.setattr(admin, "verifySuperUser", lambda request: 1)
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
@pytest.mark.parametrize("view_name", [v for v, c in CASES.items() if c[4] == "sync"])
def test_no_row_when_the_write_failed(view_name, monkeypatch):
    """A sync view writes all or nothing, so a failure is raised and nothing is enqueued."""
    with pytest.raises(RuntimeError):
        _call(view_name, monkeypatch, write_fails=True)

    assert not GraphSyncOutbox.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("view_name", [v for v, c in CASES.items() if c[4] == "workbench"])
def test_no_row_when_every_record_failed(view_name, monkeypatch):
    """The workbench reports a bad row instead of raising, and a batch that committed
    nothing must still enqueue nothing: that is what `on_change` is conditional for."""
    response, _db = _call(view_name, monkeypatch, write_fails=True)

    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["status"] == 0 and body["updated"] == 0 and body["errors"]
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
def test_the_hook_fires_after_the_write_and_never_inside_the_row_loop(view_name):
    """One `hooks.enqueue(kind, "*")` per view, with its own kind, outside every loop.

    A sync view still ends `hooks.enqueue(...)` then `return`. A workbench view ends in
    `_wb_batch(...)` and hands the call over as `on_change=lambda: hooks.enqueue(...)`,
    which the helper runs after the loop when a record committed. Both are "after the
    write and once"; neither may sit inside the loop that does the writing.
    """
    view = _view(view_name)
    kind, shape = CASES[view_name][3], CASES[view_name][4]

    calls = [n for n in ast.walk(view)
             if isinstance(n, ast.Call) and ast.unparse(n.func) == "hooks.enqueue"]
    assert len(calls) == 1, f"{view_name} enqueues {len(calls)} times"
    assert [ast.literal_eval(arg) for arg in calls[0].args] == [kind, "*"]
    assert not calls[0].keywords

    in_a_loop = [n for loop in ast.walk(view) if isinstance(loop, (ast.For, ast.While))
                 for n in ast.walk(loop)
                 if isinstance(n, ast.Call) and ast.unparse(n.func) == "hooks.enqueue"]
    assert not in_a_loop, f"{view_name} enqueues inside a loop"

    if shape == "sync":
        assert isinstance(view.body[-1], ast.Return)
        assert ast.unparse(view.body[-2].value.func) == "hooks.enqueue"
    else:
        last = view.body[-1]
        assert isinstance(last, ast.Return) and isinstance(last.value, ast.Call)
        assert ast.unparse(last.value.func) == "_wb_batch"
        assert [k.arg for k in last.value.keywords][-1] == "on_change"


def test_no_other_view_in_the_module_enqueues():
    """The read-only pages (the two renders, the retrieval download) write nothing, so they enqueue nothing."""
    enqueuing = {node.name for node in ast.walk(ast.parse(ADMIN_SOURCE))
                 if isinstance(node, ast.FunctionDef) and "hooks.enqueue" in ast.unparse(node)}
    assert enqueuing == set(CASES)

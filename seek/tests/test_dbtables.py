"""Every ``DBtable`` subclass in ``seek/`` must declare a usable schema.

``tablename``, ``fields`` and ``primaryField`` are all assigned in ``__init__``
rather than as class attributes, so the only way to check them is to construct
the object. This is the test that catches an attribute lost while moving code
between modules -- and, because the dbtable modules are where the shared-base
refactor lands (plan Step 18), it is worth having before that starts.

No database is involved: ``DBconnection`` is stubbed out. The real one opens a
cursor in its constructor (``dmac/dbconn_django.py``:
``self.cursor = self.conn.cursor()``), which would otherwise make constructing
any table object require a live MySQL server.
"""

import importlib
import inspect

import pytest

from dmac.dbtable import DBtable
from .discovery import module_names


class _StubConnection:
    """Stand-in for ``dmac.dbconnection.DBconnection``; deliberately inert."""

    def __init__(self, *args, **kwargs):
        pass


def _table_classes():
    found = []
    for name in (n for n in module_names() if n.startswith("seek.dbtable_")):
        module = importlib.import_module(name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is not DBtable and issubclass(obj, DBtable) and obj.__module__ == name:
                found.append(obj)
    return sorted(found, key=lambda c: c.__name__)


TABLE_CLASSES = _table_classes()

# DBtable_ontology never assigns self.tablemodel, so it inherits None from the
# base and `self.fulltablename = self.tablemodel` leaves fulltablename None --
# which is why getAttributeInfo passes None to retrieveRecords. See
# /workspace/deslopify/LATENT_BUGS.md #25. Out of scope for the refactor; this
# mark documents the bug and starts failing (XPASS) the day it is fixed.
NO_TABLEMODEL = {"DBtable_ontology": "never sets self.tablemodel (LATENT_BUGS.md #25)"}


@pytest.fixture
def stub_connection(monkeypatch):
    monkeypatch.setattr("dmac.dbtable.DBconnection", _StubConnection)


def _param(cls):
    reason = NO_TABLEMODEL.get(cls.__name__)
    if reason is None:
        return cls
    return pytest.param(cls, marks=pytest.mark.xfail(strict=True, reason=reason))


def test_discovery_found_the_tables():
    assert len(TABLE_CLASSES) >= 13, [c.__name__ for c in TABLE_CLASSES]


@pytest.mark.parametrize("cls", TABLE_CLASSES, ids=lambda c: c.__name__)
def test_table_declares_its_schema(cls, stub_connection):
    table = cls()
    assert table.tablename, "tablename is unset"
    assert table.fields, "fields is empty"
    assert table.primaryField, "primaryField is unset"


@pytest.mark.parametrize(
    "cls", [_param(c) for c in TABLE_CLASSES], ids=lambda c: c.__name__
)
def test_table_resolves_to_a_model(cls, stub_connection):
    assert cls().fulltablename is not None

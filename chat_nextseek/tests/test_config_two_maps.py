"""config.py builds SEPARATE project and investigation name->id maps from the DB.

Projects and investigations are kept in distinct maps (PROJECT_NAME_TO_ID,
INVESTIGATION_NAME_TO_ID) — never blended — so the report can offer an
investigation-scoped path without touching the project path. Both are dynamic
from the live DB; {} when the DB is down (never the removed hardcoded literal).
"""
from __future__ import annotations

from chat_nextseek.config import ChatConfig


class _FakeCursor:
    def __init__(self, tables):
        self._tables = tables
        self._rows = []

    def execute(self, sql):
        self._rows = self._tables.get(sql.rsplit(".", 1)[-1].strip(), [])

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, tables):
        self._tables = tables

    def cursor(self, dictionary=False):
        return _FakeCursor(self._tables)


def _cfg(tables) -> ChatConfig:
    cfg = ChatConfig.__new__(ChatConfig)
    cfg._db_conn = _FakeConn(tables)
    return cfg


def test_projects_and_investigations_load_into_separate_maps():
    cfg = _cfg({
        "projects": [(1, "Published Data")],
        "investigations": [(1, "CSBC"), (3, "Impact"), (6, "SRP")],
    })
    proj = cfg._load_name_to_id_from_db("seek_production.projects")
    inv = cfg._load_name_to_id_from_db("seek_production.investigations")
    assert proj == {"PUBLISHED DATA": 1}
    assert inv == {"CSBC": 1, "IMPACT": 3, "SRP": 6}
    # No blending: investigation names are NOT in the project map.
    assert "IMPACT" not in proj
    assert "PUBLISHED DATA" not in inv


def test_db_down_returns_empty_not_stale():
    cfg = ChatConfig.__new__(ChatConfig)
    cfg._db_conn = None
    cfg._connect_db = lambda env="prod": None  # type: ignore[assignment]
    assert cfg._load_name_to_id_from_db("seek_production.projects") == {}
    assert cfg._load_name_to_id_from_db("seek_production.investigations") == {}

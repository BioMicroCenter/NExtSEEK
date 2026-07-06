"""Dynamic project/investigation name->id map (issue #3, 2026-07-06).

The old hardcoded PROJECT_NAME_TO_ID held ids from another instance (IMPACT:2,
CSBC:10, ...) that matched neither this DB's projects nor its investigations, so
report scoping silently resolved to wrong/nonexistent ids. The map is now built
from the live DB (projects + investigations), projects winning on name collision.
"""
from __future__ import annotations

from chat_nextseek.config import ChatConfig


class _FakeCursor:
    def __init__(self, tables):
        self._tables = tables
        self._rows = []

    def execute(self, sql):
        # sql looks like: SELECT id, title FROM seek_production.<table>
        table = sql.rsplit(".", 1)[-1].strip()
        self._rows = self._tables.get(table, [])

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, tables):
        self._tables = tables

    def cursor(self, dictionary=False):
        return _FakeCursor(self._tables)


def _cfg_with_db(tables) -> ChatConfig:
    cfg = ChatConfig.__new__(ChatConfig)
    cfg._db_conn = _FakeConn(tables)
    return cfg


def test_builds_map_from_projects_and_investigations():
    cfg = _cfg_with_db({
        "projects": [(1, "Published Data")],
        "investigations": [(1, "CSBC"), (3, "Impact"), (6, "SRP")],
    })
    m = cfg._load_project_name_to_id_from_db()
    assert m["PUBLISHED DATA"] == 1
    assert m["CSBC"] == 1          # investigation id (no project collision)
    assert m["IMPACT"] == 3        # real investigation id, not the stale literal's 2
    assert m["SRP"] == 6           # real id, not the stale literal's 3


def test_project_wins_on_name_collision():
    cfg = _cfg_with_db({
        "projects": [(2, "IMPACT")],           # prod-style: a real IMPACT project
        "investigations": [(9, "IMPACT")],     # same name, different id
    })
    m = cfg._load_project_name_to_id_from_db()
    assert m["IMPACT"] == 2, "project id must win so project-scoped reports resolve"


def test_no_stale_hardcoded_ids_when_db_absent():
    cfg = ChatConfig.__new__(ChatConfig)
    cfg._db_conn = None
    # _connect_db would try the network; force it to None to simulate DB-down.
    cfg._connect_db = lambda env="prod": None  # type: ignore[assignment]
    assert cfg._load_project_name_to_id_from_db() == {}

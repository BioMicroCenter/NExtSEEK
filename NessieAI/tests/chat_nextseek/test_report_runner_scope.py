"""The report runners' relational reads carry the caller's project scope.

The sample, protocol and published reports read ``seek_production`` directly. A caller who is not an admin reads only
the rows of its own projects: an unnamed project means the caller's projects, a named project outside them is refused
before any statement runs, and a caller who sees no project reads nothing. A config with no scope (``None``, a plain
dict, a ``MagicMock``) refuses. An admin is unchanged. The same holds through the reporter summary and the CC
``report`` op, which both call these runners.

The runners' own SQL runs for real on an in-process SQLite database laid out as the ``seek_production`` tables they
read (only the ``%s`` placeholders are translated), so what is asserted is the rows the statements return, not their
text. Nothing reaches MySQL, Neo4j or a model.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 4.3 and 14.
"""
from __future__ import annotations

import json
import sqlite3
import types
from unittest.mock import MagicMock

import pytest

from chat_nextseek.graph_scope import SCOPE_ATTR, GraphScope
from chat_nextseek.reports import runners
from NessieAI.ns.granular import run_op
from NessieAI.ns.write_gate import build_gate, load_allowlist_from_entries

# (sample id, uid, project ids): the lab code in each uid names who may read it.
SAMPLES = [
    (1, "TIS-230101AAA-1", [1]),
    (2, "MUS-230102AAA-2", [1]),
    (3, "TIS-230201BBB-1", [2]),
    (4, "SLD-230202BBB-2", [2]),
    (5, "TIS-230301CCC-1", [1, 2]),
    (6, "CHM-230401DDD-1", [3]),
    (7, "TIS-230501EEE-1", []),
]
# (sop id, title, project ids)
SOPS = [
    (1, "P.AAA-230101-dissect.docx", [1]),
    (2, "P.BBB-230201-stain.docx", [2]),
    (3, "P.CCC-230301-shared.docx", [1, 2]),
    (4, "P.DDD-230401-treat.docx", [3]),
]
PROJECTS = [(1, "Project one"), (2, "Project two"), (3, "Project three")]


def _uids(project_ids) -> set[str]:
    return {uid for _, uid, pids in SAMPLES if set(pids) & set(project_ids)}


def _titles(project_ids) -> set[str]:
    return {title for _, title, pids in SOPS if set(pids) & set(project_ids)}


ALL_UIDS = {uid for _, uid, pids in SAMPLES if pids}
ALL_TITLES = {title for _, title, _ in SOPS}


class _Cursor:
    def __init__(self, db: "_Database"):
        self._db = db
        self._cursor = db.sqlite.cursor()

    def execute(self, sql, params=None):
        self._db.statements.append((sql, list(params or [])))
        self._cursor.execute(sql.replace("%s", "?"), list(params or []))

    def _dicts(self, rows):
        names = [d[0] for d in self._cursor.description or ()]
        return [dict(zip(names, row)) for row in rows]

    def fetchall(self):
        return self._dicts(self._cursor.fetchall())

    def fetchone(self):
        rows = self._dicts(self._cursor.fetchmany(1))
        return rows[0] if rows else None

    def close(self):
        self._cursor.close()


class _Database:
    """A connection the runners accept (``cursor(dictionary=True)``, ``is_connected``), over SQLite."""

    def __init__(self):
        self.statements: list[tuple[str, list]] = []
        self.sqlite = sqlite3.connect(":memory:")
        self.sqlite.execute("ATTACH DATABASE ':memory:' AS seek_production")
        for ddl in ("projects (id INTEGER, title TEXT)", "samples (id INTEGER, uuid TEXT)",
                    "projects_samples (project_id INTEGER, sample_id INTEGER)", "sops (id INTEGER, title TEXT)",
                    "projects_sops (project_id INTEGER, sop_id INTEGER)"):
            self.sqlite.execute(f"CREATE TABLE seek_production.{ddl}")
        self.sqlite.executemany("INSERT INTO seek_production.projects VALUES (?, ?)", PROJECTS)
        for sample_id, uid, pids in SAMPLES:
            self.sqlite.execute("INSERT INTO seek_production.samples VALUES (?, ?)", (sample_id, uid))
            self.sqlite.executemany("INSERT INTO seek_production.projects_samples VALUES (?, ?)",
                                    [(pid, sample_id) for pid in pids])
        for sop_id, title, pids in SOPS:
            self.sqlite.execute("INSERT INTO seek_production.sops VALUES (?, ?)", (sop_id, title))
            self.sqlite.executemany("INSERT INTO seek_production.projects_sops VALUES (?, ?)",
                                    [(pid, sop_id) for pid in pids])

    def cursor(self, dictionary=False):
        return _Cursor(self)

    def is_connected(self):
        return True

    def close(self):
        pass


def _config(db: _Database, scope, *, attach: bool = True):
    config = types.SimpleNamespace(
        _db_conn=db, _connect_db=lambda env="prod": db, is_umbrella_published_project=None,
        PROJECT_NAME_TO_ID={"PROJECT ONE": 1, "PROJECT TWO": 2, "PROJECT THREE": 3},
        INVESTIGATION_NAME_TO_ID={},
    )
    if attach:
        setattr(config, SCOPE_ATTR, scope)
    return config


def _magicmock(db: _Database):
    config = MagicMock()  # every attribute exists, GRAPH_SCOPE included, and none is a GraphScope
    config._db_conn, config._connect_db = db, (lambda env="prod": db)
    config.is_umbrella_published_project = None
    config.PROJECT_NAME_TO_ID, config.INVESTIGATION_NAME_TO_ID = {"PROJECT ONE": 1}, {}
    return config


@pytest.fixture
def db():
    return _Database()


@pytest.fixture(autouse=True)
def _no_graph(monkeypatch):
    # The published report's graph half runs through the Neo4j tool, which holds its own scope; not under test here.
    monkeypatch.setattr(runners, "tool_neo4j_query", lambda *a, **k: {"ok": True, "data": [], "count": 0})


ADMIN = GraphScope.admin("test")
PROJECT_2 = GraphScope.for_projects([2], source="test")
PROJECTS_1_3 = GraphScope.for_projects([1, 3], source="test")
NO_PROJECTS = GraphScope.for_projects([], source="test")


def _no_scope_configs(db):
    return {
        "scope_none": _config(db, None),
        "no_scope_attribute": _config(db, None, attach=False),
        "plain_dict_admin": _config(db, {"is_admin": True, "project_ids": []}),
        "magicmock_config": _magicmock(db),
    }


# --------------------------------------------------------------------------- #
# The sample report
# --------------------------------------------------------------------------- #

def test_an_admin_sample_report_reads_every_project(db, tmp_path):
    result = runners.run_project_sample_report(_config(db, ADMIN), None, outputs_root=tmp_path)

    assert result["ok"] is True, result.get("error")
    assert set(result["uuids"]) == ALL_UIDS


@pytest.mark.parametrize("scope, projects", [(PROJECT_2, [2]), (PROJECTS_1_3, [1, 3])], ids=["project_2", "projects_1_3"])
def test_a_non_admin_sample_report_for_every_project_reads_only_its_own(db, tmp_path, scope, projects):
    result = runners.run_project_sample_report(_config(db, scope), None, outputs_root=tmp_path)

    assert result["ok"] is True, result.get("error")
    assert set(result["uuids"]) == _uids(projects)
    assert set(result["uuid_preview"]) <= _uids(projects)
    assert set(result["labs_table"]) == {uid.split("-")[1][6:] for uid in _uids(projects)}


def test_a_non_admin_sample_report_for_its_own_project_reads_that_project(db, tmp_path):
    result = runners.run_project_sample_report(_config(db, PROJECTS_1_3), "Project three", outputs_root=tmp_path)

    assert result["ok"] is True, result.get("error")
    assert set(result["uuids"]) == _uids([3])


def test_a_non_admin_sample_report_for_another_project_is_refused_before_any_statement(db, tmp_path):
    result = runners.run_project_sample_report(_config(db, PROJECT_2), "Project one", outputs_root=tmp_path)

    assert result["ok"] is False
    assert "uuids" not in result and "db_diagnostic" not in result
    assert db.statements == [], "no statement may run for a project outside the caller's scope"
    assert _foreign(json.dumps(result), [2]) == []


def test_a_caller_with_no_projects_reads_no_sample(db, tmp_path):
    result = runners.run_project_sample_report(_config(db, NO_PROJECTS), None, outputs_root=tmp_path)

    assert result["ok"] is True, result.get("error")
    assert result["uuids"] == [] and result["rows_returned"] == 0


@pytest.mark.parametrize("name", ["scope_none", "no_scope_attribute", "plain_dict_admin", "magicmock_config"])
def test_a_sample_report_without_a_scope_is_refused(db, tmp_path, name):
    result = runners.run_project_sample_report(_no_scope_configs(db)[name], None, outputs_root=tmp_path)

    assert result["ok"] is False
    assert db.statements == []


# --------------------------------------------------------------------------- #
# The protocols report and the published report's relational half
# --------------------------------------------------------------------------- #

def test_an_admin_protocols_report_reads_every_project(db, tmp_path):
    result = runners.run_project_protocols_report(_config(db, ADMIN), None, outputs_root=tmp_path)

    assert result["ok"] is True, result.get("error")
    assert set(result["titles"]) == ALL_TITLES


def test_a_non_admin_protocols_report_reads_only_its_own(db, tmp_path):
    result = runners.run_project_protocols_report(_config(db, PROJECT_2), None, outputs_root=tmp_path)

    assert result["ok"] is True, result.get("error")
    assert set(result["titles"]) == _titles([2])


def test_a_non_admin_protocols_report_for_another_project_is_refused(db, tmp_path):
    result = runners.run_project_protocols_report(_config(db, PROJECT_2), "Project three", outputs_root=tmp_path)

    assert result["ok"] is False
    assert db.statements == []


@pytest.mark.parametrize("name", ["scope_none", "no_scope_attribute", "plain_dict_admin", "magicmock_config"])
def test_a_protocols_report_without_a_scope_is_refused(db, tmp_path, name):
    result = runners.run_project_protocols_report(_no_scope_configs(db)[name], None, outputs_root=tmp_path)

    assert result["ok"] is False
    assert db.statements == []


def test_a_non_admin_published_report_reads_only_its_own_protocols(db, tmp_path):
    result = runners.run_project_published_report(_config(db, PROJECT_2), None, outputs_root=tmp_path)

    assert result["protocols"]["ok"] is True, result["protocols"].get("error")
    assert set(result["protocols"]["titles"]) == _titles([2])


def test_an_admin_published_report_reads_every_projects_protocols(db, tmp_path):
    result = runners.run_project_published_report(_config(db, ADMIN), None, outputs_root=tmp_path)

    assert set(result["protocols"]["titles"]) == ALL_TITLES


@pytest.mark.parametrize("name", ["scope_none", "no_scope_attribute", "plain_dict_admin", "magicmock_config"])
def test_a_published_report_without_a_scope_reads_no_protocol(db, tmp_path, name):
    result = runners.run_project_published_report(_no_scope_configs(db)[name], None, outputs_root=tmp_path)

    assert result["protocols"]["ok"] is False
    assert db.statements == []


# --------------------------------------------------------------------------- #
# Through the reporter summary and the CC report op
# --------------------------------------------------------------------------- #

def _foreign(text: str, projects) -> list[str]:
    """Every sample uid or protocol title in `text` that the caller limited to `projects` may not read."""
    return sorted(value for value in (ALL_UIDS | ALL_TITLES) - _uids(projects) - _titles(projects) if value in text)


@pytest.mark.parametrize("summary_mode", ["samples", "protocols", "published", "RPPR"])
def test_a_non_admin_reporter_summary_carries_no_foreign_row(db, tmp_path, summary_mode):
    plan = types.SimpleNamespace(project=None, years=[], month_range=None, day_range=None,
                                 summary_mode=summary_mode, reporter_context=None)

    result, saved, summary = runners.run_reporter_summary(_config(db, PROJECT_2), plan, str(tmp_path), lab_codes=[])

    assert result.get("ok") is True, result.get("error")
    text = json.dumps([result, summary], default=str)
    for path in saved.values():
        text += open(path, encoding="utf-8").read()
    assert _foreign(text, [2]) == []
    assert summary["project_scope"] == {"project_ids": [2]}


def test_an_admin_reporter_summary_names_no_project_scope(db, tmp_path):
    plan = types.SimpleNamespace(project=None, years=[], month_range=None, day_range=None,
                                 summary_mode="samples", reporter_context=None)

    _result, _saved, summary = runners.run_reporter_summary(_config(db, ADMIN), plan, str(tmp_path), lab_codes=[])

    assert "project_scope" not in summary


@pytest.mark.parametrize("mode", ["samples", "protocols", "rppr"])
def test_the_cc_report_op_carries_no_foreign_row(db, tmp_path, mode):
    out = run_op("report", {"mode": mode, "project": None}, config=_config(db, PROJECT_2), session=None,
                 write_gate=build_gate(load_allowlist_from_entries([])), outputs_dir=str(tmp_path))

    text = json.dumps(out, default=str)
    for path in (out.get("saved_files") or {}).values():
        text += open(path, encoding="utf-8").read()
    assert _foreign(text, [2]) == []
    assert out["rows"].get("ok") is True, out["rows"].get("error")


def test_the_cc_report_op_refuses_another_projects_report(db, tmp_path):
    out = run_op("report", {"mode": "samples", "project": "Project one"}, config=_config(db, PROJECT_2),
                 session=None, write_gate=build_gate(load_allowlist_from_entries([])), outputs_dir=str(tmp_path))

    assert out["rows"]["ok"] is False
    assert db.statements == []

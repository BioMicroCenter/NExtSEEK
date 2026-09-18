"""The daily context export reads SEEK's labs first and carries them on every project row.

Spec 2026-09-18, sections 4.4, 6.3, 6.4 and 6.5:

* ``_fetch_context_files_from_db`` runs the labs read (``chat_nextseek.labs``) as its
  first statement, writes ``labs_db.json``, and injects ``labs: [{code, name,
  affiliation}]`` into every project row of ``projects_db.json``. Investigation rows
  carry no ``labs`` key.
* When the read fails, the labs come from the ``labs_db.json`` already on disk; with
  neither, every project row gets ``labs: []``.
* ``map_project`` stays a nested function reading every curated column through
  ``lower.get`` (``NessieAI/tests/api/test_context_gen.py`` parses it).
* ``ChatConfig.LABS`` is the checked list, or ``None`` when unavailable, and
  ``LABS_STATUS`` says where it came from.

Every lab title is invented. Hermetic: a recording fake connection, no database.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path

import pytest

from NessieAI import paths
from chat_nextseek import labs
from chat_nextseek.config import ChatConfig


CONFIG_PY = paths.CHAT_NEXTSEEK_DIR / "src" / "chat_nextseek" / "config.py"
CONTEXT = paths.CHAT_NEXTSEEK_DIR / "src" / "chat_nextseek" / "context"

INSTITUTIONS = [
    (7, "Example Institute of Technology", 2),
    (41, "ASH-Ashgrove Lab (BWH)", 4),
    (41, "ASH-Ashgrove Lab (BWH)", 12),
    (43, "BRK-Birchwood Lab (MIT)", 4),
    (44, "CED-Cedarfield Lab (Harvard)", None),
]

PROJECT_ROWS = [
    {"name": "Alpha", "entity_type": "project", "project_id": 4, "alternative_names": '["Alpha Project"]',
     "pi": "curated prose", "tags": "t"},
    {"name": "Beta", "entity_type": None, "project_id": 12, "alternative_names": None},
    {"name": "Gamma", "entity_type": "project", "project_id": 30, "alternative_names": None},
    {"name": "Alpha Study", "entity_type": "investigation", "project_id": 4, "parent_project": "Alpha",
     "alternative_names": '["Alpha"]'},
]

TABLES = {
    "dmac.sample_types_context": [{"sample_type": "TIS", "name": "Tissue"}],
    "dmac.assay_context": [{"assay_name": "RNA-seq"}],
    "dmac.projects_context": PROJECT_ROWS,
}


class _Cursor:
    def __init__(self, conn, dictionary):
        self._conn = conn
        self._dictionary = dictionary
        self._rows = []
        self.column_names = []

    def execute(self, sql, params=None):
        self._conn.calls.append(("execute", sql))
        if sql == labs.INSTITUTIONS_SQL:
            if self._conn.labs_error is not None:
                raise self._conn.labs_error
            self._rows = list(self._conn.institutions)
            return
        table = sql.rsplit(" ", 1)[-1]
        self._rows = [dict(r) for r in TABLES[table]]

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _Conn:
    def __init__(self, institutions=INSTITUTIONS, labs_error=None):
        self.institutions = institutions
        self.labs_error = labs_error
        self.calls: list[tuple] = []

    def rollback(self):
        self.calls.append(("rollback",))

    def start_transaction(self, **kwargs):
        self.calls.append(("start_transaction", kwargs))

    def cursor(self, dictionary=False):
        return _Cursor(self, dictionary)

    def close(self):
        self.calls.append(("close",))


def _bare(tmp_path, conn) -> ChatConfig:
    cfg = ChatConfig.__new__(ChatConfig)  # no __init__: no provider, no Django
    cfg.CONTEXT_DIR = str(tmp_path)
    cfg._db_conn = conn
    return cfg


def _projects(tmp_path) -> dict[str, dict]:
    rows = json.loads((tmp_path / "projects_db.json").read_text(encoding="utf-8"))
    return {r["name"]: r for r in rows}


# --------------------------------------------------------------------------------------
# The export
# --------------------------------------------------------------------------------------

def test_the_labs_read_is_the_first_statement_of_the_export(tmp_path):
    conn = _Conn()
    _bare(tmp_path, conn)._fetch_context_files_from_db(env="prod")

    executes = [c[1] for c in conn.calls if c[0] == "execute"]
    assert executes[0] == labs.INSTITUTIONS_SQL
    assert executes.count(labs.INSTITUTIONS_SQL) == 1
    assert ("start_transaction", {"readonly": True}) in conn.calls


def test_the_export_writes_the_labs_file(tmp_path):
    _bare(tmp_path, _Conn())._fetch_context_files_from_db(env="prod")

    doc = labs.load_labs_file(tmp_path)
    assert doc is not None
    assert [(r["code"], r["institution_id"]) for r in doc["labs"]] == [("ASH", 41), ("BRK", 43), ("CED", 44)]
    assert [u["institution_id"] for u in doc["unparsed"]] == [7]


def test_project_rows_carry_their_labs_and_investigation_rows_none(tmp_path):
    _bare(tmp_path, _Conn())._fetch_context_files_from_db(env="prod")
    rows = _projects(tmp_path)

    assert rows["Alpha"]["labs"] == [
        {"code": "ASH", "name": "Ashgrove", "affiliation": "BWH"},
        {"code": "BRK", "name": "Birchwood", "affiliation": "MIT"},
    ]
    assert rows["Beta"]["labs"] == [{"code": "ASH", "name": "Ashgrove", "affiliation": "BWH"}], \
        "a row with no entity_type is a project row"
    assert rows["Gamma"]["labs"] == [], "a project no parsed lab belongs to"
    assert "labs" not in rows["Alpha Study"], "an investigation's labs are its parent project's"


# Production's projects_context, before the gated 6.16 write, types most PROJECT rows
# 'investigation' with no parent_project, and one sub-project row 'study'. Those project rows
# must keep their labs on a box that never runs 6.16. Invented names.
LEGACY_ROWS = [
    {"name": "Alder", "entity_type": "investigation", "parent_project": None, "project_id": 4,
     "alternative_names": '["Alder Consortium"]'},
    {"name": "Birch", "entity_type": "investigation", "parent_project": "", "project_id": 12,
     "alternative_names": None},
    {"name": "Alder Core", "entity_type": "study", "parent_project": "Alder", "project_id": 4,
     "alternative_names": None},
    {"name": "PUBLISHED", "entity_type": "project", "parent_project": None, "project_id": 30,
     "alternative_names": None},
]


def test_legacy_project_rows_typed_investigation_carry_their_labs(tmp_path, monkeypatch):
    monkeypatch.setitem(TABLES, "dmac.projects_context", LEGACY_ROWS)
    _bare(tmp_path, _Conn())._fetch_context_files_from_db(env="prod")
    rows = _projects(tmp_path)

    assert rows["Alder"]["labs"] == [
        {"code": "ASH", "name": "Ashgrove", "affiliation": "BWH"},
        {"code": "BRK", "name": "Birchwood", "affiliation": "MIT"},
    ]
    assert rows["Birch"]["labs"] == [{"code": "ASH", "name": "Ashgrove", "affiliation": "BWH"}]
    assert rows["PUBLISHED"]["labs"] == []
    assert "labs" not in rows["Alder Core"], "a study row is not a project row"
    assert rows["Alder"]["entity_type"] == "investigation", "the export rewrites no row's type"


def test_every_existing_key_is_unchanged(tmp_path):
    _bare(tmp_path, _Conn())._fetch_context_files_from_db(env="prod")
    alpha = _projects(tmp_path)["Alpha"]

    assert alpha["alternative_names"] == ["Alpha Project"]
    assert alpha["pi"] == "curated prose"
    assert alpha["entity_type"] == "project" and alpha["project_id"] == 4
    assert list(alpha)[:12] == [
        "name", "alternative_names", "entity_type", "project_id", "parent_project", "pi",
        "research_focus", "key_data_types", "description", "nih_reporter_link",
        "fairdomhub_published_link", "tags",
    ]
    assert list(alpha)[12:] == ["labs"]


def test_a_failed_read_falls_back_to_the_previous_labs_file(tmp_path):
    previous = labs.build_labs_document([(50, "DUN-Dunmore Lab (MIT)", 30)], fetched_at="2026-09-18T06:00:00Z")
    labs.write_labs_file(previous, tmp_path)
    cfg = _bare(tmp_path, _Conn(labs_error=RuntimeError("no such table")))

    exports = cfg._fetch_context_files_from_db(env="prod")

    rows = _projects(tmp_path)
    assert rows["Gamma"]["labs"] == [{"code": "DUN", "name": "Dunmore", "affiliation": "MIT"}]
    assert rows["Alpha"]["labs"] == []
    assert labs.load_labs_file(tmp_path) == previous, "the previous file is not touched"
    assert {"projects_full", "sampletypes_full", "assays_full"} <= set(exports), \
        "a failed labs read never blocks the export"


def test_an_empty_answer_is_a_failed_read_and_keeps_the_last_good_labs(tmp_path):
    previous = labs.build_labs_document([(50, "DUN-Dunmore Lab (MIT)", 30)], fetched_at="2026-09-18T06:00:00Z")
    labs.write_labs_file(previous, tmp_path)
    cfg = _bare(tmp_path, _Conn(institutions=[]))

    cfg._fetch_context_files_from_db(env="prod")
    records, status = cfg._load_labs()

    assert labs.load_labs_file(tmp_path) == previous, "zero rows never overwrite a good labs file"
    assert _projects(tmp_path)["Gamma"]["labs"] == [{"code": "DUN", "name": "Dunmore", "affiliation": "MIT"}]
    assert [r["code"] for r in records] == ["DUN"]
    assert status["source"] == "previous_file"


def test_a_failed_read_with_no_previous_file_gives_every_project_row_empty_labs(tmp_path):
    cfg = _bare(tmp_path, _Conn(labs_error=RuntimeError("no such schema")))

    cfg._fetch_context_files_from_db(env="prod")

    rows = _projects(tmp_path)
    assert [rows[n]["labs"] for n in ("Alpha", "Beta", "Gamma")] == [[], [], []]
    assert "labs" not in rows["Alpha Study"]
    assert not (tmp_path / "labs_db.json").exists()


def test_the_labs_file_is_not_an_export_so_it_cannot_mark_the_day_fresh(tmp_path):
    """Only the three table exports decide the refresh marker (_ensure_context_files)."""
    exports = _bare(tmp_path, _Conn())._fetch_context_files_from_db(env="prod")
    assert all(Path(p).name != "labs_db.json" for p in exports.values())


def test_the_labs_file_is_not_a_refresh_target(tmp_path, monkeypatch):
    """Its absence must never force a refresh on every start (spec 6.1)."""
    cfg = ChatConfig.__new__(ChatConfig)
    cfg.CONTEXT_DIR = str(tmp_path)
    for name in ("sampletypes_db.json", "min_sampletypes_db.json", "assays_db.json",
                 "min_assays_db.json", "projects_db.json"):
        (tmp_path / name).write_text("[]", encoding="utf-8")
    cfg._write_refresh_marker()
    calls = []
    monkeypatch.setattr(cfg, "_fetch_context_files_from_db", lambda env="prod": calls.append(env) or {})

    paths_ = cfg._ensure_context_files()

    assert calls == [], "a missing labs_db.json forced a refresh"
    assert "labs_db.json" not in {Path(p).name for p in paths_.values()}


# --------------------------------------------------------------------------------------
# map_project keeps its shape (the column-fidelity contract, spec 6.4 and 12.4)
# --------------------------------------------------------------------------------------

def _function_def(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(name)


def test_map_project_is_still_nested_in_the_export():
    tree = ast.parse(CONFIG_PY.read_text(encoding="utf-8"))
    export = _function_def(tree, "_fetch_context_files_from_db")
    nested = {n.name for n in export.body if isinstance(n, ast.FunctionDef)}
    nested |= {n.name for stmt in export.body if isinstance(stmt, ast.Try)
               for n in stmt.body if isinstance(n, ast.FunctionDef)}
    assert "map_project" in nested


def test_map_project_reads_the_same_columns_through_lower_get():
    """The read list test_context_gen parses, reproduced here so this branch sees it.

    The parse is the one in NessieAI/tests/api/test_context_gen.py::_reader_columns:
    from `def map_project(row: dict) -> dict:` to the next 12-space `def`, every
    `lower.get("...")`. The labs key comes from the enclosing scope, never from a column.
    """
    source = CONFIG_PY.read_text(encoding="utf-8")
    assert "            def map_project(row: dict) -> dict:" in source
    body = source.split("def map_project(row: dict) -> dict:", 1)[1]
    body = body.split("\n            def ", 1)[0]
    assert set(re.findall(r'lower\.get\("(\w+)"\)', body)) == {
        "name", "alternative_names", "entity_type", "project_id", "parent_project", "pi",
        "research_focus", "key_data_types", "description", "nih_reporter_link",
        "fairdomhub_published_link", "tags",
    }


# --------------------------------------------------------------------------------------
# ChatConfig.LABS and LABS_STATUS (spec 6.5)
# --------------------------------------------------------------------------------------

def test_labs_come_from_this_process_fetch(tmp_path):
    cfg = _bare(tmp_path, _Conn())
    cfg._fetch_context_files_from_db(env="prod")

    records, status = cfg._load_labs()

    assert [r["code"] for r in records] == ["ASH", "BRK", "CED"]
    assert status["source"] == "fetched"
    assert status["unparsed"] == 1
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", status["fetched_at"])


def test_labs_come_from_the_file_on_disk_when_this_process_did_not_fetch(tmp_path):
    labs.write_labs_file(labs.build_labs_document(INSTITUTIONS, fetched_at="2026-09-18T06:00:00Z"), tmp_path)
    cfg = ChatConfig.__new__(ChatConfig)
    cfg.CONTEXT_DIR = str(tmp_path)

    records, status = cfg._load_labs()

    assert [r["code"] for r in records] == ["ASH", "BRK", "CED"]
    assert status == {"source": "previous_file", "fetched_at": "2026-09-18T06:00:00Z", "unparsed": 1}


def test_labs_from_a_failed_fetch_are_the_previous_file(tmp_path):
    labs.write_labs_file(labs.build_labs_document(INSTITUTIONS, fetched_at="2026-09-18T06:00:00Z"), tmp_path)
    cfg = _bare(tmp_path, _Conn(labs_error=RuntimeError("down")))
    cfg._fetch_context_files_from_db(env="prod")

    records, status = cfg._load_labs()

    assert len(records) == 3
    assert status["source"] == "previous_file"


def test_no_labs_anywhere_is_none_not_empty(tmp_path):
    cfg = ChatConfig.__new__(ChatConfig)
    cfg.CONTEXT_DIR = str(tmp_path)

    records, status = cfg._load_labs()

    assert records is None
    assert status == {"source": "unavailable", "fetched_at": None, "unparsed": None}


def test_a_document_with_no_parseable_lab_is_an_empty_list_not_none(tmp_path):
    labs.write_labs_file(labs.build_labs_document([(7, "Example Institute of Technology", 2)],
                                                  fetched_at="2026-09-18T06:00:00Z"), tmp_path)
    cfg = ChatConfig.__new__(ChatConfig)
    cfg.CONTEXT_DIR = str(tmp_path)

    records, status = cfg._load_labs()

    assert records == []
    assert status["source"] == "previous_file" and status["unparsed"] == 1


def test_malformed_records_are_dropped_from_labs(tmp_path):
    (tmp_path / "labs_db.json").write_text(json.dumps({
        "version": 1, "fetched_at": "2026-09-18T06:00:00Z", "unparsed": [], "conflicts": [],
        "labs": [{"code": "ASH", "name": "Ashgrove", "affiliation": "BWH", "project_ids": [4]},
                 {"code": "as", "name": "Ashgrove"}, {"code": "BRK", "name": ""}],
    }), encoding="utf-8")
    cfg = ChatConfig.__new__(ChatConfig)
    cfg.CONTEXT_DIR = str(tmp_path)

    records, _ = cfg._load_labs()

    assert [r["code"] for r in records] == ["ASH"]


# --------------------------------------------------------------------------------------
# The attributes on a constructed ChatConfig
# --------------------------------------------------------------------------------------

def _construct(tmp_path, monkeypatch) -> ChatConfig:
    monkeypatch.setenv("NEXTSEEK_MODE", "gcp")
    for name in ("SEMANTIC_SHORTLIST_ENABLED", "SEMANTIC_ENDPOINTS_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("chat_nextseek.config.build_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(ChatConfig, "_build_secondary_clients", lambda self: {})
    monkeypatch.setattr(ChatConfig, "_connect_db", lambda self, env="dev": None)
    monkeypatch.setattr(ChatConfig, "_load_name_to_id_from_db", lambda self, table, env="prod": {})
    return ChatConfig({
        "CONTEXT_DIR": str(tmp_path),
        "CATALOG_FILE": str(paths.CHAT_NEXTSEEK_DIR / "agent_model_catalog.json"),
        "GCP_API_KEY": "dummy",
        "API_SCHEMA": {},
    })


@pytest.fixture
def ctx(tmp_path):
    ctx = tmp_path / "context"
    shutil.copytree(CONTEXT, ctx)
    (ctx / "labs_db.json").unlink(missing_ok=True)
    return ctx


def test_a_constructed_config_carries_labs_from_the_file(ctx, monkeypatch):
    labs.write_labs_file(labs.build_labs_document(INSTITUTIONS, fetched_at="2026-09-18T06:00:00Z"), ctx)

    config = _construct(ctx, monkeypatch)

    assert [r["code"] for r in config.LABS] == ["ASH", "BRK", "CED"]
    assert config.LABS_STATUS == {"source": "previous_file", "fetched_at": "2026-09-18T06:00:00Z", "unparsed": 1}


def test_a_constructed_config_without_a_labs_file_says_unavailable(ctx, monkeypatch):
    config = _construct(ctx, monkeypatch)

    assert config.LABS is None
    assert config.LABS_STATUS["source"] == "unavailable"
    assert not (ctx / "labs_db.json").exists(), "construction with no database writes no labs file"

"""config.py builds SEPARATE project and investigation name->id maps from the DB.

Projects and investigations are kept in distinct maps (PROJECT_NAME_TO_ID,
INVESTIGATION_NAME_TO_ID) — never blended — so the report can offer an
investigation-scoped path without touching the project path. Both are dynamic
from the live DB; {} when the DB is down (never the removed hardcoded literal).
"""
from __future__ import annotations

import json
import shutil

import pytest

from NessieAI import paths
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


# --------------------------------------------------------------------------------------
# projects_db.json holds project AND investigation rows (spec 2026-09-18, section 6.5)
# --------------------------------------------------------------------------------------
#
# An investigation row carries its owner's project_id and may share its exact name with a
# project row (the real CSBC and MetNet do). So the name-keyed map and the name->id merge
# read project rows only; FULL_INVESTIGATIONS_MAP holds the investigation rows; the entity
# agent's MIN_PROJECTS keeps every row, each saying its entity_type. Names are invented.

_ROWS = [
    {"name": "Alpha", "entity_type": "project", "project_id": 4, "alternative_names": ["Alpha Project"]},
    {"name": "Alpha", "entity_type": "investigation", "project_id": 4, "parent_project": "Alpha",
     "alternative_names": ["Alpha Inv"]},
    {"name": "Beta", "project_id": 12, "alternative_names": []},                      # no entity_type: a project
    {"name": "Gamma Study", "entity_type": "investigation", "project_id": 7, "parent_project": "Gamma",
     "alternative_names": ["Gamma Collection"]},
]


def test_the_name_to_id_merge_reads_project_rows_only():
    cfg = ChatConfig.__new__(ChatConfig)

    merged = cfg._merge_project_name_to_id({"PUBLISHED DATA": 1}, _ROWS)

    assert merged == {"PUBLISHED DATA": 1, "ALPHA": 4, "ALPHA PROJECT": 4, "BETA": 12}
    # an investigation's own name or alias never becomes a whole-project scope
    for name in ("ALPHA INV", "GAMMA STUDY", "GAMMA COLLECTION"):
        assert name not in merged


def _construct(ctx, monkeypatch) -> ChatConfig:
    monkeypatch.setenv("NEXTSEEK_MODE", "gcp")
    for name in ("SEMANTIC_SHORTLIST_ENABLED", "SEMANTIC_ENDPOINTS_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("chat_nextseek.config.build_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(ChatConfig, "_build_secondary_clients", lambda self: {})
    monkeypatch.setattr(ChatConfig, "_connect_db", lambda self, env="dev": None)
    monkeypatch.setattr(ChatConfig, "_load_name_to_id_from_db", lambda self, table, env="prod": {})
    return ChatConfig({
        "CONTEXT_DIR": str(ctx),
        "CATALOG_FILE": str(paths.CHAT_NEXTSEEK_DIR / "agent_model_catalog.json"),
        "GCP_API_KEY": "dummy",
        "API_SCHEMA": {},
    })


@pytest.fixture
def config_over_rows(tmp_path, monkeypatch):
    ctx = tmp_path / "context"
    shutil.copytree(paths.CHAT_NEXTSEEK_DIR / "src" / "chat_nextseek" / "context", ctx)
    (ctx / "projects_db.json").write_text(json.dumps(_ROWS), encoding="utf-8")
    return _construct(ctx, monkeypatch)


def test_full_projects_map_holds_project_rows_only(config_over_rows):
    config = config_over_rows

    assert set(config.FULL_PROJECTS_MAP) == {"Alpha", "Beta"}
    assert config.FULL_PROJECTS_MAP["Alpha"]["entity_type"] == "project", \
        "a same-named investigation row must not replace the project row"


def test_full_investigations_map_holds_investigation_rows(config_over_rows):
    config = config_over_rows

    assert set(config.FULL_INVESTIGATIONS_MAP) == {"Alpha", "Gamma Study"}
    assert config.FULL_INVESTIGATIONS_MAP["Alpha"]["entity_type"] == "investigation"


def test_min_projects_keeps_every_row(config_over_rows):
    config = config_over_rows

    assert config.MIN_PROJECTS == _ROWS
    assert config.FULL_PROJECTS == _ROWS


def test_project_name_to_id_ignores_investigation_rows(config_over_rows):
    config = config_over_rows

    assert config.PROJECT_NAME_TO_ID == {"ALPHA": 4, "ALPHA PROJECT": 4, "BETA": 12}


# --------------------------------------------------------------------------------------
# The legacy shape: what production's projects_context holds before the gated 6.16 write
# --------------------------------------------------------------------------------------
#
# Production (and every box restored from it) types most PROJECT rows 'investigation', with
# no parent_project; one sub-project row 'study'; a few rows 'project'. The code must be right
# on a box that never runs 6.16: a row is an investigation only when it is typed
# 'investigation' AND names its parent_project, and a 'study' row is neither. Invented names.

_LEGACY_ROWS = [
    {"name": "Alder", "entity_type": "investigation", "parent_project": None, "project_id": 9,
     "alternative_names": ["Alder Consortium"]},
    {"name": "Birch", "entity_type": "investigation", "parent_project": None, "project_id": 10,
     "alternative_names": []},
    {"name": "Cedar", "entity_type": "investigation", "parent_project": "", "project_id": 11,
     "alternative_names": None},
    {"name": "Alder Core", "entity_type": "study", "parent_project": "Alder", "project_id": 9,
     "alternative_names": ["Alder Core Study"]},
    {"name": "PUBLISHED", "entity_type": "project", "parent_project": None, "project_id": 6,
     "alternative_names": []},
]


@pytest.fixture
def config_over_legacy_rows(tmp_path, monkeypatch):
    ctx = tmp_path / "context"
    shutil.copytree(paths.CHAT_NEXTSEEK_DIR / "src" / "chat_nextseek" / "context", ctx)
    (ctx / "projects_db.json").write_text(json.dumps(_LEGACY_ROWS), encoding="utf-8")
    return _construct(ctx, monkeypatch)


def test_legacy_project_rows_typed_investigation_stay_projects(config_over_legacy_rows):
    config = config_over_legacy_rows

    assert set(config.FULL_PROJECTS_MAP) == {"Alder", "Birch", "Cedar", "PUBLISHED"}
    assert config.FULL_INVESTIGATIONS_MAP == {}, \
        "a row with no parent_project is a project, whatever its entity_type says"


def test_legacy_project_rows_keep_their_names_and_aliases_as_scopes(config_over_legacy_rows):
    config = config_over_legacy_rows

    assert config.PROJECT_NAME_TO_ID == {
        "ALDER": 9, "ALDER CONSORTIUM": 9, "BIRCH": 10, "CEDAR": 11, "PUBLISHED": 6,
    }


def test_a_study_row_is_neither_a_project_nor_an_investigation(config_over_legacy_rows):
    config = config_over_legacy_rows

    assert "Alder Core" not in config.FULL_PROJECTS_MAP
    assert "Alder Core" not in config.FULL_INVESTIGATIONS_MAP
    assert "ALDER CORE" not in config.PROJECT_NAME_TO_ID
    assert "ALDER CORE STUDY" not in config.PROJECT_NAME_TO_ID
    assert config.MIN_PROJECTS == _LEGACY_ROWS, "the entity agent still sees every row"

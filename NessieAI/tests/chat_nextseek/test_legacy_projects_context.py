"""Production's legacy projects_context, end to end through every reader the three units touch.

Until the gated 6.16 write, production's table (and every box restored from it) types most
PROJECT rows 'investigation' with an empty parent_project, one sub-project row 'study', and a
few rows 'project'. The adversarial review of 2026-09-18 found that the labs and
investigations units, merged, read those projects as investigations: they left the project
maps and the name-to-id merge, lost their labs and their page header, and reached the system
agent as "<name> (investigation)". This walks rows of that shape through the daily export
(with SEEK's labs), a constructed ChatConfig, the entity agent's lab matcher, the system agent
and the project page, and requires every project to stay a project and keep its labs.

Spec 2026-09-18, section 11.1. Every name and title is invented.
"""
from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from NessieAI import paths
from chat_nextseek import labs
from chat_nextseek.agents import system as system_mod
from chat_nextseek.config import ChatConfig
from chat_nextseek.helpers.lab_code import resolve_labs
from chat_nextseek.schemas import ParserPlan, SystemAgentOutput

CONTEXT = paths.CHAT_NEXTSEEK_DIR / "src" / "chat_nextseek" / "context"

INSTITUTIONS = [
    (41, "ASH-Ashgrove Lab (BWH)", 9),
    (43, "BRK-Birchwood Lab (MIT)", 9),
    (43, "BRK-Birchwood Lab (MIT)", 10),
    (44, "CED-Cedarfield Lab (Harvard)", 11),
    (7, "Example Institute of Technology", 6),
]

# Production's shape: projects typed 'investigation' with no parent_project (NULL or ''),
# one 'study' row under one of them, and one 'project' row.
LEGACY_ROWS = [
    {"name": "Alder", "entity_type": "investigation", "parent_project": None, "project_id": 9,
     "alternative_names": '["Alder Consortium"]', "research_focus": "Alder's focus."},
    {"name": "Birch", "entity_type": "investigation", "parent_project": None, "project_id": 10,
     "alternative_names": None, "research_focus": "Birch's focus."},
    {"name": "Cedar", "entity_type": "investigation", "parent_project": "", "project_id": 11,
     "alternative_names": '["Cedar Center"]', "research_focus": "Cedar's focus."},
    {"name": "Alder Core", "entity_type": "study", "parent_project": "Alder", "project_id": 9,
     "alternative_names": None, "research_focus": "A study inside Alder."},
    {"name": "PUBLISHED", "entity_type": "project", "parent_project": None, "project_id": 6,
     "alternative_names": None, "research_focus": "Published data."},
]
PROJECTS = {"Alder": 9, "Birch": 10, "Cedar": 11, "PUBLISHED": 6}
EXPECTED_LABS = {
    "Alder": ["ASH", "BRK"],
    "Birch": ["BRK"],
    "Cedar": ["CED"],
    "PUBLISHED": [],
}

TABLES = {
    "dmac.sample_types_context": [{"sample_type": "TIS", "name": "Tissue"}],
    "dmac.assay_context": [{"assay_name": "RNA-seq"}],
    "dmac.projects_context": LEGACY_ROWS,
}


class _Cursor:
    def __init__(self):
        self._rows = []

    def execute(self, sql, params=None):
        if sql == labs.INSTITUTIONS_SQL:
            self._rows = list(INSTITUTIONS)
        else:
            self._rows = [dict(r) for r in TABLES[sql.rsplit(" ", 1)[-1]]]

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _Conn:
    def rollback(self):
        pass

    def start_transaction(self, **kwargs):
        pass

    def cursor(self, dictionary=False):
        return _Cursor()


@pytest.fixture
def config(tmp_path, monkeypatch) -> ChatConfig:
    """The daily export over legacy rows, then a ChatConfig built on what it wrote."""
    ctx = tmp_path / "context"
    shutil.copytree(CONTEXT, ctx)
    (ctx / labs.LABS_FILE_NAME).unlink(missing_ok=True)
    exporter = ChatConfig.__new__(ChatConfig)
    exporter.CONTEXT_DIR = str(ctx)
    exporter._db_conn = _Conn()
    exporter._fetch_context_files_from_db(env="prod")

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


def test_every_legacy_project_is_a_project_and_none_an_investigation(config):
    assert set(config.FULL_PROJECTS_MAP) == set(PROJECTS)
    assert config.FULL_INVESTIGATIONS_MAP == {}
    assert [r["name"] for r in config.MIN_PROJECTS] == [r["name"] for r in LEGACY_ROWS]


def test_every_legacy_project_keeps_its_name_and_aliases_as_a_scope(config):
    assert config.PROJECT_NAME_TO_ID == {
        "ALDER": 9, "ALDER CONSORTIUM": 9, "BIRCH": 10, "CEDAR": 11, "CEDAR CENTER": 11, "PUBLISHED": 6,
    }


def test_every_legacy_project_keeps_its_labs(config):
    assert [r["code"] for r in config.LABS] == ["ASH", "BRK", "CED"]
    for name, codes in EXPECTED_LABS.items():
        assert [lab["code"] for lab in config.FULL_PROJECTS_MAP[name]["labs"]] == codes, name
    study = next(r for r in config.MIN_PROJECTS if r["name"] == "Alder Core")
    assert "labs" not in study


def test_the_entity_agent_resolves_a_lab_in_a_legacy_project(config):
    resolution = resolve_labs(
        "RNA from the Birchwood lab in the Alder project",
        ["Birchwood", "Alder"],
        records=config.LABS,
        projects=config.MIN_PROJECTS,
    )
    assert resolution.lab_codes == ["BRK"]
    assert resolution.labs == ["Birchwood"]
    assert "Alder" not in resolution.scientists, "a project name is never a person"


def test_the_system_agent_is_told_a_legacy_project_is_a_project(config, monkeypatch):
    seen = {}

    def fake(**kwargs):
        seen["messages"] = kwargs["messages"]
        return SystemAgentOutput(mode="get_entities", narrative="ok")

    monkeypatch.setattr(system_mod, "live_catalog_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(system_mod, "call_llm_structured", fake)
    monkeypatch.setattr(config, "get_agent_model", lambda *a, **k: (object(), "model", None))
    system_mod.system_agent(config, "tell me about them", {"projects": ["Alder", "Cedar", "Alder Core"]},
                            ParserPlan(mode="system_question"))
    block = next(m["content"] for m in seen["messages"] if m["content"].startswith("ENTITY_DETAILS"))
    details = json.loads(block.split("\n", 1)[1])

    assert set(details) == {"Alder", "Cedar"}
    assert [lab["code"] for lab in details["Alder"]["labs"]] == ["ASH", "BRK"]


def test_every_legacy_project_page_keeps_its_header(monkeypatch):
    from nextseek_api.services import context_catalog  # noqa: PLC0415

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE projects_context (name TEXT, entity_type TEXT, project_id INTEGER, "
                 "parent_project TEXT, research_focus TEXT)")
    conn.executemany("INSERT INTO projects_context VALUES (?, ?, ?, ?, ?)",
                     [(r["name"], r["entity_type"], r["project_id"], r["parent_project"], r["research_focus"])
                      for r in LEGACY_ROWS])

    def query(sql, params=None):
        return context_catalog._rows_from_cursor(conn.execute(sql.replace("%s", "?"), params or []))

    monkeypatch.setattr(context_catalog, "_query", query)
    for name, project_id in PROJECTS.items():
        header = context_catalog.load_project_context(project_id)
        assert header is not None and header["name"] == name, name

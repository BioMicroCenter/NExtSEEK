"""The parser's known investigation and project titles come from each box's own graph (nessie_fixes fix 9)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import chat_nextseek
from chat_nextseek import graph_catalog, graph_context as gctx, known_titles

PACKAGE = Path(chat_nextseek.__file__).resolve().parent
CORE = (PACKAGE / "prompts" / "parser_core_routing.txt").read_text(encoding="utf-8")
SCHEMA = json.loads((PACKAGE / "context" / "min_graph_schema.json").read_text(encoding="utf-8"))

DEV = SimpleNamespace(investigation_titles=("Griffith", "Impact", "SRP", "Shoulders", "MetNet"),
                      project_titles=("Published Data",))
PROD = SimpleNamespace(investigation_titles=("Endometriosis", "Impactb Investigation", "MIT_SRP", "Collagen Study"),
                       project_titles=("CGR-Endo", "IMPAcTb", "MIT_SRP", "Shoulders Independent Projects"))
OTHER = SimpleNamespace(investigation_titles=("Alpha Cohort", "Beta Study"), project_titles=("Gamma Group",))


def _use(monkeypatch, vocab):
    def get(config):
        if isinstance(vocab, Exception):
            raise vocab
        return vocab
    monkeypatch.setattr(graph_catalog, "get_vocabulary", get)


def _filled(monkeypatch, vocab, text=CORE):
    _use(monkeypatch, vocab)
    return known_titles.fill(None, text)


def _line(text, start):
    return next(l for l in text.splitlines() if l.startswith(start))


def test_a_box_with_other_titles_gets_exactly_those(monkeypatch):
    text = _filled(monkeypatch, OTHER)
    inv = _line(text, "Known investigation titles:")
    assert "Alpha Cohort, Beta Study" in inv
    for word in ("Griffith", "Impact", "SRP", "Shoulders", "Endometriosis"):
        assert word not in inv
    assert "Gamma Group" in text and "{{" not in text


def test_an_alias_word_maps_to_each_boxs_own_titles(monkeypatch):
    prod = _filled(monkeypatch, PROD)
    assert "Griffith for Endometriosis" in prod
    assert "Impact for Impactb Investigation" in prod
    assert "SRP for MIT_SRP" in prod and "Shoulders for Collagen Study" in prod
    assert "Griffith for CGR-Endo" in prod and "Impact for IMPAcTb" in prod
    dev = _filled(monkeypatch, DEV)
    assert "Griffith for" not in dev  # the word is itself a title there
    assert "Griffith, Impact, MetNet, SRP, Shoulders" in dev


def test_an_alias_without_titles_on_this_box_resolves_to_nothing():
    assert known_titles.resolve_aliases(["Alpha Cohort"], "investigation") == {}
    assert known_titles.resolve_aliases(["CGR-Endo"], "investigation") == {}  # a project title is not an investigation's


def test_a_graph_that_is_down_names_no_titles(monkeypatch):
    text = _filled(monkeypatch, RuntimeError("neo4j down"))
    assert "none could be read" in _line(text, "Known investigation titles:")
    assert "{{" not in text


def test_the_schema_is_filled_and_stays_valid_json(monkeypatch):
    _use(monkeypatch, OTHER)
    out = known_titles.schema_json(None, SCHEMA)
    assert "{{" not in out
    data = json.loads(out)
    rule = next(r for r in data["disambiguation_rules"] if r.startswith("Investigation titles to recognize"))
    assert "Alpha Cohort, Beta Study" in rule and "Griffith" not in rule
    node = next(n for n in data["node_types"] if n["label"] == "Investigation")
    assert "Alpha Cohort" in node["description"]


def test_the_committed_files_carry_placeholders_not_one_boxs_titles():
    blob = CORE + json.dumps(SCHEMA)
    for word in ("Griffith", "Shoulders", "Endometriosis", "MIT_SRP"):
        assert word not in blob.split("Known investigation titles:")[1].split("\n")[0], word
    assert known_titles.INVESTIGATIONS in CORE and known_titles.PROJECTS in CORE
    assert known_titles.INVESTIGATIONS in json.dumps(SCHEMA)


# --- the scope falls back to the investigation of that name ----------------------------------------------------------

PROJ_ROW = {"name": "Impact", "alternative_names": ["IMPAcTb"], "entity_type": "project"}


def _levels(inv=(), proj=()):
    return {"study": (), "investigation": inv, "project": proj}


def test_a_project_name_without_a_project_scopes_to_the_investigation():
    hits = gctx.container_titles_for(["Impact"], [PROJ_ROW], _levels(inv=("Impact",), proj=("Published Data",)))
    assert hits == {"Impact": ("investigation", "Impact", True)}


def test_the_same_fallback_for_another_name():
    row = {"name": "Zeta", "alternative_names": ["Zeta Lab"], "entity_type": "project"}
    hits = gctx.container_titles_for(["Zeta"], [row], _levels(inv=("Zeta",)))
    assert hits == {"Zeta": ("investigation", "Zeta", True)}


def test_the_project_still_wins_when_it_exists():
    hits = gctx.container_titles_for(["Impact"], [PROJ_ROW], _levels(inv=("Impact",), proj=("IMPAcTb",)))
    assert hits == {"Impact": ("project", "IMPAcTb", True)}


def test_a_name_matching_neither_stays_unscoped():
    assert gctx.container_titles_for(["Impact"], [PROJ_ROW], _levels(inv=("Other",), proj=("Published Data",))) == {}

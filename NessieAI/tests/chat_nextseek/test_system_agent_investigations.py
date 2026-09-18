"""The system agent's ENTITY_DETAILS once projects_context holds investigation rows.

An investigation row and a project row may share a name (the real CSBC and MetNet do), so the
two live in separate maps on the config (`FULL_PROJECTS_MAP`, `FULL_INVESTIGATIONS_MAP`) and the
system agent sends both, the investigation under "<name> (investigation)" (spec 2026-09-18,
section 11). `FULL_INVESTIGATIONS_MAP` is added by another unit, so it is read with getattr, and a
map that is not a dict (a MagicMock config) reads as empty. Every row here is invented.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from chat_nextseek.agents import system as system_mod
from chat_nextseek.schemas import ParserPlan, SystemAgentOutput

PROJECT = {"name": "Zephyr", "entity_type": "project", "project_id": 4, "research_focus": "A project."}
INVESTIGATION = {"name": "Zephyr", "entity_type": "investigation", "project_id": 4,
                 "parent_project": "Zephyr", "research_focus": "Its investigation."}
OTHER = {"name": "Alder Study", "entity_type": "investigation", "project_id": 4,
         "parent_project": "Zephyr", "research_focus": "Another investigation."}

_UNSET = object()


def _config(projects=_UNSET, investigations=_UNSET):
    c = MagicMock()
    c.FULL_SAMPLETYPES_MAP, c.FULL_ASSAYS_MAP = {}, {}
    if projects is not _UNSET:
        c.FULL_PROJECTS_MAP = projects
    if investigations is not _UNSET:
        c.FULL_INVESTIGATIONS_MAP = investigations
    c.MIN_SAMPLETYPES, c.MIN_ASSAYS, c.MIN_API_ENDPOINTS = [], [], []
    c.CAPABILITIES_DOC = "caps"
    c.NEO4J_SCHEMA = {}
    c.SYSTEM_AGENT_SYSTEM_PROMPT = "system prompt"
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


def _entity_details(monkeypatch, config, projects) -> dict:
    seen = {}

    def fake(**kwargs):
        seen["messages"] = kwargs["messages"]
        return SystemAgentOutput(mode="get_entities", narrative="ok")

    monkeypatch.setattr(system_mod, "live_catalog_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(system_mod, "call_llm_structured", fake)
    system_mod.system_agent(config, "tell me about it", {"projects": projects},
                            ParserPlan(mode="system_question"))
    block = next(m["content"] for m in seen["messages"] if m["content"].startswith("ENTITY_DETAILS"))
    return json.loads(block.split("\n", 1)[1])


def test_a_project_and_its_same_named_investigation_both_reach_the_agent(monkeypatch):
    config = _config({"Zephyr": PROJECT}, {"Zephyr": INVESTIGATION})
    details = _entity_details(monkeypatch, config, ["Zephyr"])
    assert details == {"Zephyr": PROJECT, "Zephyr (investigation)": INVESTIGATION}


def test_an_investigation_with_no_project_row_of_its_name_is_sent_as_an_investigation(monkeypatch):
    config = _config({"Zephyr": PROJECT}, {"Alder Study": OTHER})
    details = _entity_details(monkeypatch, config, ["Alder Study"])
    assert details == {"Alder Study (investigation)": OTHER}


def test_a_config_without_the_investigation_map_sends_the_project_rows_alone(monkeypatch):
    """A MagicMock answers any attribute with another MagicMock: that is not a map."""
    details = _entity_details(monkeypatch, _config({"Zephyr": PROJECT}), ["Zephyr"])
    assert details == {"Zephyr": PROJECT}


def test_maps_that_are_not_dicts_read_as_empty(monkeypatch):
    details = _entity_details(monkeypatch, _config(), ["Zephyr"])
    assert details == {}


def test_a_name_in_neither_map_adds_nothing(monkeypatch):
    config = _config({"Zephyr": PROJECT}, {"Zephyr": INVESTIGATION})
    assert _entity_details(monkeypatch, config, ["Nowhere"]) == {}


# --- the legacy shape: production's rows before the gated 6.16 write ---------------------
#
# Production's projects_context types most PROJECT rows 'investigation' with no
# parent_project, and one sub-project row 'study'. Whatever map a row arrives in, the system
# agent is told what chat_nextseek.context_rows says it is: a row with no parent_project is a
# project, never "<name> (investigation)", and a 'study' row is neither.

LEGACY = {"name": "Zephyr", "entity_type": "investigation", "parent_project": None, "project_id": 4,
          "research_focus": "A project, typed the legacy way."}
STUDY = {"name": "Alder Core", "entity_type": "study", "parent_project": "Zephyr", "project_id": 4,
         "research_focus": "A study inside the project."}


def test_a_legacy_project_row_is_sent_as_a_project(monkeypatch):
    config = _config({"Zephyr": LEGACY}, {})
    assert _entity_details(monkeypatch, config, ["Zephyr"]) == {"Zephyr": LEGACY}


def test_a_legacy_project_row_is_never_labelled_an_investigation(monkeypatch):
    """A map built by testing entity_type alone files it as an investigation: the label must not follow."""
    config = _config({"Zephyr": LEGACY}, {"Zephyr": LEGACY})
    details = _entity_details(monkeypatch, config, ["Zephyr"])
    assert details == {"Zephyr": LEGACY}
    assert "Zephyr (investigation)" not in details


def test_a_study_row_is_sent_as_neither(monkeypatch):
    config = _config({"Alder Core": STUDY}, {"Alder Core": STUDY})
    assert _entity_details(monkeypatch, config, ["Alder Core"]) == {}

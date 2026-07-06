"""Tests for the published-report umbrella opt-in (issue #1, option 2).

The published-mode samples report filters Neo4j investigations by
`inv.title CONTAINS <project-name hint>`. That heuristic works on prod (projects
map closely to investigation titles) but returns 0 for a dev-style umbrella
project (e.g. "Published Data") that contains every investigation and matches no
title. This opt-in lets such a project skip the hint and report ALL samples.

PROD-SAFETY: the opt-in is driven by NEXTSEEK_PUBLISHED_UMBRELLA_PROJECTS and
DEFAULTS EMPTY. With nothing configured, `is_umbrella_published_project` is
always False -> the hint is applied exactly as before -> prod is unchanged.
"""
from __future__ import annotations

from chat_nextseek.config import ChatConfig


def _cfg(raw: str) -> ChatConfig:
    cfg = ChatConfig.__new__(ChatConfig)  # bypass __init__ (no Django/DB)
    cfg.PUBLISHED_UMBRELLA_PROJECTS = cfg._parse_umbrella_projects(raw)
    return cfg


def test_default_empty_is_prod_safe_noop():
    cfg = _cfg("")
    assert cfg.PUBLISHED_UMBRELLA_PROJECTS == set()
    # Nothing is ever an umbrella when unconfigured -> hint always applied (prod).
    assert cfg.is_umbrella_published_project("Published Data", 1) is False
    assert cfg.is_umbrella_published_project("IMPACT", 2) is False


def test_missing_attribute_is_prod_safe():
    """A config that never set the attribute (older path) must not error or opt in."""
    cfg = ChatConfig.__new__(ChatConfig)
    assert cfg.is_umbrella_published_project("Published Data", 1) is False


def test_configured_by_name_matches_case_and_space_insensitive():
    cfg = _cfg("Published Data")
    assert cfg.is_umbrella_published_project("Published Data", 1) is True
    assert cfg.is_umbrella_published_project("published data", None) is True
    assert cfg.is_umbrella_published_project("PUBLISHEDDATA", None) is True
    # A non-umbrella project (the prod-style case) still gets the hint.
    assert cfg.is_umbrella_published_project("IMPACT", 2) is False


def test_configured_by_id_matches():
    cfg = _cfg("1")
    assert cfg.is_umbrella_published_project(None, 1) is True
    assert cfg.is_umbrella_published_project("Published Data", 1) is True
    assert cfg.is_umbrella_published_project("Impact", 2) is False


def test_parse_accepts_mixed_names_and_ids():
    cfg = _cfg("Published Data, 1 , , All Data")
    assert cfg.PUBLISHED_UMBRELLA_PROJECTS == {"publisheddata", "1", "alldata"}

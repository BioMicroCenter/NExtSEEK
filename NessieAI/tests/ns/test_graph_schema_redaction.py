"""The graph-schema op for a caller who is not a superuser: the live schema without counts or ranges.

Spec: ``docs/superpowers/specs/2026-09-18-graph-cypher-scope.md`` sections 8 and 11.3. The op serves
``graph_schema_snapshot``, which reads the catalog through ``graph_catalog.get_snapshot`` and ``get_type_details``
with the request's config, so the redaction there reaches the Container-CC agent with no change to the op. The
vocabulary it carries is read through the caller's project scope (``graph_catalog.get_vocabulary``), so a caller who
is not an admin is shown only titles from its own projects, and a caller who sees no project is shown none.

FREE tests: the catalog reader is stubbed below ``graph_catalog`` (``_make_driver`` and ``_read``), so the op, the
projection, the getters and the renderer all run for real and nothing reaches Neo4j or a model.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_catalog as gc
from chat_nextseek.graph_scope import GraphScope, with_scope
from NessieAI.ns.granular import run_op
from NessieAI.ns.write_gate import build_gate, load_allowlist_from_entries

URI = "bolt://graph-schema-redaction:7687"

ROWS = {
    "META": [{"schema_version": "1.2", "catalog_hash": "hash-op", "synced_at": "2026-01-02T03:04:05Z",
              "has_usage": False}],
    "INDEX": [{"title": "TIS", "label": "T_TIS", "name": "Tissue Sample", "clade": "Source", "sample_count": 72614,
               "deprecated": False, "attributes_with_values": 2}],
    "GUARD": [{"label": "T_TIS", "titles": ["Organ", "Weight"]}],
    "TYPES_ADMIN": [{
        "title": "TIS", "label": "T_TIS", "name": "Tissue Sample", "summary": "A piece of tissue.",
        "clade": "Source", "sample_count": 72614, "curated_parents": None, "curated_children": None,
        "attributes": [
            {"title": "Organ", "value_type": "string", "declared": True, "needs_backticks": False,
             "sample_count": 8093},
            {"title": "Weight", "value_type": "number", "declared": True, "needs_backticks": False,
             "sample_count": 2471, "num_min": 0.125, "num_max": 804.5},
        ],
        "never_filled": 0,
    }],
    "VOCAB_INVESTIGATIONS": [{"title": "Investigation A"}, {"title": "Investigation of another project"}],
    "VOCAB_PROJECTS": [{"title": "Project A"}, {"title": "Another project"}],
    "VOCAB_STUDIES": [],
    "VOCAB_PUBLISHED": [],
    "VOCAB_EDGES": [],
    # the same sources read through the caller's project scope
    "VOCAB_INVESTIGATIONS_SCOPED": [{"title": "Investigation A"}],
    "VOCAB_PROJECTS_SCOPED": [{"title": "Project A"}],
    "VOCAB_STUDIES_SCOPED": [],
    "VOCAB_PUBLISHED_SCOPED": [],
    "VOCAB_EDGES_SCOPED": [],
}
STATEMENTS = {getattr(gc, name): name for name in ROWS if isinstance(getattr(gc, name, None), str)}
FOREIGN_TITLES = ("Investigation of another project", "Another project")
# 6,158 and marker-organ-value were the Organ attribute's example top value. No Attribute node ever carried one
# (no writer), so 889abe89 removed the reader, the renderer and the "values:" column; there is no value left for
# the redaction to strip. The counts and ranges it strips are real.
ADMIN_ONLY_TEXT = ("72,614", "8,093", "2,471", "0.125", "804.5")
COLUMN_TOKENS = ("n=", "range")
LEGEND_PREFIX = "## Resolved sample types:"  # names the columns for every caller (graph_context._assemble)


@pytest.fixture(autouse=True)
def stub_reader(monkeypatch):
    gc.reset_cache()
    monkeypatch.setattr(gc, "_now", lambda: 1000.0)
    monkeypatch.setattr(gc, "_make_driver", lambda config: object())
    monkeypatch.setattr(gc, "_read", lambda driver, database, statement, params=None, *, timeout_s=None: [
        dict(row) for row in ROWS[STATEMENTS[statement]]])
    yield
    gc.reset_cache()


def _base():
    return SimpleNamespace(NEO4J_URI=URI, NEO4J_DATABASE="neo4j", NEO4J_USER="neo4j", NEO4J_PASSWORD="not-a-secret")


def _magicmock():
    config = MagicMock()
    config.NEO4J_URI, config.NEO4J_DATABASE = URI, "neo4j"
    config.NEO4J_USER, config.NEO4J_PASSWORD = "neo4j", "not-a-secret"
    return config


def _schema(config) -> dict:
    out = run_op("graph-schema", {"types": "TIS", "query": "which tissue samples"}, config=config, session=None,
                 write_gate=build_gate(load_allowlist_from_entries([])))
    assert out["source"] == "catalog", out.get("unavailable_reason")
    assert out["resolved_types"] == ["TIS"]
    return out


def test_an_admin_schema_carries_counts_and_ranges():
    # The control: what the non-admin test looks for is really there for an admin.
    text = _schema(with_scope(_base(), GraphScope.admin("test")))["schema"]

    for value in ADMIN_ONLY_TEXT:
        assert value in text, value
    for token in COLUMN_TOKENS:
        assert any(token in line for line in text.splitlines() if not line.startswith(LEGEND_PREFIX)), token


def test_an_admin_schema_carries_every_projects_vocabulary():
    # The control for the vocabulary tests below.
    vocabulary = _schema(with_scope(_base(), GraphScope.admin("test")))["vocabulary"]

    for title in FOREIGN_TITLES + ("Project A", "Investigation A"):
        assert title in vocabulary, title


@pytest.mark.parametrize("make_config", [
    lambda: with_scope(_base(), GraphScope.for_projects([1, 3], source="test")),
    lambda: with_scope(_base(), GraphScope.for_projects([], source="test")),
    lambda: with_scope(_base(), None),
    _base,
    _magicmock,
], ids=["non_admin", "no_projects", "scope_none", "no_scope_attribute", "magicmock_config"])
def test_a_non_admin_schema_has_no_counts_or_ranges(make_config):
    out = _schema(make_config())
    text = out["schema"]

    for line in text.splitlines():
        if line.startswith(LEGEND_PREFIX):
            continue
        for token in COLUMN_TOKENS:
            assert token not in line, (token, line)
    for value in ADMIN_ONLY_TEXT:
        assert value not in text, value
    # the structure, names and types stay, and so does the rest of the answer
    assert 'TIS :T_TIS "Tissue Sample" clade Source, sample count unknown, 2 attributes with values' in text
    assert "- Organ [string]" in text and "- Weight [number]" in text
    assert (out["catalog_hash"], out["schema_version"], out["sample_types"]) == ("hash-op", "1.2", 1)
    for title in FOREIGN_TITLES:
        assert title not in out["vocabulary"], title


def test_a_non_admin_schema_carries_only_its_own_projects_vocabulary():
    out = _schema(with_scope(_base(), GraphScope.for_projects([1, 3], source="test")))

    assert "Project A" in out["vocabulary"] and "Investigation A" in out["vocabulary"]
    for title in FOREIGN_TITLES:
        assert title not in out["vocabulary"], title


@pytest.mark.parametrize("make_config", [
    lambda: with_scope(_base(), GraphScope.for_projects([], source="test")),
    lambda: with_scope(_base(), None),
    _base,
    _magicmock,
], ids=["no_projects", "scope_none", "no_scope_attribute", "magicmock_config"])
def test_a_caller_who_sees_no_project_gets_no_vocabulary(make_config):
    assert _schema(make_config())["vocabulary"] == ""

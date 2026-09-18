"""A prompt variant's ``allowed_procedures`` widen the read-only text check for that variant's turns only.

``cypher_text.ALLOWED_PROCEDURES`` stays exactly ``db.index.fulltext.queryNodes``. A variant's list rides on the
per-request config copy as ``EXTRA_ALLOWED_PROCEDURES`` and ``tool_neo4j_query`` hands it to ``write_clause``;
a turn without the variant still refuses every other procedure. Nothing here opens a driver: a statement that
passes the text check stops at the missing password, which proves it got past the check.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat_nextseek import prompt_variants as pv
from chat_nextseek.cypher_text import ALLOWED_PROCEDURES, write_clause
from chat_nextseek.helpers.tools.neo4j import tool_neo4j_query

APOC = ("MATCH (s:T_TIS {uuid: $uid}) "
        "CALL apoc.path.subgraphNodes(s, {relationshipFilter: 'DERIVED_FROM>', maxLevel: 10}) YIELD node "
        "RETURN count(node) AS n")
EXPAND = "MATCH (s:Sample {uuid: $uid}) CALL apoc.path.expand(s, 'DERIVED_FROM>', null, 1, 10) YIELD path RETURN count(path)"
NO_PASSWORD = "NEO4J_PASSWORD not configured"


def _config(**attrs):
    return SimpleNamespace(NEO4J_PASSWORD=None, NEO4J_URI="bolt://nowhere:7687", NEO4J_USER="neo4j", **attrs)


def test_the_default_allowlist_is_unchanged():
    assert ALLOWED_PROCEDURES == frozenset({"db.index.fulltext.queryNodes"})


def test_by_default_an_apoc_call_is_refused():
    assert write_clause(APOC) == "CALL apoc.path.subgraphNodes"


def test_an_extra_procedure_is_allowed_only_when_passed():
    assert write_clause(APOC, extra_procedures=frozenset({"apoc.path.subgraphNodes"})) is None
    assert write_clause(APOC) == "CALL apoc.path.subgraphNodes"


def test_an_extra_procedure_allows_nothing_else():
    extra = frozenset({"apoc.path.subgraphNodes"})
    assert write_clause(EXPAND, extra_procedures=extra) == "CALL apoc.path.expand"
    assert write_clause("CALL apoc.path.subgraphNodes.x(1) YIELD a RETURN a", extra_procedures=extra) \
        == "CALL apoc.path.subgraphNodes.x"
    assert write_clause("CALL `apoc.path.subgraphNodes`(1) YIELD a RETURN a", extra_procedures=extra) \
        == "CALL with a backticked procedure name"
    assert write_clause(APOC + " WITH 1 AS x MATCH (s) SET s.a = 1", extra_procedures=extra) == "SET"


def test_the_fulltext_procedure_stays_allowed_beside_an_extra_one():
    q = "CALL db.index.fulltext.queryNodes('sample_search', $q) YIELD node RETURN count(node)"
    assert write_clause(q, extra_procedures=frozenset({"apoc.path.expand"})) is None


@pytest.mark.parametrize("junk", [None, "apoc.path.subgraphNodes", MagicMock(), 7, {"apoc.path.subgraphNodes": 1}])
def test_anything_but_a_collection_of_names_widens_nothing(junk):
    """A MagicMock config's attribute, or a bare string, must never read as an allowlist."""
    assert write_clause(APOC, extra_procedures=junk) == "CALL apoc.path.subgraphNodes"


def test_a_turn_without_the_variant_refuses_the_apoc_call_before_any_driver():
    res = tool_neo4j_query(_config(), APOC, {"uid": "TIS-1"})
    assert res["ok"] is False
    assert "Refused: CALL apoc.path.subgraphNodes" in res["error"]


def test_a_mock_config_refuses_the_apoc_call():
    config = MagicMock()
    res = tool_neo4j_query(config, APOC, {"uid": "TIS-1"})
    assert res["ok"] is False and "Refused: CALL apoc.path.subgraphNodes" in res["error"]


def test_the_variant_copy_passes_the_text_check_and_the_singleton_still_refuses(tmp_path):
    variants = tmp_path / "variants"
    (variants / "v2_apoc").mkdir(parents=True)
    (variants / "v2_apoc" / "variant.json").write_text(
        json.dumps({"allowed_procedures": ["apoc.path.subgraphNodes"]}), encoding="utf-8")
    singleton = _config(PROMPTS_DIR=str(tmp_path))

    variant_config = pv.apply_variant(singleton, "v2_apoc", variants_dir=variants)

    assert tool_neo4j_query(variant_config, APOC, {"uid": "TIS-1"})["error"] == NO_PASSWORD
    refused = tool_neo4j_query(singleton, APOC, {"uid": "TIS-1"})
    assert "Refused: CALL apoc.path.subgraphNodes" in refused["error"]
    assert "Refused: CALL apoc.path.expand" in tool_neo4j_query(variant_config, EXPAND, {"uid": "x"})["error"]

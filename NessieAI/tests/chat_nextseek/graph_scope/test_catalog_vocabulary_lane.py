"""
The graph vocabulary on a real Neo4j: a caller who is not an admin is shown only its own projects' titles.

Runs only under lane.sh (a private, throwaway Neo4j holding fixture_graph.py); elsewhere every test here skips.

The vocabulary (investigation, project and study titles, published studies with their DOI and PMID, assay and
protocol titles, assay connections) reaches a caller two ways: the graph agent's catalog context
(``live_catalog_context``) and the ``graph-schema`` op (``graph_schema_snapshot``). For every caller who is not an
admin, both read the real catalog on the fixture graph, and:

- the vocabulary equals what the fixture says the caller may see, computed here from the fixture's own data: a study
  that holds a visible sample, an investigation of the caller's projects or of such a study, the caller's projects,
  and the assay and protocol titles of relationships whose two ends are both visible;
- neither surface carries a marker the caller may not read (the op's ``catalog_hash`` is left out: on a real graph
  it is a digest of type and attribute names, and the fixture marks it as catalog only to prove it is never a value).

An admin is the control: the whole vocabulary, from every project.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 8 and 11.2.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from chat_nextseek import graph_catalog as gc
from chat_nextseek import graph_context
from chat_nextseek.agents.graph import graph_schema_snapshot, live_catalog_context
from chat_nextseek.graph_scope import GraphScope, with_scope

from NessieAI.tests.chat_nextseek.graph_scope import fixture_graph

URI = os.environ.get("GRAPH_SCOPE_NEO4J_URI", "")
PASSWORD = os.environ.get("GRAPH_SCOPE_NEO4J_PASSWORD", "")

pytestmark = pytest.mark.skipif(not (URI and PASSWORD), reason="the graph scope lane runs only under lane.sh")

NON_ADMIN = {name: ids for name, ids in fixture_graph.CALLERS.items() if ids is not None}
TYPES = ["CHM", "MUS", "SLD", "TIS"]
# Opens every keyword-gated block: studies and papers, assays, protocols.
QUESTION = "which study or paper (DOI, PMID), which assay and which protocol made these samples"


def _config(scope: GraphScope):
    base = SimpleNamespace(NEO4J_URI=URI, NEO4J_USER="neo4j", NEO4J_PASSWORD=PASSWORD, NEO4J_DATABASE="neo4j")
    return with_scope(base, scope)


@pytest.fixture(scope="module")
def graph(lane):
    lane.reload()
    gc.reset_cache()
    yield lane
    gc.reset_cache()


# --------------------------------------------------------------------------- #
# The oracle: what each caller may see, from the fixture's data alone
# --------------------------------------------------------------------------- #

def _expected(caller: tuple[int, ...]) -> dict:
    samples = fixture_graph.samples()
    sample_type = {s["key"]: s["props"]["type"] for s in samples}
    visible = {s["key"] for s in samples if set(s["props"].get("project_ids") or ()) & set(caller)}
    studies = [st for st in fixture_graph.STUDIES if visible.intersection(st["samples"])]
    study_investigations = {st["investigation"] for st in studies}
    edges = [(c, p, assay, protocol) for c, p, assay, protocol in fixture_graph.DERIVED_FROM
             if c in visible and p in visible]
    return {
        "investigation_titles": tuple(sorted(
            i["title"] for i in fixture_graph.INVESTIGATIONS
            if i["project_id"] in caller or i["id"] in study_investigations)),
        "project_titles": tuple(sorted(p["title"] for p in fixture_graph.PROJECTS if p["id"] in caller)),
        "study_titles": tuple(sorted(st["title"] for st in studies)),
        "published_studies": sorted((st["title"], st["DOI"], st["PMID"]) for st in studies
                                    if st["DOI"] or st["PMID"]),
        "assay_titles": tuple(sorted({assay for _, _, assay, _ in edges})),
        "protocol_titles": tuple(sorted({protocol for _, _, _, protocol in edges})),
        "assay_connections": sorted({(assay, sample_type[p], sample_type[c]) for c, p, assay, _ in edges}),
    }


def _actual(vocab: gc.Vocabulary) -> dict:
    return {
        "investigation_titles": vocab.investigation_titles,
        "project_titles": vocab.project_titles,
        "study_titles": vocab.study_titles,
        "published_studies": sorted((s["title"], s["doi"], s["pmid"]) for s in vocab.published_studies),
        "assay_titles": vocab.assay_titles,
        "protocol_titles": vocab.protocol_titles,
        "assay_connections": sorted((c["assay"], c["parent_type"], c["child_type"]) for c in vocab.assay_connections),
    }


def test_the_oracle_separates_the_callers():
    # Guards the oracle itself: each caller sees something the others do not, and the empty caller sees nothing.
    one_three, two = set(_expected((1, 3))["study_titles"]), set(_expected((2,))["study_titles"])
    assert one_three - two and two - one_three
    assert all(not value for value in _expected(()).values())


# --------------------------------------------------------------------------- #
# Non-admin callers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("caller", list(NON_ADMIN.values()), ids=list(NON_ADMIN))
def test_the_vocabulary_is_the_callers_own(graph, caller):
    vocab = gc.get_vocabulary(_config(GraphScope.for_projects(caller, source="test")))

    assert _actual(vocab) == _expected(caller)


@pytest.mark.parametrize("caller", list(NON_ADMIN.values()), ids=list(NON_ADMIN))
def test_the_graph_schema_op_carries_no_foreign_value(graph, caller):
    out = graph_schema_snapshot(_config(GraphScope.for_projects(caller, source="test")), types=TYPES,
                                question=QUESTION)
    assert out["source"] == "catalog", out.get("unavailable_reason")

    text = json.dumps({key: value for key, value in out.items() if key != "catalog_hash"}, default=str)
    assert fixture_graph.forbidden_markers(text, caller) == []
    assert out["vocabulary"] == graph_context.render_vocabulary(
        gc.get_vocabulary(_config(GraphScope.for_projects(caller, source="test"))), QUESTION)


@pytest.mark.parametrize("caller", list(NON_ADMIN.values()), ids=list(NON_ADMIN))
def test_the_graph_agent_context_carries_no_foreign_value(graph, caller):
    context = live_catalog_context(_config(GraphScope.for_projects(caller, source="test")), QUESTION,
                                   {"sampletypes": [{"code": code} for code in TYPES]}, {})
    assert context is not None, "the catalog context fell back to the committed schema"

    assert fixture_graph.forbidden_markers(context.schema + "\n" + context.vocabulary, caller) == []


# --------------------------------------------------------------------------- #
# The admin control
# --------------------------------------------------------------------------- #

def test_an_admin_vocabulary_holds_every_project(graph):
    vocab = gc.get_vocabulary(_config(GraphScope.admin("test")))

    assert set(vocab.study_titles) == {st["title"] for st in fixture_graph.STUDIES}
    assert set(vocab.investigation_titles) == {i["title"] for i in fixture_graph.INVESTIGATIONS}
    assert set(vocab.project_titles) == {p["title"] for p in fixture_graph.PROJECTS}
    assert set(vocab.protocol_titles) == {protocol for *_, protocol in fixture_graph.DERIVED_FROM}

"""The assay layer's writer statements (graph schema 1.3) on a real Neo4j, and gate G's family 13 on what they wrote;
and the full sync's lineage step on a doubled DERIVED_FROM, with gate G's check 1 after it.

Runs only under lane.sh (a private, throwaway Neo4j); elsewhere every test here skips. The lane has no MySQL, so the
``graph_sync.sources`` readers the code under test calls answer from this module's MySQL rows, and no graph-write lock
is taken: the writer is called below the entry points that take it. The module wipes the lane's database and loads its
own small graph; the constraints and indexes it creates are dropped and the folder's fixture loaded again when it ends.
Every id and title is synthetic.

The graph: two lineage pairs, 11 -> 10 in SEEK assay 5 and 13 -> 12 in SEEK assay 7, mapped to internal assays 99 and
120, both run in SEEK study 70; 16 -> 10 from a sample graph_sync never wrote, which MySQL no longer holds; and an
Assay 404 that ``internal_assays`` no longer holds, with an INPUT_TO from 14, a sample no rewrite here touches.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

URI = os.environ.get("GRAPH_SCOPE_NEO4J_URI", "")
PASSWORD = os.environ.get("GRAPH_SCOPE_NEO4J_PASSWORD", "")

pytestmark = pytest.mark.skipif(not (URI and PASSWORD), reason="the graph scope lane runs only under lane.sh")

DB = "neo4j"

TYPES = [{"id": 26, "title": "TIS", "uuid": "st-26", "description": "Tissue"},
         {"id": 33, "title": "D.SEQ", "uuid": "st-33", "description": "Sequencing"}]
INTERNAL = [{"id": 99, "title": "Patient Visit"}, {"id": 120, "title": "Short Read Sequencing"}]
PAIRS = [(5, 99), (7, 120)]
SEEK_STUDIES = [(5, 70), (7, 70)]
STUDIES = [{"id": 70, "title": "Study seventy", "description": None, "investigation_id": 3}]
CONTEXT = [{"id": 1, "internal_assay_id": 99, "assay_name": "Patient Visit", "alternative_assay_names": None,
            "description": "A visit.", "tags": None, "parent_clade_type": None, "child_clade_type": None,
            "required_parent_sample_types": "TIS", "optional_parent_sample_types": None,
            "children_sample_types": "D.SEQ"},
           {"id": 2, "internal_assay_id": 120, "assay_name": "Short Read Sequencing",
            "alternative_assay_names": "Illumina Sequencing", "description": "Reads.", "tags": None,
            "parent_clade_type": None, "child_clade_type": None, "required_parent_sample_types": "TIS",
            "optional_parent_sample_types": None, "children_sample_types": "D.SEQ"}]
MEMBERS = {10: [5], 11: [5], 12: [7], 13: [7], 16: [5]}
SYNCED = {10: 26, 11: 33, 12: 26, 13: 33}          # Sample nodes graph_sync wrote, to their type
NEVER_SYNCED = 16
LINEAGE = [(11, 10), (13, 12)]                       # what MySQL declares
GRAPH_LINEAGE = LINEAGE + [(NEVER_SYNCED, 10)]
GONE_ASSAY = 404
STALE = 14                                           # holds the gone Assay's edge, and no lineage


def _show(lane, what: str) -> set[str]:
    return {r["name"] for r in lane.read(f"SHOW {what} YIELD name RETURN name")}


@pytest.fixture(scope="module")
def assay_lane(lane):
    """The lane, with this module's constraints and indexes dropped and the folder's fixture back when it ends."""
    constraints, indexes = _show(lane, "CONSTRAINTS"), _show(lane, "INDEXES")
    yield lane
    for name in sorted(_show(lane, "CONSTRAINTS") - constraints):
        lane.write(f"DROP CONSTRAINT `{name}` IF EXISTS")
    for name in sorted(_show(lane, "INDEXES") - indexes):
        lane.write(f"DROP INDEX `{name}` IF EXISTS")
    lane.reload()


@pytest.fixture
def graph(assay_lane, monkeypatch):
    """This module's graph in the lane's Neo4j, and MySQL's side faked in ``sources``."""
    from nextseek_api.graph_sync import sources
    from nextseek_graph import schema

    def load(tx):
        tx.run("MATCH (n) DETACH DELETE n").consume()
        tx.run("CREATE (:GraphMeta {schema_version: $v, catalog_hash: 'lane'})", v=schema.SCHEMA_VERSION).consume()
        tx.run("CREATE (:Investigation {id: 3, title: 'Lane Investigation'})").consume()
        for t in TYPES:
            tx.run("CREATE (:SampleType {id: $id, title: $title})", id=t["id"], title=t["title"]).consume()
        for sid, type_id in SYNCED.items():
            tx.run("MATCH (t:SampleType {id: $type}) "
                   "CREATE (s:Sample {id: $id, uuid: $uuid, synced_at: datetime()})-[:OF_TYPE]->(t)",
                   id=sid, uuid=f"TIS-000000LNE-{sid}", type=type_id).consume()
        tx.run("CREATE (:Sample {id: $id, uuid: $uuid})", id=NEVER_SYNCED,
               uuid=f"TIS-000000LNE-{NEVER_SYNCED}").consume()
        for child, parent in GRAPH_LINEAGE:
            tx.run("MATCH (c:Sample {id: $c}), (p:Sample {id: $p}) CREATE (c)-[:DERIVED_FROM]->(p)",
                   c=child, p=parent).consume()
        tx.run("MATCH (t:SampleType {id: 26}) CREATE (t)<-[:OF_TYPE]-(s:Sample {id: $sid, uuid: $uuid, "
               "synced_at: datetime()})-[:INPUT_TO {seek_assay_ids: [7]}]->(:Assay {id: $id, title: 'Gone'})",
               sid=STALE, uuid=f"TIS-000000LNE-{STALE}", id=GONE_ASSAY).consume()

    with assay_lane.driver.session() as session:
        session.execute_write(load)
    monkeypatch.setattr(sources, "sample_types", lambda: [dict(t) for t in TYPES])
    monkeypatch.setattr(sources, "internal_assays", lambda: [dict(r) for r in INTERNAL])
    monkeypatch.setattr(sources, "assay_internal_pairs", lambda: list(PAIRS))
    monkeypatch.setattr(sources, "assay_studies", lambda: list(SEEK_STUDIES))
    monkeypatch.setattr(sources, "assay_context_rows", lambda: [dict(r) for r in CONTEXT])
    monkeypatch.setattr(sources, "studies", lambda: [dict(s) for s in STUDIES])
    monkeypatch.setattr(sources, "sample_assay_ids_for",
                        lambda ids: {i: list(MEMBERS[i]) for i in sorted(set(ids)) if i in MEMBERS})
    return assay_lane


def _edges(lane) -> dict[int, set]:
    rows = lane.read("MATCH (s)-[r:INPUT_TO|OUTPUT_OF]->(a:Assay) "
                     "RETURN s.id AS id, type(r) AS type, a.id AS assay, r.seek_assay_ids AS runs")
    out: dict[int, set] = {}
    for r in rows:
        out.setdefault(r["id"], set()).add((r["type"], r["assay"], tuple(r["runs"])))
    return out


def _rows_for(driver, st, ids):
    """The sample edge rows the role rule gives ``ids`` from the graph's lineage, as ``targeted`` builds them."""
    from nextseek_api.graph_sync import assays as assay_rules
    from nextseek_api.graph_sync import sources, writer

    pairs = writer.lineage_pairs_incident(driver, DB, ids)
    by_sample = sources.sample_assay_ids_for({v for pair in pairs for v in pair} | set(ids))
    roles = assay_rules.roles_for_pairs(pairs, by_sample, st.internal_by_seek)
    return assay_rules.sample_edge_rows({i: roles.get(i, {}) for i in ids})


def _gate(lane, st) -> dict:
    from nextseek_api.graph_sync import run, verify

    lineage = {run.encode_pair(child, parent) for child, parent in LINEAGE}
    checks: list = []
    verify._check_assays(lane.driver, DB, st, SimpleNamespace(lineage=lineage), verify._endpoint_assays(lineage),
                         {sid: {} for sid in SYNCED}, checks, {})
    return {c["name"]: c for c in checks}


def test_the_writer_builds_the_assay_layer_and_gate_g_reads_it(graph, tmp_path):
    from nextseek_api.graph_sync import run, writer

    constraints = writer.ensure_constraints_v11(graph.driver, DB)
    assert constraints["schema_statements"] > 0
    assert "assay_id_unique" in _show(graph, "CONSTRAINTS") and "assay_title" in _show(graph, "INDEXES")

    st = run.read_assays()
    assert writer.write_assays(graph.driver, DB, st.catalog.nodes) == {"assays_written": 2}
    assert {r["id"]: r["title"] for r in graph.read("MATCH (a:Assay) RETURN a.id AS id, a.title AS title")} == {
        99: "Patient Visit", 120: "Short Read Sequencing", GONE_ASSAY: "Gone"}

    catalog = writer.replace_assay_catalog_edges(graph.driver, DB, st.catalog.accepted_by, st.catalog.generates)
    assert catalog == {"accepted_by": 2, "accepted_by_written": 2, "generates": 2, "generates_written": 2}
    assert writer.replace_assay_runs(graph.driver, DB, st.runs, st.studies) == {
        "assay_runs": 2, "assay_runs_written": 2, "assay_runs_dropped": 0}
    assert sorted((r["a"], r["st"], r["runs"]) for r in graph.read(
        "MATCH (a:Assay)-[r:RUN_IN]->(st:Study) RETURN a.id AS a, st.seek_study_id AS st, r.seek_assay_ids AS runs")
    ) == [(99, 70, [5]), (120, 70, [7])]

    rows = _rows_for(graph.driver, st, sorted(SYNCED) + [NEVER_SYNCED])
    by_id = {row["id"]: row for row in rows}
    by_id[13]["outputs"].append({"assay_id": 555, "seek_assay_ids": [7]})     # an Assay the graph does not hold
    rows.append({"id": 999, "inputs": [{"assay_id": 99, "seek_assay_ids": [5]}], "outputs": []})   # no Sample
    assert writer.replace_sample_assay_edges(graph.driver, DB, rows) == {
        "assay_edge_samples": 6, "assay_edge_samples_missing": 1, "assay_edges_written": 5, "assay_edges_dropped": 1}
    assert _edges(graph) == {10: {("INPUT_TO", 99, (5,))}, 11: {("OUTPUT_OF", 99, (5,))},
                             12: {("INPUT_TO", 120, (7,))}, 13: {("OUTPUT_OF", 120, (7,))},
                             NEVER_SYNCED: {("OUTPUT_OF", 99, (5,))}, STALE: {("INPUT_TO", GONE_ASSAY, (7,))}}

    # MySQL no longer holds 16, which graph_sync never wrote: ORPHAN_SWAP keeps its lineage, drops its assay edges.
    retired = writer.retire_samples(graph.driver, DB, [NEVER_SYNCED], str(tmp_path / "retired.tsv"))
    assert (retired["retired_orphaned"], retired["retired_deleted"]) == (1, 0)
    assert graph.read("MATCH (o:OrphanSample {id: $id})-[:DERIVED_FROM]->(p:Sample) RETURN p.id AS p, "
                      "COUNT { (o)-[:INPUT_TO|OUTPUT_OF]->() } AS assay_edges", {"id": NEVER_SYNCED}) == [
        {"p": 10, "assay_edges": 0}]
    assert writer.lineage_pairs_incident(graph.driver, DB, [10]) == [(11, 10)]
    assert writer.replace_sample_assay_edges(graph.driver, DB, _rows_for(graph.driver, st, [10]))[
        "assay_edges_written"] == 1

    # Before the gone Assay is deleted, gate G sees it, and the edge it still holds.
    before = _gate(graph, st)
    assert not before["13.assays.ids"]["pass"]
    assert not before["13.assays.sample_edge_count"]["pass"]

    assert writer.delete_gone_assays(graph.driver, DB, st.ids) == {
        "assays_deleted": 1, "assay_edges_deleted_with_gone_assays": 1}
    assert graph.read("MATCH (a:Assay {id: $id}) RETURN count(a) AS n", {"id": GONE_ASSAY}) == [{"n": 0}]
    assert _edges(graph) == {10: {("INPUT_TO", 99, (5,))}, 11: {("OUTPUT_OF", 99, (5,))},
                             12: {("INPUT_TO", 120, (7,))}, 13: {("OUTPUT_OF", 120, (7,))}}

    after = _gate(graph, st)
    assert sorted(after) == ["13.assays.catalog_edges", "13.assays.ids", "13.assays.run_in",
                             "13.assays.sample_edge_count", "13.assays.sampled_sample_edges"]
    assert [name for name, check in after.items() if not check["pass"]] == []
    assert after["13.assays.sample_edge_count"]["actual"] == 4


def test_the_full_syncs_lineage_step_keeps_one_edge_of_a_doubled_declared_pair(graph, tmp_path):
    """A second DERIVED_FROM for one (child, parent) pair fails gate G check 1. The full sync's lineage step archives
    and deletes every edge of a declared pair after the first, and the check then passes."""
    from nextseek_api.graph_sync import run, verify, writer

    graph.write("MATCH (c:Sample {id: 11}), (p:Sample {id: 10}) CREATE (c)-[:DERIVED_FROM {copy: 2}]->(p)")
    codes = sorted(run.encode_pair(child, parent) for child, parent in LINEAGE)
    mysql = SimpleNamespace(lineage=set(codes))
    before: list = []
    verify._check_lineage(graph.driver, DB, mysql, before, {})
    assert {c["name"]: c["actual"] for c in before}["1.lineage.duplicate_edges"] == 1

    counts = writer.archive_and_drop_undeclared_derived_from(graph.driver, DB, str(tmp_path / "archive.tsv"),
                                                             run.DeclaredIdPairs(codes))
    assert (counts["derived_from_doubled"], counts["derived_from_undeclared"], counts["derived_from_deleted"]) == (
        1, 1, 2)                                                     # the undeclared one is 16 -> 10
    assert graph.read("MATCH (:Sample {id: 11})-[e:DERIVED_FROM]->(:Sample {id: 10}) RETURN count(e) AS n") == [
        {"n": 1}]
    after: list = []
    verify._check_lineage(graph.driver, DB, mysql, after, {})
    assert [c["name"] for c in after if not c["pass"]] == []

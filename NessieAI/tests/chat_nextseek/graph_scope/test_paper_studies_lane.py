"""The studies tool's graph statements on a real Neo4j (nextseek_api/graph_sync/paper_studies.py).

Runs only under lane.sh, selected with -k paper_studies; elsewhere every test here skips. Each test wipes the lane's
private database and loads the synthetic fixture below; the fixture's teardown reloads the folder's own fixture, so
the other modules find their graph as they expect. The GraphMeta version is the contract's, never a literal.
"""
from __future__ import annotations

import json
import os

import pytest

URI = os.environ.get("GRAPH_SCOPE_NEO4J_URI", "")
PASSWORD = os.environ.get("GRAPH_SCOPE_NEO4J_PASSWORD", "")
pytestmark = pytest.mark.skipif(not (URI and PASSWORD), reason="the graph scope lane runs only under lane.sh")

DB = "neo4j"
PAPER = 9001
FIXTURE = """
CREATE (:GraphMeta {schema_version: $version})
CREATE (inv:Investigation {id: 501, title: 'Lane Investigation'})
CREATE (paper:Study {id: $paper, title: 'Lane Paper', DOI: '10.0000/lane.1', description: 'A synthetic paper'})
CREATE (paper)-[:IN_INVESTIGATION]->(inv)
CREATE (seek:Study {seek_study_id: 801, title: 'Lane SEEK Study'})-[:IN_INVESTIGATION]->(inv)
CREATE (empty:Study {seek_study_id: 802, title: 'Lane Empty SEEK Study'})-[:IN_INVESTIGATION]->(inv)
WITH paper, seek
UNWIND [1, 2, 3, 4] AS n
CREATE (s:Sample {id: 70000 + n, uuid: 'TIS-260101LNE-' + toString(n)})-[:IN_STUDY]->(paper)
WITH s, seek WHERE s.id = 70001
CREATE (s)-[:IN_STUDY]->(seek)
"""


def _count(lane, cypher, **params) -> int:
    return lane.read(cypher, params)[0]["n"]


@pytest.fixture
def fresh(lane):
    from nextseek_graph import schema

    with lane.driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n").consume()
        session.run(FIXTURE, version=schema.SCHEMA_VERSION, paper=PAPER).consume()
    yield lane
    lane.reload()


def test_paper_studies_retire_delete_and_restore(fresh, tmp_path):
    from nextseek_api.graph_sync import paper_studies

    driver = fresh.driver
    links = "MATCH (:Sample)-[e:IN_STUDY]->(:Study {id: $paper}) RETURN count(e) AS n"
    archive = tmp_path / paper_studies.IN_STUDY_REMOVED_FILE
    nodes = tmp_path / paper_studies.STUDY_NODES_REMOVED_FILE

    first = paper_studies.retire_paper_links(driver, DB, PAPER, [70001, 70002, 79999], archive)
    assert first == {"paper_links_found": 2, "paper_links_retired": 2}
    assert _count(fresh, links, paper=PAPER) == 2
    assert _count(fresh, "MATCH (:Sample {id: 70001})-[e:IN_STUDY]->(:Study {seek_study_id: 801}) "
                         "RETURN count(e) AS n") == 1
    assert paper_studies.delete_empty_paper_study_nodes(driver, DB, [PAPER],
                                                        archive_path=nodes)["study_nodes_deleted"] == 0

    paper_studies.retire_paper_links(driver, DB, PAPER, [70003, 70004], archive)
    gone = paper_studies.delete_empty_paper_study_nodes(driver, DB, [PAPER], archive_path=nodes)
    assert gone == {"study_nodes_empty": 1, "study_nodes_deleted": 1}
    assert _count(fresh, "MATCH (st:Study {id: $paper}) RETURN count(st) AS n", paper=PAPER) == 0
    assert _count(fresh, "MATCH (i:Investigation {id: 501}) RETURN count(i) AS n") == 1
    assert len(archive.read_text().splitlines()) == 5
    assert json.loads(nodes.read_text().splitlines()[0])["props"]["DOI"] == "10.0000/lane.1"

    restored = paper_studies.restore_paper_links(driver, DB, tmp_path)
    assert restored == {"study_nodes_restored": 1, "paper_links_restored": 4}
    assert _count(fresh, links, paper=PAPER) == 4
    assert fresh.read("MATCH (st:Study {id: $paper})-[:IN_INVESTIGATION]->(i) RETURN st.DOI AS doi, i.id AS inv",
                      {"paper": PAPER}) == [{"doi": "10.0000/lane.1", "inv": 501}]
    assert paper_studies.restore_paper_links(driver, DB, tmp_path) == {"study_nodes_restored": 0,
                                                                      "paper_links_restored": 0}


def test_paper_studies_never_delete_a_seek_keyed_node_even_an_empty_one(fresh, tmp_path):
    from nextseek_api.graph_sync import paper_studies

    report = paper_studies.delete_empty_paper_study_nodes(fresh.driver, DB, [801, 802],
                                                          archive_path=tmp_path / "nodes.jsonl")
    assert report == {"study_nodes_empty": 0, "study_nodes_deleted": 0}
    assert _count(fresh, "MATCH (st:Study) WHERE st.seek_study_id IN [801, 802] RETURN count(st) AS n") == 2


def test_paper_studies_retire_never_touches_a_seek_keyed_study(fresh, tmp_path):
    from nextseek_api.graph_sync import paper_studies

    report = paper_studies.retire_paper_links(fresh.driver, DB, 801, [70001], tmp_path / "x.tsv")
    assert report == {"paper_links_found": 0, "paper_links_retired": 0}
    assert _count(fresh, "MATCH (:Sample {id: 70001})-[e:IN_STUDY]->() RETURN count(e) AS n") == 2

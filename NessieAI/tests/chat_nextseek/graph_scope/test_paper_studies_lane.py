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
CREATE (seek:Study {id: 801, seek_study_id: 801, title: 'Lane SEEK Study'})-[:IN_INVESTIGATION]->(inv)
CREATE (empty:Study {id: 802, seek_study_id: 802, title: 'Lane Empty SEEK Study'})-[:IN_INVESTIGATION]->(inv)
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


def test_paper_studies_lane_each_delete_and_the_restore_recheck_that_the_study_is_not_seeks(fresh, tmp_path):
    """A SEEK study's node carries an id beside its seek_study_id (a rekey keeps both). Each delete checks again at
    delete time that its Study has no seek_study_id, so an edge or a node that became a SEEK study's after the read
    stays; and a restore never links a sample to a SEEK study's node."""
    from nextseek_api.graph_sync import cypher as q
    from nextseek_api.graph_sync import paper_studies, writer

    driver = fresh.driver
    [edge] = fresh.read("MATCH (:Sample {id: 70001})-[e:IN_STUDY]->(:Study {id: 801}) RETURN elementId(e) AS e")
    [node] = fresh.read("MATCH (st:Study {id: 802}) RETURN elementId(st) AS e")

    def deleted(query, **params):
        return driver.execute_query(query, params, database_=DB).records[0]["deleted"]

    assert deleted(q.DELETE_PAPER_IN_STUDY, paper_id=801, element_ids=[edge["e"]]) == 0
    assert deleted(q.DELETE_EMPTY_PAPER_STUDY_NODES, element_ids=[node["e"]]) == 0
    assert _count(fresh, "MATCH (:Sample {id: 70001})-[e:IN_STUDY]->(:Study {id: 801}) RETURN count(e) AS n") == 1
    assert _count(fresh, "MATCH (st:Study {id: 802}) RETURN count(st) AS n") == 1
    (tmp_path / paper_studies.IN_STUDY_REMOVED_FILE).write_text(
        writer.IN_STUDY_ARCHIVE_HEADER + "70002\t\t802\tx\tstudies_tool_paper\n", encoding="utf-8")
    assert paper_studies.restore_paper_links(driver, DB, tmp_path)["paper_links_restored"] == 0
    assert _count(fresh, "MATCH (:Sample {id: 70002})-[e:IN_STUDY]->(:Study {id: 802}) RETURN count(e) AS n") == 0


def test_paper_studies_lane_a_rerun_after_a_stop_restores_each_link_and_node_once(fresh, tmp_path, monkeypatch):
    """The step stopped between each archive and its delete and was run again, so both archives hold their rows
    twice. Under the unique Study id constraint the live graph carries, the restore makes one paper node and one
    link a sample."""
    from nextseek_api.graph_sync import cypher as q
    from nextseek_api.graph_sync import paper_studies

    real, stopped = paper_studies._run, []

    def stop_once(driver, db, query, params=None, **kwargs):
        if query in (q.DELETE_PAPER_IN_STUDY, q.DELETE_EMPTY_PAPER_STUDY_NODES) and query not in stopped:
            stopped.append(query)
            raise RuntimeError("stopped between the archive and the delete")
        return real(driver, db, query, params, **kwargs)

    monkeypatch.setattr(paper_studies, "_run", stop_once)
    driver, samples = fresh.driver, [70001, 70002, 70003, 70004]
    archive = tmp_path / paper_studies.IN_STUDY_REMOVED_FILE
    nodes = tmp_path / paper_studies.STUDY_NODES_REMOVED_FILE
    fresh.write("CREATE CONSTRAINT study_id_unique IF NOT EXISTS FOR (st:Study) REQUIRE st.id IS UNIQUE")
    try:
        with pytest.raises(RuntimeError):
            paper_studies.retire_paper_links(driver, DB, PAPER, samples, archive)
        assert paper_studies.retire_paper_links(driver, DB, PAPER, samples, archive)["paper_links_retired"] == 4
        with pytest.raises(RuntimeError):
            paper_studies.delete_empty_paper_study_nodes(driver, DB, [PAPER], archive_path=nodes)
        assert paper_studies.delete_empty_paper_study_nodes(driver, DB, [PAPER],
                                                            archive_path=nodes)["study_nodes_deleted"] == 1
        assert (len(archive.read_text().splitlines()), len(nodes.read_text().splitlines())) == (1 + 2 * 4, 2)

        restored = paper_studies.restore_paper_links(driver, DB, tmp_path)
        assert restored == {"study_nodes_restored": 1, "paper_links_restored": 4}
        assert _count(fresh, "MATCH (st:Study {id: $paper}) RETURN count(st) AS n", paper=PAPER) == 1
        assert fresh.read("MATCH (s:Sample)-[e:IN_STUDY]->(:Study {id: $paper}) RETURN s.id AS id, count(e) AS n "
                          "ORDER BY id", {"paper": PAPER}) == [{"id": i, "n": 1} for i in samples]
    finally:
        fresh.write("DROP CONSTRAINT study_id_unique IF EXISTS")


# --- the share mode: a shared paper sample's drain (tool spec 16.8) -----------------------------------------------

SHARE_FIXTURE = """
CREATE (:GraphMeta {schema_version: $version, catalog_hash: 'lane'})
CREATE (:SampleType {id: 26, title: 'TIS'})
CREATE (a:Investigation {id: 101, title: 'Alder Investigation'})
CREATE (b:Investigation {id: 102, title: 'Birch Investigation'})
CREATE (:Study {seek_study_id: 1, title: 'Alder Unpublished'})-[:IN_INVESTIGATION]->(a)
CREATE (:Study {seek_study_id: 3, title: 'Birch Study'})-[:IN_INVESTIGATION]->(b)
CREATE (p:Study {id: 9, title: 'A paper', DOI: '10.0000/lane.9'})-[:IN_INVESTIGATION]->(a)
CREATE (:Sample {id: 1003, uuid: 'TIS-260101LNE-1003'})-[:IN_STUDY]->(p)
"""
SHARE_TYPES = [{"id": 26, "title": "TIS", "uuid": "st-26", "description": "Tissue"}]
SHARE_STUDIES = [{"id": 1, "title": "Alder Unpublished", "description": None, "investigation_id": 101},
                 {"id": 3, "title": "Birch Study", "description": None, "investigation_id": 102}]


def test_paper_studies_lane_a_shared_paper_sample_links_the_destination_study(lane, monkeypatch, tmp_path):
    """After a share, sample 1003 (on graph-only paper 9 of investigation 101) is a member of study 1's assay 11 and
    of study 3's assay 31, and holds projects 3 and 5. One by-id sync with the switch on writes project 5 (property and
    IN_PROJECT) and the IN_STUDY to study 3; study 1, in the paper's own investigation, stays withheld."""
    from nextseek_api.graph_sync import sources, study_links, targeted, writer
    from nextseek_graph import schema

    with lane.driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n").consume()
        session.run(SHARE_FIXTURE, version=schema.SCHEMA_VERSION).consume()
    row = {"id": 1003, "uuid": "TIS-260101LNE-1003", "title": "s1003", "sample_type_id": 26,
           "json_metadata": json.dumps({"UID": "TIS-260101LNE-1003"})}
    links = [{"sample_id": 1003, "study_id": s["id"], "study_title": s["title"], "study_description": None,
              "investigation_id": s["investigation_id"]} for s in SHARE_STUDIES]
    patches = {
        "samples_by_ids": lambda ids: [dict(row)] if 1003 in set(ids) else [],
        "sample_projects_for": lambda ids: {1003: [3, 5]} if 1003 in set(ids) else {},
        "sample_assay_ids_for": lambda ids: {1003: [11, 31]} if 1003 in set(ids) else {},
        "uuid_to_ids_for": lambda tokens: {}, "parent_identities": lambda uuids: {},
        "seek_study_links_for": lambda ids: [dict(link) for link in links if link["sample_id"] in set(ids)],
        "resolved_assay_map": lambda: {11: (900, "RNA-seq"), 31: (900, "RNA-seq")}, "sops_map": lambda: {},
        "sample_types": lambda: [dict(t) for t in SHARE_TYPES], "sample_attributes": lambda: [],
        "sample_attribute_types": lambda: {}, "type_context": lambda: {}, "type_clades": lambda: {},
        "deprecated_titles": lambda: set(), "attribute_meanings": lambda: {},
        "projects": lambda: [{"id": 3, "title": "Alder"}, {"id": 5, "title": "Birch"}],
        "investigations": lambda: [{"id": 101, "title": "Alder Investigation", "description": None},
                                   {"id": 102, "title": "Birch Investigation", "description": None}],
        "investigation_projects": lambda: [{"investigation_id": 101, "project_id": 3},
                                           {"investigation_id": 102, "project_id": 5}],
        "memberships": lambda: [], "studies": lambda: [dict(s) for s in SHARE_STUDIES],
    }
    for name, fn in patches.items():
        monkeypatch.setattr(sources, name, fn)
    monkeypatch.setenv(study_links.SWITCH_ENV, "follow")
    try:
        result = targeted.sync_samples(lane.driver, DB, [1003], run_dir=str(tmp_path))
        assert result["status"] == targeted.OK, result
        check = writer.share_graph_check(lane.driver, DB, [1003], project_id=5, study_id=3)
        assert {k: check[k] for k in ("found", "has_project", "in_project", "in_study", "paper",
                                      "paper_in_study")} == {"found": 1, "has_project": 1, "in_project": 1,
                                                             "in_study": 1, "paper": 1, "paper_in_study": 1}
        assert _count(lane, "MATCH (:Sample {id: 1003})-[e:IN_STUDY]->(:Study {seek_study_id: 1}) "
                            "RETURN count(e) AS n") == 0
    finally:
        lane.reload()

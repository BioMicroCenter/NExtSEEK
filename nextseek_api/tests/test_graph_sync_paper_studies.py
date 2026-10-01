"""The studies tool's graph functions (nextseek_api/graph_sync/paper_studies.py) and targeted.preview_labels.

No database and no Neo4j: ``PaperGraph`` does what each new statement does to a small in-memory graph of Study nodes
and IN_STUDY edges, and checks that every delete arrives after its archive line is on disk. The statements
themselves are proven on a real Neo4j by NessieAI/tests/chat_nextseek/graph_scope/test_paper_studies_lane.py.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from neo4j import RoutingControl

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import labels, paper_studies, sources, study_links, targeted, writer

DB = "neo4j"


class PaperGraph:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.nodes = {"n90": {"props": {"id": 90, "title": "Paper One", "DOI": "10.0000/one"}, "invs": [7]},
                      "n91": {"props": {"id": 91, "title": "Paper Two", "DOI": "10.0000/two"}, "invs": [7]},
                      "n500": {"props": {"seek_study_id": 500, "title": "Paper One"}, "invs": [7]},
                      "n501": {"props": {"seek_study_id": 501, "title": "Empty"}, "invs": [7]}}
        self.edges = {"e1": (1, "n90"), "e2": (2, "n90"), "e3": (3, "n90"), "e4": (1, "n500"), "e5": (4, "n91")}
        self.calls = []
        self.handlers = {
            q.PAPER_IN_STUDY_OF: self._in_study_of, q.DELETE_PAPER_IN_STUDY: self._delete_edges,
            q.EMPTY_PAPER_STUDY_NODES: self._empty, q.DELETE_EMPTY_PAPER_STUDY_NODES: self._delete_nodes,
            q.RESTORE_PAPER_STUDY_NODES: self._restore_nodes, q.RESTORE_PAPER_IN_STUDY: self._restore_edges,
        }

    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        read = kwargs.get("routing_") == RoutingControl.READ
        self.calls.append((query, read))
        return SimpleNamespace(records=self.handlers[query](parameters_ or {}))

    def _paper(self, eid, paper_id):
        props = self.nodes[eid]["props"]
        return props.get("id") == paper_id and props.get("seek_study_id") is None

    def _in_study_of(self, p):
        return [{"sample_id": s, "element_id": e} for e, (s, n) in sorted(self.edges.items())
                if s in p["ids"] and self._paper(n, p["paper_id"])]

    def _delete_edges(self, p):
        archived = (self.tmp / paper_studies.IN_STUDY_REMOVED_FILE).read_text()
        gone = 0
        for eid in p["element_ids"]:
            assert f"\t{eid}\t" in archived, "a delete arrived before its archive line"
            if eid in self.edges and self._paper(self.edges[eid][1], p["paper_id"]):
                del self.edges[eid]
                gone += 1
        return [{"deleted": gone}]

    def _holds(self, eid):
        return any(n == eid for _s, n in self.edges.values())

    def _empty(self, p):
        out = []
        for eid, node in sorted(self.nodes.items()):
            props = node["props"]
            if props.get("id") in p["ids"] and props.get("seek_study_id") is None and not self._holds(eid):
                out.append({"study_id": props.get("id"), "seek_study_id": props.get("seek_study_id"),
                            "element_id": eid, "props": dict(props), "investigation_ids": list(node["invs"])})
        return out

    def _delete_nodes(self, p):
        archived = (self.tmp / paper_studies.STUDY_NODES_REMOVED_FILE).read_text()
        gone = 0
        for eid in p["element_ids"]:
            assert f'"{eid}"' in archived, "a node delete arrived before its archive line"
            if eid in self.nodes and not self._holds(eid):
                del self.nodes[eid]
                gone += 1
        return [{"deleted": gone}]

    def _restore_nodes(self, p):
        made = 0
        for row in p["rows"]:
            if not any(n["props"].get("id") == row["study_id"] for n in self.nodes.values()):
                self.nodes[f"r{row['study_id']}"] = {"props": dict(row["props"]),
                                                    "invs": list(row["investigation_ids"])}
                made += 1
        return [{"restored": made}]

    def _restore_edges(self, p):
        made = 0
        for row in p["rows"]:
            node = next((e for e, n in self.nodes.items() if n["props"].get("id") == row["study_id"]
                         and n["props"].get("seek_study_id") is None), None)
            if node and (row["sample_id"], node) not in self.edges.values():
                self.edges[f"x{len(self.edges)}"] = (row["sample_id"], node)
                made += 1
        return [{"restored": made}]


@pytest.fixture
def graph(tmp_path):
    return PaperGraph(tmp_path)


def test_retire_archives_then_deletes_only_the_papers_links_of_the_listed_samples(graph, tmp_path):
    archive = tmp_path / paper_studies.IN_STUDY_REMOVED_FILE
    report = paper_studies.retire_paper_links(graph, DB, 90, [1, 2, 4], archive)
    assert report == {"paper_links_found": 2, "paper_links_retired": 2}
    assert set(graph.edges) == {"e3", "e4", "e5"}
    lines = archive.read_text().splitlines()
    assert lines[0] == writer.IN_STUDY_ARCHIVE_HEADER.rstrip("\n")   # the studies release's archive, one header
    assert lines[1:] == ["1\t\t90\te1\tstudies_tool_paper", "2\t\t90\te2\tstudies_tool_paper"]


def test_retire_of_nothing_writes_no_archive(graph, tmp_path):
    assert paper_studies.retire_paper_links(graph, DB, 90, [], tmp_path / "x.tsv")["paper_links_retired"] == 0
    assert not (tmp_path / "x.tsv").exists()


def test_an_empty_paper_node_is_archived_then_deleted_one_holding_links_is_kept(graph, tmp_path):
    archive = tmp_path / paper_studies.STUDY_NODES_REMOVED_FILE
    assert paper_studies.delete_empty_paper_study_nodes(graph, DB, [90], archive_path=archive)[
        "study_nodes_deleted"] == 0
    paper_studies.retire_paper_links(graph, DB, 90, [1, 2, 3], tmp_path / paper_studies.IN_STUDY_REMOVED_FILE)
    report = paper_studies.delete_empty_paper_study_nodes(graph, DB, [90], archive_path=archive)
    assert report == {"study_nodes_empty": 1, "study_nodes_deleted": 1}
    assert "n90" not in graph.nodes
    [line] = archive.read_text().splitlines()
    assert json.loads(line)["props"]["DOI"] == "10.0000/one"


def test_a_seek_keyed_node_is_never_deleted_even_when_empty(graph, tmp_path):
    """Study nodes of SEEK studies are not deleted yet (operator ruling of 2026-09-30): a node keyed only by
    seek_study_id never matches, whatever ids are named."""
    report = paper_studies.delete_empty_paper_study_nodes(graph, DB, [500, 501],
                                                    archive_path=tmp_path / paper_studies.STUDY_NODES_REMOVED_FILE)
    assert report == {"study_nodes_empty": 0, "study_nodes_deleted": 0}
    assert {"n500", "n501"} <= set(graph.nodes)


def test_restore_brings_back_the_node_and_the_links_and_is_idempotent(graph, tmp_path):
    paper_studies.retire_paper_links(graph, DB, 90, [1, 2, 3], tmp_path / paper_studies.IN_STUDY_REMOVED_FILE)
    paper_studies.delete_empty_paper_study_nodes(graph, DB, [90],
                                           archive_path=tmp_path / paper_studies.STUDY_NODES_REMOVED_FILE)
    first = paper_studies.restore_paper_links(graph, DB, tmp_path)
    assert first == {"study_nodes_restored": 1, "paper_links_restored": 3}
    again = paper_studies.restore_paper_links(graph, DB, tmp_path)
    assert again == {"study_nodes_restored": 0, "paper_links_restored": 0}


def test_restore_ignores_rows_another_path_removed(graph, tmp_path):
    (tmp_path / paper_studies.IN_STUDY_REMOVED_FILE).write_text(
        writer.IN_STUDY_ARCHIVE_HEADER + "4\t500\t\te9\tby_id_sync\n")
    assert paper_studies.restore_paper_links(graph, DB, tmp_path)["paper_links_restored"] == 0


def test_the_archive_is_the_studies_releases_file():
    assert paper_studies.IN_STUDY_REMOVED_FILE == study_links.ARCHIVE_FILE


def test_the_statements_live_in_cypher_py_and_paper_studies_holds_none():
    import inspect

    source = inspect.getsource(paper_studies)
    for word in ("MATCH", "MERGE", "DELETE e", "DETACH"):
        assert word not in source.replace('"""', ""), word
    assert "WHERE st.seek_study_id IS NULL" in q.PAPER_IN_STUDY_OF


# --- preview_labels: read only, classes as sync_samples does ------------------------------------

ASSAY_MAP = {5: (99, "Patient Visit"), 6: (None, "Seq run"), 7: (99, "Patient Visit")}
KEYS = q.EDGE_LABEL_KEYS


def _stored(**values):
    return {k: values.get(k) for k in KEYS}


class EdgeGraph:
    def __init__(self, edges, *, write_ok=False):
        self.edges = edges
        self.calls = []
        self.write_ok = write_ok

    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        read = kwargs.get("routing_") == RoutingControl.READ
        self.calls.append((query, read))
        if query == q.EDGES_INCIDENT:
            ids = set(parameters_["ids"])
            return SimpleNamespace(records=[e for e in self.edges if e["child_id"] in ids or e["parent_id"] in ids])
        if self.write_ok and query == q.WRITE_EDGE_LABELS_NEW:
            n = len(parameters_["rows"])
            return SimpleNamespace(records=[{"matched": n, "written": n, "pairs": n}])
        raise AssertionError(f"unexpected statement {query[:60]}")


@pytest.fixture
def world(monkeypatch):
    assays = {11: [5], 10: [5, 6], 13: [7], 12: [7]}
    monkeypatch.setattr(sources, "sample_assay_ids_for", lambda ids: {i: assays[i] for i in ids if i in assays})
    monkeypatch.setattr(sources, "samples_by_ids", lambda ids: [{"id": i, "json_metadata": "{}"} for i in ids])
    monkeypatch.setattr(sources, "resolved_assay_map", lambda: dict(ASSAY_MAP))
    monkeypatch.setattr(sources, "sops_map", lambda: {})
    equal = labels.edge_labels([5], [5, 6], ASSAY_MAP)
    return [
        {"child_id": 11, "parent_id": 10, "element_id": "a", "stored": equal},
        {"child_id": 13, "parent_id": 12, "element_id": "b", "stored": _stored()},
        {"child_id": 12, "parent_id": 10, "element_id": "c",
         "stored": _stored(assay_id=5, internal_assay_id=99, internal_assay_title="Patient Visit",
                           internal_assay_ids=[99], internal_assay_titles=["Patient Visit"])},
    ]


def test_preview_classes_each_edge_and_writes_nothing(world):
    driver = EdgeGraph(world)
    got = {e["element_id"]: e for e in targeted.preview_labels(driver, DB, [10, 11, 12, 13])}
    assert got["a"]["class"] == labels.EQUAL and got["a"]["properties"] == []
    assert got["b"]["class"] == labels.NEW
    assert got["c"]["class"] == labels.CLEARED
    assert got["c"]["properties"] == ["assay_id", "internal_assay_id", "internal_assay_title", "internal_assay_ids",
                                      "internal_assay_titles"]
    assert all(read for _q, read in driver.calls)


def test_preview_classes_as_sync_samples_counts(world):
    preview = targeted.preview_labels(EdgeGraph(world), DB, [10, 11, 12, 13])
    report = targeted._label_edges(EdgeGraph(world, write_ok=True), DB, world, targeted._Context(None))
    for cls in labels.CLASSES:
        assert report[f"labels_{cls}"] == sum(1 for e in preview if e["class"] == cls), cls


def test_preview_of_no_ids_reads_nothing():
    driver = EdgeGraph([])
    assert targeted.preview_labels(driver, DB, []) == []
    assert driver.calls == []

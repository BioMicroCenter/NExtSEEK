"""The Neo4j writer for graph schema v1.2 (nextseek_api/graph_sync/writer.py, cypher.py).

A fake driver records every ``execute_query`` call; no Neo4j is needed.
"""
import ast
import hashlib
import inspect
import json
import os
import re
from datetime import date
from types import SimpleNamespace

import pytest

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import writer as w
from nextseek_api.graph_sync.projection import SampleProjection
from nextseek_graph import schema


class FakeDriver:
    """Records ``execute_query(query, params, database_=...)`` calls.

    ``responder(query, params)`` returns the records (dicts) for a call, or raises. A call with a
    ``result_transformer_`` gets the records as an iterable, as the real driver's Result is.
    ``counters`` gives the summary counters of every call.
    """

    def __init__(self, responder=None, counters=None):
        self.calls = []
        self.responder = responder or (lambda query, params: [])
        self.counters = counters or {}

    def execute_query(self, query, parameters_=None, database_=None, result_transformer_=None, **kwargs):
        params = parameters_ or {}
        self.calls.append(SimpleNamespace(query=query, params=params, database=database_, kwargs=kwargs))
        records = list(self.responder(query, params))
        if result_transformer_ is not None:
            return result_transformer_(iter(records))
        summary = SimpleNamespace(counters=SimpleNamespace(**self.counters))
        return SimpleNamespace(records=records, summary=summary)

    def queries(self):
        return [c.query for c in self.calls]

    def calls_of(self, query):
        return [c for c in self.calls if c.query == query]


def _projection(sid, label="T_TIS", type_id=26, project_ids=(2,), failures=()):
    props = {"id": sid, "uuid": f"u-{sid}", "type": label[2:], "project_ids": list(project_ids),
             "search_text": "x", "Organ": "Lung"}
    return SampleProjection(sid, type_id, label, props, list(failures))


# --- cypher.py -----------------------------------------------------------------------------------

def test_write_samples_statement_replaces_properties_and_labels():
    assert q.WRITE_SAMPLES.lstrip().startswith("CYPHER 25")
    assert "SET s = r.props" in q.WRITE_SAMPLES
    assert "s.parent_titles = pt, s.parent_title_hashes = pth" in q.WRITE_SAMPLES
    assert "SET s:$(r.label)" in q.WRITE_SAMPLES
    assert "REMOVE s:$(stale)" in q.WRITE_SAMPLES
    assert "MATCH (s)-[o:OF_TYPE|IN_PROJECT]->() DELETE o" in q.WRITE_SAMPLES


def test_write_samples_takes_the_parent_lists_from_the_props_when_present():
    # Schema 1.2: the projection owns the parent lists; a node written without them keeps the node's own
    for key, alias in (("parent_titles", "pt"), ("parent_title_hashes", "pth")):
        pattern = rf"CASE WHEN '{key}' IN keys\(r\.props\) THEN r\.props\.{key}\s+ELSE s\.{key} END AS {alias}\b"
        assert re.search(pattern, q.WRITE_SAMPLES), key
    # the lists are read before the replace, so the node's own values are still there to keep
    assert q.WRITE_SAMPLES.index("AS pth") < q.WRITE_SAMPLES.index("SET s = r.props")


# The schema DDL as it was written by hand before it was rendered from the contract's 1.1 triples: the rendering must
# give these statements byte for byte.
GOLDEN_CONSTRAINTS_V11 = [
    "CREATE CONSTRAINT sample_id_unique IF NOT EXISTS FOR (s:Sample) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT sample_type_id_unique IF NOT EXISTS FOR (t:SampleType) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT sample_type_title_unique IF NOT EXISTS FOR (t:SampleType) REQUIRE t.title IS UNIQUE",
    "CREATE CONSTRAINT sample_type_label_unique IF NOT EXISTS FOR (t:SampleType) REQUIRE t.label IS UNIQUE",
    "CREATE CONSTRAINT attribute_key_unique IF NOT EXISTS FOR (a:Attribute) REQUIRE a.key IS UNIQUE",
    "CREATE CONSTRAINT attribute_id_unique IF NOT EXISTS FOR (a:Attribute) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT project_id_unique IF NOT EXISTS FOR (p:Project) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT person_id_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT study_id_unique IF NOT EXISTS FOR (s:Study) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT investigation_id_unique IF NOT EXISTS FOR (i:Investigation) REQUIRE i.id IS UNIQUE",
    "CREATE INDEX sample_uuid IF NOT EXISTS FOR (s:Sample) ON (s.uuid)",
    "CREATE INDEX sample_type IF NOT EXISTS FOR (s:Sample) ON (s.type)",
    "CREATE INDEX study_seek_study_id IF NOT EXISTS FOR (s:Study) ON (s.seek_study_id)",
]
GOLDEN_FULLTEXT = "CREATE FULLTEXT INDEX sample_search_text IF NOT EXISTS FOR (s:Sample) ON EACH [s.search_text]"


def test_the_schema_ddl_is_byte_identical_to_the_hand_written_statements():
    assert isinstance(q.CONSTRAINTS_V11, list)
    assert q.CONSTRAINTS_V11 == GOLDEN_CONSTRAINTS_V11
    assert q.FULLTEXT == GOLDEN_FULLTEXT
    assert q.FULLTEXT_INDEX == "sample_search_text"


def test_constraints_are_the_v11_set():
    joined = "\n".join(q.CONSTRAINTS_V11)
    # uuid is indexed but never unique while MySQL holds duplicate uuids
    assert "REQUIRE s.uuid" not in joined
    assert any("ON (s.uuid)" in stmt for stmt in q.CONSTRAINTS_V11)


@pytest.mark.parametrize("title, quoted", [
    ("Organ", "`Organ`"), ("Catalog#", "`Catalog#`"), ("Media supplement ", "`Media supplement `"),
    ("odd`name", "`odd``name`"), ("``", "``````"),
])
def test_quote_doubles_backticks(title, quoted):
    assert q.quote(title) == quoted


def test_budget_index_name_and_statement():
    name, stmt = q.budget_index("T_D_SEQ", "Read length")
    digest = hashlib.sha1("Read length".encode("utf-8")).hexdigest()[:10]
    assert name == f"gs_T_D_SEQ_{digest}"
    assert stmt == f"CREATE INDEX {name} IF NOT EXISTS FOR (s:`T_D_SEQ`) ON (s.`Read length`)"


@pytest.mark.parametrize("label", ["TIS", "T_D.SEQ", "T_", "T_a`b", ""])
def test_budget_index_refuses_a_label_outside_the_rule(label):
    with pytest.raises(ValueError):
        q.budget_index(label, "Organ")


# --- write_samples -------------------------------------------------------------------------------

def test_write_samples_sends_one_statement_per_chunk():
    driver = FakeDriver(lambda query, params: [{"written": len(params["rows"]), "typed": len(params["rows"]),
                                                "linked": len(params["rows"])}])
    projections = [_projection(i) for i in range(1, 6)]
    counts = w.write_samples(driver, "neo4j", projections, chunk=2)

    calls = driver.calls_of(q.WRITE_SAMPLES)
    assert [len(c.params["rows"]) for c in calls] == [2, 2, 1]
    assert all(c.database == "neo4j" for c in calls)
    row = calls[0].params["rows"][0]
    assert set(row) == {"id", "label", "sample_type_id", "props"}
    assert row == {"id": 1, "label": "T_TIS", "sample_type_id": 26, "props": projections[0].props}
    assert counts["samples_written"] == 5
    assert counts["of_type"] == 5
    assert counts["untyped"] == 0
    assert counts["in_project"] == 5
    assert counts["in_project_missing"] == 0


def test_write_samples_reports_missing_types_and_projects_and_cast_failures():
    driver = FakeDriver(lambda query, params: [{"written": 2, "typed": 1, "linked": 1}])
    projections = [_projection(1, project_ids=(2, 16)), _projection(2, project_ids=(), failures=["Age"])]
    counts = w.write_samples(driver, "neo4j", projections)
    assert counts["untyped"] == 1
    assert counts["in_project_expected"] == 2
    assert counts["in_project_missing"] == 1
    assert counts["cast_failures"] == 1


def test_write_samples_with_nothing_sends_nothing():
    driver = FakeDriver()
    assert w.write_samples(driver, "neo4j", [])["samples_written"] == 0
    assert driver.calls == []


# --- write_attributes ----------------------------------------------------------------------------

def _attribute(key, type_id=26, **extra):
    row = {"key": key, "sample_type_id": type_id, "title": key.split(":", 1)[1], "value_type": "string",
           "declared": True}
    row.update(extra)
    return row


def test_write_attributes_deletes_every_key_not_in_rows_before_merging():
    rows = [_attribute("26:Organ", id=1), _attribute("26:Age", id=2), _attribute("33:Organ ", 33, id=3)]
    driver = FakeDriver(lambda query, params: [{"linked": len(params.get("rows", []))}]
                        if query == q.MERGE_ATTRIBUTES else [])
    counts = w.write_attributes(driver, "neo4j", rows)

    queries = driver.queries()
    assert queries.index(q.DELETE_GONE_ATTRIBUTES) < queries.index(q.MERGE_ATTRIBUTES)
    (delete,) = driver.calls_of(q.DELETE_GONE_ATTRIBUTES)
    assert sorted(delete.params["keys"]) == ["26:Age", "26:Organ", "33:Organ "]
    merged = [r for c in driver.calls_of(q.MERGE_ATTRIBUTES) for r in c.params["rows"]]
    assert merged == rows
    assert counts["attributes_written"] == 3
    assert counts["attributes_without_type"] == 0


def test_write_attributes_refuses_duplicate_keys():
    with pytest.raises(ValueError, match="26:Organ"):
        w.write_attributes(FakeDriver(), "neo4j", [_attribute("26:Organ"), _attribute("26:Organ")])


# --- the index budget ----------------------------------------------------------------------------

def _entry(sample_type, title, value_type="string", sample_count=5000, max_len=20, **extra):
    row = {"sample_type": sample_type, "title": title, "value_type": value_type,
           "sample_count": sample_count, "max_len": max_len}
    row.update(extra)
    return row


def _created(driver):
    return [c.query for c in driver.calls if c.query.startswith("CREATE INDEX gs_")]


def test_index_budget_creates_one_index_per_qualifying_pair():
    census = {
        "26:CellCount": _entry("TIS", "CellCount", "integer", sample_count=3),       # numeric with values
        "26:Weight": _entry("TIS", "Weight", "float", sample_count=0),               # numeric, no values
        "26:Collected": _entry("TIS", "Collected", "date", sample_count=1),          # date with values
        "26:Organ": _entry("TIS", "Organ", "string", sample_count=1000),             # string at threshold
        "26:Rare": _entry("TIS", "Rare", "string", sample_count=999),                # string under threshold
        "26:Notes": _entry("TIS", "Notes", "string", sample_count=50000, max_len=4001),  # a value too long
        "26:Parent": _entry("TIS", "Parent", "string", sample_count=90000),          # lineage
        "26:TissueParent": _entry("TIS", "TissueParent", "integer", sample_count=9),  # lineage by title
        "26:File_R1": _entry("TIS", "File_R1", "string", sample_count=90000),        # file
        "33:Read `len`": _entry("D.SEQ", "Read `len`", "integer", sample_count=10),  # backtick in title
    }
    driver = FakeDriver()
    names = w.ensure_index_budget(driver, "neo4j", census)

    created = _created(driver)
    expected = {("T_TIS", "CellCount"), ("T_TIS", "Collected"), ("T_TIS", "Organ"), ("T_D_SEQ", "Read `len`")}
    assert len(created) == len(expected)
    assert sorted(names) == sorted(q.budget_index(label, title)[0] for label, title in expected)
    assert "CREATE INDEX {} IF NOT EXISTS FOR (s:`T_D_SEQ`) ON (s.`Read ``len```)".format(
        q.budget_index("T_D_SEQ", "Read `len`")[0]) in created
    for title in ("Weight", "Rare", "Notes", "Parent", "TissueParent", "File_R1"):
        assert not any(f"ON (s.`{title}`)" in stmt for stmt in created), title
    for name in names:
        assert name.startswith("gs_T_")


def test_index_budget_names_each_index_by_label_and_title_hash():
    driver = FakeDriver()
    names = w.ensure_index_budget(driver, "neo4j", {"26:Organ": _entry("TIS", "Organ")})
    assert names == ["gs_T_TIS_" + hashlib.sha1(b"Organ").hexdigest()[:10]]


def test_index_budget_takes_benchmark_keys_but_never_lineage_or_file():
    census = {
        "26:Rare": _entry("TIS", "Rare", sample_count=12),
        "26:Odd": _entry("TIS", "Odd", sample_count=12),
        "26:MouseParent": _entry("TIS", "MouseParent", sample_count=12),
        "26:Link_x": _entry("TIS", "Link_x", sample_count=12),
    }
    bench = {("TIS", "Rare"), "26:Odd", ("TIS", "MouseParent"), ("TIS", "Link_x")}
    driver = FakeDriver()
    w.ensure_index_budget(driver, "neo4j", census, bench_keys=bench)
    created = _created(driver)
    assert any("ON (s.`Rare`)" in s for s in created)
    assert any("ON (s.`Odd`)" in s for s in created)
    assert not any("MouseParent" in s or "Link_x" in s for s in created)


def test_index_budget_uses_a_supplied_role_and_label():
    census = {"x": _entry("TIS", "Organ", role="file"),
              "y": _entry("ignored", "Age", "integer", label="T_PAV", sample_count=1)}
    driver = FakeDriver()
    w.ensure_index_budget(driver, "neo4j", census)
    created = _created(driver)
    assert created == [q.budget_index("T_PAV", "Age")[1]]


def test_index_budget_drops_stale_gs_indexes_only():
    keep = q.budget_index("T_TIS", "Organ")[0]
    existing = [{"name": keep}, {"name": "gs_T_TIS_0000000000"}]
    driver = FakeDriver(lambda query, params: existing if query == q.GS_INDEX_NAMES else [])
    w.ensure_index_budget(driver, "neo4j", {"26:Organ": _entry("TIS", "Organ")})
    dropped = [c for c in driver.queries() if c.startswith("DROP INDEX")]
    assert dropped == ["DROP INDEX gs_T_TIS_0000000000 IF EXISTS"]


# --- CHILD_OF ------------------------------------------------------------------------------------

def test_archive_writes_the_tsv_before_any_delete(tmp_path):
    out = tmp_path / "runs" / "child_of_archive.tsv"
    pairs = [{"child_uuid": "c1", "parent_uuid": "p1"}, {"child_uuid": "c2", "parent_uuid": "p2"},
             {"child_uuid": "c3", "parent_uuid": None}]
    deleted = iter([2, 1, 0])
    seen_file_at_delete = []

    def responder(query, params):
        if query == q.CHILD_OF_COUNT:
            return [{"n": 3}]
        if query == q.CHILD_OF_PAIRS:
            return pairs
        if query == q.DELETE_CHILD_OF_BATCH:
            seen_file_at_delete.append(out.exists() and out.read_text().count("\n"))
            assert params["batch"] == 50_000
            return [{"deleted": next(deleted)}]
        return []

    driver = FakeDriver(responder)
    counts = w.archive_and_drop_child_of(driver, "neo4j", str(out), {("c1", "p1")})

    queries = driver.queries()
    assert queries.index(q.CHILD_OF_PAIRS) < queries.index(q.DELETE_CHILD_OF_BATCH)
    assert seen_file_at_delete and seen_file_at_delete[0] == 4  # header plus three rows, all before deleting
    lines = out.read_text().splitlines()
    assert lines == ["child_uuid\tparent_uuid\tdeclared", "c1\tp1\ttrue", "c2\tp2\tfalse", "c3\t\tfalse"]
    assert counts == {"child_of_pairs": 3, "child_of_undeclared": 2, "child_of_deleted": 3,
                      "archive_path": str(out)}


def test_archive_with_no_child_of_keeps_an_earlier_archive(tmp_path):
    out = tmp_path / "child_of_archive.tsv"
    out.write_text("child_uuid\tparent_uuid\tdeclared\nc1\tp1\ttrue\n")
    driver = FakeDriver(lambda query, params: [{"n": 0}] if query == q.CHILD_OF_COUNT else [])
    counts = w.archive_and_drop_child_of(driver, "neo4j", str(out), set())
    assert counts["child_of_pairs"] == 0
    assert out.read_text().count("\n") == 2
    assert q.DELETE_CHILD_OF_BATCH not in driver.queries()


def test_archive_refuses_to_delete_when_the_file_cannot_be_written(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    driver = FakeDriver(lambda query, params: [{"n": 1}] if query == q.CHILD_OF_COUNT
                        else [{"child_uuid": "c", "parent_uuid": "p"}])
    with pytest.raises(OSError):
        w.archive_and_drop_child_of(driver, "neo4j", str(blocker / "sub" / "a.tsv"), set())
    assert q.DELETE_CHILD_OF_BATCH not in driver.queries()


# --- undeclared DERIVED_FROM ---------------------------------------------------------------------

NBSP_UUID = "MUS-240910LAU-68 "


class LineageGraph:
    """A small graph for the undeclared DERIVED_FROM step.

    Answers the stream statement with the edges whose endpoints are both Sample nodes (what its pattern matches),
    and applies the delete statement to the named edges whose endpoints are both Sample nodes. ``on_delete`` runs
    before each delete is applied.
    """

    def __init__(self, on_delete=None):
        self.nodes = {"n10": ({"Sample"}, 10, "TIS-10"), "n11": ({"Sample"}, 11, "D.SEQ-11"),
                      "n12": ({"Sample"}, 12, "TIS-12"), "n70": ({"Sample"}, 70, "TIS-70"),
                      "n13": ({"Sample"}, 13, NBSP_UUID), "n99": ({"OrphanSample"}, 99, "OLD-99")}
        self.edges = {
            "e1": ("n11", "n10", {"child_id": 11, "parent_id": 10}),                   # declared
            "e2": ("n12", "n10", {"parent_id": 10, "child_id": 12, "note": "stale"}),  # stale after a Parent edit
            "e3": ("n70", "n70", {}),                                                  # self-loop
            "e4": ("n11", "n99", {"child_id": 11}),                                    # to an orphan
            "e5": ("n99", "n10", {}),                                                  # from an orphan
            "e6": ("n11", "n13", {"child_id": 11, "parent_id": 13}),                   # nbsp uuid
            "e7": ("n12", "n11", {"child_id": 12, "parent_id": 11}),                   # declared
        }
        self.deleted = []
        self.on_delete = on_delete

    def _between_samples(self, eid):
        child, parent, _ = self.edges[eid]
        return "Sample" in self.nodes[child][0] and "Sample" in self.nodes[parent][0]

    def __call__(self, query, params):
        if query == q.DERIVED_FROM_BETWEEN_SAMPLES:
            rows = []
            for eid in sorted(self.edges):
                if not self._between_samples(eid):
                    continue
                child, parent, props = self.edges[eid]
                rows.append({"child_id": self.nodes[child][1], "parent_id": self.nodes[parent][1],
                             "child_uuid": self.nodes[child][2], "parent_uuid": self.nodes[parent][2],
                             "props": dict(props), "element_id": eid})
            return rows
        if query == q.DELETE_UNDECLARED_DERIVED_FROM:
            if self.on_delete:
                self.on_delete(params)
            n = 0
            for eid in params["element_ids"]:
                if eid in self.edges and self._between_samples(eid):
                    del self.edges[eid]
                    self.deleted.append(eid)
                    n += 1
            return [{"deleted": n}]
        raise AssertionError(f"unexpected statement: {query}")


DECLARED = {(11, 10), (12, 11)}


def test_undeclared_derived_from_statements_touch_only_edges_between_samples():
    assert "MATCH (c:Sample)-[e:DERIVED_FROM]->(p:Sample)" in q.DERIVED_FROM_BETWEEN_SAMPLES
    for key in ("child_id", "parent_id", "child_uuid", "parent_uuid", "props", "element_id"):
        assert f" AS {key}" in q.DERIVED_FROM_BETWEEN_SAMPLES
    assert "properties(e) AS props" in q.DERIVED_FROM_BETWEEN_SAMPLES
    assert "MATCH (:Sample)-[e:DERIVED_FROM]->(:Sample)" in q.DELETE_UNDECLARED_DERIVED_FROM
    assert "elementId(e) = eid" in q.DELETE_UNDECLARED_DERIVED_FROM
    assert "OrphanSample" not in q.DERIVED_FROM_BETWEEN_SAMPLES + q.DELETE_UNDECLARED_DERIVED_FROM


def test_undeclared_derived_from_archive_is_written_before_any_delete(tmp_path):
    out = tmp_path / "runs" / "derived_from_undeclared_archive.tsv"
    seen_at_delete = []

    def on_delete(params):
        partial = out.with_name(out.name + ".partial")
        seen_at_delete.append((out.exists() and out.read_text(encoding="utf-8").count("\n"), partial.exists()))

    graph = LineageGraph(on_delete)
    driver = FakeDriver(graph)
    w.archive_and_drop_undeclared_derived_from(driver, "neo4j", str(out), DECLARED)

    queries = driver.queries()
    assert queries.index(q.DERIVED_FROM_BETWEEN_SAMPLES) < queries.index(q.DELETE_UNDECLARED_DERIVED_FROM)
    assert seen_at_delete == [(4, False)]  # header plus three rows, renamed into place before the first delete


def test_undeclared_derived_from_never_deletes_declared_or_orphan_edges(tmp_path):
    graph = LineageGraph()
    driver = FakeDriver(graph)
    w.archive_and_drop_undeclared_derived_from(driver, "neo4j", str(tmp_path / "a.tsv"), DECLARED)

    sent = [eid for c in driver.calls_of(q.DELETE_UNDECLARED_DERIVED_FROM) for eid in c.params["element_ids"]]
    assert sorted(sent) == ["e2", "e3", "e6"]
    assert sorted(graph.deleted) == ["e2", "e3", "e6"]
    assert sorted(graph.edges) == ["e1", "e4", "e5", "e7"]  # the declared pairs and both orphan edges


def test_undeclared_derived_from_returns_its_counts_and_archives_each_edge(tmp_path):
    out = tmp_path / "derived_from_undeclared_archive.tsv"
    counts = w.archive_and_drop_undeclared_derived_from(FakeDriver(LineageGraph()), "neo4j", str(out), DECLARED)

    assert counts == {"derived_from_between_samples": 5, "derived_from_undeclared": 3, "derived_from_doubled": 0,
                      "derived_from_deleted": 3, "derived_from_archive_path": str(out)}
    lines = out.read_text(encoding="utf-8").split("\n")
    assert lines == [  # one row per undeclared edge, in stream order (e2, e3, e6)
        "child_id\tparent_id\tchild_uuid\tparent_uuid\tprops",
        '12\t10\tTIS-12\tTIS-10\t{"child_id": 12, "note": "stale", "parent_id": 10}',
        "70\t70\tTIS-70\tTIS-70\t{}",
        '11\t13\tD.SEQ-11\tMUS-240910LAU-68 \t{"child_id": 11, "parent_id": 13}',
        "",
    ]
    assert not os.path.exists(str(out) + ".partial")


def test_undeclared_derived_from_keeps_each_row_on_one_line(tmp_path):
    out = tmp_path / "a.tsv"
    record = {"child_id": 5, "parent_id": 6, "child_uuid": "a\tb\\c", "parent_uuid": "d\ne\rf",
              "props": {"when": date(2024, 1, 31), "text": "x\ty z"}, "element_id": "e"}
    driver = FakeDriver(lambda query, params: [record] if query == q.DERIVED_FROM_BETWEEN_SAMPLES
                        else [{"deleted": 1}])
    w.archive_and_drop_undeclared_derived_from(driver, "neo4j", str(out), set())

    header, row = out.read_text(encoding="utf-8").splitlines()
    fields = row.split("\t")
    assert fields[:4] == ["5", "6", "a\\tb\\\\c", "d\\ne\\rf"]
    assert json.loads(fields[4]) == {"when": "2024-01-31", "text": "x\ty z"}


def test_undeclared_derived_from_with_nothing_undeclared_keeps_an_earlier_archive(tmp_path):
    out = tmp_path / "derived_from_undeclared_archive.tsv"
    out.write_text("child_id\tparent_id\tchild_uuid\tparent_uuid\tprops\n12\t10\tTIS-12\tTIS-10\t{}\n")
    graph = LineageGraph()
    driver = FakeDriver(graph)
    counts = w.archive_and_drop_undeclared_derived_from(driver, "neo4j", str(out),
                                                        DECLARED | {(12, 10), (70, 70), (11, 13)})

    assert counts == {"derived_from_between_samples": 5, "derived_from_undeclared": 0, "derived_from_doubled": 0,
                      "derived_from_deleted": 0, "derived_from_archive_path": None}
    assert out.read_text().count("\n") == 2
    assert not os.path.exists(str(out) + ".partial")
    assert q.DELETE_UNDECLARED_DERIVED_FROM not in driver.queries()


def test_undeclared_derived_from_refuses_to_delete_when_the_file_cannot_be_written(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    driver = FakeDriver(LineageGraph())
    with pytest.raises(OSError):
        w.archive_and_drop_undeclared_derived_from(driver, "neo4j", str(blocker / "sub" / "a.tsv"), DECLARED)
    assert q.DELETE_UNDECLARED_DERIVED_FROM not in driver.queries()


def test_undeclared_derived_from_leaves_no_partial_when_the_stream_fails(tmp_path):
    out = tmp_path / "a.tsv"
    broken = {"child_id": 5, "parent_id": 6, "child_uuid": "a", "parent_uuid": "b", "element_id": "e"}  # no props
    driver = FakeDriver(lambda query, params: [broken] if query == q.DERIVED_FROM_BETWEEN_SAMPLES else [])
    with pytest.raises(KeyError):
        w.archive_and_drop_undeclared_derived_from(driver, "neo4j", str(out), set())
    assert not out.exists() and not os.path.exists(str(out) + ".partial")
    assert q.DELETE_UNDECLARED_DERIVED_FROM not in driver.queries()


def test_undeclared_derived_from_deletes_in_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "DERIVED_FROM_DELETE_BATCH", 2)
    driver = FakeDriver(LineageGraph())
    counts = w.archive_and_drop_undeclared_derived_from(driver, "neo4j", str(tmp_path / "a.tsv"), DECLARED)
    assert [len(c.params["element_ids"]) for c in driver.calls_of(q.DELETE_UNDECLARED_DERIVED_FROM)] == [2, 1]
    assert counts["derived_from_deleted"] == 3


def test_undeclared_derived_from_reads_as_a_read_and_deletes_as_a_write(tmp_path):
    from neo4j import RoutingControl

    driver = FakeDriver(LineageGraph())
    w.archive_and_drop_undeclared_derived_from(driver, "neo4j", str(tmp_path / "a.tsv"), DECLARED)
    (stream,) = driver.calls_of(q.DERIVED_FROM_BETWEEN_SAMPLES)
    assert stream.kwargs.get("routing_") == RoutingControl.READ
    assert all("routing_" not in c.kwargs for c in driver.calls_of(q.DELETE_UNDECLARED_DERIVED_FROM))
    assert all(c.database == "neo4j" for c in driver.calls)


def _doubled_graph(cls):
    """``cls()`` with a second DERIVED_FROM for the declared pair (11, 10): ``e8``, streamed after ``e1``."""
    graph = cls()
    graph.edges["e8"] = ("n11", "n10", {"child_id": 11, "parent_id": 10, "copy": 2})
    return graph


def _gate_lineage(graph, declared) -> dict:
    """Gate G check 1 over what ``graph`` holds, as verify reads it."""
    from nextseek_api.graph_sync import run, verify

    def responder(query, params):
        if query == verify.LINEAGE_PAIRS:
            return [{"child": graph.nodes[c][1], "parent": graph.nodes[p][1]}
                    for eid, (c, p, _) in graph.edges.items() if graph._between_samples(eid)]
        if query == verify.LINEAGE_ON_ORPHANS:
            return [{"n": sum(1 for c, p, _ in graph.edges.values()
                              if "OrphanSample" in graph.nodes[c][0] | graph.nodes[p][0])}]
        raise AssertionError(f"unexpected statement: {query}")

    checks: list = []
    verify._check_lineage(FakeDriver(responder), "neo4j",
                          SimpleNamespace(lineage={run.encode_pair(c, p) for c, p in declared}), checks, {})
    return {c["name"]: c for c in checks}


def test_a_second_edge_of_a_declared_pair_is_archived_and_deleted_and_gate_g_check_1_then_passes(tmp_path):
    graph = _doubled_graph(LineageGraph)
    assert not _gate_lineage(graph, DECLARED)["1.lineage.duplicate_edges"]["pass"]
    out = tmp_path / "derived_from_undeclared_archive.tsv"
    counts = w.archive_and_drop_undeclared_derived_from(FakeDriver(graph), "neo4j", str(out), DECLARED)

    assert counts == {"derived_from_between_samples": 6, "derived_from_undeclared": 3, "derived_from_doubled": 1,
                      "derived_from_deleted": 4, "derived_from_archive_path": str(out)}
    assert sorted(graph.deleted) == ["e2", "e3", "e6", "e8"]          # the first edge of (11, 10), e1, stays
    assert '11\t10\tD.SEQ-11\tTIS-10\t{"child_id": 11, "copy": 2, "parent_id": 10}' in out.read_text(
        encoding="utf-8").splitlines()
    gate = _gate_lineage(graph, DECLARED)
    assert [name for name, check in gate.items() if not check["pass"]] == []


def test_a_by_id_sync_deletes_a_second_edge_of_a_declared_pair_of_its_children(tmp_path):
    graph = _doubled_graph(ChildLineageGraph)
    out = tmp_path / "a.tsv"
    counts = w.archive_and_drop_undeclared_for_children(FakeDriver(graph), "neo4j", [11, 12], DECLARED, str(out))

    assert counts == {"derived_from_of_children": 5, "derived_from_undeclared": 2, "derived_from_doubled": 1,
                      "derived_from_deleted": 3, "derived_from_archive_path": str(out)}
    assert sorted(graph.deleted) == ["e2", "e6", "e8"]
    assert [line.split("\t")[:2] for line in out.read_text(encoding="utf-8").splitlines()[1:]] == [
        ["12", "10"], ["11", "13"], ["11", "10"]]


# --- ghosts and orphans --------------------------------------------------------------------------

def test_find_ghosts_splits_ghosts_orphans_and_unresolved():
    sample_ids = [{"id": i} for i in (1, 2, 3, 3, 4, 4, 5, 5, 9, 900, 900)] + [{"id": None}]

    def responder(query, params):
        if query == q.SAMPLE_IDS:
            return sample_ids
        if query == q.DUPLICATE_SAMPLE_IDS:
            return [{"id": 3}, {"id": 4}, {"id": 5}, {"id": 900}]
        if query == q.NODES_FOR_IDS:
            assert sorted(params["ids"]) == [3, 4, 5, 900]
            return [{"id": 3, "element_id": "e3a", "uuid": "live-3"}, {"id": 3, "element_id": "e3b", "uuid": "ghost-3"},
                    {"id": 4, "element_id": "e4a", "uuid": "live-4"}, {"id": 4, "element_id": "e4b", "uuid": "live-x"},
                    {"id": 5, "element_id": "e5a", "uuid": "g-5a"}, {"id": 5, "element_id": "e5b", "uuid": "g-5b"},
                    {"id": 900, "element_id": "e9a", "uuid": "o-a"}, {"id": 900, "element_id": "e9b", "uuid": "o-b"}]
        if query == q.SAMPLES_WITHOUT_ID:
            return [{"element_id": "enull"}]
        return []

    driver = FakeDriver(responder)
    found = w.find_ghosts(driver, "neo4j", {1, 2, 3, 4, 5, 6}, {"live-3", "live-4", "live-x"})
    assert sorted(found["ghost_element_ids"]) == ["e3b", "e5a", "e5b"]
    assert found["orphan_ids"] == [9, 900]
    assert found["unresolved_duplicate_ids"] == [4]
    assert found["idless_element_ids"] == ["enull"]
    assert found["sample_nodes"] == 12
    assert found["duplicate_ids"] == 4


def test_delete_ghosts_and_relabel_orphans_send_their_ids():
    driver = FakeDriver(lambda query, params: [{"n": len(params.get("element_ids", params.get("ids", [])))}])
    assert w.delete_ghosts(driver, "neo4j", ["e1", "e2"]) == {"ghosts_deleted": 2}
    assert driver.calls_of(q.DELETE_GHOSTS)[0].params == {"element_ids": ["e1", "e2"]}
    counts = w.relabel_orphans(driver, "neo4j", [9, 900], element_ids=["enull"])
    assert driver.calls_of(q.RELABEL_ORPHANS)[0].params == {"ids": [9, 900]}
    assert driver.calls_of(q.RELABEL_ORPHANS_BY_ELEMENT_ID)[0].params == {"element_ids": ["enull"]}
    assert counts == {"orphans_relabeled": 3}


def test_delete_ghosts_with_nothing_sends_nothing():
    driver = FakeDriver()
    assert w.delete_ghosts(driver, "neo4j", []) == {"ghosts_deleted": 0}
    assert w.relabel_orphans(driver, "neo4j", []) == {"orphans_relabeled": 0}
    assert driver.calls == []


# --- constraints and fulltext --------------------------------------------------------------------

def test_ensure_constraints_runs_every_statement_and_drops_the_v10_uuid_constraint():
    driver = FakeDriver()
    counts = w.ensure_constraints_v11(driver, "neo4j")
    queries = driver.queries()
    assert queries[0] == q.DROP_V10_CONSTRAINTS[0]
    assert queries[1:] == list(q.CONSTRAINTS_V11)
    assert counts == {"schema_statements": len(q.DROP_V10_CONSTRAINTS) + len(q.CONSTRAINTS_V11)}


def test_ensure_constraints_fails_loudly():
    def responder(query, params):
        if "sample_id_unique" in query:
            raise RuntimeError("duplicate ids")
        return []

    with pytest.raises(RuntimeError, match="duplicate ids"):
        w.ensure_constraints_v11(FakeDriver(responder), "neo4j")


def test_ensure_fulltext():
    driver = FakeDriver()
    assert w.ensure_fulltext(driver, "neo4j") == {"fulltext_index": "sample_search_text"}
    assert driver.queries() == [q.FULLTEXT]


def test_await_indexes_raises_on_a_failed_index():
    driver = FakeDriver(lambda query, params: [{"name": "gs_T_TIS_x", "state": "FAILED", "populationPercent": 3.0}])
    with pytest.raises(RuntimeError, match="gs_T_TIS_x"):
        w.await_indexes(driver, "neo4j", timeout_s=1, poll_s=0)


def test_await_indexes_returns_when_all_online():
    driver = FakeDriver(lambda query, params: [{"name": "a", "state": "ONLINE", "populationPercent": 100.0}])
    assert w.await_indexes(driver, "neo4j", timeout_s=1, poll_s=0) == {"indexes_online": 1}


# --- catalog, projects, people, investigations ---------------------------------------------------

def test_write_sample_types_backfills_by_title_then_merges_by_id():
    rows = [{"id": 26, "title": "TIS", "label": "T_TIS", "deprecated": False, "has_context": True},
            {"id": 33, "title": "D.SEQ", "label": "T_D_SEQ", "deprecated": False, "has_context": False}]
    driver = FakeDriver(lambda query, params: [{"title": "OLD"}] if query == q.SAMPLE_TYPES_NOT_IN else [])
    counts = w.write_sample_types(driver, "neo4j", rows)
    queries = driver.queries()
    assert queries.index(q.BACKFILL_SAMPLE_TYPE_ID) < queries.index(q.MERGE_SAMPLE_TYPES)
    assert driver.calls_of(q.BACKFILL_SAMPLE_TYPE_ID)[0].params["rows"] == [{"id": 26, "title": "TIS"},
                                                                          {"id": 33, "title": "D.SEQ"}]
    assert driver.calls_of(q.MERGE_SAMPLE_TYPES)[0].params["rows"] == rows
    assert counts == {"sample_types_written": 2, "graph_only_sample_types": ["OLD"], "sample_types_deleted": 0}
    assert driver.calls_of(q.SAMPLE_TYPE_TITLE_CONFLICTS)[0].params["ids"] == [26, 33]
    assert q.SAMPLE_TYPES_GONE not in queries                    # no archive path: nothing is deleted


def test_write_sample_types_refuses_a_title_held_by_another_id():
    driver = FakeDriver(lambda query, params: [{"title": "TIS", "graph_id": 7, "mysql_id": 26}]
                        if query == q.SAMPLE_TYPE_TITLE_CONFLICTS else [])
    with pytest.raises(ValueError, match="TIS"):
        w.write_sample_types(driver, "neo4j", [{"id": 26, "title": "TIS", "label": "T_TIS"}])
    assert q.MERGE_SAMPLE_TYPES not in driver.queries()


def test_a_gone_sample_type_no_sample_reaches_is_archived_then_deleted(tmp_path):
    gone = [{"element_id": "4:t:9", "id": 9, "title": "OLD", "label": "T_OLD", "attribute_keys": ["9:Organ"]}]
    archive = tmp_path / w.SAMPLE_TYPES_DELETED_FILE
    seen = []

    def responder(query, params):
        if query == q.SAMPLE_TYPES_GONE:
            return gone
        if query == q.DELETE_SAMPLE_TYPES:
            seen.append(archive.read_text(encoding="utf-8").splitlines())
            return [{"deleted": len(params["element_ids"])}]
        return []

    driver = FakeDriver(responder)
    counts = w.write_sample_types(driver, "neo4j", [{"id": 26, "title": "TIS", "label": "T_TIS"}],
                                  archive_path=str(archive))
    assert seen == [[w.SAMPLE_TYPES_ARCHIVE_HEADER.rstrip("\n"), "9\tOLD\tT_OLD\t9:Organ"]]
    assert driver.calls_of(q.DELETE_SAMPLE_TYPES)[0].params["element_ids"] == ["4:t:9"]
    queries = driver.queries()
    assert queries.index(q.DELETE_SAMPLE_TYPES) < queries.index(q.MERGE_SAMPLE_TYPES)
    assert counts["sample_types_deleted"] == 1


def test_a_title_held_by_a_gone_empty_type_is_no_conflict():
    """A type deleted and recreated in SEEK under its old title: the old node (its id gone, no sample) is not a
    conflict, so the catalog step deletes it and writes the new one instead of refusing every night."""
    text = q.SAMPLE_TYPE_TITLE_CONFLICTS
    assert "t.id IN $ids OR EXISTS { (t)<-[:OF_TYPE]-(:Sample) }" in text
    assert "NOT EXISTS { (t)<-[:OF_TYPE]-(:Sample) }" in q.SAMPLE_TYPES_GONE
    assert "NOT EXISTS { (t)<-[:OF_TYPE]-(:Sample) }" in q.DELETE_SAMPLE_TYPES


def test_a_gone_investigation_no_study_holds_is_archived_then_deleted_and_a_held_one_kept(tmp_path):
    gone = [{"element_id": "4:i:7", "id": 7, "title": "Gone", "project_ids": [5, 3], "held": False},
            {"element_id": "4:i:8", "id": 8, "title": "Still held", "project_ids": [], "held": True}]
    archive = tmp_path / w.INVESTIGATIONS_DELETED_FILE

    def responder(query, params):
        if query == q.INVESTIGATIONS_GONE:
            return gone
        if query == q.DELETE_INVESTIGATIONS:
            return [{"deleted": len(params["element_ids"])}]
        return [{"linked": 0}] if query == q.MERGE_INVESTIGATION_IN_PROJECT else []

    driver = FakeDriver(responder)
    counts = w.write_investigation_projects(driver, "neo4j", [{"id": 2, "title": "A", "description": None}], [],
                                            archive_path=str(archive), seek_study_ids=[])
    assert archive.read_text(encoding="utf-8").splitlines() == [w.INVESTIGATIONS_ARCHIVE_HEADER.rstrip("\n"),
                                                                "7\tGone\t3,5"]
    assert driver.calls_of(q.DELETE_INVESTIGATIONS)[0].params["element_ids"] == ["4:i:7"]
    assert (counts["investigations_deleted"], counts["investigations_not_in_seek_held"]) == (1, 1)
    assert "NOT EXISTS { (i)<-[:IN_INVESTIGATION]-(st:Study)" in q.DELETE_INVESTIGATIONS


def test_a_gone_investigation_is_held_only_by_a_live_seek_study_or_a_paper_node(tmp_path):
    """SEEK deletes an investigation after its studies and this release deletes no Study node, so a node whose
    SEEK study is gone does not hold its Investigation. The read and the delete's own re-check both take SEEK's study
    ids; a Study with no seek_study_id (a graph-only paper) still holds."""
    gone = [{"element_id": "4:i:7", "id": 7, "title": "Gone", "project_ids": [], "held": False}]

    def responder(query, params):
        if query == q.INVESTIGATIONS_GONE:
            return gone
        return [{"deleted": len(params["element_ids"])}] if query == q.DELETE_INVESTIGATIONS else []

    driver = FakeDriver(responder)
    w.write_investigation_projects(driver, "neo4j", [{"id": 2, "title": "A", "description": None}], [],
                                   archive_path=str(tmp_path / "a.tsv"), seek_study_ids=[42, 41, 42])
    assert driver.calls_of(q.INVESTIGATIONS_GONE)[0].params["study_ids"] == [41, 42]
    assert driver.calls_of(q.DELETE_INVESTIGATIONS)[0].params["study_ids"] == [41, 42]
    held = ("EXISTS { (i)<-[:IN_INVESTIGATION]-(st:Study) "
            "WHERE st.seek_study_id IS NULL OR st.seek_study_id IN $study_ids }")
    for statement in (q.INVESTIGATIONS_GONE, q.DELETE_INVESTIGATIONS, q.GRAPH_INVESTIGATIONS):
        assert " ".join(held.split()) in " ".join(statement.split())


def test_the_investigation_deletes_need_seeks_study_ids(tmp_path):
    driver = FakeDriver()
    with pytest.raises(ValueError, match="SEEK's study ids"):
        w.write_investigation_projects(driver, "neo4j", [{"id": 2, "title": "A", "description": None}], [],
                                       archive_path=str(tmp_path / "a.tsv"))
    assert driver.calls == []


def test_an_empty_investigation_list_refuses_to_delete_every_investigation(tmp_path):
    driver = FakeDriver(lambda query, params: [{"element_id": "4:i:7", "id": 7, "title": "x", "project_ids": [],
                                                "held": False}] if query == q.INVESTIGATIONS_GONE else [])
    with pytest.raises(ValueError, match="every Investigation"):
        w.write_investigation_projects(driver, "neo4j", [], [], archive_path=str(tmp_path / "a.tsv"),
                                       seek_study_ids=[])
    assert q.DELETE_INVESTIGATIONS not in driver.queries() and q.MERGE_INVESTIGATIONS not in driver.queries()


def test_write_projects_drops_none_and_deletes_gone_projects():
    driver = FakeDriver()
    counts = w.write_projects(driver, "neo4j", [{"id": 2, "title": "P2"}, {"id": 16, "title": None}])
    assert driver.calls_of(q.MERGE_PROJECTS)[0].params["rows"] == [{"id": 2, "title": "P2"}, {"id": 16}]
    assert driver.calls_of(q.DELETE_GONE_PROJECTS)[0].params["ids"] == [2, 16]
    assert counts == {"projects_written": 2}


def test_write_people_replaces_every_member_of():
    rows = [{"person_id": 144, "project_id": 2, "has_left": False, "time_left_at": None},
            {"person_id": 144, "project_id": 5, "has_left": True, "time_left_at": "t"},
            {"person_id": 145, "project_id": 16, "has_left": False, "time_left_at": None}]
    driver = FakeDriver(lambda query, params: [{"linked": 2}] if query == q.MERGE_MEMBER_OF else [])
    counts = w.write_people_and_memberships(driver, "neo4j", rows)
    queries = driver.queries()
    assert queries.index(q.DELETE_MEMBER_OF) < queries.index(q.MERGE_MEMBER_OF)
    assert driver.calls_of(q.MERGE_PEOPLE)[0].params["ids"] == [144, 145]
    assert driver.calls_of(q.DELETE_GONE_PEOPLE)[0].params["ids"] == [144, 145]
    assert driver.calls_of(q.MERGE_MEMBER_OF)[0].params["rows"] == rows
    assert counts == {"people_written": 2, "memberships_written": 2, "memberships_dropped": 1}


def test_write_investigation_projects_sets_the_lowest_project_id():
    investigations = [{"id": 2, "title": "A", "description": None}, {"id": 30, "title": "TCGA", "description": "d"}]
    links = [{"investigation_id": 30, "project_id": 16}, {"investigation_id": 2, "project_id": 5},
             {"investigation_id": 2, "project_id": 3}]
    driver = FakeDriver(lambda query, params: [{"linked": 3}] if query == q.MERGE_INVESTIGATION_IN_PROJECT else [])
    counts = w.write_investigation_projects(driver, "neo4j", investigations, links)
    rows = driver.calls_of(q.MERGE_INVESTIGATIONS)[0].params["rows"]
    assert rows == [{"id": 2, "title": "A", "description": None, "project_id": 3},
                    {"id": 30, "title": "TCGA", "description": "d", "project_id": 16}]
    queries = driver.queries()
    assert queries.index(q.DELETE_INVESTIGATION_IN_PROJECT) < queries.index(q.MERGE_INVESTIGATION_IN_PROJECT)
    assert counts == {"investigations_written": 2, "investigation_links": 3, "investigation_links_dropped": 0,
                      "investigations_deleted": 0, "investigations_not_in_seek_held": 0}
    assert q.INVESTIGATIONS_GONE not in queries                  # no archive path: nothing is deleted


# --- lineage, studies, counts, GraphMeta ----------------------------------------------------------

@pytest.mark.parametrize("existing, by_uuid", [([{"child_id": 12}], False), ([{"child_id": "UID-1"}], True),
                                              ([], False)])
def test_write_missing_lineage_follows_the_existing_edge_form(existing, by_uuid):
    def responder(query, params):
        if query == q.DERIVED_FROM_ID_FORM:
            return existing
        if query == q.WRITE_MISSING_LINEAGE:
            return [{"matched": len(params["rows"])}]
        return []

    driver = FakeDriver(responder, counters={"relationships_created": 1})
    counts = w.write_missing_lineage(driver, "neo4j", [(1, 2), (3, 4), (1, 2)], chunk=10)
    (call,) = driver.calls_of(q.WRITE_MISSING_LINEAGE)
    assert call.params["rows"] == [[1, 2], [3, 4]]
    assert call.params["by_uuid"] is by_uuid
    assert counts == {"lineage_pairs": 2, "lineage_matched": 2, "lineage_created": 1, "lineage_dropped": 0}



def test_write_attribute_counts_sets_counts_and_zeroes_the_rest():
    driver = FakeDriver(lambda query, params: [{"n": len(params.get("rows", []))}]
                        if query == q.SET_ATTRIBUTE_COUNTS else [{"n": 7}])
    counts = w.write_attribute_counts(driver, "neo4j", {"26:Organ": 10, "26:Age": 0})
    rows = driver.calls_of(q.SET_ATTRIBUTE_COUNTS)[0].params["rows"]
    assert sorted(rows, key=lambda r: r["key"]) == [{"key": "26:Age", "count": 0}, {"key": "26:Organ", "count": 10}]
    assert sorted(driver.calls_of(q.ZERO_ATTRIBUTE_COUNTS)[0].params["keys"]) == ["26:Age", "26:Organ"]
    assert counts == {"attribute_counts_set": 2, "attribute_counts_zeroed": 7}


def test_write_sample_type_counts():
    driver = FakeDriver(lambda query, params: [{"n": 118}])
    assert w.write_sample_type_counts(driver, "neo4j") == {"sample_type_counts_set": 118}
    assert driver.queries() == [q.SET_SAMPLE_TYPE_COUNTS]


def test_the_schema_version_is_the_contracts():
    assert w.SCHEMA_VERSION is schema.SCHEMA_VERSION


def test_write_graphmeta_stamps_the_schema_version():
    driver = FakeDriver()
    assert w.write_graphmeta(driver, "neo4j", "abc") == {"schema_version": schema.SCHEMA_VERSION,
                                                         "catalog_hash": "abc"}
    (call,) = driver.calls
    assert call.query == q.WRITE_GRAPHMETA
    assert call.params == {"schema_version": schema.SCHEMA_VERSION, "catalog_hash": "abc"}


def test_write_graphmeta_without_label_maps_hash_keeps_the_stored_one():
    # SET of named properties, never a replace of the map, so a label_maps_hash written earlier survives a catalog sync
    assert "label_maps_hash" not in q.WRITE_GRAPHMETA
    assert "SET m =" not in q.WRITE_GRAPHMETA and "SET m +=" not in q.WRITE_GRAPHMETA


def test_write_graphmeta_with_label_maps_hash():
    driver = FakeDriver()
    counts = w.write_graphmeta(driver, "neo4j", "abc", label_maps_hash="def")
    assert counts == {"schema_version": schema.SCHEMA_VERSION, "catalog_hash": "abc", "label_maps_hash": "def"}
    (call,) = driver.calls
    assert call.query == q.WRITE_GRAPHMETA_WITH_LABEL_MAPS
    assert call.params == {"schema_version": schema.SCHEMA_VERSION, "catalog_hash": "abc",
                           "label_maps_hash": "def"}
    assert "m.label_maps_hash = $label_maps_hash" in q.WRITE_GRAPHMETA_WITH_LABEL_MAPS
    assert "m.schema_version = $schema_version, m.catalog_hash = $catalog_hash" in q.WRITE_GRAPHMETA_WITH_LABEL_MAPS


def test_graphmeta_reads_the_single_node():
    from neo4j import RoutingControl

    props = {"schema_version": "1.2", "catalog_hash": "abc", "label_maps_hash": "def", "synced_at": "t"}
    driver = FakeDriver(lambda query, params: [{"props": props}] if query == q.READ_GRAPHMETA else [])
    meta = w.graphmeta(driver, "neo4j")
    assert meta == {"nodes": 1, "schema_version": "1.2", "catalog_hash": "abc", "label_maps_hash": "def",
                    "synced_at": "t"}
    (call,) = driver.calls
    assert call.kwargs.get("routing_") == RoutingControl.READ


def test_graphmeta_of_a_v11_graph_has_no_label_maps_hash():
    driver = FakeDriver(lambda query, params: [{"props": {"schema_version": "1.1", "catalog_hash": "abc"}}])
    meta = w.graphmeta(driver, "neo4j")
    assert meta["schema_version"] == "1.1" and meta["label_maps_hash"] is None and meta["synced_at"] is None


@pytest.mark.parametrize("records", [[], [{"props": {"schema_version": "1.2"}}, {"props": {"schema_version": "1.2"}}]])
def test_graphmeta_with_no_or_several_nodes_reads_as_no_version(records):
    meta = w.graphmeta(FakeDriver(lambda query, params: records), "neo4j")
    assert meta == {"nodes": len(records), "schema_version": None, "catalog_hash": None, "label_maps_hash": None,
                    "synced_at": None}


def test_graphmeta_turns_a_temporal_synced_at_into_iso_text():
    class Stamp:
        def iso_format(self):
            return "2026-09-15T02:00:00Z"

    driver = FakeDriver(lambda query, params: [{"props": {"schema_version": "1.2", "synced_at": Stamp()}}])
    assert w.graphmeta(driver, "neo4j")["synced_at"] == "2026-09-15T02:00:00Z"


# --- retries -------------------------------------------------------------------------------------

def test_a_transient_error_is_retried(monkeypatch):
    from neo4j.exceptions import TransientError

    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    attempts = []

    def responder(query, params):
        attempts.append(query)
        if len(attempts) == 1:
            raise TransientError("deadlock")
        return []

    driver = FakeDriver(responder)
    w.ensure_fulltext(driver, "neo4j")
    assert len(attempts) == 2


def test_retry_gives_up_after_three_transient_errors(monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    slept = []
    monkeypatch.setattr(w.time, "sleep", slept.append)
    attempts = []

    def fn():
        attempts.append(1)
        raise ServiceUnavailable("down")

    with pytest.raises(ServiceUnavailable):
        w._retry(fn)
    assert len(attempts) == 3 and len(slept) == 2


def test_retry_does_not_retry_a_permanent_error(monkeypatch):
    monkeypatch.setattr(w.time, "sleep", lambda s: pytest.fail("slept on a permanent error"))
    attempts = []

    def fn():
        attempts.append(1)
        raise ValueError("syntax")

    with pytest.raises(ValueError):
        w._retry(fn)
    assert attempts == [1]


def test_the_writer_imports_nothing_from_batch_upload():
    tree = ast.parse(inspect.getsource(w))
    imported = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imported += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not [m for m in imported if m and "batch_upload" in m]
    assert "_retry" in vars(w) and w._retry.__module__ == w.__name__


def test_reads_are_routed_as_reads():
    driver = FakeDriver(lambda query, params: [{"n": 0}])
    w.archive_and_drop_child_of(driver, "neo4j", os.devnull, set())
    (call,) = driver.calls
    from neo4j import RoutingControl
    assert call.kwargs.get("routing_") == RoutingControl.READ


# --- the deletion rule (schema 1.2; the sync design, section 9) -----------------------------------

class RetireGraph:
    """Sample nodes for the retire rule.

    Answers the candidate read for ``:Sample`` nodes only, and applies the delete and the orphan swap as their
    guards say: the delete only to a node carrying ``synced_at``, the swap only to one without. ``on_delete`` runs
    before each delete is applied.
    """

    def __init__(self, on_delete=None):
        self.nodes = {
            "n1": {"labels": {"Sample", "T_TIS"}, "rels": {"OF_TYPE", "IN_PROJECT", "DERIVED_FROM"},
                   "props": {"id": 1, "uuid": "TIS-1", "type": "TIS", "synced_at": "t"}, "edges": 3},
            "n2": {"labels": {"Sample", "T_TIS", "T_OLD"},
                   "rels": {"OF_TYPE", "IN_PROJECT", "DERIVED_FROM", "IN_STUDY"},
                   "props": {"id": 2, "uuid": "OLD-2", "type": "TIS", "Organ": "Lung"}, "edges": 4},
            "n4": {"labels": {"OrphanSample"}, "rels": {"DERIVED_FROM"}, "props": {"id": 4, "uuid": "OLD-4"},
                   "edges": 1},
            "n5": {"labels": {"Sample", "T_D_SEQ"}, "rels": set(),
                   "props": {"id": 5, "uuid": "SEQ\t5", "type": "D.SEQ", "synced_at": "t"}, "edges": 0},
        }
        self.on_delete = on_delete
        self.deleted = []

    def __call__(self, query, params):
        if query == q.RETIRE_CANDIDATES:
            rows = []
            for sid in params["ids"]:
                for eid, node in sorted(self.nodes.items()):
                    if "Sample" in node["labels"] and node["props"]["id"] == sid:
                        rows.append({"element_id": eid, "id": sid, "uuid": node["props"]["uuid"],
                                     "type": node["props"].get("type"), "synced": "synced_at" in node["props"],
                                     "incident_edges": node["edges"]})
            return rows
        if query == q.DELETE_RETIRED:
            if self.on_delete:
                self.on_delete(params)
            n = 0
            for eid in params["element_ids"]:
                node = self.nodes.get(eid)
                if node and "Sample" in node["labels"] and "synced_at" in node["props"]:
                    del self.nodes[eid]
                    self.deleted.append(eid)
                    n += 1
            return [{"n": n}]
        if query == q.RELABEL_ORPHANS_BY_ELEMENT_ID:
            n = 0
            for eid in params["element_ids"]:
                node = self.nodes.get(eid)
                if node and "Sample" in node["labels"] and "synced_at" not in node["props"]:
                    node["labels"] = {"OrphanSample"} | {l for l in node["labels"]
                                                        if l != "Sample" and not l.startswith("T_")}
                    node["rels"] -= {"OF_TYPE", "IN_PROJECT"}
                    node["props"]["orphaned_at"] = "now"
                    n += 1
            return [{"n": n}]
        raise AssertionError(f"unexpected statement: {query}")


def test_retire_statements_delete_a_synced_node_and_orphan_a_never_synced_one():
    # the read sees only live Sample nodes; an OrphanSample is left as it is
    assert "MATCH (s:Sample {id: id})" in q.RETIRE_CANDIDATES
    assert "s.synced_at IS NOT NULL AS synced" in q.RETIRE_CANDIDATES
    assert "COUNT { (s)--() } AS incident_edges" in q.RETIRE_CANDIDATES
    # a node graph_sync wrote mirrors a row that is gone: deleted with its edges
    assert "s.synced_at IS NOT NULL" in q.DELETE_RETIRED
    assert "DETACH DELETE s" in q.DELETE_RETIRED
    # a node graph_sync never wrote becomes an OrphanSample, by id or by element id, through one body
    for statement in (q.RELABEL_ORPHANS, q.RELABEL_ORPHANS_BY_ELEMENT_ID):
        assert statement.lstrip().startswith("CYPHER 25")
        assert statement.rstrip().endswith(q.ORPHAN_SWAP.strip())
        assert "s.synced_at IS NULL" in statement
    assert "MATCH (s)-[o:OF_TYPE|IN_PROJECT]->() DELETE o" in q.ORPHAN_SWAP
    assert "[l IN labels(s) WHERE l STARTS WITH 'T_']" in q.ORPHAN_SWAP
    assert "REMOVE s:Sample" in q.ORPHAN_SWAP and "REMOVE s:$(types)" in q.ORPHAN_SWAP
    assert "SET s:OrphanSample, s.orphaned_at = datetime()" in q.ORPHAN_SWAP
    # properties and DERIVED_FROM stay on an orphan
    assert "DERIVED_FROM" not in q.ORPHAN_SWAP and "DETACH" not in q.ORPHAN_SWAP and "SET s =" not in q.ORPHAN_SWAP


def test_retire_deletes_the_synced_node_and_orphans_the_never_synced_one(tmp_path):
    graph = RetireGraph()
    archive = tmp_path / "runs" / "retired.tsv"
    counts = w.retire_samples(FakeDriver(graph), "neo4j", [1, 2, 3, 4], str(archive))

    assert graph.deleted == ["n1"]
    assert graph.nodes["n2"]["labels"] == {"OrphanSample"}  # no Sample and no T_ label left
    assert graph.nodes["n2"]["rels"] == {"DERIVED_FROM", "IN_STUDY"}
    assert graph.nodes["n2"]["props"]["Organ"] == "Lung" and "orphaned_at" in graph.nodes["n2"]["props"]
    assert graph.nodes["n4"]["labels"] == {"OrphanSample"} and "orphaned_at" not in graph.nodes["n4"]["props"]
    assert counts == {"retire_requested": 4, "retired_deleted": 1, "retired_orphaned": 1, "retire_not_found": 2,
                      "retired_archive_path": str(archive)}
    assert archive.read_text(encoding="utf-8").splitlines() == ["id\tuuid\ttype\tincident_edges", "1\tTIS-1\tTIS\t3"]


def test_retire_writes_the_archive_before_the_delete(tmp_path):
    archive = tmp_path / "retired.tsv"
    seen = []
    graph = RetireGraph(on_delete=lambda params: seen.append(archive.exists() and archive.read_text()))
    driver = FakeDriver(graph)
    w.retire_samples(driver, "neo4j", [1, 5], str(archive))

    queries = driver.queries()
    assert queries.index(q.RETIRE_CANDIDATES) < queries.index(q.DELETE_RETIRED)
    assert seen == ["id\tuuid\ttype\tincident_edges\n1\tTIS-1\tTIS\t3\n5\tSEQ\\t5\tD.SEQ\t0\n"]
    assert sorted(graph.deleted) == ["n1", "n5"]


def test_retire_appends_to_the_runs_archive(tmp_path):
    archive = tmp_path / "retired.tsv"
    w.retire_samples(FakeDriver(RetireGraph()), "neo4j", [1], str(archive))
    w.retire_samples(FakeDriver(RetireGraph()), "neo4j", [5], str(archive))
    assert archive.read_text().splitlines() == ["id\tuuid\ttype\tincident_edges", "1\tTIS-1\tTIS\t3",
                                                "5\tSEQ\\t5\tD.SEQ\t0"]


def test_retire_refuses_to_delete_when_the_archive_cannot_be_written(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    graph = RetireGraph()
    driver = FakeDriver(graph)
    with pytest.raises(OSError):
        w.retire_samples(driver, "neo4j", [1, 2], str(blocker / "sub" / "retired.tsv"))
    assert q.DELETE_RETIRED not in driver.queries()
    assert q.RELABEL_ORPHANS_BY_ELEMENT_ID not in driver.queries()
    assert "n1" in graph.nodes


def test_retire_refuses_to_delete_without_an_archive_path():
    driver = FakeDriver(RetireGraph())
    with pytest.raises(ValueError, match="archive"):
        w.retire_samples(driver, "neo4j", [1], None)
    assert q.DELETE_RETIRED not in driver.queries()


def test_retire_of_never_synced_nodes_only_needs_no_archive():
    graph = RetireGraph()
    counts = w.retire_samples(FakeDriver(graph), "neo4j", [2], None)
    assert counts["retired_orphaned"] == 1 and counts["retired_deleted"] == 0
    assert counts["retired_archive_path"] is None


def test_retire_with_nothing_sends_nothing(tmp_path):
    driver = FakeDriver()
    counts = w.retire_samples(driver, "neo4j", [], str(tmp_path / "retired.tsv"))
    assert driver.calls == []
    assert counts == {"retire_requested": 0, "retired_deleted": 0, "retired_orphaned": 0, "retire_not_found": 0,
                      "retired_archive_path": None}
    assert not (tmp_path / "retired.tsv").exists()


def test_retire_reads_as_a_read_in_chunks(monkeypatch, tmp_path):
    from neo4j import RoutingControl

    monkeypatch.setattr(w, "REL_CHUNK", 2)
    driver = FakeDriver(RetireGraph())
    w.retire_samples(driver, "neo4j", [1, 2, 3, 2, 5], str(tmp_path / "retired.tsv"))
    reads = driver.calls_of(q.RETIRE_CANDIDATES)
    assert [c.params["ids"] for c in reads] == [[1, 2], [3, 5]]  # a repeated id is sent once
    assert all(c.kwargs.get("routing_") == RoutingControl.READ for c in reads)
    assert all("routing_" not in c.kwargs for c in driver.calls_of(q.DELETE_RETIRED))


# --- undeclared DERIVED_FROM of given children -----------------------------------------------------

class ChildLineageGraph(LineageGraph):
    """``LineageGraph`` that also answers the per-child stream: each child's DERIVED_FROM to a Sample parent."""

    def __call__(self, query, params):
        if query == q.DERIVED_FROM_OF_CHILDREN:
            rows = []
            for eid in sorted(self.edges):
                child, parent, props = self.edges[eid]
                if not self._between_samples(eid) or self.nodes[child][1] not in params["ids"]:
                    continue
                rows.append({"child_id": self.nodes[child][1], "parent_id": self.nodes[parent][1],
                             "child_uuid": self.nodes[child][2], "parent_uuid": self.nodes[parent][2],
                             "props": dict(props), "element_id": eid})
            return rows
        return super().__call__(query, params)


def test_children_statement_streams_only_their_edges_to_samples():
    assert "UNWIND $ids AS id" in q.DERIVED_FROM_OF_CHILDREN
    assert "MATCH (c:Sample {id: id})-[e:DERIVED_FROM]->(p:Sample)" in q.DERIVED_FROM_OF_CHILDREN
    for key in ("child_id", "parent_id", "child_uuid", "parent_uuid", "props", "element_id"):
        assert f" AS {key}" in q.DERIVED_FROM_OF_CHILDREN


def test_children_archive_and_drop_only_their_undeclared_edges(tmp_path):
    graph = ChildLineageGraph()
    out = tmp_path / "runs" / "derived_from_undeclared_archive.tsv"
    counts = w.archive_and_drop_undeclared_for_children(FakeDriver(graph), "neo4j", [11, 12], DECLARED, str(out))

    assert sorted(graph.deleted) == ["e2", "e6"]  # e3 (child 70) is not theirs; e4 points at an orphan
    assert sorted(graph.edges) == ["e1", "e3", "e4", "e5", "e7"]
    assert counts == {"derived_from_of_children": 4, "derived_from_undeclared": 2, "derived_from_doubled": 0,
                      "derived_from_deleted": 2, "derived_from_archive_path": str(out)}
    assert out.read_text(encoding="utf-8").splitlines() == [
        "child_id\tparent_id\tchild_uuid\tparent_uuid\tprops",
        '12\t10\tTIS-12\tTIS-10\t{"child_id": 12, "note": "stale", "parent_id": 10}',
        f'11\t13\tD.SEQ-11\t{NBSP_UUID}\t{{"child_id": 11, "parent_id": 13}}',
    ]


def test_children_archive_is_written_before_any_delete(tmp_path):
    out = tmp_path / "a.tsv"
    seen = []
    graph = ChildLineageGraph(on_delete=lambda params: seen.append(out.exists() and out.read_text().count("\n")))
    driver = FakeDriver(graph)
    w.archive_and_drop_undeclared_for_children(driver, "neo4j", [11, 12], DECLARED, str(out))
    queries = driver.queries()
    assert queries.index(q.DERIVED_FROM_OF_CHILDREN) < queries.index(q.DELETE_UNDECLARED_DERIVED_FROM)
    assert seen == [3]  # header plus both rows, before the first delete


def test_children_archive_appends_across_calls(tmp_path):
    out = tmp_path / "a.tsv"
    w.archive_and_drop_undeclared_for_children(FakeDriver(ChildLineageGraph()), "neo4j", [12], DECLARED, str(out))
    w.archive_and_drop_undeclared_for_children(FakeDriver(ChildLineageGraph()), "neo4j", [11], DECLARED, str(out))
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "child_id\tparent_id\tchild_uuid\tparent_uuid\tprops"
    assert [line.split("\t")[:2] for line in lines[1:]] == [["12", "10"], ["11", "13"]]


def test_children_with_nothing_undeclared_write_no_file_and_delete_nothing(tmp_path):
    out = tmp_path / "a.tsv"
    driver = FakeDriver(ChildLineageGraph())
    counts = w.archive_and_drop_undeclared_for_children(driver, "neo4j", [11, 12],
                                                        DECLARED | {(12, 10), (11, 13)}, str(out))
    assert counts == {"derived_from_of_children": 4, "derived_from_undeclared": 0, "derived_from_doubled": 0,
                      "derived_from_deleted": 0, "derived_from_archive_path": None}
    assert not out.exists()
    assert q.DELETE_UNDECLARED_DERIVED_FROM not in driver.queries()


def test_children_refuse_to_delete_when_the_archive_cannot_be_written(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    driver = FakeDriver(ChildLineageGraph())
    with pytest.raises(OSError):
        w.archive_and_drop_undeclared_for_children(driver, "neo4j", [11, 12], DECLARED,
                                                   str(blocker / "sub" / "a.tsv"))
    assert q.DELETE_UNDECLARED_DERIVED_FROM not in driver.queries()


def test_children_refuse_to_delete_without_an_archive_path():
    driver = FakeDriver(ChildLineageGraph())
    with pytest.raises(ValueError, match="archive"):
        w.archive_and_drop_undeclared_for_children(driver, "neo4j", [11, 12], DECLARED, None)
    assert q.DELETE_UNDECLARED_DERIVED_FROM not in driver.queries()


def test_children_are_streamed_in_chunks(monkeypatch, tmp_path):
    from neo4j import RoutingControl

    monkeypatch.setattr(w, "REL_CHUNK", 1)
    driver = FakeDriver(ChildLineageGraph())
    counts = w.archive_and_drop_undeclared_for_children(driver, "neo4j", [11, 12, 11], DECLARED,
                                                        str(tmp_path / "a.tsv"))
    reads = driver.calls_of(q.DERIVED_FROM_OF_CHILDREN)
    assert [c.params["ids"] for c in reads] == [[11], [12]]
    assert all(c.kwargs.get("routing_") == RoutingControl.READ for c in reads)
    assert counts["derived_from_deleted"] == 2


# --- DERIVED_FROM labels (schema 1.2; the sync design, section 7.3) -----------------------------

LABEL_KEYS = ("assay_id", "internal_assay_id", "internal_assay_title", "internal_assay_ids", "internal_assay_titles",
              "protocol_id", "protocol_title")
SINGULAR = ("assay_id", "internal_assay_id", "internal_assay_title")


def _labels(assay_id=None, ia_id=None, ia_title=None, protocol_id=None, protocol_title=None):
    return {"assay_id": assay_id, "internal_assay_id": ia_id, "internal_assay_title": ia_title,
            "internal_assay_ids": [ia_id] if ia_id is not None else [],
            "internal_assay_titles": [ia_title] if ia_title is not None else [],
            "protocol_id": protocol_id, "protocol_title": protocol_title}


class EdgeGraph:
    """DERIVED_FROM edges between Sample nodes, keyed by (child id, parent id), for the label statements.

    Answers the incident-edge read and applies both label writes as their guards say: the default write only to an
    edge whose three singular assay fields are all absent; the approved write only to an edge whose seven label
    properties still equal the row's ``stored``. A write sets the five assay properties (None removes one) and drops
    ``assay_title``; the default write keeps a stored protocol and fills the pair only where nothing is stored.
    """

    def __init__(self, edges):
        self.edges = {pair: dict(props) for pair, props in edges.items()}

    def _apply(self, props, labels, *, keep_protocol=False):
        for key in LABEL_KEYS:
            if keep_protocol and key in ("protocol_id", "protocol_title"):
                if labels[key] is not None and props.get(key) is None:
                    props[key] = labels[key]
                continue
            if labels[key] is None:
                props.pop(key, None)
            else:
                props[key] = labels[key]
        props.pop("assay_title", None)

    def __call__(self, query, params):
        if query == q.EDGES_INCIDENT:
            ids = set(params["ids"])
            return [{"child_id": c, "parent_id": p, "element_id": f"e{c}-{p}",
                     "stored": {k: props.get(k) for k in LABEL_KEYS}}
                    for (c, p), props in sorted(self.edges.items()) if c in ids or p in ids]
        if query in (q.WRITE_EDGE_LABELS_NEW, q.WRITE_EDGE_LABELS_CHANGED):
            matched = written = 0
            pairs = set()
            for row in params["rows"]:
                pair = (row["child_id"], row["parent_id"])
                props = self.edges.get(pair)
                if props is None:
                    continue
                matched += 1
                pairs.add(pair)
                if query == q.WRITE_EDGE_LABELS_NEW:
                    ok = all(props.get(k) is None for k in SINGULAR)
                else:
                    ok = all(props.get(k) == row["stored"][k] for k in LABEL_KEYS)
                if ok:
                    # read the rule out of the statement, so these tests fail if the guard leaves the Cypher
                    self._apply(props, row["labels"], keep_protocol="coalesce(e.protocol_id" in query)
                    written += 1
            return [{"matched": matched, "written": written, "pairs": len(pairs)}]
        raise AssertionError(f"unexpected statement: {query}")


def _assignments(statement):
    return dict(re.findall(r"e\.(\w+) = r\.labels\.(\w+)", statement))


def test_the_label_keys_are_the_five_assay_properties_and_the_protocol_pair():
    assert q.EDGE_LABEL_KEYS == LABEL_KEYS
    assert q.EDGE_SINGULAR_ASSAY_KEYS == SINGULAR


@pytest.mark.parametrize("statement", ["WRITE_EDGE_LABELS_NEW", "WRITE_EDGE_LABELS_CHANGED"])
def test_each_label_write_sets_all_seven_properties_and_removes_assay_title(statement):
    text = getattr(q, statement)
    protocol = ("protocol_id", "protocol_title")
    if statement == "WRITE_EDGE_LABELS_NEW":
        # The five assay properties are replaced; the protocol pair is written only where nothing is stored (R5).
        assert _assignments(text) == {k: k for k in LABEL_KEYS if k not in protocol}
        for key in protocol:
            assert f"e.{key} = coalesce(e.{key}, r.labels.{key})" in text
    else:
        assert _assignments(text) == {k: k for k in LABEL_KEYS}  # never a subset
    assert "REMOVE e.assay_title" in text
    assert "MATCH (:Sample {id: r.child_id})-[e:DERIVED_FROM]->(:Sample {id: r.parent_id})" in text
    assert text.index("WHERE") < text.index("SET e.assay_id")  # the guard is in the statement, before the SET


def test_the_default_label_write_is_guarded_on_the_three_singular_fields():
    text = q.WRITE_EDGE_LABELS_NEW
    assert "WHERE e.assay_id IS NULL AND e.internal_assay_id IS NULL AND e.internal_assay_title IS NULL" in text
    assert "r.stored" not in text


def test_the_approved_label_write_is_guarded_on_the_values_read():
    text = q.WRITE_EDGE_LABELS_CHANGED
    for key in LABEL_KEYS:
        assert f"'{key}'" in text
    assert "(e[k] IS NULL AND r.stored[k] IS NULL) OR coalesce(e[k] = r.stored[k], false)" in text
    assert "e.assay_id IS NULL AND e.internal_assay_id IS NULL" not in text


def test_the_default_write_labels_new_edges_and_leaves_every_labelled_one():
    graph = EdgeGraph({
        (11, 10): {"child_id": 11, "parent_id": 10, "assay_title": "legacy"},   # new
        (12, 10): {"protocol_id": 4, "protocol_title": "old SOP"},               # new: no singular assay field
        (13, 10): {"internal_assay_title": "Patient Visit"},                    # any singular field set: kept
        (14, 10): {"assay_id": 7, "internal_assay_id": 99, "internal_assay_title": "Old", "protocol_id": 1},
    })
    rows = [{"child_id": c, "parent_id": 10, "labels": _labels(5, 33, "Flow Cytometry", 9, "SOP 9")}
            for c in (11, 12, 13, 14, 15)]
    counts = w.write_edge_labels(FakeDriver(graph), "neo4j", rows)

    expected = {"assay_id": 5, "internal_assay_id": 33, "internal_assay_title": "Flow Cytometry",
                "internal_assay_ids": [33], "internal_assay_titles": ["Flow Cytometry"], "protocol_id": 9,
                "protocol_title": "SOP 9"}
    assert graph.edges[(11, 10)] == {"child_id": 11, "parent_id": 10, **expected}  # assay_title gone
    # a stored protocol is kept (R5): V1 measured 402 production edges carrying a protocol and no assay label
    assert graph.edges[(12, 10)] == {**expected, "protocol_id": 4, "protocol_title": "old SOP"}
    assert graph.edges[(13, 10)] == {"internal_assay_title": "Patient Visit"}
    assert graph.edges[(14, 10)] == {"assay_id": 7, "internal_assay_id": 99, "internal_assay_title": "Old",
                                     "protocol_id": 1}
    assert counts == {"labels_rows": 5, "labels_written": 2, "labels_skipped_labelled": 2,
                      "labels_skipped_changed": 0, "labels_edges_missing": 1}


def test_a_label_that_clears_writes_nulls_and_empty_lists_but_keeps_a_stored_protocol():
    graph = EdgeGraph({(11, 10): {"protocol_id": 4}})
    w.write_edge_labels(FakeDriver(graph), "neo4j", [{"child_id": 11, "parent_id": 10, "labels": _labels()}])
    assert graph.edges[(11, 10)] == {"protocol_id": 4, "internal_assay_ids": [], "internal_assay_titles": []}


def test_with_label_changes_an_edge_changed_since_the_read_is_left_alone():
    graph = EdgeGraph({
        (11, 10): {"assay_id": 7, "internal_assay_id": 99, "internal_assay_title": "Old"},
        (12, 10): {"assay_id": 7, "internal_assay_id": 99, "internal_assay_title": "Old"},
        (13, 10): {"internal_assay_id": 33, "internal_assay_title": "Flow Cytometry", "assay_id": 5},
    })
    driver = FakeDriver(graph)
    read = w.edges_incident(driver, "neo4j", [10])
    graph.edges[(12, 10)]["internal_assay_title"] = "Renamed meanwhile"  # a writer between the read and the write
    new = _labels(5, 33, "Flow Cytometry")
    rows = [{"child_id": e["child_id"], "parent_id": e["parent_id"], "labels": new, "stored": e["stored"]}
            for e in read]
    counts = w.write_edge_labels(driver, "neo4j", rows, apply_label_changes=True)

    assert graph.edges[(11, 10)] == {k: v for k, v in new.items() if v is not None}
    assert graph.edges[(12, 10)] == {"assay_id": 7, "internal_assay_id": 99,
                                     "internal_assay_title": "Renamed meanwhile"}
    assert graph.edges[(13, 10)]["internal_assay_ids"] == [33]  # a missing plural list, written on approval
    assert counts == {"labels_rows": 3, "labels_written": 2, "labels_skipped_labelled": 0,
                      "labels_skipped_changed": 1, "labels_edges_missing": 0}
    assert driver.calls_of(q.WRITE_EDGE_LABELS_CHANGED) and not driver.calls_of(q.WRITE_EDGE_LABELS_NEW)
    sent = driver.calls_of(q.WRITE_EDGE_LABELS_CHANGED)[0].params["rows"][0]
    assert set(sent) == {"child_id", "parent_id", "labels", "stored"}
    assert set(sent["stored"]) == set(LABEL_KEYS)


@pytest.mark.parametrize("missing", LABEL_KEYS)
def test_a_row_missing_any_label_key_is_refused_before_anything_is_sent(missing):
    labels = _labels(5, 33, "Flow Cytometry")
    del labels[missing]
    rows = [{"child_id": 11, "parent_id": 10, "labels": _labels()},
            {"child_id": 12, "parent_id": 10, "labels": labels}]
    driver = FakeDriver()
    with pytest.raises(ValueError, match=missing):
        w.write_edge_labels(driver, "neo4j", rows)
    assert driver.calls == []


@pytest.mark.parametrize("value", [None, "33"])
def test_a_plural_label_that_is_not_a_list_is_refused(value):
    labels = _labels(5, 33, "Flow Cytometry")
    labels["internal_assay_ids"] = value
    with pytest.raises(ValueError, match="internal_assay_ids"):
        w.write_edge_labels(FakeDriver(), "neo4j", [{"child_id": 11, "parent_id": 10, "labels": labels}])


def test_plural_tuples_are_sent_as_lists_and_extra_keys_are_dropped():
    labels = _labels(5, 33, "Flow Cytometry") | {"internal_assay_ids": (33,), "assay_title": "x"}
    driver = FakeDriver(lambda query, params: [{"matched": 1, "written": 1, "pairs": 1}])
    w.write_edge_labels(driver, "neo4j", [{"child_id": 11, "parent_id": 10, "labels": labels}])
    (sent,) = driver.calls[0].params["rows"]
    assert sent == {"child_id": 11, "parent_id": 10, "labels": _labels(5, 33, "Flow Cytometry")}


@pytest.mark.parametrize("stored", [None, {k: None for k in LABEL_KEYS if k != "protocol_title"}])
def test_approved_changes_need_the_values_read(stored):
    row = {"child_id": 11, "parent_id": 10, "labels": _labels(5, 33, "Flow Cytometry")}
    if stored is not None:
        row["stored"] = stored
    driver = FakeDriver()
    with pytest.raises(ValueError, match="stored"):
        w.write_edge_labels(driver, "neo4j", [row], apply_label_changes=True)
    assert driver.calls == []


def test_label_rows_are_sent_once_per_pair_in_chunks(monkeypatch):
    monkeypatch.setattr(w, "REL_CHUNK", 2)
    driver = FakeDriver(lambda query, params: [{"matched": len(params["rows"]), "written": len(params["rows"]),
                                                "pairs": len(params["rows"])}])
    rows = [{"child_id": c, "parent_id": 10, "labels": _labels()} for c in (11, 12, 11, 13)]
    counts = w.write_edge_labels(driver, "neo4j", rows)
    assert [[r["child_id"] for r in c.params["rows"]] for c in driver.calls] == [[11, 12], [13]]
    assert all(c.query == q.WRITE_EDGE_LABELS_NEW and "routing_" not in c.kwargs for c in driver.calls)
    assert counts["labels_rows"] == 3 and counts["labels_written"] == 3


def test_label_write_with_nothing_sends_nothing():
    driver = FakeDriver()
    assert w.write_edge_labels(driver, "neo4j", []) == {
        "labels_rows": 0, "labels_written": 0, "labels_skipped_labelled": 0, "labels_skipped_changed": 0,
        "labels_edges_missing": 0}
    assert driver.calls == []


# --- the edges incident to samples ---------------------------------------------------------------

def test_incident_statement_reads_both_directions_between_samples():
    text = q.EDGES_INCIDENT
    assert "MATCH (c:Sample {id: id})-[e:DERIVED_FROM]->(p:Sample)" in text
    assert "MATCH (c:Sample)-[e:DERIVED_FROM]->(p:Sample {id: id})" in text
    assert "\nUNION\n" in text and "UNION ALL" not in text
    assert "e {" + ", ".join(f".{k}" for k in LABEL_KEYS) + "} AS stored" in text


def test_edges_incident_returns_each_edge_once_with_every_label_key(monkeypatch):
    from neo4j import RoutingControl

    monkeypatch.setattr(w, "REL_CHUNK", 1)
    both = {"child_id": 11, "parent_id": 10, "element_id": "e1", "stored": {"assay_id": 5}}
    other = {"child_id": 12, "parent_id": 11, "element_id": "e2", "stored": None}

    def responder(query, params):
        return {10: [both], 11: [both, other]}[params["ids"][0]]

    driver = FakeDriver(responder)
    edges = w.edges_incident(driver, "neo4j", [11, 10, 11])
    assert [c.params["ids"] for c in driver.calls] == [[11], [10]]
    assert all(c.query == q.EDGES_INCIDENT and c.kwargs.get("routing_") == RoutingControl.READ for c in driver.calls)
    assert edges == [
        {"child_id": 11, "parent_id": 10, "element_id": "e1", "stored": {k: 5 if k == "assay_id" else None
                                                                          for k in LABEL_KEYS}},
        {"child_id": 12, "parent_id": 11, "element_id": "e2", "stored": {k: None for k in LABEL_KEYS}},
    ]


def test_edges_incident_with_nothing_sends_nothing():
    driver = FakeDriver()
    assert w.edges_incident(driver, "neo4j", []) == []
    assert driver.calls == []


# --- source hashes -------------------------------------------------------------------------------

def test_sample_hashes_statement_pages_by_id():
    text = q.SAMPLE_HASHES_PAGE
    assert "WHERE s.id > $after" in text
    assert "RETURN s.id AS id, s.source_hash AS source_hash" in text
    assert "ORDER BY s.id" in text and "LIMIT $limit" in text


def test_sample_hashes_streams_every_node_in_id_order(monkeypatch):
    from neo4j import RoutingControl

    monkeypatch.setattr(w, "HASH_PAGE", 2)
    nodes = [(3, "h3"), (5, None), (8, "h8"), (9, "h9"), (12, "h12")]

    def responder(query, params):
        assert query == q.SAMPLE_HASHES_PAGE
        rows = [{"id": i, "source_hash": h} for i, h in nodes if i > params["after"]]
        return rows[:params["limit"]]

    driver = FakeDriver(responder)
    stream = w.sample_hashes(driver, "neo4j")
    assert driver.calls == []  # a generator: nothing is read until it is iterated
    assert list(stream) == nodes
    assert [c.params["after"] for c in driver.calls] == [-(2 ** 63), 5, 9]
    assert all(c.params["limit"] == 2 for c in driver.calls)
    assert all(c.kwargs.get("routing_") == RoutingControl.READ for c in driver.calls)


def test_sample_hashes_of_an_empty_graph():
    driver = FakeDriver()
    assert list(w.sample_hashes(driver, "neo4j")) == []
    assert len(driver.calls) == 1


# --- the studies release: Study nodes and IN_STUDY -----------------------------------------------------------------

from nextseek_api.tests.graph_sync_study_fakes import StudyGraph  # noqa: E402

DB = "neo4j"


def _study_row(sid, title="Alder", description=None, investigation_id=101):
    return {"id": sid, "title": title, "description": description, "investigation_id": investigation_id}


# SEEK's side of the unit worlds below: investigations 101 and 102, studies 1 and 2 under 101, 3 under 102.
SEEK_STUDIES = [_study_row(1, "Alder Unpublished"), _study_row(2, "Birch"), _study_row(3, "Birch Study", None, 102)]
SEEK_INVESTIGATIONS = [{"id": 101, "title": "Alder Investigation", "description": None},
                       {"id": 102, "title": "Birch Investigation", "description": None}]
SCOPE = w.paper_scope(SEEK_STUDIES, SEEK_INVESTIGATIONS)


def test_in_study_statements_hold_the_rule():
    replace = q.REPLACE_SEEK_IN_STUDY
    assert replace.lstrip().startswith("CYPHER 25")
    for part in ("elementId(e) = eid", "st.seek_study_id IS NOT NULL", "NOT st.seek_study_id IN r.study_ids",
                 "p.seek_study_id IS NULL", "MERGE (s)-[:IN_STUDY]->(st)", "RETURN count(s) AS samples",
                 "[sid IN r.study_ids WHERE NOT sid IN withhold]",
                 "CASE WHEN paper AND NOT r.paper THEN r.study_ids ELSE r.withhold END", "AS paper_added"):
        assert part in replace, part
    assert "DETACH" not in replace
    statement = q.MERGE_SEEK_STUDIES
    assert statement.lstrip().startswith("CYPHER 25")
    assert "MERGE (st:Study {seek_study_id: r.study_id})" in statement
    assert "SET st.title = r.title, st.description = r.description" in statement
    assert "DELETE old" in statement and "MERGE (st)-[:IN_INVESTIGATION]->(i)" in statement
    assert "AS investigation_missing" in statement
    assert not hasattr(q, "SET_SEEK_STUDIES")
    assert "investigations: [(st)-[:IN_INVESTIGATION]->(i:Investigation)" in q.SAMPLE_STUDIES_OF
    assert "ORDER BY s.id LIMIT $limit" in q.SAMPLE_STUDIES_PAGE and "s.id > $after" in q.SAMPLE_STUDIES_PAGE
    assert "count(*) AS nodes" in q.STUDY_SEEK_ID_DUPLICATES and "nodes > 1" in q.STUDY_SEEK_ID_DUPLICATES
    assert "NOT x:Sample" in q.ORPHAN_IN_STUDY


def test_write_seek_study_nodes_takes_seeks_title_description_and_investigation():
    g = StudyGraph()
    old_inv, new_inv = g.add_investigation(101, "Alder Investigation"), g.add_investigation(102, "Birch Investigation")
    node = g.add_study(seek_study_id=7, title="Old title", description="Old text", investigation=old_inv)
    counts = w.write_seek_study_nodes(g, DB, [_study_row(7, "Alder", "New text", 102), _study_row(8, "Birch")])
    assert counts == {"seek_studies": 2, "seek_study_nodes_written": 2, "seek_study_investigation_missing": 0}
    assert g.studies[node] == {"seek_study_id": 7, "title": "Alder", "description": "New text"}
    assert g.in_investigation[node] == [new_inv]
    (created,) = g.studies_by_seek(8)
    assert g.investigation_ids_of(created) == [101]


def test_a_study_with_no_investigation_loses_its_link():
    g = StudyGraph()
    inv = g.add_investigation(101, "Alder Investigation")
    node = g.add_study(seek_study_id=7, title="Alder", description="text", investigation=inv)
    counts = w.write_seek_study_nodes(g, DB, [_study_row(7, "Alder", None, None)])
    assert g.in_investigation[node] == []
    assert "description" not in g.studies[node]      # SEEK's null removes the property
    assert counts["seek_study_investigation_missing"] == 0


def test_a_study_in_an_investigation_with_no_node_gets_it_written_first():
    """A study created in SEEK in a new investigation: the investigation's node and its IN_PROJECT come first, so the
    study is linked when its first samples are written, not at the next nightly."""
    g = StudyGraph()
    g.add_project(5, "Poplar")
    tables = w.SeekTables(studies=(_study_row(9, "Poplar Study", "About", 103),),
                          investigations=({"id": 103, "title": "Poplar Investigation", "description": None},
                                          {"id": 104, "title": "Not named", "description": None}),
                          investigation_projects=({"investigation_id": 103, "project_id": 5},
                                                  {"investigation_id": 104, "project_id": 5}))
    counts = w.write_seek_study_nodes(g, DB, tables.studies, tables=tables)
    (node,) = g.studies_by_seek(9)
    inv = g.investigation_by_id(103)
    assert g.in_investigation[node] == [inv] and g.inv_projects[inv] == {5}
    assert g.investigations[inv] == {"id": 103, "title": "Poplar Investigation", "project_id": 5}
    assert g.investigation_by_id(104) is None                           # only the investigations named
    assert (counts["investigations_written"], counts["investigation_links"],
            counts["seek_study_investigation_missing"]) == (1, 1, 0)
    assert [c.query for c in g.writes()] == [q.MERGE_INVESTIGATIONS, q.MERGE_INVESTIGATION_IN_PROJECT,
                                             q.MERGE_SEEK_STUDIES]
    assert q.DELETE_INVESTIGATION_IN_PROJECT not in [c.query for c in g.calls]


def test_a_study_whose_investigation_is_still_missing_is_counted():
    g = StudyGraph()
    counts = w.write_seek_study_nodes(g, DB, [_study_row(9, "Poplar Study", None, 103)])
    assert counts["seek_study_investigation_missing"] == 1
    assert g.in_investigation[g.studies_by_seek(9)[0]] == []


def _in_study_world():
    g = StudyGraph()
    inv = g.add_investigation(101, "Alder Investigation")
    seek1 = g.add_study(seek_study_id=1, title="Alder", investigation=inv)
    seek2 = g.add_study(seek_study_id=2, title="Birch", investigation=inv)
    paper = g.add_study(id=9, title="A paper", DOI="10.9999/p9", investigation=inv)
    for sid in (1001, 1002, 1003, 1004):
        g.add_sample(sid)
    g.link(1001, seek2)             # SEEK holds 1 only: 2 is stale, 1 is missing
    g.link(1002, seek1)             # SEEK places it in no study: kept
    g.link(1003, paper)             # a paper sample: SEEK's 2 (its own investigation) is withheld, the paper link kept
    g.link(1003, seek1)             # ...but its stale SEEK link still goes
    return g, {"seek1": seek1, "seek2": seek2, "paper": paper}


def test_replace_seek_in_study_follows_seek_and_archives_before_it_deletes(tmp_path):
    g, _ = _in_study_world()
    archive = tmp_path / "in_study_removed.tsv"
    seen = []
    g.before_write = lambda query, params: seen.append(archive.exists())
    rows = [{"sample_id": 1001, "study_ids": [1]}, {"sample_id": 1002, "study_ids": []},
            {"sample_id": 1003, "study_ids": [2]}, {"sample_id": 1004, "study_ids": [2, 3]},
            {"sample_id": 1099, "study_ids": [1]}]
    counts = w.replace_seek_in_study(g, DB, rows, remove=True, archive_path=str(archive), scope=SCOPE,
                                     path="studies")

    assert g.keys_of(1001) == {("seek", 1)}
    assert g.keys_of(1002) == {("seek", 1)}
    assert g.keys_of(1003) == {("id", 9)}
    assert g.keys_of(1004) == {("seek", 2)}
    assert seen == [True]
    lines = archive.read_text(encoding="utf-8").splitlines()
    assert lines[0] == w.IN_STUDY_ARCHIVE_HEADER.rstrip("\n")
    assert sorted(line.split("\t")[:3] + line.split("\t")[4:] for line in lines[1:]) == [
        ["1001", "2", "", "studies"], ["1003", "1", "", "studies"]]
    assert counts == {"in_study_rows": 5, "in_study_added": 2, "in_study_removed": 2, "in_study_stale": 0,
                      "in_study_kept_no_seek_study": 1, "in_study_paper_samples": 1, "in_study_withheld": 1,
                      "in_study_paper_links_written": 0, "in_study_paper_investigation_unknown": 0,
                      "in_study_samples_missing": 1, "in_study_studies_missing": 1}


def test_without_remove_nothing_is_archived_or_deleted(tmp_path):
    g, _ = _in_study_world()
    counts = w.replace_seek_in_study(g, DB, [{"sample_id": 1001, "study_ids": [1]}], remove=False,
                                     archive_path=str(tmp_path / "a.tsv"), scope=SCOPE)
    assert g.keys_of(1001) == {("seek", 1), ("seek", 2)}
    assert counts["in_study_stale"] == 1 and counts["in_study_removed"] == 0
    assert not (tmp_path / "a.tsv").exists()


def test_removing_with_no_archive_path_raises_before_any_write():
    g, _ = _in_study_world()
    with pytest.raises(ValueError, match="archive"):
        w.replace_seek_in_study(g, DB, [{"sample_id": 1001, "study_ids": [1]}], remove=True, archive_path=None,
                                scope=SCOPE)
    assert g.writes() == []


def test_replace_seek_in_study_takes_its_scope_as_a_required_keyword(tmp_path):
    g, _ = _in_study_world()
    with pytest.raises(TypeError):
        w.replace_seek_in_study(g, DB, [{"sample_id": 1001, "study_ids": [1]}], remove=False, archive_path=None)


def test_parallel_stale_edges_are_archived_and_removed(tmp_path):
    g, nodes = _in_study_world()
    g.link(1001, nodes["seek2"])     # a second, parallel stale edge
    archive = tmp_path / "a.tsv"
    counts = w.replace_seek_in_study(g, DB, [{"sample_id": 1001, "study_ids": [1]}], remove=True,
                                     archive_path=str(archive), scope=SCOPE)
    assert g.keys_of(1001) == {("seek", 1)}
    assert counts["in_study_removed"] == 2
    assert len(archive.read_text(encoding="utf-8").splitlines()) == 3


def test_replace_seek_in_study_works_in_chunks(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "REL_CHUNK", 2)
    g = StudyGraph()
    g.add_study(seek_study_id=1, title="Alder")
    for sid in range(1001, 1006):
        g.add_sample(sid)
    counts = w.replace_seek_in_study(g, DB, [{"sample_id": s, "study_ids": [1]} for s in range(1001, 1006)],
                                     remove=True, archive_path=str(tmp_path / "a.tsv"), scope=SCOPE)
    assert len(g.of(q.REPLACE_SEEK_IN_STUDY)) == 3 and len(g.of(q.SAMPLE_STUDIES_OF)) == 3
    assert counts["in_study_added"] == 5


def test_sample_study_pages_walk_every_numeric_sample_by_keyset():
    g = StudyGraph()
    for sid in (1003, 1001, 1005, 1002, 1004):
        g.add_sample(sid)
    g.add_sample("legacy-1")         # a non-numeric id is never paged
    pages = list(w.sample_study_pages(g, DB, page=2))
    assert [[r["id"] for r in page] for page in pages] == [[1001, 1002], [1003, 1004], [1005]]
    assert [c.params["after"] for c in g.of(q.SAMPLE_STUDIES_PAGE)][1:] == [1002, 1004]
    assert all(c.read for c in g.calls)


def test_duplicates_and_orphan_links_are_read_only_counts():
    g = StudyGraph()
    a, b = g.add_study(seek_study_id=4, title="x"), g.add_study(seek_study_id=4, title="y")
    g.link(g.add_sample(1001, label="OrphanSample"), a)
    assert w.seek_study_id_duplicates(g, DB) == [{"seek_study_id": 4, "nodes": 2}]
    assert w.orphan_in_study(g, DB) == 1
    assert all(c.read for c in g.calls)


# --- a paper sample's SEEK links are withheld only inside its own investigation (operator ruling SHARED SAMPLES) ----

def _two_investigation_world():
    g = StudyGraph()
    alder = g.add_investigation(101, "Alder Investigation")
    birch = g.add_investigation(102, "Birch Investigation")
    bucket = g.add_study(seek_study_id=1, title="Alder Unpublished", investigation=alder)
    shared = g.add_study(seek_study_id=3, title="Birch Study", investigation=birch)
    paper = g.add_study(id=9, title="A paper", DOI="10.9999/p9", investigation=alder)
    g.add_sample(1003)
    g.link(1003, paper)
    return g, {"bucket": bucket, "shared": shared, "paper": paper}


def test_a_paper_sample_links_another_investigations_study_and_never_its_bucket(tmp_path):
    g, _ = _two_investigation_world()
    counts = w.replace_seek_in_study(g, DB, [{"sample_id": 1003, "study_ids": [1, 3]}], remove=True,
                                     archive_path=str(tmp_path / "a.tsv"), scope=SCOPE)
    assert g.keys_of(1003) == {("id", 9), ("seek", 3)}
    assert [c.params["rows"] for c in g.of(q.REPLACE_SEEK_IN_STUDY)] == [[
        {"sample_id": 1003, "study_ids": [1, 3], "withhold": [1], "paper": True, "remove": []}]]
    assert (counts["in_study_added"], counts["in_study_paper_samples"], counts["in_study_withheld"],
            counts["in_study_paper_links_written"], counts["in_study_paper_investigation_unknown"]) == (1, 1, 1, 1, 0)


def _paper_links(*investigations):
    """One paper link per entry: None for a Study with no IN_INVESTIGATION, else a list of (id, title)."""
    return [{"element_id": f"e:{n}", "seek_study_id": None, "id": 90 + n,
             "investigations": [] if invs is None else [{"id": i, "title": t} for i, t in invs]}
            for n, invs in enumerate(investigations)]


@pytest.mark.parametrize("links, own, written, withheld", [
    (_paper_links(None), None, (), (1, 3)),                                                     # no IN_INVESTIGATION
    (_paper_links([(101, "Alder Investigation"), (102, "Birch Investigation")]), None, (), (1, 3)),   # two
    (_paper_links([(105, "Alder Investigation")]), None, (), (1, 3)),                          # an id SEEK lacks
    (_paper_links([(101, "Alder investigation 2")]), None, (), (1, 3)),                        # a title that differs
    (_paper_links([(101, "  ALDER investigation ")]), frozenset({101}), (3,), (1,)),            # case and spaces aside
    (_paper_links([(101, "Alder Investigation")], [(102, "Birch Investigation")]), frozenset({101, 102}), (), (1, 3)),
], ids=["no investigation", "two", "id seek lacks", "title differs", "case and spaces", "two papers"])
def test_paper_split_withholds_only_the_papers_own_investigations(links, own, written, withheld):
    split = w.paper_split(links, [3, 1], SCOPE)
    assert (split.paper, split.own, split.written, split.withheld) == (True, own, written, withheld)


def test_paper_split_withholds_a_study_seek_files_under_no_investigation():
    scope = w.paper_scope([_study_row(4, "Loose", None, None)], SEEK_INVESTIGATIONS)
    split = w.paper_split(_paper_links([(101, "Alder Investigation")]), [4], scope)
    assert (split.written, split.withheld) == ((), (4,))


def test_paper_split_links_every_study_of_a_sample_that_is_not_a_paper_sample():
    links = [{"element_id": "e:1", "seek_study_id": 1, "id": None, "investigations": []}]
    assert w.paper_split(links, [3, 1], SCOPE) == w.PaperSplit(False, None, (1, 3), ())


def test_an_unknown_paper_investigation_withholds_every_link_and_is_counted(tmp_path):
    g, nodes = _two_investigation_world()
    g.in_investigation[nodes["paper"]] = []
    counts = w.replace_seek_in_study(g, DB, [{"sample_id": 1003, "study_ids": [1, 3]}], remove=False,
                                     archive_path=None, scope=SCOPE)
    assert g.keys_of(1003) == {("id", 9)}
    assert (counts["in_study_withheld"], counts["in_study_paper_investigation_unknown"]) == (2, 1)


def test_a_sample_that_became_a_paper_sample_after_the_read_withholds_every_study(tmp_path):
    g, nodes = _two_investigation_world()
    g.add_sample(1004)
    g.link(1004, nodes["paper"])
    rows = [{"sample_id": 1004, "study_ids": [1, 3], "withhold": [], "paper": False, "remove": []}]
    g.execute_query(q.REPLACE_SEEK_IN_STUDY, {"rows": rows})
    assert g.keys_of(1004) == {("id", 9)}


def test_a_shared_link_seek_no_longer_holds_is_removed_like_any_other(tmp_path):
    g, nodes = _two_investigation_world()
    g.link(1003, nodes["shared"])
    archive = tmp_path / "a.tsv"
    w.replace_seek_in_study(g, DB, [{"sample_id": 1003, "study_ids": [1]}], remove=True, archive_path=str(archive),
                            scope=SCOPE)
    assert g.keys_of(1003) == {("id", 9)}
    assert archive.read_text(encoding="utf-8").splitlines()[1].split("\t")[:2] == ["1003", "3"]
    g2, nodes2 = _two_investigation_world()
    g2.link(1003, nodes2["shared"])
    counts = w.replace_seek_in_study(g2, DB, [{"sample_id": 1003, "study_ids": [1]}], remove=False,
                                     archive_path=None, scope=SCOPE)
    assert counts["in_study_stale"] == 1 and g2.keys_of(1003) == {("id", 9), ("seek", 3)}


# --- the by-id path: write_seek_studies ----------------------------------------------------------------------------

def _link(sample_id, study_id, title="Alder", description=None, investigation_id=101):
    return {"sample_id": sample_id, "study_id": study_id, "study_title": title,
            "study_description": description, "investigation_id": investigation_id}


TABLES = w.SeekTables(studies=tuple(SEEK_STUDIES), investigations=tuple(SEEK_INVESTIGATIONS))


def test_write_seek_studies_writes_the_nodes_then_one_row_per_written_sample(tmp_path):
    g = StudyGraph()
    inv = g.add_investigation(101, "Alder Investigation")
    paper = g.add_study(id=9, title="A paper", DOI="10.9999/p9", investigation=inv)
    old = g.add_study(seek_study_id=2, title="Birch", investigation=inv)
    for sid in (1001, 1002, 1003):
        g.add_sample(sid)
    g.link(1002, old)           # SEEK now places 1002 in no study: its link is kept and counted
    g.link(1003, paper)         # a paper sample: its SEEK link to its own investigation's study is withheld
    counts = w.write_seek_studies(g, DB, [_link(1001, 1, "Alder", "About alder"), _link(1003, 1)],
                                  [1001, 1002, 1003], remove=True, archive_path=str(tmp_path / "a.tsv"),
                                  tables=TABLES)
    (node,) = g.studies_by_seek(1)
    assert g.studies[node] == {"seek_study_id": 1, "title": "Alder", "description": "About alder"}
    assert [c.params["rows"] for c in g.of(q.REPLACE_SEEK_IN_STUDY)] == [[
        {"sample_id": 1001, "study_ids": [1], "withhold": [], "paper": False, "remove": []},
        {"sample_id": 1002, "study_ids": [], "withhold": [], "paper": False, "remove": []},
        {"sample_id": 1003, "study_ids": [1], "withhold": [1], "paper": True, "remove": []}]]
    assert g.keys_of(1001) == {("seek", 1)} and g.keys_of(1002) == {("seek", 2)} and g.keys_of(1003) == {("id", 9)}
    assert (counts["seek_studies"], counts["in_study_added"], counts["in_study_kept_no_seek_study"],
            counts["in_study_paper_samples"], counts["in_study_withheld"]) == (1, 1, 1, 1, 1)


def test_a_study_whose_samples_are_all_paper_samples_still_gets_its_node():
    g = StudyGraph()
    alder = g.add_investigation(101, "Alder Investigation")
    paper = g.add_study(id=9, title="A paper", DOI="10.9999/p9", investigation=alder)
    g.add_sample(1001)
    g.link(1001, paper)
    counts = w.write_seek_studies(g, DB, [_link(1001, 1)], [1001], remove=False, archive_path=None, tables=TABLES)
    assert len(g.studies_by_seek(1)) == 1
    assert counts["in_study_withheld"] == 1 and g.keys_of(1001) == {("id", 9)}


def test_write_seek_studies_removes_a_moved_samples_old_link_when_asked(tmp_path):
    g = StudyGraph()
    old = g.add_study(seek_study_id=2, title="Birch")
    g.add_sample(1001)
    g.link(1001, old)
    archive = tmp_path / "in_study_removed.tsv"
    counts = w.write_seek_studies(g, DB, [_link(1001, 1)], [1001], remove=True, archive_path=str(archive),
                                  tables=TABLES)
    assert g.keys_of(1001) == {("seek", 1)}
    assert counts["in_study_removed"] == 1 and archive.read_text().splitlines()[1].split("\t")[4] == "by_id"


def test_one_by_id_sync_links_a_shared_paper_sample_to_the_destination_study_only(tmp_path):
    """The production shape: a paper sample shared into another investigation's study."""
    g, _ = _two_investigation_world()
    w.write_seek_studies(g, DB, [_link(1003, 1, "Alder Unpublished"), _link(1003, 3, "Birch Study",
                                                                             investigation_id=102)],
                         [1003], remove=True, archive_path=str(tmp_path / "a.tsv"), tables=TABLES)
    assert g.keys_of(1003) == {("id", 9), ("seek", 3)}


def test_the_by_id_path_writes_a_new_investigation_before_its_study():
    g = StudyGraph()
    g.add_project(5, "Poplar")
    g.add_sample(1001)
    tables = w.SeekTables(studies=(_study_row(9, "Poplar Study", None, 103),),
                          investigations=({"id": 103, "title": "Poplar Investigation", "description": None},),
                          investigation_projects=({"investigation_id": 103, "project_id": 5},))
    counts = w.write_seek_studies(g, DB, [_link(1001, 9, "Poplar Study", investigation_id=103)], [1001],
                                  remove=False, archive_path=None, tables=tables)
    (node,) = g.studies_by_seek(9)
    assert g.investigation_ids_of(node) == [103] and g.keys_of(1001) == {("seek", 9)}
    assert (counts["investigations_written"], counts["seek_study_investigation_missing"]) == (1, 0)


# --- missing Project nodes, and a sample that is not fully linked -------------------------------------------------

def test_write_samples_leaves_the_hash_of_a_half_linked_sample_null():
    assert ("SET s.source_hash = CASE WHEN typed = 0 OR linked < size(coalesce(r.props.project_ids, [])) THEN null"
            in q.WRITE_SAMPLES)
    assert q.WRITE_SAMPLES.index("AS linked") < q.WRITE_SAMPLES.index("SET s.source_hash") < \
        q.WRITE_SAMPLES.index("RETURN count(s) AS written")


def test_merge_missing_projects_writes_only_the_missing_ones_and_counts_the_ones_seek_lacks():
    g = StudyGraph()
    g.add_project(2, "Kept")
    counts = w.merge_missing_projects(g, DB, [2, 5, 77, 5], [{"id": 2, "title": "Alder"}, {"id": 5, "title": "Birch"}])
    assert counts == {"projects_written_for_links": 1, "project_ids_not_in_seek": 1}
    assert g.projects == {2: {"id": 2, "title": "Kept"}, 5: {"id": 5, "title": "Birch"}}
    assert [c.query for c in g.writes()] == [q.MERGE_PROJECTS]
    assert q.DELETE_GONE_PROJECTS not in [c.query for c in g.calls]


def test_merge_missing_projects_reads_nothing_for_no_id():
    g = StudyGraph()
    assert w.merge_missing_projects(g, DB, [], []) == {"projects_written_for_links": 0, "project_ids_not_in_seek": 0}
    assert g.calls == []


def test_a_new_investigations_project_is_written_before_its_link():
    g = StudyGraph()
    tables = w.SeekTables(studies=(_study_row(9, "Poplar Study", None, 103),),
                          investigations=({"id": 103, "title": "Poplar Investigation", "description": None},),
                          investigation_projects=({"investigation_id": 103, "project_id": 5},),
                          projects=({"id": 5, "title": "Poplar"},))
    counts = w.write_seek_study_nodes(g, DB, tables.studies, tables=tables)
    inv = g.investigation_by_id(103)
    assert g.inv_projects[inv] == {5} and g.projects[5] == {"id": 5, "title": "Poplar"}
    assert (counts["investigation_projects_written"], counts["investigation_links"]) == (1, 1)
    writes = [c.query for c in g.writes()]
    assert writes.index(q.MERGE_PROJECTS) < writes.index(q.MERGE_INVESTIGATION_IN_PROJECT)


# --- renames and filled protocols need no approval (the operator's RELABEL ruling) ---------------------------------

REFRESH_LABELS = {"assay_id": 5, "internal_assay_id": 99, "internal_assay_title": "New name",
                  "internal_assay_ids": [99], "internal_assay_titles": ["New name"], "protocol_id": 7,
                  "protocol_title": "SOP 7"}


def _refresh_row(**stored):
    return {"child_id": 11, "parent_id": 10, "labels": dict(REFRESH_LABELS),
            "stored": dict(REFRESH_LABELS, **stored)}


def test_write_edge_label_refreshes_sends_the_compare_and_set_statement():
    driver = FakeDriver(lambda query, params: [{"matched": 2, "written": 1, "pairs": 2}]
                        if query == q.WRITE_EDGE_LABELS_CHANGED else [])
    rows = [_refresh_row(internal_assay_title="Old name", internal_assay_titles=["Old name"]),
            dict(_refresh_row(protocol_id=None, protocol_title=None), child_id=12)]
    counts = w.write_edge_label_refreshes(driver, "neo4j", rows)
    (call,) = driver.calls_of(q.WRITE_EDGE_LABELS_CHANGED)
    assert [r["stored"]["internal_assay_title"] for r in call.params["rows"]] == ["Old name", "New name"]
    assert counts == {"labels_refresh_rows": 2, "labels_refreshed": 1, "labels_refresh_skipped_changed": 1,
                      "labels_refresh_edges_missing": 0}


@pytest.mark.parametrize("stored", [{"internal_assay_id": 98}, {"protocol_id": 6}, {}],
                         ids=["another internal assay", "another protocol", "equal"])
def test_write_edge_label_refreshes_refuses_any_other_class_before_sending(stored):
    driver = FakeDriver()
    with pytest.raises(ValueError, match="approval"):
        w.write_edge_label_refreshes(driver, "neo4j", [_refresh_row(**stored)])
    assert driver.calls == []


def test_write_edge_label_refreshes_refuses_a_rename_that_would_add_an_assay():
    """An edge stored with singular fields only: renaming its title would also write the plural lists, and a second
    internal assay the pair shares would reach the edge without approval."""
    computed = dict(REFRESH_LABELS, internal_assay_ids=[99, 120], internal_assay_titles=["New name", "Other assay"])
    stored = dict(REFRESH_LABELS, internal_assay_title="Old name", internal_assay_ids=None,
                  internal_assay_titles=None)
    driver = FakeDriver()
    with pytest.raises(ValueError, match="changed label needs the operator's approval"):
        w.write_edge_label_refreshes(driver, "neo4j",
                                     [{"child_id": 11, "parent_id": 10, "labels": computed, "stored": stored}])
    assert driver.calls == []


def test_write_edge_label_refreshes_needs_the_stored_values():
    row = _refresh_row(internal_assay_title="Old name")
    del row["stored"]
    with pytest.raises(ValueError, match="stored"):
        w.write_edge_label_refreshes(FakeDriver(), "neo4j", [row])


# --- the studies tool's share check (tool spec 16.8) -------------------------------------------------------------

def test_the_share_check_is_one_read_only_statement_in_cypher_py():
    text = q.SHARE_CHECK
    assert "OPTIONAL MATCH (s:Sample {id: id})" in text and "$project_id IN s.project_ids" in text
    assert "(:Study {seek_study_id: $study_id})" in text and "WHERE p.seek_study_id IS NULL" in text
    for word in ("MERGE", "CREATE", "SET ", "DELETE", "REMOVE"):
        assert word not in text, word


def test_the_share_check_counts_and_caps_the_missing_ids(monkeypatch):
    monkeypatch.setattr(w, "REL_CHUNK", 2)
    rows = {1: (True, True, True, True, False), 2: (True, True, True, False, True), 3: (True, True, True, True, True),
            4: (False, False, False, False, False)}

    def respond(query, params):
        assert query == q.SHARE_CHECK and (params["project_id"], params["study_id"]) == (5, 40)
        keys = ("found", "has_project", "in_project", "in_study", "paper")
        return [{"id": i, **dict(zip(keys, rows[i]))} for i in params["ids"]]

    driver = FakeDriver(respond)
    got = w.share_graph_check(driver, "neo4j", [4, 3, 2, 1, 1], project_id=5, study_id=40)
    assert got == {"ids": 4, "found": 3, "has_project": 3, "in_project": 3, "in_study": 2, "paper": 2,
                   "paper_in_study": 1, "missing_ids": [4]}
    assert len(driver.calls) == 2 and all(c.kwargs.get("routing_") is not None for c in driver.calls)

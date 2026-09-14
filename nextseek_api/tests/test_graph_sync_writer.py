"""The Neo4j writer for graph schema v1.1 (nextseek_api/graph_sync/writer.py, cypher.py).

A fake driver records every ``execute_query`` call; no Neo4j is needed.
"""
import hashlib
import os
from types import SimpleNamespace

import pytest

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import writer as w
from nextseek_api.graph_sync.projection import SampleProjection


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


def test_constraints_are_the_v11_set():
    joined = "\n".join(q.CONSTRAINTS_V11)
    for prop in ("(s:Sample) REQUIRE s.id", "(t:SampleType) REQUIRE t.id", "(t:SampleType) REQUIRE t.title",
                 "(t:SampleType) REQUIRE t.label", "(a:Attribute) REQUIRE a.key", "(a:Attribute) REQUIRE a.id",
                 "(p:Project) REQUIRE p.id", "(p:Person) REQUIRE p.id", "(s:Study) REQUIRE s.id",
                 "(i:Investigation) REQUIRE i.id"):
        assert prop in joined
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
    assert counts == {"sample_types_written": 2, "graph_only_sample_types": ["OLD"]}


def test_write_sample_types_refuses_a_title_held_by_another_id():
    driver = FakeDriver(lambda query, params: [{"title": "TIS", "graph_id": 7, "mysql_id": 26}]
                        if query == q.SAMPLE_TYPE_TITLE_CONFLICTS else [])
    with pytest.raises(ValueError, match="TIS"):
        w.write_sample_types(driver, "neo4j", [{"id": 26, "title": "TIS", "label": "T_TIS"}])
    assert q.MERGE_SAMPLE_TYPES not in driver.queries()


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
    assert counts == {"investigations_written": 2, "investigation_links": 3, "investigation_links_dropped": 0}


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


def test_write_seek_studies_skips_samples_in_a_paper_level_study():
    links = [{"sample_id": 1, "study_id": 40, "study_title": "S40", "investigation_id": 30},
             {"sample_id": 2, "study_id": 40, "study_title": "S40", "investigation_id": 30},
             {"sample_id": 2, "study_id": 41, "study_title": "S41", "investigation_id": 30},
             {"sample_id": 3, "study_id": 42, "study_title": "S42", "investigation_id": None}]

    def responder(query, params):
        if query == q.SAMPLES_IN_PAPER_STUDIES:
            return [{"id": 1}]
        if query == q.MERGE_SEEK_IN_STUDY:
            return [{"linked": len(params["rows"])}]
        return []

    driver = FakeDriver(responder)
    counts = w.write_seek_studies(driver, "neo4j", links)
    studies = driver.calls_of(q.MERGE_SEEK_STUDIES)[0].params["rows"]
    assert studies == [{"study_id": 40, "title": "S40", "investigation_id": 30},
                       {"study_id": 41, "title": "S41", "investigation_id": 30},
                       {"study_id": 42, "title": "S42", "investigation_id": None}]
    edges = driver.calls_of(q.MERGE_SEEK_IN_STUDY)[0].params["rows"]
    assert edges == [{"sample_id": 2, "study_id": 40}, {"sample_id": 2, "study_id": 41},
                     {"sample_id": 3, "study_id": 42}]
    assert counts == {"seek_studies": 3, "in_study_written": 3, "in_study_dropped": 0,
                      "samples_skipped_in_paper_study": 1}


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


def test_write_graphmeta_stamps_the_schema_version():
    driver = FakeDriver()
    assert w.write_graphmeta(driver, "neo4j", "abc") == {"schema_version": "1.1", "catalog_hash": "abc"}
    (call,) = driver.calls
    assert call.query == q.WRITE_GRAPHMETA
    assert call.params == {"schema_version": "1.1", "catalog_hash": "abc"}


# --- retries -------------------------------------------------------------------------------------

def test_a_transient_error_is_retried(monkeypatch):
    from neo4j.exceptions import TransientError

    from nextseek_api.batch_upload import neo4j_sync

    monkeypatch.setattr(neo4j_sync.time, "sleep", lambda s: None)
    attempts = []

    def responder(query, params):
        attempts.append(query)
        if len(attempts) == 1:
            raise TransientError("deadlock")
        return []

    driver = FakeDriver(responder)
    w.ensure_fulltext(driver, "neo4j")
    assert len(attempts) == 2


def test_reads_are_routed_as_reads():
    driver = FakeDriver(lambda query, params: [{"n": 0}])
    w.archive_and_drop_child_of(driver, "neo4j", os.devnull, set())
    (call,) = driver.calls
    from neo4j import RoutingControl
    assert call.kwargs.get("routing_") == RoutingControl.READ

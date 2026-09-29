"""The parity harness's fixtures (gate E) and how it reads each one.

scripts/graph_search/parity.py compares graph_search with advanced_search per query and scope. A compat fixture is
one of three forms:

- ``body`` only: the same body goes to both engines (advanced_search's view and graph_search);
- ``body`` plus ``graph_body``: advanced_search's view takes ``body``, graph_search takes ``graph_body``. This is how the
  Sample Search page's query text is compared: advanced_search parses it inside ``filter_searchText``, graph_search
  in ``extensions.query``;
- ``engine: "FILTERING"`` plus ``filters`` and ``body``: advanced_search's side is the Simple box's own path
  (SampleSearchMixin.searchAdvanced with searchType FILTERING, what /seek/samples/searching/ ran), graph_search's is
  ``body``.

Declared difference 2 (PubMed syntax inside filter_searchText) excuses only the first form. The fixture files are
checked here without a database: every body validates, every query text parses.
"""
import importlib.util
import json
import logging
from pathlib import Path

import pytest

from nextseek_api.graph_search import text_query
from nextseek_api.models import GraphSearchRequest, SampleAdvancedSearchRequest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = [ROOT / "scripts" / "graph_search" / "queries.json",
            ROOT / "scripts" / "graph_search" / "synthetic_queries.json"]
FILTER_KEYS = {"sampletype", "attribute", "filter_rule", "filter_valueFrom", "filter_valueTo"}
RULES = {"No Filter", "Contain", "Not Contain", "Equal", "Not Equal", "Less", "Greater", "Before", "After",
         "Between", "True", "False"}


@pytest.fixture(scope="module")
def parity():
    """scripts/graph_search/parity.py, imported by path; its import sets the root logger's level, put back after."""
    root = logging.getLogger()
    level = root.level
    spec = importlib.util.spec_from_file_location("gs_parity", ROOT / "scripts" / "graph_search" / "parity.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        root.setLevel(level)
    return module


def _entries():
    for path in FIXTURES:
        for entry in json.loads(path.read_text(encoding="utf-8")):
            yield pytest.param(entry, id=f"{path.stem}:{entry['name']}")


def test_both_fixture_files_exist_and_names_are_unique():
    for path in FIXTURES:
        names = [q["name"] for q in json.loads(path.read_text(encoding="utf-8"))]
        assert names and len(names) == len(set(names)), path


@pytest.mark.parametrize("entry", _entries())
def test_every_fixture_is_one_of_the_forms_and_validates(entry):
    assert entry["kind"] in ("compat", "graph_only")
    assert set(entry) <= {"name", "kind", "body", "graph_body", "engine", "filters", "note"}
    if entry.get("engine") is not None:
        assert entry["kind"] == "compat" and entry["engine"] == "FILTERING"
        assert set(entry["filters"]) == FILTER_KEYS and entry["filters"]["filter_rule"] in RULES
        assert "graph_body" not in entry
        graph = GraphSearchRequest.model_validate(entry["body"])
    elif "graph_body" in entry:
        assert entry["kind"] == "compat"
        SampleAdvancedSearchRequest.model_validate(entry["body"])
        graph = GraphSearchRequest.model_validate(entry["graph_body"])
    else:
        graph = GraphSearchRequest.model_validate(entry["body"])
    if graph.extensions is not None and graph.extensions.query:
        text_query.parse(graph.extensions.query)


def test_the_new_operators_each_have_a_compat_fixture_in_both_files():
    for path in FIXTURES:
        compat = [q for q in json.loads(path.read_text(encoding="utf-8")) if q["kind"] == "compat"]
        queries = " ".join(q["graph_body"]["extensions"]["query"] for q in compat if "graph_body" in q)
        rules = {q["filters"]["filter_rule"] for q in compat if q.get("engine")}
        assert " NOT " in queries and " OR " in queries and "(" in queries and "[" in queries, path
        assert "Not Contain" in rules, path
    synthetic = json.loads(FIXTURES[1].read_text(encoding="utf-8"))
    assert {"True", "False"} <= {q["filters"]["filter_rule"] for q in synthetic if q.get("engine")}


def test_sides_names_what_each_engine_is_given(parity):
    plain = {"name": "p", "kind": "compat", "body": {"filter_searchText": "lung"}}
    pubmed = {"name": "q", "kind": "compat", "body": {"filter_searchText": "lung NOT granuloma"},
              "graph_body": {"filter_searchText": "", "extensions": {"query": "lung NOT granuloma"}}}
    filtering = {"name": "f", "kind": "compat", "engine": "FILTERING",
                 "filters": {"sampletype": "TIS", "attribute": "Organ", "filter_rule": "Not Contain",
                             "filter_valueFrom": "Lung", "filter_valueTo": ""},
                 "body": {"filter_searchText": "", "extensions": {"where": [
                     {"sample_type": "TIS", "attribute": "Organ", "op": "NOT CONTAINS", "value": "Lung"}]}}}
    assert parity.sides(plain) == (plain["body"], plain["body"], None)
    assert parity.sides(pubmed) == (pubmed["graph_body"], pubmed["body"], None)
    assert parity.sides(filtering) == (filtering["body"], None, filtering["filters"])


def test_declared_difference_2_excuses_only_pubmed_text_sent_to_both_engines(parity):
    assert parity._declared_for({"body": {"filter_searchText": "lung NOT granuloma"}}) == 2
    assert parity._declared_for({"body": {"filter_searchText": "lung"}}) is None
    assert parity._declared_for({"body": {"filter_searchText": "lung NOT granuloma"},
                                 "graph_body": {"filter_searchText": "",
                                                "extensions": {"query": "lung NOT granuloma"}}}) is None
    assert parity._declared_for({"engine": "FILTERING", "filters": {}, "body": {"filter_searchText": ""}}) is None


def test_filtering_filters_are_what_the_simple_box_sent(parity):
    spec = {"sampletype": "TIS", "attribute": "Organ", "filter_rule": "Not Contain", "filter_valueFrom": "Lung",
            "filter_valueTo": ""}
    assert parity.filtering_filters(spec, resolve=lambda title: {"TIS": "26"}.get(title)) == {
        "sampletype_id": 26, "attribute": "Organ", "filter_rule": "Not Contain", "filter_valueFrom": "Lung",
        "filter_valueTo": ""}
    with pytest.raises(parity.HarnessError, match="NOPE"):
        parity.filtering_filters(dict(spec, sampletype="NOPE"), resolve=lambda title: None)


def test_filtering_kept_reproduces_the_simple_box_paths_index_slip(parity):
    """seek/sample/queries.py _filterSamples skips `index += 1` after a row that passes the rule but holds no value
    for the attribute, so every later row is judged by the rule's result on the row before it (declared
    difference 7). ``slip=False`` is the same loop without the slip: what graph_search answers."""
    rows = [(1, {"Organ": "Lung"}), (2, {"Organ": None}), (3, {"Organ": "Lung"}), (4, {"Organ": "Heart"}),
            (5, {})]
    passes = [False, True, False, True, True]  # Not Contain "Lung": None and a missing key read as " " and pass
    assert parity.filtering_kept(rows, passes, "Organ", slip=True) == [3]
    assert parity.filtering_kept(rows, passes, "Organ", slip=False) == [4]


def test_declared_difference_7_is_the_index_slip(parity):
    assert 7 in parity.DECLARED and "_filterSamples" in parity.DECLARED[7]

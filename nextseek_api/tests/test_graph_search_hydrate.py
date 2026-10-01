"""Hermetic tests for graph_search's catalog cache and page hydration.

The catalog cache reads the graph through a fake driver that counts its calls, with the module
clock patched; hydration reads SEEK's MySQL through a patched ``seek`` connection. No database is
touched.
"""

import datetime
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from neo4j import RoutingControl

from nextseek_api.graph_search import catalog_cache, hydrate
from nextseek_api.graph_search.catalog_cache import (
    CATALOG_CYPHER,
    HASH_CYPHER,
    RECHECK_SECONDS,
    CatalogCache,
    build_catalog,
    get_catalog,
)
from nextseek_api.graph_search.hydrate import ASSAYS_SQL, ROWS_SQL
from nextseek_api.graph_search.query import Catalog
from nextseek_api.models import SampleAdvancedSearchResult


# --- the catalog cache -----------------------------------------------------------------------------


TIS = {"id": 3, "title": "TIS", "label": "T_TIS",
       "attributes": [["Organ", "string"], ["organ", "string"], ["CellCount", "float"], ["Legacy note", "string"]]}
DSEQ = {"id": 7, "title": "D.SEQ", "label": "T_D_SEQ", "attributes": [["Read length", "integer"]]}
EMPTY = {"id": 9, "title": "A.NEW", "label": "T_A_NEW", "attributes": [[None, None]]}


class FakeDriver:
    """Answers the two catalog statements and records every call."""

    def __init__(self, catalog_hash="h1", types=(TIS, DSEQ, EMPTY)):
        self.catalog_hash = catalog_hash
        self.types = list(types)
        self.calls = []

    def execute_query(self, query_, parameters_=None, routing_=RoutingControl.WRITE, database_=None, **kwargs):
        text = getattr(query_, "text", query_)
        self.calls.append(SimpleNamespace(text=text, query=query_, params=parameters_, routing=routing_,
                                          database=database_))
        if text == HASH_CYPHER:
            records = [] if self.catalog_hash is None else [{"catalog_hash": self.catalog_hash}]
        elif text == CATALOG_CYPHER:
            records = [dict(t) for t in self.types]
        else:
            raise AssertionError(f"unexpected statement: {text}")
        return SimpleNamespace(records=records, summary=None, keys=[])

    def count(self, text):
        return sum(1 for c in self.calls if c.text == text)


@pytest.fixture
def clock():
    """The cache's clock, moved by hand."""
    now = {"t": 1000.0}
    with patch.object(catalog_cache, "_now", lambda: now["t"]):
        yield now


@pytest.fixture(autouse=True)
def _fresh_process_cache():
    catalog_cache.clear()
    yield
    catalog_cache.clear()


def test_catalog_statements_are_the_planned_reads():
    assert CATALOG_CYPHER.startswith("CYPHER 25")
    assert "MATCH (t:SampleType) OPTIONAL MATCH (t)-[:HAS_ATTRIBUTE]->(a:Attribute)" in CATALOG_CYPHER
    assert "collect([a.title, a.value_type])" in CATALOG_CYPHER
    assert HASH_CYPHER.startswith("CYPHER 25")
    assert "MATCH (g:GraphMeta) RETURN g.catalog_hash" in HASH_CYPHER
    assert RECHECK_SECONDS == 60


def test_build_catalog_fills_every_field():
    catalog = build_catalog([TIS, DSEQ, EMPTY])

    assert isinstance(catalog, Catalog)
    assert catalog.type_title_by_id == {3: "TIS", 7: "D.SEQ", 9: "A.NEW"}
    assert catalog.label_by_title == {"TIS": "T_TIS", "D.SEQ": "T_D_SEQ", "A.NEW": "T_A_NEW"}
    assert catalog.titles_by_type == {
        "TIS": frozenset({"Organ", "organ", "CellCount", "Legacy note"}),
        "D.SEQ": frozenset({"Read length"}),
        "A.NEW": frozenset(),
    }
    assert catalog.value_type[("TIS", "CellCount")] == "float"
    assert catalog.value_type[("D.SEQ", "Read length")] == "integer"
    assert catalog.value_type[("TIS", "Organ")] == "string"


def test_build_catalog_keeps_titles_byte_exact_and_skips_incomplete_nodes():
    records = [
        {"id": 4, "title": "CEL", "label": "T_CEL",
         "attributes": [["Media supplement ", "string"], ["No type", None]]},
        {"id": None, "title": "OLD", "label": None, "attributes": []},
        {"id": 5, "title": None, "label": "T_X", "attributes": [["A", "string"]]},
    ]

    catalog = build_catalog(records)

    assert catalog.titles_by_type["CEL"] == frozenset({"Media supplement ", "No type"})
    assert ("CEL", "No type") not in catalog.value_type  # the query builder defaults it to string
    assert catalog.titles_by_type["OLD"] == frozenset()
    assert "OLD" not in catalog.label_by_title
    assert None not in catalog.type_title_by_id.values()
    assert 5 not in catalog.type_title_by_id


def test_first_call_reads_the_hash_then_the_catalog_as_reads_with_a_timeout(clock):
    driver = FakeDriver()

    catalog = get_catalog(driver, "neo4j")

    assert catalog.type_title_by_id[3] == "TIS"
    assert [c.text for c in driver.calls] == [HASH_CYPHER, CATALOG_CYPHER]
    for call in driver.calls:
        assert call.routing == RoutingControl.READ
        assert call.database == "neo4j"
        assert call.query.timeout == catalog_cache.TIMEOUT_SECONDS


def test_within_sixty_seconds_nothing_is_read(clock):
    driver = FakeDriver()
    first = get_catalog(driver, "neo4j")

    clock["t"] += 59
    second = get_catalog(driver, "neo4j")

    assert second is first
    assert len(driver.calls) == 2


def test_after_sixty_seconds_an_unchanged_hash_reads_only_the_hash(clock):
    driver = FakeDriver()
    first = get_catalog(driver, "neo4j")

    clock["t"] += 61
    second = get_catalog(driver, "neo4j")
    clock["t"] += 30
    third = get_catalog(driver, "neo4j")

    assert second is first and third is first
    assert driver.count(HASH_CYPHER) == 2
    assert driver.count(CATALOG_CYPHER) == 1


def test_a_changed_hash_rereads_the_catalog(clock):
    driver = FakeDriver()
    first = get_catalog(driver, "neo4j")

    driver.catalog_hash = "h2"
    driver.types = [DSEQ]
    clock["t"] += 10
    assert get_catalog(driver, "neo4j") is first  # not re-checked yet
    clock["t"] += 60
    second = get_catalog(driver, "neo4j")

    assert second is not first
    assert second.type_title_by_id == {7: "D.SEQ"}
    assert driver.count(CATALOG_CYPHER) == 2
    assert driver.count(HASH_CYPHER) == 2


def test_the_cache_is_per_process_and_survives_a_new_driver(clock):
    first = get_catalog(FakeDriver(), "neo4j")
    other = FakeDriver()

    assert get_catalog(other, "neo4j") is first
    assert other.calls == []


def test_databases_are_cached_separately(clock):
    driver = FakeDriver()
    get_catalog(driver, "neo4j")
    get_catalog(driver, "other")

    assert driver.count(CATALOG_CYPHER) == 2
    assert {c.database for c in driver.calls} == {"neo4j", "other"}


def test_a_graph_without_graphmeta_rereads_at_every_recheck(clock, caplog):
    driver = FakeDriver(catalog_hash=None)
    with caplog.at_level(logging.WARNING, logger=catalog_cache.log.name):
        get_catalog(driver, "neo4j")
    clock["t"] += 30
    get_catalog(driver, "neo4j")
    clock["t"] += 61
    get_catalog(driver, "neo4j")

    assert driver.count(CATALOG_CYPHER) == 2
    assert "GraphMeta" in caplog.text


def test_a_separate_cache_instance_does_not_share_the_process_cache(clock):
    driver = FakeDriver()
    get_catalog(driver, "neo4j")
    own = CatalogCache()

    own.get(driver, "neo4j")

    assert driver.count(CATALOG_CYPHER) == 2


# --- hydration ------------------------------------------------------------------------------------


CREATED = datetime.datetime(2024, 3, 5, 14, 7, 9)


def _sample(sample_id, *, metadata='{"Organ": "Lung"}', created=CREATED, first_name="Demo"):
    return (sample_id, f"S{sample_id}", 3, "TIS", f"TIS-{sample_id:06d}", 145, first_name, created, metadata)


def _seek(sample_rows, assay_rows=()):
    """A patched ``connections`` whose cursor answers the two hydration statements in order."""
    cursor = MagicMock()
    cursor.fetchall.side_effect = [list(sample_rows), list(assay_rows)]
    connections = MagicMock()
    connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = cursor
    return connections, cursor


def _sql_calls(cursor):
    return [(" ".join(c.args[0].split()), list(c.args[1])) for c in cursor.execute.call_args_list]


def test_rows_come_back_in_the_given_order():
    connections, cursor = _seek([_sample(2), _sample(5), _sample(9)])
    with patch.object(hydrate, "connections", connections):
        rows = hydrate.hydrate([9, 2, 5])

    assert [r["id"] for r in rows] == [9, 2, 5]
    connections.__getitem__.assert_called_with("seek")


def test_the_statements_bind_every_id_and_interpolate_none():
    connections, cursor = _seek([_sample(2), _sample(5), _sample(9)])
    with patch.object(hydrate, "connections", connections):
        hydrate.hydrate([9, 2, 5])

    (rows_sql, rows_params), (assays_sql, assays_params) = _sql_calls(cursor)
    assert rows_sql == (
        "SELECT A.id, A.title, A.sample_type_id, B.title AS sample_type, A.uuid, A.contributor_id, "
        "C.first_name, A.created_at, A.json_metadata "
        "FROM samples A LEFT JOIN sample_types B ON A.sample_type_id = B.id "
        "LEFT JOIN people C ON A.contributor_id = C.id "
        "WHERE A.id IN (%s, %s, %s)"
    )
    assert rows_params == [9, 2, 5]
    assert assays_sql == (
        "SELECT D.asset_id, E.title FROM assay_assets D JOIN assays E ON E.id = D.assay_id "
        "WHERE D.asset_type = 'Sample' AND D.asset_id IN (%s, %s, %s) ORDER BY D.asset_id, E.title"
    )
    assert assays_params == [9, 2, 5]
    assert "GROUP_CONCAT" not in ROWS_SQL + ASSAYS_SQL


def test_an_id_missing_from_mysql_is_dropped_and_logged(caplog):
    connections, _ = _seek([_sample(2), _sample(9)])
    with patch.object(hydrate, "connections", connections), \
            caplog.at_level(logging.WARNING, logger=hydrate.log.name):
        rows = hydrate.hydrate([9, 404, 2])

    assert [r["id"] for r in rows] == [9, 2]
    assert "404" in caplog.text


def test_assays_join_several_titles_with_commas_and_none_when_absent():
    connections, _ = _seek(
        [_sample(2), _sample(5)],
        [(2, "Bulk RNA-seq"), (2, "Histology"), (2, "Proteomics"), (2, None)],
    )
    with patch.object(hydrate, "connections", connections):
        rows = hydrate.hydrate([2, 5])

    assert rows[0]["assays"] == "Bulk RNA-seq,Histology,Proteomics"
    assert rows[1]["assays"] is None


def test_an_empty_id_list_issues_no_sql():
    connections, cursor = _seek([])
    with patch.object(hydrate, "connections", connections):
        assert hydrate.hydrate([]) == []

    connections.__getitem__.assert_not_called()
    cursor.execute.assert_not_called()


def test_a_row_has_advanced_search_columns_and_no_html():
    connections, _ = _seek([_sample(2)], [(2, "Histology")])
    with patch.object(hydrate, "connections", connections):
        (row,) = hydrate.hydrate([2])

    assert row == {
        "id": 2, "title": "S2", "uuid": "TIS-000002", "sample_type_id": 3, "contributor_id": 145,
        "created_at": "2024-03-05 14:07:09", "json_metadata": {"Organ": "Lung"}, "sample_type": "TIS",
        "first_name": "Demo", "assays": "Histology", "attributeValue": "",
    }
    for html_key in ("idlink", "idurl", "uid"):
        assert html_key not in row


def test_created_at_renders_as_advanced_search_does():
    micro = datetime.datetime(2024, 3, 5, 14, 7, 9, 250000)
    connections, _ = _seek([_sample(2, created=micro), _sample(3, created=None)])
    with patch.object(hydrate, "connections", connections):
        rows = hydrate.hydrate([2, 3])

    assert rows[0]["created_at"] == str(micro)
    assert rows[1]["created_at"] is None


@pytest.mark.parametrize("raw,expected", [
    ('{"Organ": "Lung", "CellCount": 12}', {"Organ": "Lung", "CellCount": 12}),
    (b'{"Organ": "Lung"}', {"Organ": "Lung"}),
    ('  {"Organ": "Lung"}\n', {"Organ": "Lung"}),
    (None, {}),
    ("", {}),
    ("not json", {}),
    ('["a", "b"]', {}),
])
def test_json_metadata_is_parsed_to_a_dict(raw, expected):
    connections, _ = _seek([_sample(2, metadata=raw)])
    with patch.object(hydrate, "connections", connections):
        (row,) = hydrate.hydrate([2])

    assert row["json_metadata"] == expected


def test_duplicate_ids_are_hydrated_once():
    connections, cursor = _seek([_sample(2), _sample(5)])
    with patch.object(hydrate, "connections", connections):
        rows = hydrate.hydrate([5, 2, 5])

    assert [r["id"] for r in rows] == [5, 2]
    assert _sql_calls(cursor)[0][1] == [5, 2]


def test_a_large_id_list_is_read_in_chunks(monkeypatch):
    monkeypatch.setattr(hydrate, "MAX_IDS_PER_STATEMENT", 2)
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [_sample(1), _sample(2)], [(1, "X")],
        [_sample(3)], [(3, "Y")],
    ]
    connections = MagicMock()
    connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = cursor
    with patch.object(hydrate, "connections", connections):
        rows = hydrate.hydrate([3, 1, 2])

    assert [r["id"] for r in rows] == [3, 1, 2]
    assert [r["assays"] for r in rows] == ["Y", "X", None]
    assert [params for _, params in _sql_calls(cursor)] == [[3, 1], [3, 1], [2], [2]]


def test_hydrated_rows_validate_in_advanced_search_envelope():
    connections, _ = _seek([_sample(2), _sample(5)], [(5, "Histology")])
    with patch.object(hydrate, "connections", connections):
        rows = hydrate.hydrate([2, 5])

    SampleAdvancedSearchResult.model_validate(
        {"total": 2, "rows": rows, "footer": [], "sampleTypes": ["TIS"], "noSampleTypes": 1,
         "msg": "okay", "status": 1}
    )

"""
Graph rows leave the driver JSON-safe: a Neo4j temporal becomes its ISO string.

Local run 2026-09-22, report.longest_running_investigation: the graph agent wrote
`RETURN min(date(s.CollectionDate)) AS minDate, ...`, the rows carried `neo4j.time.Date`
values, and the first save of the turn raised `TypeError: Object of type Date is not JSON
serializable`, so the user saw "Internal pipeline error" for a reply already written.
"""
from __future__ import annotations

import json

from neo4j.time import Date, DateTime, Duration

from chat_nextseek.helpers.tools.neo4j import _read_rows, plain_value


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def consume(self):
        return None


class _Tx:
    def __init__(self, rows):
        self._rows = rows

    def run(self, cypher, params):
        return _Result(self._rows)


def test_a_date_row_serialises():
    rows = [{"investigation": "MIT_SRP", "minDate": Date(2002, 11, 16), "maxDate": Date(2025, 1, 30), "span": 8111}]
    records, _ = _read_rows(_Tx(rows), "RETURN 1", {})
    assert records == [{"investigation": "MIT_SRP", "minDate": "2002-11-16", "maxDate": "2025-01-30", "span": 8111}]
    json.dumps(records)


def test_nested_temporals_and_other_values_survive():
    value = {"when": [Date(2024, 6, 10), DateTime(2025, 2, 23, 12, 0, 0)], "took": Duration(days=3),
             "n": 5, "name": "x", "none": None}
    out = plain_value(value)
    assert out["when"] == ["2024-06-10", "2025-02-23T12:00:00.000000000"]
    assert out["took"] == "P3D"
    assert (out["n"], out["name"], out["none"]) == (5, "x", None)
    json.dumps(out)


def test_a_non_neo4j_object_with_iso_format_is_left_alone():
    class Other:
        def iso_format(self):
            raise AssertionError("must not be called")

    other = Other()
    assert plain_value(other) is other

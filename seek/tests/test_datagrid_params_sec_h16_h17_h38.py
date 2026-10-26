"""SEC-H16/H17/H38: the sample grid's client-supplied filter keys, sort, rows, page, and the clade stats query.

Hermetic: the model manager is a recording fake, the clade query is captured before it reaches MySQL.
"""

import json
from unittest.mock import MagicMock, patch

from django.db.models import Q
from django.test import RequestFactory

from dmac.dbconn_django import DBconn_django
from dmac.dbtable_clades import DBtable_clades
from dmac.datagrid_custom import DataGrid
from seek.models import Samples


class _Chain:
    def __init__(self, log):
        self.log = log

    def filter(self, q):
        self.log["filters"].append(q)
        return self

    def order_by(self, key):
        self.log["order_by"].append(key)
        return self

    def __getitem__(self, s):
        self.log["slices"].append((s.start, s.stop))
        return self

    def __iter__(self):
        return iter([])

    def count(self):
        self.log["count"] += 1
        return 7

    def __len__(self):
        self.log["len"] += 1
        return 7


def _run(params):
    log = {"filters": [], "order_by": [], "slices": [], "count": 0, "len": 0}
    db = DBconn_django.__new__(DBconn_django)
    request = RequestFactory().get("/", params)
    filtersdic = DataGrid(None).getDatagridFilters(request.GET)
    with patch.object(Samples, "objects", _Chain(log)):
        out = db.retrieve_table_list(Samples, "id", filtersdic, {})
    return out, log


def test_a_normal_request_keeps_its_shape():
    rules = json.dumps([{"field": "title", "op": "contains", "value": "abc"}])
    (rows, footer, total), log = _run({"page": "2", "rows": "50", "sort": "title", "order": "desc", "filterRules": rules})
    assert (rows, footer) == ([], [])
    assert str(log["filters"][0]) == str(Q(title__icontains="abc"))
    assert log["order_by"][0] == "-title"
    assert log["slices"][0] == (50, 100)


def test_sort_and_filter_keys_outside_the_models_fields_are_dropped():
    rules = json.dumps([
        {"field": "json_metadata__regex", "op": "contains", "value": "a"},
        {"field": "nosuchfield", "op": "contains", "value": "b"},
        {"field": "title", "op": "contains", "value": "ok"},
    ])
    _, log = _run({"page": "1", "rows": "10", "sort": "json_metadata__regex", "order": "asc", "filterRules": rules})
    assert str(log["filters"][0]) == str(Q(title__icontains="ok"))
    assert log["order_by"] == []


def test_order_other_than_asc_desc_is_ignored_and_rows_are_clamped():
    _, log = _run({"page": "1", "rows": "100000000", "sort": "title", "order": "title; drop"})
    assert log["order_by"][0] == "title"
    assert log["slices"][0] == (0, 500)


def test_a_non_integer_page_answers_empty_not_a_500():
    (rows, footer, total), log = _run({"page": "x", "rows": "10"})
    assert (rows, total) == ([], 0)
    assert log["slices"] == []


def test_the_total_is_counted_not_len():
    _, log = _run({"page": "1", "rows": "10"})
    assert log["count"] >= 1
    assert log["len"] == 0


def test_clade_stats_bind_project_id():
    captured = {}
    t = DBtable_clades.__new__(DBtable_clades)
    t.dbname = "d"
    t._DBtable_clades__sendQuery = lambda query, params=None: captured.update(q=query, p=params) or []
    t.getCladeProjectStats("5 OR 1=1")
    assert "5 OR 1=1" not in captured["q"]
    assert "p.id = %s" in captured["q"]
    assert list(captured["p"]) == ["5 OR 1=1"]

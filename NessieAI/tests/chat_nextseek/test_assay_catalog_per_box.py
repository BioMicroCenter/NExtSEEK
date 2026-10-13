"""The assay list comes from each box's own dmac.internal_assays, with the committed file as the fallback (fix 21)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import chat_nextseek
from chat_nextseek import assay_catalog
from chat_nextseek.config import ChatConfig
from chat_nextseek.helpers.tools.catalog_match import _hybrid_shortlist

FILE = json.loads((Path(chat_nextseek.__file__).parent / "context" / "min_assays_db.json").read_text(encoding="utf-8"))


class Cursor:
    def __init__(self, conn): self.conn = conn
    def execute(self, sql): self.conn.sql.append(sql)
    def fetchall(self): return [(t,) for t in self.conn.titles]
    def close(self): pass


class Conn:
    def __init__(self, titles): self.titles, self.sql = titles, []
    def cursor(self): return Cursor(self)


def box(titles):
    """A config stand-in whose live connection serves ``titles`` (None: no connection)."""
    cfg = SimpleNamespace(_db_conn=None if titles is None else Conn(titles), _connect_db=lambda env="prod": None)
    return cfg


@pytest.fixture(autouse=True)
def _fresh():
    assay_catalog._cache.clear()


def names(rows): return [r["Name"] for r in rows]


def test_a_box_lists_exactly_its_own_titles():
    cfg = box(["Alpha Assay", "Beta Panel"])
    assert names(assay_catalog.min_assays(cfg, FILE)) == ["Alpha Assay", "Beta Panel"]


def test_file_names_the_box_lacks_are_not_listed():
    keep = FILE[0]["Name"]
    out = assay_catalog.min_assays(box([keep, "Gamma Run"]), FILE)
    assert names(out) == [keep, "Gamma Run"]
    assert out[0] == FILE[0]  # a shared title keeps the file's Description and Tags
    assert out[1] == {"Name": "Gamma Run", "Description": None, "Tags": None}
    assert len(out) < len(FILE)


def test_database_down_serves_the_committed_file():
    assert assay_catalog.min_assays(box(None), FILE) == FILE


def test_empty_table_serves_the_committed_file():
    assert assay_catalog.min_assays(box([]), FILE) == FILE


def test_two_calls_make_one_read():
    cfg = box(["Alpha Assay"])
    assay_catalog.min_assays(cfg, FILE)
    assay_catalog.min_assays(cfg, FILE)
    assert len(cfg._db_conn.sql) == 1


def test_two_boxes_do_not_share_a_list():
    a, b = box(["Alpha Assay"]), box(["Beta Panel"])
    assert names(assay_catalog.min_assays(a, FILE)) == ["Alpha Assay"]
    assert names(assay_catalog.min_assays(b, FILE)) == ["Beta Panel"]


def test_chatconfig_property_reads_the_table_and_keeps_the_file_setter():
    cfg = ChatConfig.__new__(ChatConfig)
    cfg.MIN_ASSAYS = FILE  # config.py's own assignment
    cfg._db_conn = Conn(["Alpha Assay"])
    assert names(cfg.MIN_ASSAYS) == ["Alpha Assay"]
    other = ChatConfig.__new__(ChatConfig)
    other.MIN_ASSAYS = FILE
    other._db_conn, other._connect_db = None, lambda env="prod": None
    assert other.MIN_ASSAYS == FILE


def test_shortlist_never_surfaces_an_index_only_assay():
    full = [{"Name": "Alpha Assay"}, {"Name": "File Only Assay"}]
    box_rows = [{"Name": "Alpha Assay", "Description": None, "Tags": None}]
    index = SimpleNamespace(query=lambda text, top_n: [(full[1], 0.9, 1), (full[0], 0.8, 2)])
    out, _ = _hybrid_shortlist("assay", box_rows, index, id_field="Name", lex_doc_fn=lambda i: (i["Name"], set()),
                               code_key=None, ratio=0.7, min_k=1, max_k=5)
    assert [i["Name"] for i in out] == ["Alpha Assay"]

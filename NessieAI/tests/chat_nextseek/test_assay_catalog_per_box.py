"""The assay list comes from each box's own dmac.internal_assays, with the catalog list as the fallback (fix 21).

Every title here is invented. ``Table`` stands in for a box's table and ``Conn`` for a MySQL connection with autocommit
off (what the app's connections run with): a connection answers from the table as it was at its first read, as an
InnoDB REPEATABLE READ transaction does, so only a new connection sees rows added since.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import chat_nextseek
from chat_nextseek import assay_catalog
from chat_nextseek.config import ChatConfig
from chat_nextseek.graph_scope import GraphScope, with_scope
from chat_nextseek.helpers.tools.catalog_match import _hybrid_shortlist

FILE = json.loads((Path(chat_nextseek.__file__).parent / "context" / "min_assays_db.json").read_text(encoding="utf-8"))


class Table:
    def __init__(self, titles):
        self.titles, self.reads, self.opened, self.closed = list(titles), 0, 0, 0


class Conn:
    def __init__(self, table):
        self.table, self.snapshot = table, None

    def cursor(self):
        return Cursor(self)

    def close(self):
        self.table.closed += 1


class Cursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql):
        if self.conn.snapshot is None:
            self.conn.snapshot = list(self.conn.table.titles)
        self.conn.table.reads += 1
        self.rows = [(t,) for t in self.conn.snapshot]

    def fetchall(self):
        return self.rows

    def close(self):
        pass


def box(titles, host="db-a"):
    """A config stand-in for a box whose table holds ``titles`` (None: the database cannot be reached)."""
    table = None if titles is None else Table(titles)

    def connect(env="prod"):
        if table is None:
            return None
        table.opened += 1
        return Conn(table)

    return SimpleNamespace(MYSQL_HOST_PROD=host, MYSQL_PORT=3306, MYSQL_USER="app", _db_conn=None,
                           _connect_db=connect, table=table)


@pytest.fixture(autouse=True)
def _fresh():
    assay_catalog._cache.clear()
    yield
    assay_catalog._cache.clear()


def names(rows):
    return [r["Name"] for r in rows]


def test_a_box_lists_exactly_its_own_titles():
    assert names(assay_catalog.min_assays(box(["Alpha Assay", "Beta Panel"]), FILE)) == ["Alpha Assay", "Beta Panel"]


def test_file_names_the_box_lacks_are_not_listed():
    keep = FILE[0]["Name"]
    out = assay_catalog.min_assays(box([keep, "Gamma Run"]), FILE)
    assert names(out) == [keep, "Gamma Run"]
    assert out[0] == FILE[0]  # a shared title keeps the file's Description and Tags
    assert out[1] == {"Name": "Gamma Run", "Description": None, "Tags": None}
    assert len(out) < len(FILE)


def test_a_title_in_other_case_keeps_the_catalog_fields_under_the_box_spelling():
    row = next(r for r in FILE if r.get("Description") and r.get("Tags"))
    for spelling in (row["Name"].lower(), row["Name"].upper()):
        assay_catalog._cache.clear()
        out = assay_catalog.min_assays(box([spelling]), FILE)
        assert out == [dict(row, Name=spelling)]


def test_database_down_serves_the_catalog_list():
    assert assay_catalog.min_assays(box(None), FILE) == FILE


def test_empty_table_serves_the_catalog_list():
    assert assay_catalog.min_assays(box([]), FILE) == FILE


def test_two_calls_make_one_read():
    cfg = box(["Alpha Assay"])
    assay_catalog.min_assays(cfg, FILE)
    assay_catalog.min_assays(cfg, FILE)
    assert cfg.table.reads == 1


def test_a_per_turn_copy_reuses_the_box_read():
    """Every turn and op runs on a copy of the config (the orchestrator's and with_scope's copy.copy)."""
    cfg = box(["Alpha Assay"])
    assay_catalog.min_assays(cfg, FILE)
    for _ in range(5):
        turn = with_scope(copy.copy(cfg), GraphScope.admin("test"))
        assert names(assay_catalog.min_assays(turn, FILE)) == ["Alpha Assay"]
    assert cfg.table.reads == 1
    assert len(assay_catalog._cache) == 1


def test_a_failed_read_is_remembered_across_turn_copies():
    calls = []
    cfg = box(None)
    cfg._connect_db = lambda env="prod": calls.append(env)
    for _ in range(5):
        assert assay_catalog.min_assays(copy.copy(cfg), FILE) == FILE
    assert calls == ["prod"]


def test_two_boxes_do_not_share_a_list():
    a, b = box(["Alpha Assay"], host="db-a"), box(["Beta Panel"], host="db-b")
    assert names(assay_catalog.min_assays(a, FILE)) == ["Alpha Assay"]
    assert names(assay_catalog.min_assays(b, FILE)) == ["Beta Panel"]


def test_each_read_opens_its_own_connection_and_closes_it():
    cfg = box(["Alpha Assay"])
    cfg._db_conn = SimpleNamespace(cursor=lambda: pytest.fail("the read used the config's long-lived connection"))
    assert names(assay_catalog.min_assays(cfg, FILE)) == ["Alpha Assay"]
    assert (cfg.table.opened, cfg.table.closed) == (1, 1)


def test_a_refresh_sees_titles_added_since_the_last_read(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(assay_catalog, "_clock", lambda: now[0])
    cfg = box(["Alpha Assay"])
    cfg._db_conn = Conn(cfg.table)  # the long-lived connection: its first read would freeze its snapshot
    assert names(assay_catalog.min_assays(cfg, FILE)) == ["Alpha Assay"]
    cfg.table.titles.append("Beta Panel")
    now[0] += assay_catalog.TTL_S - 1
    assert names(assay_catalog.min_assays(cfg, FILE)) == ["Alpha Assay"]  # still cached
    now[0] += 2
    assert names(assay_catalog.min_assays(cfg, FILE)) == ["Alpha Assay", "Beta Panel"]


def test_chatconfig_property_reads_the_table_once_per_box_and_keeps_the_file_setter():
    table = Table(["Alpha Assay"])
    cfg = ChatConfig.__new__(ChatConfig)
    cfg.MIN_ASSAYS = FILE  # config.py's own assignment
    cfg.MYSQL_HOST_PROD, cfg.MYSQL_PORT, cfg.MYSQL_USER = "db-a", 3306, "app"
    cfg._connect_db = lambda env="prod": Conn(table)
    assert names(cfg.MIN_ASSAYS) == ["Alpha Assay"]
    assert names(copy.copy(cfg).MIN_ASSAYS) == ["Alpha Assay"]
    assert table.reads == 1
    other = ChatConfig.__new__(ChatConfig)
    other.MIN_ASSAYS = FILE
    other.MYSQL_HOST_PROD, other._connect_db = "db-b", lambda env="prod": None
    assert other.MIN_ASSAYS == FILE


def test_shortlist_never_surfaces_an_index_only_assay():
    full = [{"Name": "Alpha Assay"}, {"Name": "File Only Assay"}]
    box_rows = [{"Name": "Alpha Assay", "Description": None, "Tags": None}]
    index = SimpleNamespace(query=lambda text, top_n: [(full[1], 0.9, 1), (full[0], 0.8, 2)])
    out, _ = _hybrid_shortlist("assay", box_rows, index, id_field="Name", lex_doc_fn=lambda i: (i["Name"], set()),
                               code_key=None, ratio=0.7, min_k=1, max_k=5)
    assert [i["Name"] for i in out] == ["Alpha Assay"]


# --- the system agent answers "which assays are registered" from the same list ---------------------------------------

def _system_config(box_titles):
    from unittest.mock import MagicMock
    full = [{"Name": "Alpha Assay", "Alternative Assay Names": "AA, alpha run", "Description": "From the catalog."},
            {"Name": "Gone Assay", "Alternative Assay Names": None, "Description": "Only in the catalog."}]
    c = MagicMock()
    c.FULL_SAMPLETYPES_MAP, c.FULL_PROJECTS_MAP, c.FULL_INVESTIGATIONS_MAP, c.LABS = {}, {}, {}, []
    c.FULL_ASSAYS_MAP = {r["Name"]: r for r in full}
    c.MIN_ASSAYS = [{"Name": t, "Description": None, "Tags": None} for t in box_titles]
    c.MIN_SAMPLETYPES, c.MIN_API_ENDPOINTS = [], []
    c.CAPABILITIES_DOC, c.NEO4J_SCHEMA, c.SYSTEM_AGENT_SYSTEM_PROMPT = "caps", {}, "system prompt"
    return c


def test_the_system_agent_counts_this_boxs_assays():
    from chat_nextseek import system_tools
    out = system_tools.list_catalog(_system_config(["Alpha Assay", "Box Only Panel"]), "assay")
    assert out["count"] == 2
    assert [r["name"] for r in out["rows"]] == ["Alpha Assay", "Box Only Panel"]
    assert out["counts"] == "2 assay definitions in the catalog"


def test_the_system_agents_index_names_only_this_boxs_assays():
    from chat_nextseek import system_tools
    index = system_tools.catalog_index(_system_config(["Alpha Assay", "Box Only Panel"]))
    assert "Assays (2):" in index and "- Box Only Panel" in index
    assert "Gone Assay" not in index


def test_an_assay_only_the_catalog_has_is_not_found():
    from chat_nextseek import system_tools
    out = system_tools.get_catalog_entry(_system_config(["Alpha Assay"]), "assay", "Gone Assay")
    assert out["ok"] is False


def test_a_box_title_in_other_case_keeps_the_catalogs_other_names():
    from chat_nextseek import system_tools
    entry = system_tools.get_catalog_entry(_system_config(["alpha assay"]), "assay", "alpha run")
    assert entry["ok"] is True
    assert entry["row"]["Name"] == "alpha assay" and entry["row"]["Description"] == "From the catalog."


def test_the_system_agent_gets_details_only_for_this_boxs_assays(monkeypatch):
    from chat_nextseek.agents import system as system_mod
    from chat_nextseek.schemas import ParserPlan
    monkeypatch.setattr(system_mod, "live_catalog_context", lambda *args, **kwargs: None)
    entity = {"assays": [{"code": "alpha assay"}, {"code": "Gone Assay"}]}
    messages = system_mod.build_messages(_system_config(["alpha assay"]), "what is it", entity,
                                         ParserPlan(mode="system_question").model_dump(), pages={})
    block = next(m["content"] for m in messages if m["content"].startswith("ENTITY_DETAILS"))
    details = json.loads(block.split("\n", 1)[1])
    assert list(details) == ["alpha assay"] and details["alpha assay"]["Description"] == "From the catalog."

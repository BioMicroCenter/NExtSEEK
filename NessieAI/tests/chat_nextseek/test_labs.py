"""Lab records mined from SEEK institution titles (spec 2026-09-18, sections 4 to 6).

SEEK records every lab as an Institution titled ``<CODE>-<Name> Lab (<Affiliation>)``.
``chat_nextseek.labs`` reads those titles with one fixed, read-only SELECT, parses them
by one strict grammar, reports every title that does not fit with a reason code, and
writes the result to ``labs_db.json``, a runtime-only context file.

Every title below is invented. No real lab title, person name or production count may
appear in this file (the repository is public).

Hermetic: no database, no network, no Django. The connection is a recording fake.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from chat_nextseek import labs


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTEXT_DIR = REPO_ROOT / "NessieAI/chat_nextseek/src/chat_nextseek/context"


# --------------------------------------------------------------------------------------
# A recording fake of a mysql-connector connection
# --------------------------------------------------------------------------------------

class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows = []

    def execute(self, sql, params=None):
        self._conn.calls.append(("execute", sql, params))
        if self._conn.fail_on_execute is not None:
            raise self._conn.fail_on_execute
        self._rows = list(self._conn.rows)

    def fetchall(self):
        self._conn.calls.append(("fetchall",))
        return self._rows

    def close(self):
        self._conn.calls.append(("cursor.close",))


class _Conn:
    def __init__(self, rows=(), *, fail_on_execute=None, fail_on_start=None):
        self.rows = list(rows)
        self.calls: list[tuple] = []
        self.fail_on_execute = fail_on_execute
        self.fail_on_start = fail_on_start

    def rollback(self):
        self.calls.append(("rollback",))

    def start_transaction(self, **kwargs):
        self.calls.append(("start_transaction", kwargs))
        if self.fail_on_start is not None:
            raise self.fail_on_start

    def cursor(self, *args, **kwargs):
        self.calls.append(("cursor", kwargs))
        return _Cursor(self)

    def commit(self):  # never called: the read never commits
        self.calls.append(("commit",))


def _names(calls):
    return [c[0] for c in calls]


# --------------------------------------------------------------------------------------
# 4.3 The query and its read-only transaction
# --------------------------------------------------------------------------------------

SPEC_SQL = (
    "SELECT i.id AS institution_id, i.title AS title, wg.project_id AS project_id\n"
    "FROM seek_production.institutions AS i\n"
    "LEFT JOIN seek_production.work_groups AS wg ON wg.institution_id = i.id\n"
    "ORDER BY i.id, wg.project_id"
)


def test_the_sql_is_the_one_fixed_select_of_the_spec():
    assert labs.INSTITUTIONS_SQL == SPEC_SQL
    sql = labs.INSTITUTIONS_SQL
    assert sql.lstrip().upper().startswith("SELECT ")
    assert ";" not in sql, "one statement only"
    assert "%" not in sql and "{" not in sql, "no interpolation"
    for verb in ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "GRANT"):
        assert not re.search(rf"\b{verb}\b", sql.upper()), verb


def test_the_read_runs_inside_a_read_only_transaction():
    conn = _Conn([(41, "ASH-Ashgrove Lab (BWH)", 4)])

    rows = labs.fetch_institution_rows(conn)

    assert rows == [(41, "ASH-Ashgrove Lab (BWH)", 4)]
    names = _names(conn.calls)
    # End any implicit transaction first, then open a read-only one, then the one SELECT,
    # then end it. The server refuses any write inside it.
    assert names[0] == "rollback"
    assert conn.calls[1] == ("start_transaction", {"readonly": True})
    executes = [c for c in conn.calls if c[0] == "execute"]
    assert executes == [("execute", labs.INSTITUTIONS_SQL, None)], "the constant, alone, no parameters"
    assert names.index("start_transaction") < names.index("execute") < names.index("fetchall")
    assert names[-1] == "rollback"
    assert "commit" not in names


def test_a_failed_select_still_ends_the_transaction_and_raises():
    conn = _Conn(fail_on_execute=RuntimeError("no such table"))

    with pytest.raises(RuntimeError):
        labs.fetch_institution_rows(conn)

    names = _names(conn.calls)
    assert names[-1] == "rollback"
    assert names.count("rollback") == 2


def test_no_select_runs_when_the_read_only_transaction_cannot_start():
    conn = _Conn([(41, "ASH-Ashgrove Lab (BWH)", 4)], fail_on_start=RuntimeError("no read-only"))

    with pytest.raises(RuntimeError):
        labs.fetch_institution_rows(conn)

    assert "execute" not in _names(conn.calls)


def test_dict_rows_and_byte_titles_are_read_too():
    conn = _Conn([
        {"institution_id": 41, "title": b"ASH-Ashgrove Lab (BWH)", "project_id": 4},
        {"institution_id": 44, "title": "CED-Cedarfield Lab (Harvard)", "project_id": None},
    ])

    assert labs.fetch_institution_rows(conn) == [
        (41, "ASH-Ashgrove Lab (BWH)", 4),
        (44, "CED-Cedarfield Lab (Harvard)", None),
    ]


# --------------------------------------------------------------------------------------
# 5. The title grammar
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("title, code, name, affiliation", [
    ("ASH-Ashgrove Lab (BWH)", "ASH", "Ashgrove", "BWH"),
    ("FEN-Fenwick Lab (MIT)", "FEN", "Fenwick", "MIT"),
    # multi-word, hyphenated, apostrophe, period and non-ASCII letters are all names
    ("VDB-Van der Birch Lab (MIT)", "VDB", "Van der Birch", "MIT"),
    ("OBP-O'Brook-Pine Lab (Harvard)", "OBP", "O'Brook-Pine", "Harvard"),
    ("STC-St. Cedar Lab (BWH)", "STC", "St. Cedar", "BWH"),
    ("MUL-Mülwood Lab (ETH Zurich)", "MUL", "Mülwood", "ETH Zurich"),
    # the affiliation may hold spaces, commas and hyphens
    ("QAL-Quail Lab (Koch Institute, MIT)", "QAL", "Quail", "Koch Institute, MIT"),
])
def test_a_title_in_the_grammar_parses(title, code, name, affiliation):
    record, reason = labs.parse_title(title)
    assert reason is None
    assert record == {"code": code, "name": name, "affiliation": affiliation}


def test_normalisation_is_nfc_strip_and_collapse_only():
    decomposed = "MUL-Mu\u0308lwood   Lab  (BWH) "
    record, reason = labs.parse_title("  " + decomposed)
    assert reason is None
    assert record == {"code": "MUL", "name": "Mülwood", "affiliation": "BWH"}
    assert labs.normalise_title("  A \t B\n C  ") == "A B C"


@pytest.mark.parametrize("title, reason", [
    (None, "no_title"),
    ("", "no_title"),
    ("   ", "no_title"),
    ("OAK\u2013Oakley Lab (MIT)", "non_ascii_dash"),        # en dash where the hyphen goes
    ("OAK\u2014Oakley Lab (MIT)", "non_ascii_dash"),        # em dash
    ("OAK \u2013 Oakley Lab (MIT)", "non_ascii_dash"),
    ("QU-Quail Lab (MIT)", "code_not_three_letters"),
    ("QUAL-Quail Lab (MIT)", "code_not_three_letters"),
    ("Qua-Quail Lab (MIT)", "code_not_three_letters"),      # no case repair
    ("Example Institute of Technology", "no_code_prefix"),
    ("PIN - Pinecrest Lab (MIT)", "no_code_prefix"),        # no space around the hyphen
    ("PIN-Pinecrest Laboratory (MIT)", "no_lab_word"),
    ("PIN-Pinecrest lab (MIT)", "no_lab_word"),
    ("PIN-Pinecrest Group (MIT)", "no_lab_word"),
    ("PIN-Pinecrest Lab", "no_affiliation"),
    ("PIN-Pinecrest Lab ()", "no_affiliation"),
    ("PIN-Pinecrest Lab (MIT", "no_affiliation"),
    ("PIN-Pinecrest Lab (MIT) old", "trailing_text"),
    ("PIN-Pinecrest Lab (MIT) (BWH)", "trailing_text"),
    ("PIN-Pinecrest2 Lab (MIT)", "bad_name_characters"),
    ("PIN-Pine_crest Lab (MIT)", "bad_name_characters"),
    ("PIN-Pine (crest) Lab (MIT)", "bad_name_characters"),
    ("PIN-Lab (MIT)", "bad_name_characters"),                # no name at all
])
def test_every_reason_code(title, reason):
    record, got = labs.parse_title(title)
    assert record is None
    assert got == reason


def test_reason_codes_are_the_spec_list_in_order():
    assert labs.REASONS == (
        "no_title", "non_ascii_dash", "code_not_three_letters", "no_code_prefix",
        "no_lab_word", "no_affiliation", "trailing_text", "bad_name_characters",
    )


@pytest.mark.parametrize("title, reason", [
    ("QU\u2013Quail lab", "non_ascii_dash"),                # dash beats code and lab word
    ("QUAL-Quail lab", "code_not_three_letters"),          # code beats lab word
    ("PIN-Pinecrest Laboratory", "no_lab_word"),           # lab word beats affiliation
    ("PIN-Pinecrest2 Lab (MIT) old", "trailing_text"),     # trailing text beats name characters
])
def test_the_first_reason_in_order_wins(title, reason):
    assert labs.parse_title(title) == (None, reason)


# --------------------------------------------------------------------------------------
# 6.2 The labs document, and the conflicts
# --------------------------------------------------------------------------------------

FETCHED_AT = "2026-09-19T06:02:11Z"

SPEC_ROWS = [
    (7, "Example Institute of Technology", 2),
    (41, "ASH-Ashgrove Lab (BWH)", 12),
    (41, "ASH-Ashgrove Lab (BWH)", 4),
    (41, "ASH-Ashgrove Lab (BWH)", 4),        # a duplicate work group collapses
    (58, "ASH-Ashby Lab (MIT)", None),        # LEFT JOIN: an institution in no project
    (60, "FEW-Fenwick Lab (Harvard)", None),
    (59, "FEN-Fenwick Lab (MIT)", 4),
]


def test_the_document_has_the_spec_shape():
    doc = labs.build_labs_document(SPEC_ROWS, fetched_at=FETCHED_AT)

    assert doc == {
        "version": 1,
        "source": "seek_production.institutions LEFT JOIN seek_production.work_groups (one read-only SELECT)",
        "fetched_at": FETCHED_AT,
        "labs": [
            {"code": "ASH", "name": "Ashgrove", "affiliation": "BWH", "title": "ASH-Ashgrove Lab (BWH)",
             "institution_id": 41, "project_ids": [4, 12]},
            {"code": "ASH", "name": "Ashby", "affiliation": "MIT", "title": "ASH-Ashby Lab (MIT)",
             "institution_id": 58, "project_ids": []},
            {"code": "FEN", "name": "Fenwick", "affiliation": "MIT", "title": "FEN-Fenwick Lab (MIT)",
             "institution_id": 59, "project_ids": [4]},
            {"code": "FEW", "name": "Fenwick", "affiliation": "Harvard", "title": "FEW-Fenwick Lab (Harvard)",
             "institution_id": 60, "project_ids": []},
        ],
        "unparsed": [
            {"institution_id": 7, "title": "Example Institute of Technology", "project_ids": [2],
             "reason": "no_code_prefix"},
        ],
        "conflicts": [
            {"kind": "name_shared", "name": "Fenwick", "codes": ["FEN", "FEW"]},
            {"kind": "code_shared", "code": "ASH", "institution_ids": [41, 58]},
        ],
    }


def test_conflicting_records_are_all_kept():
    doc = labs.build_labs_document(SPEC_ROWS, fetched_at=FETCHED_AT)
    kept = {(r["code"], r["institution_id"]) for r in doc["labs"]}
    assert {("FEN", 59), ("FEW", 60), ("ASH", 41), ("ASH", 58)} <= kept


def test_a_surname_shared_by_one_code_is_not_a_name_conflict():
    rows = [(41, "ASH-Ashgrove Lab (BWH)", 4), (42, "ASH-Ashgrove Lab (BWH)", 5)]
    doc = labs.build_labs_document(rows, fetched_at=FETCHED_AT)
    assert doc["conflicts"] == [{"kind": "code_shared", "code": "ASH", "institution_ids": [41, 42]}]


def test_a_name_conflict_ignores_case_and_accents():
    rows = [(1, "MUL-Mülwood Lab (BWH)", 4), (2, "MUW-Mulwood Lab (MIT)", 4)]
    doc = labs.build_labs_document(rows, fetched_at=FETCHED_AT)
    assert doc["conflicts"] == [{"kind": "name_shared", "name": "Mülwood", "codes": ["MUL", "MUW"]}]


def test_an_unparsed_title_is_reported_never_guessed():
    rows = [(9, "OAK\u2013Oakley Lab (MIT)", 3), (8, None, None)]
    doc = labs.build_labs_document(rows, fetched_at=FETCHED_AT)
    assert doc["labs"] == []
    assert doc["unparsed"] == [
        {"institution_id": 8, "title": None, "project_ids": [], "reason": "no_title"},
        {"institution_id": 9, "title": "OAK\u2013Oakley Lab (MIT)", "project_ids": [3], "reason": "non_ascii_dash"},
    ]
    assert doc["conflicts"] == []


def test_fetched_at_defaults_to_now_in_utc_seconds():
    doc = labs.build_labs_document([], fetched_at=None)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", doc["fetched_at"])
    assert doc["labs"] == [] and doc["unparsed"] == [] and doc["conflicts"] == []


# --------------------------------------------------------------------------------------
# 6.1 The file: atomic write, tolerant read
# --------------------------------------------------------------------------------------

def test_the_file_name_is_labs_db_json():
    assert labs.LABS_FILE_NAME == "labs_db.json"


def test_write_is_atomic_through_a_temporary_file(tmp_path, monkeypatch):
    doc = labs.build_labs_document(SPEC_ROWS, fetched_at=FETCHED_AT)
    replaced = []
    real_replace = os.replace

    def _spy(src, dst):
        replaced.append((Path(src), Path(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(labs.os, "replace", _spy)

    dest = labs.write_labs_file(doc, tmp_path)

    assert dest == tmp_path / "labs_db.json"
    assert json.loads(dest.read_text(encoding="utf-8")) == doc
    assert len(replaced) == 1
    src, dst = replaced[0]
    assert dst == dest and src.parent == tmp_path and src != dest
    assert sorted(p.name for p in tmp_path.iterdir()) == ["labs_db.json"], "no temporary file left"


def test_a_failed_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    previous = labs.build_labs_document([(41, "ASH-Ashgrove Lab (BWH)", 4)], fetched_at=FETCHED_AT)
    labs.write_labs_file(previous, tmp_path)
    before = (tmp_path / "labs_db.json").read_bytes()

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(labs.json, "dump", _boom)
    with pytest.raises(OSError):
        labs.write_labs_file(labs.build_labs_document([], fetched_at=FETCHED_AT), tmp_path)

    assert (tmp_path / "labs_db.json").read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["labs_db.json"], "no temporary file left"


def test_load_returns_none_for_a_missing_or_unreadable_file(tmp_path):
    assert labs.load_labs_file(tmp_path) is None
    (tmp_path / "labs_db.json").write_text("{not json", encoding="utf-8")
    assert labs.load_labs_file(tmp_path) is None
    (tmp_path / "labs_db.json").write_text("[]", encoding="utf-8")
    assert labs.load_labs_file(tmp_path) is None
    (tmp_path / "labs_db.json").write_text('{"labs": "ASH"}', encoding="utf-8")
    assert labs.load_labs_file(tmp_path) is None


def test_load_round_trips_a_written_file(tmp_path):
    doc = labs.build_labs_document(SPEC_ROWS, fetched_at=FETCHED_AT)
    labs.write_labs_file(doc, tmp_path)
    assert labs.load_labs_file(tmp_path) == doc


def test_checked_labs_keeps_only_well_formed_records():
    doc = {"labs": [
        {"code": "ASH", "name": "Ashgrove", "affiliation": "BWH", "project_ids": [4]},
        {"code": "as", "name": "Ashgrove"},
        {"code": "ASHX", "name": "Ashgrove"},
        {"code": "BRK", "name": ""},
        {"code": "BRK", "name": "   "},
        {"code": "BRK"},
        "not a record",
    ]}
    assert labs.checked_labs(doc) == [
        {"code": "ASH", "name": "Ashgrove", "affiliation": "BWH", "project_ids": [4]},
    ]
    assert labs.checked_labs({"labs": []}) == []
    assert labs.checked_labs(None) is None
    assert labs.checked_labs({"labs": "ASH"}) is None


# --------------------------------------------------------------------------------------
# 6.3 labs_by_project
# --------------------------------------------------------------------------------------

def test_labs_by_project_maps_each_project_to_its_labs_sorted_by_code():
    doc = labs.build_labs_document(SPEC_ROWS + [(43, "BRK-Birchwood Lab (MIT)", 4)], fetched_at=FETCHED_AT)

    by_project = labs.labs_by_project(doc)

    assert by_project == {
        4: [
            {"code": "ASH", "name": "Ashgrove", "affiliation": "BWH"},
            {"code": "BRK", "name": "Birchwood", "affiliation": "MIT"},
            {"code": "FEN", "name": "Fenwick", "affiliation": "MIT"},
        ],
        12: [{"code": "ASH", "name": "Ashgrove", "affiliation": "BWH"}],
    }
    # an unparsed institution contributes nothing, even to its own project
    assert 2 not in by_project


def test_labs_by_project_collapses_a_record_listed_twice():
    doc = {"labs": [
        {"code": "ASH", "name": "Ashgrove", "affiliation": "BWH", "institution_id": 41, "project_ids": [4]},
        {"code": "ASH", "name": "Ashgrove", "affiliation": "BWH", "institution_id": 42, "project_ids": [4]},
    ]}
    assert labs.labs_by_project(doc) == {4: [{"code": "ASH", "name": "Ashgrove", "affiliation": "BWH"}]}


def test_labs_by_project_of_nothing_is_empty():
    assert labs.labs_by_project(None) == {}
    assert labs.labs_by_project({"labs": []}) == {}


# --------------------------------------------------------------------------------------
# The refresh the export runs: fetch, build, write; else the previous file
# --------------------------------------------------------------------------------------

def test_refresh_writes_the_file_and_says_fetched(tmp_path):
    conn = _Conn([(41, "ASH-Ashgrove Lab (BWH)", 4), (7, "Example Institute of Technology", 2)])
    out = io.StringIO()
    with redirect_stdout(out):
        doc, source = labs.refresh_labs_file(conn, tmp_path)

    assert source == "fetched"
    assert labs.load_labs_file(tmp_path) == doc
    assert [r["code"] for r in doc["labs"]] == ["ASH"]
    lines = [l for l in out.getvalue().splitlines() if l.startswith("[CONFIG][LABS]")]
    assert len(lines) == 1, "one log line per refresh"
    assert "unparsed institution ids [7]" in lines[0]


def test_refresh_falls_back_to_the_previous_file_when_the_read_fails(tmp_path):
    previous = labs.build_labs_document([(41, "ASH-Ashgrove Lab (BWH)", 4)], fetched_at=FETCHED_AT)
    labs.write_labs_file(previous, tmp_path)
    conn = _Conn(fail_on_execute=RuntimeError("no such schema"))

    with redirect_stdout(io.StringIO()):
        doc, source = labs.refresh_labs_file(conn, tmp_path)

    assert (doc, source) == (previous, "previous_file")
    assert labs.load_labs_file(tmp_path) == previous


def test_refresh_with_neither_is_unavailable_and_never_raises(tmp_path):
    conn = _Conn(fail_on_start=RuntimeError("gone"))
    with redirect_stdout(io.StringIO()):
        doc, source = labs.refresh_labs_file(conn, tmp_path)
    assert (doc, source) == (None, "unavailable")
    assert not (tmp_path / "labs_db.json").exists()


def test_refresh_keeps_the_fetched_document_when_the_write_fails(tmp_path, monkeypatch):
    conn = _Conn([(41, "ASH-Ashgrove Lab (BWH)", 4)])

    def _boom(doc, context_dir):
        raise OSError("read-only file system")

    monkeypatch.setattr(labs, "write_labs_file", _boom)
    with redirect_stdout(io.StringIO()):
        doc, source = labs.refresh_labs_file(conn, tmp_path)
    assert source == "fetched"
    assert [r["code"] for r in doc["labs"]] == ["ASH"]


# --------------------------------------------------------------------------------------
# 4.5 python -m chat_nextseek.labs --report: read only, prints, writes nothing
# --------------------------------------------------------------------------------------

def _fake_mysql(monkeypatch, conn=None, raises=None):
    captured = {}

    def _connect(**kwargs):
        captured.update(kwargs)
        if raises is not None:
            raise raises
        return conn

    connector = types.ModuleType("mysql.connector")
    connector.connect = _connect
    mysql = types.ModuleType("mysql")
    mysql.connector = connector
    monkeypatch.setitem(sys.modules, "mysql", mysql)
    monkeypatch.setitem(sys.modules, "mysql.connector", connector)
    return captured


_PROD_ENV = {
    "MYSQL_HOST_PROD": "db.invalid", "MYSQL_PORT": "3307",
    "MYSQL_USER": "reader", "MYSQL_PROD_PASSWORD": "not-a-secret",
}


def test_report_prints_the_document_and_writes_nothing(tmp_path, monkeypatch, capsys):
    conn = _Conn([(41, "ASH-Ashgrove Lab (BWH)", 4), (7, "Example Institute of Technology", 2)])
    captured = _fake_mysql(monkeypatch, conn)
    monkeypatch.chdir(tmp_path)

    def _no_write(*a, **k):
        raise AssertionError("--report must not write")

    monkeypatch.setattr(labs, "write_labs_file", _no_write)

    code = labs.main(["--report"], env=dict(_PROD_ENV))

    assert code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["version"] == 1
    assert [r["code"] for r in doc["labs"]] == ["ASH"]
    assert doc["unparsed"][0]["reason"] == "no_code_prefix"
    assert list(tmp_path.iterdir()) == []
    # it connects exactly as ChatConfig._connect_db(env="prod") does
    assert captured == {
        "host": "db.invalid", "port": 3307, "user": "reader", "password": "not-a-secret",
        "charset": "utf8mb4", "collation": "utf8mb4_unicode_ci", "use_pure": True,
    }
    assert ("start_transaction", {"readonly": True}) in conn.calls


@pytest.mark.parametrize("missing", ["MYSQL_HOST_PROD", "MYSQL_USER", "MYSQL_PROD_PASSWORD"])
def test_report_exits_2_without_credentials(monkeypatch, capsys, missing):
    _fake_mysql(monkeypatch, _Conn())
    env = dict(_PROD_ENV)
    env.pop(missing)
    assert labs.main(["--report"], env=env) == 2
    assert capsys.readouterr().out == ""


def test_report_exits_2_when_the_connection_fails(monkeypatch, capsys):
    _fake_mysql(monkeypatch, raises=RuntimeError("refused"))
    assert labs.main(["--report"], env=dict(_PROD_ENV)) == 2
    assert capsys.readouterr().out == ""


def test_report_exits_1_when_the_read_fails(monkeypatch, capsys):
    _fake_mysql(monkeypatch, _Conn(fail_on_execute=RuntimeError("no such table")))
    assert labs.main(["--report"], env=dict(_PROD_ENV)) == 1
    assert capsys.readouterr().out == ""


def test_the_module_does_nothing_without_report(monkeypatch, capsys):
    _fake_mysql(monkeypatch, raises=AssertionError("must not connect"))
    assert labs.main([], env=dict(_PROD_ENV)) == 1


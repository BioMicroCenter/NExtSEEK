"""Nessie fixes U6: protocol counts follow the scope word (fix 10), protocol links use this box's public address
(fix 11), the RPPR names the sample types it left out (fix 12), the reply keeps the applied filters (fix 14),
and the capabilities text says charts and statistics are possible (fix 13)."""
from __future__ import annotations

import sqlite3
import types

import pytest

from chat_nextseek.agents import reporter as reporter_mod
from chat_nextseek.graph_scope import SCOPE_ATTR, GraphScope
from chat_nextseek.reports import runners

SOPS = [(1, "P.AAA-230101-a"), (2, "P.AAA-230102-b"), (3, "P.AAA-230103-c"), (4, "P.BBB-230201-d"),
        (5, "P.AAA-230105-e")]
PROJECTS_SOPS = [(1, 1), (1, 2), (1, 3), (2, 4), (2, 1), (1, 5)]       # (project, sop)
STUDIES = [(100, 10), (101, 10), (200, 20)]                            # (study, investigation)
SOPS_STUDIES = [(1, 100), (1, 101), (2, 100), (4, 101), (3, 200)]     # (sop, study); sop 1 sits in two studies


class _Cur:
    def __init__(self, db):
        self.c = db.cursor()

    def execute(self, sql, params=None):
        self.c.execute(sql.replace("%s", "?"), list(params or []))

    def fetchall(self):
        names = [d[0] for d in self.c.description]
        return [dict(zip(names, r)) for r in self.c.fetchall()]

    def close(self):
        pass


class _Db:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("ATTACH DATABASE ':memory:' AS seek_production")
        for ddl in ("sops (id INTEGER, title TEXT)", "projects_sops (project_id INTEGER, sop_id INTEGER)",
                    "studies (id INTEGER, investigation_id INTEGER)", "sops_studies (sop_id INTEGER, study_id INTEGER)"):
            self.db.execute(f"CREATE TABLE seek_production.{ddl}")
        for table, rows in (("sops", SOPS), ("projects_sops", PROJECTS_SOPS), ("studies", STUDIES),
                            ("sops_studies", SOPS_STUDIES)):
            self.db.executemany(f"INSERT INTO seek_production.{table} VALUES (?, ?)", rows)

    def cursor(self, dictionary=False):
        return _Cur(self.db)

    def is_connected(self):
        return True

    def close(self):
        pass


def _cfg(projects, investigations):
    db = _Db()
    cfg = types.SimpleNamespace(_db_conn=db, _connect_db=lambda env="prod": db, is_umbrella_published_project=None,
                                PROJECT_NAME_TO_ID=projects, INVESTIGATION_NAME_TO_ID=investigations)
    setattr(cfg, SCOPE_ATTR, GraphScope.admin("test"))
    return cfg


SHARED = _cfg({"ALPHA": 1, "GAMMA": 3}, {"ALPHA": 10, "DELTA": 20})   # ALPHA is both; DELTA investigation only; GAMMA project only


def _titles(name, kind, tmp_path):
    r = runners.run_project_protocols_report(SHARED, name, outputs_root=tmp_path, scope_kind=kind)
    assert r["ok"], r
    return r


# --- fix 10 ---
def test_a_shared_name_with_the_word_investigation_counts_the_studies_protocols(tmp_path):
    r = _titles("ALPHA", "investigation", tmp_path)
    assert r["rows_returned"] == 3                      # sops 1, 2, 4: sop 1 sits in two studies and counts once
    assert r["scope"]["kind"] == "investigation" and r["scope"]["investigation_id"] == 10


def test_a_shared_name_without_the_word_stays_the_projects_filing(tmp_path):
    assert _titles("ALPHA", None, tmp_path)["rows_returned"] == 4      # sops 1, 2, 3, 5 filed under project 1
    assert _titles("ALPHA", "project", tmp_path)["rows_returned"] == 4


def test_an_investigation_that_is_no_project_needs_no_word(tmp_path):
    r = _titles("DELTA", None, tmp_path)
    assert r["rows_returned"] == 1 and r["scope"]["investigation_title"] == "DELTA"


def test_a_project_that_is_no_investigation_ignores_the_word(tmp_path):
    assert _titles("GAMMA", "investigation", tmp_path)["rows_returned"] == 0     # falls to the project; none filed


# R7a (operator 2026-10-13, "Protocols only"): only protocol counts follow the scope word; a samples or published
# count for a name that is both stays on the project, as the graph route does.
@pytest.mark.parametrize("mode, gets_word", [
    ("protocols", {"protocols"}), ("samples", set()), ("published", set()), ("RPPR", {"protocols"}),
])
def test_only_the_protocols_runner_gets_the_scope_word(monkeypatch, tmp_path, mode, gets_word):
    seen = {}
    for block, fn in (("samples", "run_project_sample_report"), ("protocols", "run_project_protocols_report"),
                      ("published", "run_project_published_report")):
        def _fake(config, project, *, _block=block, **kw):
            seen[_block] = kw.get("scope_kind")
            return {"ok": True}
        monkeypatch.setattr(runners, fn, _fake)
    plan = types.SimpleNamespace(project="ALPHA", years=[], month_range=None, day_range=None, summary_mode=mode,
                                 reporter_context=None)
    runners.run_reporter_summary(SHARED, plan, str(tmp_path), lab_codes=[], scope_kind="investigation")
    assert seen and {b for b, word in seen.items() if word == "investigation"} == gets_word


def test_a_samples_report_saying_investigation_stays_on_the_shared_project():
    import inspect
    assert "scope_kind" not in inspect.signature(runners.run_project_sample_report).parameters
    assert "scope_kind" not in inspect.signature(runners.run_project_published_report).parameters
    assert runners._resolve_report_scope(SHARED, "ALPHA") == ("project", 1)


@pytest.mark.parametrize("q, kind", [
    ("How many protocols are registered for the Zed investigation?", "investigation"),
    ("protocols of the Zed project", "project"),
    ("protocols for Zed", None),
    ("the Zed project and its investigations", None),
])
def test_scope_kind_word(q, kind):
    assert runners.scope_kind_word(q) == kind


# --- fix 11 ---
# N2: a localhost link in the writer's input becomes this box's public address, never another box's host.
@pytest.mark.parametrize("url", ["https://box-one.example.org", "https://box-two.example.org/"])
def test_sanitized_protocol_links_use_the_boxs_public_address(monkeypatch, url):
    from chat_nextseek.reports.protocols import sanitize_protocols_for_llm
    monkeypatch.setenv("SEEK_PUBLIC_URL", url)
    out = sanitize_protocols_for_llm({"P.X": {"link": "http://localhost:3000/sops/5",
                                              "files": ["http://127.0.0.1/sops/6/content_blobs/2"],
                                              "kept": "https://elsewhere.example.org/sops/7"}})["P.X"]
    base = url.rstrip("/")
    assert out == {"link": f"{base}/sops/5", "files": [f"{base}/sops/6/content_blobs/2"],
                   "kept": "https://elsewhere.example.org/sops/7"}


def test_with_no_public_address_a_sanitized_link_keeps_only_its_path(monkeypatch):
    from chat_nextseek.reports.protocols import sanitize_protocols_for_llm
    monkeypatch.delenv("SEEK_PUBLIC_URL", raising=False)
    out = sanitize_protocols_for_llm({"P.X": {"link": "http://localhost:3000/sops/5"}})["P.X"]
    assert out == {"link": "/sops/5"}


@pytest.mark.parametrize("url", ["https://box-one.example.org", "https://box-two.example.org/"])
def test_protocol_links_use_the_boxs_public_address(monkeypatch, url):
    monkeypatch.setenv("SEEK_PUBLIC_URL", url)
    note = reporter_mod.protocol_link_note()
    assert url.rstrip("/") + "/sops/" in note and "fairdata-dev" not in note


def test_with_no_public_address_no_link_is_written(monkeypatch):
    monkeypatch.delenv("SEEK_PUBLIC_URL", raising=False)
    note = reporter_mod.protocol_link_note()
    assert "Write no protocol URLs" in note and "http" not in note


def test_the_writer_prompt_names_no_box():
    from pathlib import Path
    text = (Path(runners.__file__).parent.parent / "prompts" / "report_writer_agent.txt").read_text()
    assert "fairdata" not in text


# --- fix 12 ---
def _footer(table, mode="RPPR"):
    samples = {"sampletypes_table": table, "rows_returned": sum(table.values())}
    result = {"samples": samples} if mode == "RPPR" else samples
    cfg = types.SimpleNamespace()
    return "\n".join(runners.reporter_reply_footer(cfg, result, {}, mode))


def test_rppr_footer_says_what_the_top_types_left_out():
    table = {f"T{i}": 100 - i for i in range(12)}
    text = _footer(table)
    assert "7 other types" in text and "not" in text
    assert f"{sum(sorted(table.values())[:7]):,} samples" in text


def test_samples_footer_says_it_too_for_another_table():
    assert "1 other types (3 samples)" in _footer({"A": 9, "B": 8, "C": 7, "D": 6, "E": 5, "F": 3}, "samples")


def test_three_of_three_types_adds_no_note():
    assert "other types" not in _footer({"A": 1, "B": 2, "C": 3})
    assert runners._types_not_itemised({"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}) is None


# --- fix 14: the applied filters reach the reply ---
def test_the_summary_carries_the_filters_the_plan_applied(tmp_path, monkeypatch):
    monkeypatch.setattr(runners, "run_project_sample_report",
                        lambda *a, **k: {"ok": True, "rows_returned": 3, "sampletypes_table": {"A": 3}})
    plan = types.SimpleNamespace(project=None, years=[], month_range=None, day_range=["2026-09-25", "2026-10-09"],
                                 summary_mode="samples", reporter_context=None)
    cfg = _cfg({}, {})
    _, _, summary = runners.run_reporter_summary(cfg, plan, tmp_path, lab_codes=[], lab_names=[])
    assert summary["filters"] == {"day_range": ["2026-09-25", "2026-10-09"]}


def test_a_year_filter_is_carried_too(tmp_path, monkeypatch):
    monkeypatch.setattr(runners, "run_project_sample_report",
                        lambda *a, **k: {"ok": True, "rows_returned": 3, "sampletypes_table": {"A": 3}})
    plan = types.SimpleNamespace(project=None, years=[2024], month_range=None, day_range=None,
                                 summary_mode="samples", reporter_context=None)
    _, _, summary = runners.run_reporter_summary(_cfg({}, {}), plan, tmp_path, lab_codes=[], lab_names=[])
    assert summary["filters"] == {"years": [2024]}


# --- fix 13 ---
def test_capabilities_says_charts_and_statistics_are_possible():
    from pathlib import Path
    text = (Path(runners.__file__).parent.parent / "context" / "capabilities.md").read_text()
    assert "Container-CC" in text and "make charts" in text and "run statistics" in text
    assert "has no plotting or rendering capability" not in text and "outside scope" not in text

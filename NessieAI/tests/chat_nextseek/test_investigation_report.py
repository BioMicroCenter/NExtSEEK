"""Investigation-scoped sample report (Neo4j path) + smart resolution.

Report resolution: PROJECT map first (unchanged); on a miss, INVESTIGATION map ->
Neo4j-scoped sample report (the relational DB has no sample->investigation link).

This module used to pre-populate sys.modules with stub `chat_nextseek.helpers`,
`chat_nextseek.artifacts` and `chat_nextseek.reports.outputs` modules at import time,
to avoid a heavy/circular import that no longer exists. It never restored them, so
every alphabetically-later test module that imported one of those paths failed to
collect (test_lineage_leaves, test_portable_contract, test_report_code). The stubs
are gone; the real modules import cleanly.
"""
from __future__ import annotations

import types

import pytest

from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.reports import runners


class _Cfg:
    def __init__(self, inv_map):
        self.INVESTIGATION_NAME_TO_ID = inv_map


def test_resolve_investigation_exact_and_smart_but_bounded():
    cfg = _Cfg({"IMPACT": 3, "SRP": 6, "TESTING 404": 8})
    assert runners._resolve_investigation(cfg, "IMPACT") == (3, "IMPACT")
    assert runners._resolve_investigation(cfg, "impact") == (3, "IMPACT")       # case-insensitive
    assert runners._resolve_investigation(cfg, "Testing-404") == (8, "TESTING 404")  # punct-insensitive
    assert runners._resolve_investigation(cfg, "the IMPACT study") == (3, "IMPACT")   # whole-token
    # Not too permissive: a 1-2 char / substring noise must NOT match.
    assert runners._resolve_investigation(cfg, "IMP") is None
    assert runners._resolve_investigation(cfg, "GBM") is None
    assert runners._resolve_investigation(cfg, "") is None


def test_resolve_investigation_empty_map():
    assert runners._resolve_investigation(_Cfg({}), "IMPACT") is None


def test_tabulate_sample_uuids_counts():
    uuids = ["TIS-230101SHA-1", "TIS-230115SHA-2", "RNA-240301KAM-9", "BADUID"]
    t = runners._tabulate_sample_uuids(uuids)
    assert t["sampletypes_table"] == {"TIS": 2, "RNA": 1}
    assert t["labs_table"] == {"SHA": 2, "KAM": 1}
    assert t["years_table"] == {"23": 2, "24": 1}
    assert t["unparsable_uids"] == 1


def test_investigation_report_uses_neo4j_and_matches_shape(monkeypatch, tmp_path):
    captured = {}

    def fake_neo4j(config, title, years=None, month_range=None, day_range=None, *, inv_id=None):
        captured["title"] = title
        captured["years"] = years
        captured["inv_id"] = inv_id
        return ["TIS-230101SHA-1", "RNA-240301KAM-9"]

    monkeypatch.setattr(runners, "_neo4j_investigation_sample_uuids", fake_neo4j)
    out = runners._run_investigation_sample_report(
        _Cfg({}), (3, "IMPACT"), "IMPACT", years=[2023, 2024], outputs_root=str(tmp_path)
    )
    assert captured["title"] == "IMPACT" and captured["inv_id"] == 3
    assert out["ok"] is True
    assert out["scope"] == "investigation"
    assert out["investigation_id"] == 3
    assert out["rows_returned"] == 2
    # Same table keys as the project sample report (chatter formats identically).
    for k in ("sampletypes_table", "labs_table", "years_table", "months_table", "uuid_preview"):
        assert k in out


def test_sample_report_dispatches_investigation_when_not_a_project(monkeypatch, tmp_path):
    """The dispatch guard: a name that is NOT a project but IS an investigation
    routes to the investigation report; the project path is untouched otherwise."""
    class Cfg:
        PROJECT_NAME_TO_ID = {"PUBLISHED DATA": 1}
        INVESTIGATION_NAME_TO_ID = {"IMPACT": 3}

    called = {}

    def _fake_inv(*a, **k):
        called["hit"] = True
        return {"ok": True, "scope": "investigation"}

    monkeypatch.setattr(runners, "_run_investigation_sample_report", _fake_inv)
    res = runners.run_project_sample_report(Cfg(), "IMPACT")
    assert called.get("hit") is True
    assert res["scope"] == "investigation"


# ---------------------------------------------------------------------------
# T1.2 — every summary runner must resolve investigations, not just `samples`.
#
# PROJECT_NAME_TO_ID holds only {PUB, PUBLISHED, PUBLISHED DATA}; the six
# investigations live in INVESTIGATION_NAME_TO_ID. run_project_protocols_report and
# run_project_published_report called _normalize_project_id bare, so
# report.build_me_a_full_nih_report_for raised
# ValueError("Unknown project 'SRP'. Expected one of: ['PUB','PUBLISHED',
# 'PUBLISHED DATA'] or a numeric project_id.") — identically in baseline task 751 and
# post-fix task 815 — and the whole reply became "The reporter agent could not run
# the project report.", while report.how_many_samples_were_uploaded_6 resolved the
# same string "SRP" to investigation 6 because summary_mode "samples" was the only
# mode that touched the guarded runner.
# ---------------------------------------------------------------------------


class _ScopeCfg:
    # A live config's project map holds every SEEK project, and a project's investigation often carries another
    # title: project ALDER holds investigation "Alderb Investigation", project BIRCH holds "MIT_BIRCH". Both
    # investigation names also reach their project first, through the project map's fuzzy match.
    PROJECT_NAME_TO_ID = {"PUB": 1, "PUBLISHED": 1, "PUBLISHED DATA": 1, "ALDER": 2, "BIRCH": 5}
    INVESTIGATION_NAME_TO_ID = {"SRP": 6, "IMPACT": 3, "METNET": 4, "ALDERB INVESTIGATION": 102, "MIT_BIRCH": 105,
                                "CEDAR INVESTIGATION": 106}
    # The runners refuse a config without a scope; scope is not what these tests are about.
    GRAPH_SCOPE = GraphScope.admin("test")

    def __init__(self, rows=()):
        self._db_conn = _FakeConn(list(rows))

    def _connect_db(self, **k):
        return self._db_conn


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        return None

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return {}

    def close(self):
        return None


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self, **k):
        return _FakeCursor(self._rows)


def test_resolve_report_scope_classifies_all_three_cases():
    cfg = _ScopeCfg()
    assert runners._resolve_report_scope(cfg, "PUB") == ("project", 1)
    assert runners._resolve_report_scope(cfg, "SRP") == ("investigation", (6, "SRP"))
    assert runners._resolve_report_scope(cfg, None) == ("all", None)
    assert runners._resolve_report_scope(cfg, "  ") == ("all", None)
    assert runners._resolve_report_scope(cfg, 42) == ("project", 42)


def test_resolve_report_scope_still_raises_for_a_genuinely_unknown_name():
    with pytest.raises(ValueError):
        runners._resolve_report_scope(_ScopeCfg(), "NOT A REAL SCOPE")


def test_protocols_report_no_longer_dies_on_an_investigation_name(tmp_path):
    """Was: ValueError('Unknown project SRP'). Protocols cannot be narrowed to an
    investigation, so it reports everything and says so rather than raising."""
    result = runners.run_project_protocols_report(_ScopeCfg(), "SRP", outputs_root=tmp_path)

    assert result["ok"] is True
    assert result["scope"]["kind"] == "investigation"
    assert result["scope"]["unsupported"] is True
    assert result["scope"]["investigation_id"] == 6


def test_published_report_no_longer_dies_on_an_investigation_name(tmp_path, monkeypatch):
    """published already filters on the raw project string, so it works the moment
    resolution stops raising."""
    monkeypatch.setattr(runners, "tool_neo4j_query", lambda *a, **k: {"ok": True, "data": []})

    result = runners.run_project_published_report(_ScopeCfg(), "SRP", outputs_root=tmp_path)

    assert result["scope"]["kind"] == "investigation"
    assert result["scope"]["investigation_id"] == 6


@pytest.mark.parametrize("summary_mode", ["samples", "protocols", "published", "RPPR"])
def test_every_summary_mode_resolves_an_investigation_name(summary_mode, tmp_path, monkeypatch):
    """Only `samples` was covered before; the other three raised."""
    monkeypatch.setattr(runners, "tool_neo4j_query", lambda *a, **k: {"ok": True, "data": []})
    monkeypatch.setattr(
        runners, "_neo4j_investigation_sample_uuids", lambda *a, **k: ["TIS-230101SHA-1"]
    )
    plan = types.SimpleNamespace(
        project="SRP", years=[], month_range=None, day_range=None,
        summary_mode=summary_mode, reporter_context=None,
    )

    reporter_result, _saved, _summary = runners.run_reporter_summary(_ScopeCfg(), plan, tmp_path)

    assert reporter_result.get("ok") is True, reporter_result.get("error")
    assert "Unknown project" not in str(reporter_result.get("error") or "")


def test_rppr_samples_block_survives_a_failing_protocols_block(tmp_path, monkeypatch):
    """
    The single blanket `except` around all three blocks discarded the *successful*
    samples block along with the failure (task 815).
    """
    monkeypatch.setattr(
        runners, "_neo4j_investigation_sample_uuids", lambda *a, **k: ["TIS-230101SHA-1"]
    )

    def _boom(*a, **k):
        raise RuntimeError("protocols exploded")

    monkeypatch.setattr(runners, "run_project_protocols_report", _boom)
    monkeypatch.setattr(runners, "tool_neo4j_query", lambda *a, **k: {"ok": True, "data": []})
    plan = types.SimpleNamespace(
        project="SRP", years=[], month_range=None, day_range=None,
        summary_mode="RPPR", reporter_context=None,
    )

    reporter_result, _saved, _summary = runners.run_reporter_summary(_ScopeCfg(), plan, tmp_path)

    assert reporter_result["ok"] is True, "one failing block killed the whole report"
    assert reporter_result["samples"]["rows_returned"] == 1
    assert reporter_result["protocols"]["ok"] is False
    assert reporter_result["protocols"]["block"] == "protocols"


# ---------------------------------------------------------------------------
# The published report counts published samples of the named investigation
# ---------------------------------------------------------------------------

def _published(monkeypatch, tmp_path, project, rows=()):
    """Run the published report with the Neo4j tool answering ``rows``; return (result, cypher, parameters)."""
    seen = {}

    def tool(config, cypher, parameters=None):
        seen.setdefault("cypher", cypher)
        seen.setdefault("params", dict(parameters or {}))
        return {"ok": True, "data": [dict(r) for r in rows]}

    monkeypatch.setattr(runners, "tool_neo4j_query", tool)
    result = runners.run_project_published_report(_ScopeCfg(), project, outputs_root=tmp_path)
    return result, seen["cypher"], seen["params"]


def test_the_published_report_matches_the_resolved_investigation_by_id(monkeypatch, tmp_path):
    """SEEK holds two investigations under one title: the name resolved to an id, so the id is what is matched."""
    _result, cypher, params = _published(monkeypatch, tmp_path, "Cedar Investigation")
    assert "inv.id = $inv_id" in cypher and params["inv_id"] == 106
    assert "CONTAINS" not in cypher and "proj_hint" not in params


@pytest.mark.parametrize("name, project_id", [("Alder", 2), ("Alderb Investigation", 2), ("Birch", 5),
                                               ("MIT_BIRCH", 5)])
def test_a_project_name_matches_that_projects_investigations_by_its_id(monkeypatch, tmp_path, name, project_id):
    """A project's investigation often carries another title, so a name that resolves to a project matches the
    investigations IN_PROJECT to it, by the project's id: never its name against investigation titles."""
    _result, cypher, params = _published(monkeypatch, tmp_path, name)
    assert "EXISTS { MATCH (inv)-[:IN_PROJECT]->(proj:Project) WHERE proj.id = $project_id }" in cypher
    assert params["project_id"] == project_id and "inv_title" not in params and "inv_id" not in params
    assert "CONTAINS" not in cypher


def test_the_published_report_keeps_only_samples_with_a_doi_or_pmid_or_in_a_paper_study(monkeypatch, tmp_path):
    _result, cypher, _params = _published(monkeypatch, tmp_path, "Cedar Investigation")
    assert "coalesce(toString(s.DOI), '') <> '' OR coalesce(toString(s.PMID), '') <> ''" in cypher
    assert "EXISTS { MATCH (s)-[:IN_STUDY]->(pub:Study)" in cypher
    assert "RETURN DISTINCT s.uuid AS uuid, study_titles" in cypher


def test_a_sample_counts_once_and_a_holding_study_is_not_a_published_study(monkeypatch, tmp_path):
    rows = [{"uuid": "TIS-230101SHA-1", "study_titles": ["A paper", "Alder Study"], "sampletype": "TIS"},
            {"uuid": "TIS-230102SHA-2", "study_titles": [], "sampletype": "TIS"}]
    result, _cypher, _params = _published(monkeypatch, tmp_path, "Cedar Investigation", rows)
    samples = result["samples"]
    assert (samples["rows_returned"], samples["study_count"], samples["studies"]) == (2, 2, ["A paper", "Alder Study"])
    assert samples["uuid_studies"] == [["TIS-230101SHA-1", "A paper"], ["TIS-230101SHA-1", "Alder Study"],
                                       ["TIS-230102SHA-2", None]]
    scoped = runners._scope_published_to_labs(result, ["SHA"])["samples"]
    assert (scoped["rows_returned"], scoped["study_count"]) == (2, 2)


def test_the_investigation_sample_report_matches_by_id_when_it_has_one(monkeypatch):
    seen = []
    monkeypatch.setattr(runners, "tool_neo4j_query",
                        lambda config, cypher, parameters=None: seen.append((cypher, parameters)) or
                        {"ok": True, "data": []})
    runners._neo4j_investigation_sample_uuids(_ScopeCfg(), "Cedar Investigation", inv_id=106)
    runners._neo4j_investigation_sample_uuids(_ScopeCfg(), "Cedar Investigation")
    assert "inv.id = $inv_id" in seen[0][0] and seen[0][1]["inv_id"] == 106
    assert "toLower(trim(inv.title)) = toLower(trim($inv_title))" in seen[1][0]
    assert seen[1][1]["inv_title"] == "Cedar Investigation"

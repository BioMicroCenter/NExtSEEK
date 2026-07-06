"""Investigation-scoped sample report (Neo4j path) + smart resolution.

Report resolution: PROJECT map first (unchanged); on a miss, INVESTIGATION map ->
Neo4j-scoped sample report (the relational DB has no sample->investigation link).
"""
from __future__ import annotations

import sys
import types

# runners.py's `chat_nextseek.helpers` package eagerly imports heavy deps
# (mysql.connector, numpy, neo4j, ...) and re-exports runners (circular). To test
# the pure report logic hermetically, pre-populate sys.modules with just the
# submodules runners imports, so importing runners never runs the heavy __init__.
def _mk(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m

_pkg = _mk("chat_nextseek.helpers"); _pkg.__path__ = []  # type: ignore[attr-defined]
sys.modules["chat_nextseek.helpers"] = _pkg
sys.modules["chat_nextseek.helpers.dates"] = _mk(
    "chat_nextseek.helpers.dates",
    _normalize_project_id=lambda config, project: (
        None if project is None
        else project if isinstance(project, int)
        else int(str(project).strip()) if str(project).strip().isdigit()
        else config.PROJECT_NAME_TO_ID[str(project).strip().upper()]
        if str(project).strip().upper() in getattr(config, "PROJECT_NAME_TO_ID", {})
        else (_ for _ in ()).throw(ValueError(f"Unknown project '{project}'"))
    ),
    _normalize_years=lambda years: [str(y).strip()[-2:] for y in years],
    _month_range_to_yymmdd_bounds=lambda mr: ("000000", "999999"),
    _day_range_to_yymmdd_bounds=lambda dr: ("000000", "999999"),
)
_tools = _mk("chat_nextseek.helpers.tools"); _tools.__path__ = []  # type: ignore[attr-defined]
sys.modules["chat_nextseek.helpers.tools"] = _tools
sys.modules["chat_nextseek.helpers.tools.neo4j"] = _mk(
    "chat_nextseek.helpers.tools.neo4j",
    tool_neo4j_query=lambda *a, **k: {"ok": True, "data": []},
)


class _FakeArtifactStore:
    def __init__(self, root):
        self.root = root

    def write_json(self, **kwargs):
        return {"path": f"/tmp/{kwargs.get('filename', 'r.json')}"}


sys.modules["chat_nextseek.artifacts"] = _mk("chat_nextseek.artifacts", ArtifactStore=_FakeArtifactStore)
sys.modules["chat_nextseek.reports.nfcore"] = _mk("chat_nextseek.reports.nfcore", top_items=lambda *a, **k: [])
sys.modules["chat_nextseek.reports.outputs"] = _mk(
    "chat_nextseek.reports.outputs", persist_report_file=lambda *a, **k: None
)

from chat_nextseek.reports import runners  # noqa: E402


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

    def fake_neo4j(config, title, years=None, month_range=None, day_range=None):
        captured["title"] = title
        captured["years"] = years
        return ["TIS-230101SHA-1", "RNA-240301KAM-9"]

    monkeypatch.setattr(runners, "_neo4j_investigation_sample_uuids", fake_neo4j)
    out = runners._run_investigation_sample_report(
        _Cfg({}), (3, "IMPACT"), "IMPACT", years=[2023, 2024], outputs_root=str(tmp_path)
    )
    assert captured["title"] == "IMPACT"
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

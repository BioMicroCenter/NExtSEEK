"""A summary whose project does not resolve must not silently cover everything.

Observed 2026-07-24 for "Put together an annual progress report for the Kamm
project": reporter_plan.project came back null (Kamm is modelled as a LAB, not a
project), so run_project_sample_report ran across ALL projects and the reply
described 50,886 samples — the entire database — as the Kamm progress report.

The 2026-07-27 fix for that read `result["uuids"]`, a key neither sample runner
returned, so every lab-scoped report collapsed to zero instead (task 812:
uuids_saved 0, rows_returned 0, labs_table {}). The original version of this file
asserted against a hand-written dict that invented the `uuids` key, so it was green
and guarded nothing.

Every fixture below is therefore produced by *calling the real runner* against a
stubbed cursor. If a runner stops returning `uuids`, these tests fail.
"""
from __future__ import annotations

import types

import pytest

from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.reports.runners import (
    _drop_uuid_list,
    _lab_of,
    _lab_of_protocol_title,
    _scope_protocols_to_labs,
    _scope_published_to_labs,
    reporter_reply_footer,
    _run_investigation_sample_report,
    _scope_report_to_labs,
    lab_names_by_name,
    run_project_sample_report,
    run_reporter_summary,
)

UIDS = [
    "TIS-240612KAM-1-PUB",
    "DNA-240612KAM-2-PUB",
    "NHP-220913SED-15-PUB1",
    "MUS-200901ENG-23-PUB",
    "CEL-250319WHI-1-PUB",
]


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


def _admin_config(rows):
    """A runner config over `rows`, as an admin: the runners refuse a config without a scope, and scope is not what
    these tests are about (test_report_runner_scope.py is)."""
    return types.SimpleNamespace(_db_conn=_FakeConn(rows), _connect_db=lambda **k: None,
                                 GRAPH_SCOPE=GraphScope.admin("test"))


@pytest.fixture
def all_projects(tmp_path):
    """The genuine return value of run_project_sample_report, not a hand-written dict."""
    rows = [{"project_id": 1, "sample_id": i, "uuid": u} for i, u in enumerate(UIDS)]
    config = _admin_config(rows)
    return run_project_sample_report(config, None, outputs_root=tmp_path)


# ------------------------------------------------------------------- contract


def test_the_sample_runner_returns_the_uuid_list(all_projects):
    """The contract _scope_report_to_labs depends on. This is the regression lock."""
    assert "uuids" in all_projects, "run_project_sample_report dropped the uuid list"
    assert all_projects["uuids"] == UIDS
    assert all_projects["uuids_saved"] == len(UIDS)


def test_the_investigation_runner_returns_the_uuid_list(tmp_path, monkeypatch):
    import chat_nextseek.reports.runners as runners

    monkeypatch.setattr(runners, "_neo4j_investigation_sample_uuids", lambda *a, **k: list(UIDS))
    result = _run_investigation_sample_report(
        types.SimpleNamespace(), (6, "SRP"), "SRP", outputs_root=tmp_path
    )

    assert "uuids" in result, "_run_investigation_sample_report dropped the uuid list"
    assert result["uuids"] == UIDS


# --------------------------------------------------------------------- lab_of


def test_lab_of_reads_the_code_out_of_a_uid():
    assert _lab_of("TIS-240612KAM-1-PUB") == "KAM"
    assert _lab_of("NHP-220913SED-15-PUB1") == "SED"
    assert _lab_of("A.ADCD-250312ALT-1-PUB") == "ALT"
    assert _lab_of("not-a-uid") is None


# -------------------------------------------------------------------- scoping


def test_report_is_narrowed_to_the_requested_lab(all_projects):
    scoped = _scope_report_to_labs(all_projects, ["KAM"])
    assert scoped["uuids_saved"] == 2
    assert scoped["rows_returned"] == 2
    assert all("KAM" in u for u in scoped["uuids"])
    assert scoped["scope"]["kind"] == "lab"
    assert scoped["scope"]["lab_codes"] == ["KAM"]


def test_tables_are_recomputed_for_the_narrowed_set(all_projects):
    scoped = _scope_report_to_labs(all_projects, ["KAM"])
    assert scoped["labs_table"] == {"KAM": 2}
    assert scoped["sampletypes_table"] == {"DNA": 1, "TIS": 1}
    assert scoped["unparsable_uids"] == 0


def test_the_preview_is_narrowed_too(all_projects):
    """task 812 left uuid_preview listing non-KAM UIDs while reporting 0 rows."""
    scoped = _scope_report_to_labs(all_projects, ["KAM"])
    assert all(_lab_of(u) == "KAM" for u in scoped["uuid_preview"])


def test_multiple_labs_are_supported_and_case_insensitive(all_projects):
    scoped = _scope_report_to_labs(all_projects, ["kam", "SED"])
    assert scoped["uuids_saved"] == 3
    assert scoped["scope"]["lab_codes"] == ["KAM", "SED"]


def test_no_lab_codes_leaves_the_report_untouched(all_projects):
    """A genuinely global request must still work."""
    assert _scope_report_to_labs(all_projects, []) is all_projects
    assert _scope_report_to_labs(all_projects, ["  "])["uuids_saved"] == 5


def test_unknown_lab_narrows_to_nothing_rather_than_everything(all_projects):
    scoped = _scope_report_to_labs(all_projects, ["ZZZ"])
    assert scoped["uuids_saved"] == 0


# -------------------------------------------------------------- loud degrade


def test_a_result_with_no_uuid_list_is_reported_loudly(capsys):
    """
    The silent version of this is exactly how the regression shipped: a missing key
    read as an empty list and every scoped report became an empty one.
    """
    scoped = _scope_report_to_labs({"ok": True, "rows_returned": 5}, ["KAM"])

    assert "[DEBUG][REPORTER][SCOPE]" in capsys.readouterr().out
    assert scoped.get("scope", {}).get("error"), "an unscopeable result must say so"


def test_a_failed_report_is_not_rewritten_as_an_empty_success():
    failed = {"ok": False, "error": "DB connection failed"}
    assert _scope_report_to_labs(failed, ["KAM"]) is failed


# ------------------------------------------- the list must not escape the runner


def test_drop_uuid_list_strips_top_level_and_rppr_blocks():
    result = {
        "ok": True,
        "uuids": UIDS,
        "uuids_saved": 5,
        "uuid_preview": UIDS[:2],
        "samples": {"uuids": UIDS, "rows_returned": 5},
        "published": {"uuids": UIDS},
    }

    cleaned = _drop_uuid_list(result)

    assert "uuids" not in cleaned
    assert "uuids" not in cleaned["samples"]
    assert "uuids" not in cleaned["published"]
    # The useful summaries survive.
    assert cleaned["uuids_saved"] == 5
    assert cleaned["uuid_preview"] == UIDS[:2]
    assert cleaned["samples"]["rows_returned"] == 5


def test_run_reporter_summary_does_not_leak_the_uuid_list(tmp_path):
    """
    reporter_result reaches debug_payload and build_metadata_bundle. A 50k-string
    list there would land in a UI payload and any LLM context built from it.
    """
    rows = [{"project_id": 1, "sample_id": i, "uuid": u} for i, u in enumerate(UIDS)]
    config = _admin_config(rows)
    plan = types.SimpleNamespace(
        project=None, years=[], month_range=None, day_range=None, summary_mode="samples",
        reporter_context=None,
    )

    reporter_result, _saved, reporter_summary = run_reporter_summary(
        config, plan, tmp_path, lab_codes=["KAM"]
    )

    assert reporter_result["ok"] is True
    assert "uuids" not in reporter_result
    assert "uuids" not in str(reporter_summary)
    # Scoping still happened, and the durable copy is still linked.
    assert reporter_result["uuids_saved"] == 2
    assert reporter_result["uuid_report_file"]


# --------------------------------------------------------------------------- #
# T1.3 — protocols and published were never scoped
#
# _scope_report_to_labs wrapped only the samples call; protocols and published
# received project=None untouched and traversed everything. Task 812:
# published.samples.rows_returned 50179, study_count 42, 26 labs including
# FLY: 15994 — all of it labelled a Kamm report.
# --------------------------------------------------------------------------- #

PROTOCOL_TITLES = [
    "P.KAM-240612-V1_protocol.docx",
    "P.KAM-240701-V2_protocol.docx",
    "P.SAS-240827-V1_RSTR_BMDM_protocol.docx",
    "not-a-protocol-title",
]


def test_lab_of_protocol_title():
    assert _lab_of_protocol_title("P.KAM-240612-V1_protocol.docx") == "KAM"
    assert _lab_of_protocol_title("P.SAS-240827-V1_RSTR_BMDM_protocol.docx") == "SAS"
    assert _lab_of_protocol_title("not-a-protocol-title") is None


def test_protocols_are_narrowed_to_the_requested_lab():
    result = {"ok": True, "rows_returned": 4, "titles_saved": 4,
              "titles": PROTOCOL_TITLES, "titles_preview": PROTOCOL_TITLES,
              "labs_table": {"KAM": 2, "SAS": 1}}

    scoped = _scope_protocols_to_labs(result, ["KAM"])

    assert scoped["rows_returned"] == 2
    assert scoped["titles_saved"] == 2
    assert scoped["labs_table"] == {"KAM": 2}
    assert all(_lab_of_protocol_title(t) == "KAM" for t in scoped["titles_preview"])
    assert scoped["scope"] == {"kind": "lab", "lab_codes": ["KAM"]}


def test_protocols_scoping_is_a_no_op_without_lab_codes():
    result = {"ok": True, "titles": PROTOCOL_TITLES}
    assert _scope_protocols_to_labs(result, []) is result


def test_published_is_narrowed_and_study_counts_are_recomputed():
    """A narrowed row count next to a global study count is a contradiction."""
    result = {
        "ok": True,
        "samples": {
            "ok": True,
            "rows_returned": 4,
            "study_count": 3,
            "studies": ["Study A", "Study B", "Study C"],
            "uuid_studies": [
                ["TIS-240612KAM-1-PUB", "Study A"],
                ["DNA-240612KAM-2-PUB", "Study A"],
                ["NHP-220913SED-15-PUB1", "Study B"],
                ["MUS-200901ENG-23-PUB", "Study C"],
            ],
        },
        "protocols": {"ok": True, "rows_returned": 9},
    }

    scoped = _scope_published_to_labs(result, ["KAM"])
    samples = scoped["samples"]

    assert samples["rows_returned"] == 2
    assert samples["study_count"] == 1, "study_count must follow the rows, not stay global"
    assert samples["studies"] == ["Study A"]
    assert samples["labs_table"] == {"KAM": 2}
    assert scoped["scope"] == {"kind": "lab", "lab_codes": ["KAM"]}


def test_published_counts_unattributable_uids_explicitly():
    """The UIDs whose lab cannot be parsed are dropped, but they are counted."""
    result = {
        "ok": True,
        "samples": {
            "ok": True, "rows_returned": 2, "study_count": 1, "studies": ["S"],
            "uuid_studies": [["TIS-240612KAM-1-PUB", "S"], ["garbage-uid", "S"]],
        },
    }

    samples = _scope_published_to_labs(result, ["KAM"])["samples"]

    assert samples["rows_returned"] == 1
    assert samples["unattributable_uids"] == 1


def test_published_scoping_leaves_a_failed_block_alone():
    result = {"ok": True, "samples": {"ok": False, "error": "Neo4j query failed"}}
    assert _scope_published_to_labs(result, ["KAM"]) is result


# --------------------------------------------------------------------------- #
# T1.4 / T1.5 — the reply footer
# --------------------------------------------------------------------------- #

class _FooterCfg:
    INVESTIGATION_NAME_TO_ID = {"CSBC": 1, "GRIFFITH": 2, "IMPACT": 3,
                                "METNET": 4, "SRP": 6, "SHOULDERS": 7}
    # The note names investigations only to an admin (test_reporter_names_scope.py covers anyone else).
    GRAPH_SCOPE = GraphScope.admin("test")


def test_footer_reads_the_rppr_row_count_from_the_samples_block():
    """RPPR has no top-level rows_returned, so the footer used to print 0."""
    result = {"ok": True, "summary_mode": "RPPR",
              "samples": {"ok": True, "rows_returned": 7412}}

    lines = reporter_reply_footer(_FooterCfg(), result, {}, "RPPR")

    assert "- **Rows returned:** 7412" in lines


def test_footer_links_every_rppr_file():
    """RPPR generates three files; none of them used to be linked."""
    result = {"ok": True, "summary_mode": "RPPR", "samples": {"rows_returned": 1}}
    saved = {"samples_report": "/o/a.json", "protocols_report": "/o/b.json",
             "published_report": "/o/c.json"}

    joined = "\n".join(reporter_reply_footer(_FooterCfg(), result, saved, "RPPR"))

    for path in saved.values():
        assert path in joined
    assert "None" not in joined


def test_footer_says_a_lab_scope_is_not_a_project():
    result = {"ok": True, "rows_returned": 12,
              "scope": {"kind": "lab", "lab_codes": ["KAM"]}}

    joined = "\n".join(reporter_reply_footer(_FooterCfg(), result, {}, "samples"))

    assert "lab KAM, not a project" in joined
    assert "not the name of a project" not in joined       # no lookup ran for this request


def test_footer_stays_quiet_when_the_scope_is_a_real_project():
    result = {"ok": True, "rows_returned": 12, "project_id": 1,
              "uuid_report_file": "/o/r.json"}

    joined = "\n".join(reporter_reply_footer(_FooterCfg(), result, {}, "samples"))

    assert "not a project" not in joined
    assert "/o/r.json" in joined


def test_scope_reaches_the_chatter_payload(tmp_path):
    """_sub_summary dropped `scope`, so the chatter reconciled the contradictory
    blocks by narrating the global one."""
    rows = [{"project_id": 1, "sample_id": i, "uuid": u} for i, u in enumerate(UIDS)]
    config = _admin_config(rows)
    plan = types.SimpleNamespace(
        project=None, years=[], month_range=None, day_range=None,
        summary_mode="samples", reporter_context=None,
    )

    _result, _saved, reporter_summary = run_reporter_summary(
        config, plan, tmp_path, lab_codes=["KAM"]
    )

    assert reporter_summary["scope"]["kind"] == "lab"
    assert reporter_summary["scope"]["lab_codes"] == ["KAM"]


def test_lab_codes_are_taken_from_the_plan_when_the_caller_passes_none(tmp_path):
    """planner/tools.py:305 and granular.py:132 call without lab_codes."""
    rows = [{"project_id": 1, "sample_id": i, "uuid": u} for i, u in enumerate(UIDS)]
    config = _admin_config(rows)
    plan = types.SimpleNamespace(
        project=None, years=[], month_range=None, day_range=None, summary_mode="samples",
        reporter_context=types.SimpleNamespace(lab_codes=["KAM"]),
    )

    result, _saved, _summary = run_reporter_summary(config, plan, tmp_path)

    assert result["uuids_saved"] == 2, "the plan's lab_codes were ignored"
    assert result["scope"]["kind"] == "lab"


# --------------------------------------------------------------------------- #
# A lab named like a project scopes the report to that project (operator ruling, 2 Oct 2026)
# --------------------------------------------------------------------------- #

def _project_run(monkeypatch, tmp_path, project_names, lab_codes, lab_names, scope=None):
    """Run the summary with the runner stubbed; return (what the runner was asked for, the result, the footer)."""
    import chat_nextseek.reports.runners as runners

    asked = []

    def fake_runner(config, project, **kw):
        asked.append(project)
        return {"ok": True, "rows_returned": 1, "uuids": list(UIDS), "uuids_saved": 5, "project_id": project}

    monkeypatch.setattr(runners, "run_project_sample_report", fake_runner)
    config = types.SimpleNamespace(PROJECT_NAME_TO_ID=project_names, INVESTIGATION_NAME_TO_ID={},
                                   GRAPH_SCOPE=scope or GraphScope.admin("test"))
    plan = types.SimpleNamespace(project=None, years=[], month_range=None, day_range=None,
                                 summary_mode="samples", reporter_context=None)
    result, _saved, summary = run_reporter_summary(config, plan, tmp_path, lab_codes=lab_codes, lab_names=lab_names)
    footer = "\n".join(reporter_reply_footer(config, result, {}, "samples"))
    return asked, result, summary, footer


def test_a_lab_named_like_a_project_scopes_the_report_to_the_project(monkeypatch, tmp_path):
    """The counter-example's shape: the lab name equals a project name."""
    asked, result, summary, footer = _project_run(
        monkeypatch, tmp_path, {"NORTHFIELD": 21, "OTHER": 22}, ["NFD"], ["Northfield"])

    assert asked == ["NORTHFIELD"]
    assert result.get("scope", {}).get("kind") != "lab"
    assert summary["project"] == "NORTHFIELD"
    assert "Northfield is both a lab and the project NORTHFIELD; this report covers that project." in footer
    assert "Ask for the lab by its code NFD" in footer
    assert "not a project" not in footer


def test_another_lab_and_project_of_the_same_kind(monkeypatch, tmp_path):
    """Another entity: the match is an alias key with a space, case-folded."""
    asked, result, _summary, footer = _project_run(
        monkeypatch, tmp_path, {"BEND LAB": 31, "NORTHFIELD": 21}, ["BND"], ["bend lab"])

    assert asked == ["BEND LAB"]
    assert "both a lab and the project BEND LAB" in footer
    assert "code BND" in footer


def test_a_lab_with_no_matching_project_stays_a_lab_scope(monkeypatch, tmp_path):
    asked, result, _summary, footer = _project_run(
        monkeypatch, tmp_path, {"NORTHFIELD": 21}, ["QLN"], ["Quillon"])

    assert asked == [None]
    assert result["scope"]["kind"] == "lab"
    assert result["lab_names"] == ["Quillon"]
    assert "this report covers lab QLN, not a project. Quillon is a lab here and is not the name of a project or investigation." in footer


def test_labs_that_resolve_to_two_projects_stay_a_lab_scope(monkeypatch, tmp_path):
    asked, result, _summary, footer = _project_run(
        monkeypatch, tmp_path, {"NORTHFIELD": 21, "BEND LAB": 31}, ["NFD", "BND"], ["Northfield", "Bend Lab"])

    assert asked == [None]
    assert result["scope"]["kind"] == "lab"
    assert "both a lab and the project" not in footer


def test_a_member_without_the_project_keeps_the_lab_scope(monkeypatch, tmp_path):
    """F2: the project is not the caller's, so the lab's samples within their projects are what they get."""
    asked, result, _summary, footer = _project_run(
        monkeypatch, tmp_path, {"NORTHFIELD": 21}, ["NFD"], ["Northfield"], scope=GraphScope.for_projects([5]))

    assert asked == [None]
    assert result["scope"]["kind"] == "lab"
    assert "both a lab and the project" not in footer
    # Review F9: the lookup found the project, so the footer never says the name is not one.
    assert "lab_names" not in result
    assert "this report covers lab NFD, not a project." in footer and "is not the name of a project" not in footer


@pytest.mark.parametrize("projects,labs,names", [
    ({"ZETA": 41, "QUILL": 42}, ["ZETA", "QUILL"], ["Zeta", "Quill"]),   # two labs naming two projects
    ({"ZETA": 41}, ["ZETA", "QUILL"], ["Zeta", "Quillon"]),                # one names a project, one names nothing
])
def test_labs_the_lookup_matched_get_no_negative_footer(monkeypatch, tmp_path, projects, labs, names):
    asked, result, _summary, footer = _project_run(monkeypatch, tmp_path, projects, labs, names)

    assert asked == [None] and result["scope"]["kind"] == "lab"
    assert "lab_names" not in result
    assert f"this report covers lab {', '.join(sorted(labs))}, not a project." in footer
    assert "is not the name of a project" not in footer


def test_a_member_with_the_project_is_redirected_to_it(monkeypatch, tmp_path):
    """Another caller of the same kind: the project id is in the member's scope."""
    asked, _result, _summary, footer = _project_run(
        monkeypatch, tmp_path, {"NORTHFIELD": 21}, ["NFD"], ["Northfield"], scope=GraphScope.for_projects([5, 21]))

    assert asked == ["NORTHFIELD"]
    assert "both a lab and the project NORTHFIELD" in footer


def _entity(*matches):
    return types.SimpleNamespace(lab_matches=[
        types.SimpleNamespace(name=n, rule=r) for n, r in matches])


def test_a_lab_asked_for_by_its_code_passes_no_name_to_the_reporter():
    """F1: the code match adds the lab's name, which must not redirect the report to a project of that name."""
    assert lab_names_by_name(_entity(("Northfield", "code"))) == []


def test_a_lab_asked_for_by_its_name_passes_the_name():
    """Another request of the same kind: a name rule (and a dict-shaped entity result) still counts."""
    assert lab_names_by_name(_entity(("Northfield", "name"), ("Bend", "possessive"), ("Northfield", "lab_phrase"))) \
        == ["Northfield", "Bend"]
    assert lab_names_by_name({"lab_matches": [{"name": "Bend", "rule": "honorific"}, {"name": "X", "rule": "code"}]}) \
        == ["Bend"]


def test_a_code_only_request_keeps_the_lab_scope(monkeypatch, tmp_path):
    asked, result, _summary, footer = _project_run(
        monkeypatch, tmp_path, {"NORTHFIELD": 21}, ["NFD"], lab_names_by_name(_entity(("Northfield", "code"))))

    assert asked == [None]
    assert result["scope"]["kind"] == "lab"


# --------------------------------------------------------------------------- #
# R4 unit D: a lab named like an investigation or an alias; footer; distinct counts
# --------------------------------------------------------------------------- #

def _inv_run(monkeypatch, tmp_path, *, projects=None, investigations=None, rows=None, lab_codes=("ZQL",),
             lab_names=("Zeta",), plan_project=None, scope=None):
    """Run the summary with both runners stubbed; return (what each was asked for, result, summary, footer)."""
    import chat_nextseek.reports.runners as runners

    asked = {"project": [], "inv": []}

    def fake_project(config, project, **kw):
        # the real runner takes the investigation path when the scope resolves to one
        kind, found = runners._resolve_report_scope(config, project)
        if kind == "investigation":
            asked["inv"].append(found)
            return {"ok": True, "scope": "investigation", "investigation_id": found[0], "rows_returned": 5,
                    "uuids": list(UIDS), "uuids_saved": 5}
        asked["project"].append(project)
        return {"ok": True, "rows_returned": 1, "uuids": list(UIDS), "uuids_saved": 5, "project_id": project}

    monkeypatch.setattr(runners, "run_project_sample_report", fake_project)
    config = types.SimpleNamespace(
        PROJECT_NAME_TO_ID=projects or {}, INVESTIGATION_NAME_TO_ID=investigations or {},
        FULL_PROJECTS=rows or [], GRAPH_SCOPE=scope or GraphScope.admin("test"))
    plan = types.SimpleNamespace(project=plan_project, years=[], month_range=None, day_range=None,
                                 summary_mode="samples", reporter_context=None)
    result, _saved, summary = run_reporter_summary(config, plan, tmp_path, lab_codes=list(lab_codes),
                                                   lab_names=list(lab_names))
    footer = "\n".join(reporter_reply_footer(config, result, {}, "samples"))
    return asked, result, summary, footer


def _inv_row(name, project_id, alts=()):
    return {"name": name, "entity_type": "investigation", "parent_project": "Owner", "project_id": project_id,
            "alternative_names": list(alts)}


def test_a_lab_named_like_an_investigation_scopes_the_report_to_it(monkeypatch, tmp_path):
    """Counter-example shape: no project of that name, an investigation whose title is the lab name."""
    asked, result, summary, footer = _inv_run(
        monkeypatch, tmp_path, investigations={"ZETA STUDY": 41}, rows=[_inv_row("Zeta Study", 3)],
        lab_names=("Zeta Study",))

    assert asked["inv"] == [(41, "ZETA STUDY")] and asked["project"] == []
    assert result.get("scope") != {"kind": "lab"} and "lab_project" in result
    assert "Zeta Study is both a lab and the investigation ZETA STUDY; this report covers that investigation." in footer
    assert "is not the name of a project" not in footer


def test_a_lab_named_like_an_investigation_alias(monkeypatch, tmp_path):
    """Another entity: the lab name is an alias in an investigation row (the row name is the SEEK title)."""
    asked, result, _s, footer = _inv_run(
        monkeypatch, tmp_path, investigations={"QUILL TRIAL": 52}, rows=[_inv_row("Quill Trial", 8, ["Quill"])],
        lab_codes=("QLL",), lab_names=("quill",))

    assert asked["inv"] == [(52, "QUILL TRIAL")]
    assert "both a lab and the investigation" in footer and "code QLL" in footer


def test_a_project_wins_over_an_investigation_of_the_same_name(monkeypatch, tmp_path):
    asked, _r, _s, footer = _inv_run(
        monkeypatch, tmp_path, projects={"ZETA": 9}, investigations={"ZETA": 41}, rows=[_inv_row("Zeta", 9)],
        lab_names=("Zeta",))

    assert asked["project"] == ["ZETA"] and asked["inv"] == []
    assert "the project" in footer


def test_an_investigation_outside_the_callers_projects_stays_a_lab_scope(monkeypatch, tmp_path):
    member = GraphScope.for_projects((3,), "test")
    asked, result, _s, footer = _inv_run(
        monkeypatch, tmp_path, investigations={"ZETA STUDY": 41}, rows=[_inv_row("Zeta Study", 99)],
        lab_names=("Zeta Study",), scope=member)

    assert asked["inv"] == [] and result["scope"]["kind"] == "lab"


def test_the_plan_may_carry_the_lab_name_as_its_project(monkeypatch, tmp_path):
    """Reporter prompt item U4-01: the model now writes the lab's name as the project. A name that is only a lab
    must not become an error or a fuzzy project: it is a lab scope with the honest footer."""
    asked, result, _s, footer = _inv_run(
        monkeypatch, tmp_path, projects={"OTHER": 1}, plan_project="Quillon", lab_codes=("QLN",),
        lab_names=("Quillon",))

    assert asked["project"] == [None]
    assert result["scope"]["kind"] == "lab"
    assert "Quillon is a lab here and is not the name of a project or investigation." in footer


def test_the_plan_carrying_an_investigation_alias_resolves_it(monkeypatch, tmp_path):
    asked, _r, _s, _f = _inv_run(
        monkeypatch, tmp_path, investigations={"ZETA STUDY": 41}, rows=[_inv_row("Zeta Study", 3, ["Zed"])],
        plan_project="Zed", lab_codes=("ZED",), lab_names=("Zed",))

    assert asked["inv"] == [(41, "ZETA STUDY")]


def test_the_negative_footer_needs_the_lookup_to_have_run():
    """A request by lab code never looked a name up, so it cannot claim the name is not a project."""
    result = {"ok": True, "rows_returned": 3, "scope": {"kind": "lab", "lab_codes": ["ZQL"]}}

    joined = "\n".join(reporter_reply_footer(_FooterCfg(), result, {}, "samples"))

    assert "this report covers lab ZQL" in joined
    assert "not the name of a project" not in joined


def test_report_counts_are_distinct_samples(tmp_path):
    """SEEK holds the same sample linked to a project twice: two rows, one sample."""
    rows = [{"project_id": 1, "sample_id": 1, "uuid": "ZZZ-990101ABC-1-PUB"},
            {"project_id": 1, "sample_id": 1, "uuid": "ZZZ-990101ABC-1-PUB"},
            {"project_id": 1, "sample_id": 2, "uuid": "ZZZ-990101ABC-2-PUB"}]

    result = run_project_sample_report(_admin_config(rows), None, outputs_root=tmp_path)

    assert result["rows_returned"] == 2 and result["uuids_saved"] == 2
    assert result["labs_table"] == {"ABC": 2}


def _summary_for_labs_table(monkeypatch, tmp_path, labs_table):
    import chat_nextseek.reports.runners as runners

    monkeypatch.setattr(runners, "run_project_sample_report", lambda config, project, **kw: {
        "ok": True, "rows_returned": 4, "uuids": [], "project_id": 9, "labs_table": labs_table})
    config = types.SimpleNamespace(PROJECT_NAME_TO_ID={"ZETA": 9}, INVESTIGATION_NAME_TO_ID={},
                                   GRAPH_SCOPE=GraphScope.admin("test"))
    plan = types.SimpleNamespace(project="Zeta", years=[], month_range=None, day_range=None,
                                 summary_mode="samples", reporter_context=None)
    return run_reporter_summary(config, plan, tmp_path, lab_codes=[], lab_names=[])[2]


def test_a_single_lab_table_is_not_offered_as_a_breakdown(monkeypatch, tmp_path):
    """U4.4: under a project scope one lab code is no breakdown, so 'all N originate from lab X' has no source."""
    summary = _summary_for_labs_table(monkeypatch, tmp_path, {"ABC": 4})

    assert not summary.get("top_labs")


def test_a_real_lab_breakdown_is_still_offered(monkeypatch, tmp_path):
    summary = _summary_for_labs_table(monkeypatch, tmp_path, {"ABC": 3, "DEF": 1})

    assert summary["top_labs"]


def test_reporter_prompt_hands_a_lab_name_to_the_runner():
    """U4-01..03 ship with the runner code that reads the lab's name; they never ship alone."""
    from pathlib import Path
    import chat_nextseek

    text = (Path(chat_nextseek.__file__).parent / "prompts" / "reporter_agent.txt").read_text()

    assert "leave project null" not in text and "A lab is not a project" not in text
    assert "the runner decides" in text
    assert "or the user named a lab (see Project above)" in text
    assert '"project": "Zeta"' in text

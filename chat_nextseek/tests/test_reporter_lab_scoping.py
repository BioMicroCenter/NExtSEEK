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

from chat_nextseek.reports.runners import (
    _drop_uuid_list,
    _lab_of,
    _lab_of_protocol_title,
    _scope_protocols_to_labs,
    _scope_published_to_labs,
    reporter_reply_footer,
    _run_investigation_sample_report,
    _scope_report_to_labs,
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


@pytest.fixture
def all_projects(tmp_path):
    """The genuine return value of run_project_sample_report, not a hand-written dict."""
    rows = [{"project_id": 1, "sample_id": i, "uuid": u} for i, u in enumerate(UIDS)]
    config = types.SimpleNamespace(_db_conn=_FakeConn(rows), _connect_db=lambda **k: None)
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
    config = types.SimpleNamespace(_db_conn=_FakeConn(rows), _connect_db=lambda **k: None)
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
    assert "SRP" in joined and "MetNet".upper() in joined.upper()


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
    config = types.SimpleNamespace(_db_conn=_FakeConn(rows), _connect_db=lambda **k: None)
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
    config = types.SimpleNamespace(_db_conn=_FakeConn(rows), _connect_db=lambda **k: None)
    plan = types.SimpleNamespace(
        project=None, years=[], month_range=None, day_range=None, summary_mode="samples",
        reporter_context=types.SimpleNamespace(lab_codes=["KAM"]),
    )

    result, _saved, _summary = run_reporter_summary(config, plan, tmp_path)

    assert result["uuids_saved"] == 2, "the plan's lab_codes were ignored"
    assert result["scope"]["kind"] == "lab"

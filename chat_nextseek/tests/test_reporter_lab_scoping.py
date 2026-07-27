"""A summary whose project does not resolve must not silently cover everything.

Observed 2026-07-24 for "Put together an annual progress report for the Kamm
project": reporter_plan.project came back null (Kamm is modelled as a LAB, not a
project), so run_project_sample_report ran across ALL projects and the reply
described 50,886 samples — the entire database — as the Kamm progress report.
"""
from __future__ import annotations

from chat_nextseek.reports.runners import _lab_of, _scope_report_to_labs

ALL_PROJECTS = {
    "ok": True,
    "project_id": None,
    "rows_returned": 5,
    "uuids_saved": 5,
    "uuids": [
        "TIS-240612KAM-1-PUB",
        "DNA-240612KAM-2-PUB",
        "NHP-220913SED-15-PUB1",
        "MUS-200901ENG-23-PUB",
        "CEL-250319WHI-1-PUB",
    ],
}


def test_lab_of_reads_the_code_out_of_a_uid():
    assert _lab_of("TIS-240612KAM-1-PUB") == "KAM"
    assert _lab_of("NHP-220913SED-15-PUB1") == "SED"
    assert _lab_of("A.ADCD-250312ALT-1-PUB") == "ALT"
    assert _lab_of("not-a-uid") is None


def test_report_is_narrowed_to_the_requested_lab():
    scoped = _scope_report_to_labs(ALL_PROJECTS, ["KAM"])
    assert scoped["uuids_saved"] == 2
    assert scoped["rows_returned"] == 2
    assert all(u.count("KAM") for u in scoped["uuids"])
    assert scoped["scope"]["kind"] == "lab"
    assert scoped["scope"]["lab_codes"] == ["KAM"]


def test_tables_are_recomputed_for_the_narrowed_set():
    scoped = _scope_report_to_labs(ALL_PROJECTS, ["KAM"])
    assert scoped["labs_table"] == {"KAM": 2}
    assert scoped["sampletypes_table"] == {"DNA": 1, "TIS": 1}
    assert scoped["unparsable_uids"] == 0


def test_multiple_labs_are_supported_and_case_insensitive():
    scoped = _scope_report_to_labs(ALL_PROJECTS, ["kam", "SED"])
    assert scoped["uuids_saved"] == 3
    assert scoped["scope"]["lab_codes"] == ["KAM", "SED"]


def test_no_lab_codes_leaves_the_report_untouched():
    """A genuinely global request must still work."""
    assert _scope_report_to_labs(ALL_PROJECTS, []) is ALL_PROJECTS
    assert _scope_report_to_labs(ALL_PROJECTS, ["  "])["uuids_saved"] == 5


def test_unknown_lab_narrows_to_nothing_rather_than_everything():
    scoped = _scope_report_to_labs(ALL_PROJECTS, ["ZZZ"])
    assert scoped["uuids_saved"] == 0

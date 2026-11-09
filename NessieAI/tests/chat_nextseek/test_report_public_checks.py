"""Post-checks and clean-up on what goes into public repository packages (GEO / SRA / PRIDE).
Made-up records only."""
from __future__ import annotations

import pytest

from chat_nextseek.reports.public_checks import (
    do_not_publish_warning,
    normalize_dates,
    scrub_public_report,
    strip_internal_attributes,
)


def _md(*samples):
    return {"ok": True, "data": {"data": [{"sample_type": "D.SEQ", "samples": list(samples)}]}}


def _s(uid, name, notes="", **extra):
    return {"uuid": uid, "metadata": {"Name": name, "Notes": notes, **extra}}


# ---- (a) accession labels ----
def test_run_label_kept_only_for_an_accession():
    rep = {"samples": [{"description": "Arm: X; SRA run: SRR123456"},
                       {"description": "Arm: Y; SRA run: Lab_Batch_7"},
                       {"description": "SRA run: Lab_Batch_8; Arm: Z"},
                       {"description": "SRA run: ERR99, DRR100"}]}
    out, stats = scrub_public_report(rep, [])
    assert [r["description"] for r in out["samples"]] == [
        "Arm: X; SRA run: SRR123456", "Arm: Y", "Arm: Z", "SRA run: ERR99, DRR100"]
    assert stats["claims_dropped"] == 2


KEEP_CASES = [
    "Data are available under BioProject: PRJNA900245 and GEO.",
    "Raw reads deposited (BioProject: PRJNA900245).",
    "SRA run: SRR123 (lane 1)",
    "BioSample: SAMN12345678 (donor A)",
    "SRA run: SRR1, Arm: X, Tissue: blood",
    "BioProject=PRJEB123, BioSample=SAME123",
    "SRA accession: SRX123456",
]


@pytest.mark.parametrize("text", KEEP_CASES)
def test_valid_accession_clauses_are_untouched(text):
    assert scrub_public_report({"c": text}, [])[0] == {"c": text}


@pytest.mark.parametrize("text,want", [
    ("SRA run accession: Lab_7", ""),
    ("Arm: X | SRA run: Lab_7 | Sex: F", "Arm: X | Sex: F"),
    ("Arm: CD8a; SRA run: Reinf_Aug_2022_6", "Arm: CD8a"),
    ("Raw reads (SRA run: Lab_7) in lane 2", "Raw reads in lane 2"),
])
def test_a_label_with_a_non_accession_value_is_cut_cleanly(text, want):
    assert scrub_public_report({"c": text}, [])[0] == {"c": want}


def test_other_accession_labels_follow_the_same_rule():
    rep = {"a": "BioProject: PRJNA12; BioSample: sample-one", "b": "BioSample: SAMN555"}
    out, _ = scrub_public_report(rep, [])
    assert out == {"a": "BioProject: PRJNA12", "b": "BioSample: SAMN555"}


# ---- (b) internal notes ----
def test_notes_never_reach_the_coder_and_are_collected():
    md = _md(_s("D.1", "libA", "Keep this internal"), _s("D.2", "libB", ""))
    clean, withheld, notes = strip_internal_attributes(md)
    metas = [s["metadata"] for s in clean["data"]["data"][0]["samples"]]
    assert all("Notes" not in m for m in metas) and metas[0]["Name"] == "libA"
    assert "Notes" in md["data"]["data"][0]["samples"][0]["metadata"]  # input untouched
    assert notes == ["Keep this internal"] and withheld == []


def test_do_not_publish_notes_are_listed_and_rows_kept_by_default():
    md = _md(_s("D.1", "libA", "Lower quality, DO NOT PUBLISH, use D.9"), _s("D.2", "libB", "not for publishing"),
             _s("D.3", "libC", "use this one"))
    clean, withheld, _ = strip_internal_attributes(md)
    assert [w["name"] for w in withheld] == ["libA", "libB"]
    assert len(clean["data"]["data"][0]["samples"]) == 3
    assert "2 libraries" in do_not_publish_warning(withheld) and "libA" in do_not_publish_warning(withheld)
    assert do_not_publish_warning([]) == ""


def test_drop_switch_removes_the_marked_rows():
    md = _md(_s("D.1", "libA", "do not publish"), _s("D.3", "libC", "fine"))
    clean, withheld, _ = strip_internal_attributes(md, drop_do_not_publish=True)
    assert [s["uuid"] for s in clean["data"]["data"][0]["samples"]] == ["D.3"] and len(withheld) == 1


def test_stored_notes_are_removed_from_public_cells():
    notes = ["Higher Quality, use this one", "ok"]
    rep = {"samples": [{"description": "Arm: A; Higher Quality, use this one; SRA run: SRR1"},
                       {"description": "Higher Quality, use this one"},
                       {"description": "Arm: B; ok pass"}]}
    out, stats = scrub_public_report(rep, notes)
    assert [r["description"] for r in out["samples"]] == ["Arm: A; SRA run: SRR1", "", "Arm: B; ok pass"]
    assert stats["notes_removed"] == 2  # a short note is removed only when a whole cell equals it


# ---- (d) dates ----
def test_midnight_dates_become_plain_dates_other_times_stay():
    md = _md(_s("D.1", "a", Born="2014-10-28 00:00:00", Seen="2014-10-28 09:30:00", Free="at 2020-01-02 00:00:00."))
    out = normalize_dates(md)
    m = out["data"]["data"][0]["samples"][0]["metadata"]
    assert (m["Born"], m["Seen"], m["Free"]) == ("2014-10-28", "2014-10-28 09:30:00", "at 2020-01-02.")
    assert md["data"]["data"][0]["samples"][0]["metadata"]["Born"].endswith("00:00:00")


# ---- wiring ----
def test_fetched_metadata_bundle_has_plain_dates(monkeypatch):
    from chat_nextseek.reports import metadata as mdmod
    raw = _md(_s("D.1", "a", SampleCreationDate="2020-02-11 00:00:00"))
    monkeypatch.setattr(mdmod, "tool_nextseek_api_request", lambda *a, **k: raw)
    out = mdmod.fetch_reporter_metadata(object(), ["D.1"])
    assert out["data"]["data"][0]["samples"][0]["metadata"]["SampleCreationDate"] == "2020-02-11"


def test_notes_that_are_also_public_values_are_not_scrubbed():
    md = _md(_s("D.1", "run7_libA", "Single Cell", SequencingType="Single Cell"),
             _s("D.2", "run7_libB", "run7", ),
             _s("D.3", "run7_libC", "Higher Quality, use this one"))
    _, _, notes = strip_internal_attributes(md)
    assert notes == ["Higher Quality, use this one"]
    rep = {"a": "Single Cell RNA-seq of run7_libA", "b": "Higher Quality, use this one"}
    assert scrub_public_report(rep, notes)[0] == {"a": "Single Cell RNA-seq of run7_libA", "b": ""}


@pytest.mark.parametrize("attr", ["Notes", "QC_notes", "ShipNotes", "receiptnotes"])
def test_every_internal_note_field_is_stripped_and_read(attr):
    md = _md({"uuid": "D.1", "metadata": {"Name": "libA", attr: "do not publish this"}})
    clean, withheld, notes = strip_internal_attributes(md)
    assert attr not in clean["data"]["data"][0]["samples"][0]["metadata"]
    assert len(withheld) == 1 and notes == ["do not publish this"]


def test_extraction_interval_notes_is_public():
    md = _md(_s("D.1", "libA", "", ExtractionIntervalNotes="Day 3 after dosing"))
    clean, _, notes = strip_internal_attributes(md)
    assert clean["data"]["data"][0]["samples"][0]["metadata"]["ExtractionIntervalNotes"] == "Day 3 after dosing"
    assert notes == []


@pytest.mark.parametrize("note", ["DO-NOT-PUBLISH", "don\u2019t publish", "not to be published", "should not be published",
                                  "never publish", "not for publishing", "not for publication",
                                  "Lower Quality, do not publish"])
def test_do_not_publish_phrasings_match(note):
    _, withheld, _ = strip_internal_attributes(_md(_s("D.1", "libA", note)))
    assert len(withheld) == 1


@pytest.mark.parametrize("note", ["Unpublished", "Lab X Unpublished", "ready to publish"])
def test_unpublished_is_not_do_not_publish(note):
    assert strip_internal_attributes(_md(_s("D.1", "libA", note)))[1] == []


def test_withheld_list_is_deduplicated_by_uid():
    s = _s("D.1", "libA", "do not publish")
    md = {"data": {"data": [{"samples": [s]}, {"samples": [s]}]}}
    assert len(strip_internal_attributes(md)[1]) == 1


def test_warning_wording_is_the_approved_text():
    w = [{"uid": f"D.{i}", "name": f"lib{i}"} for i in range(7)]
    assert do_not_publish_warning(w) == (
        '7 libraries are marked "do not publish" in their lab notes: lib0, lib1, lib2, lib3, lib4 and 2 more. '
        "They are still in the package; remove those rows before you submit.")


def test_only_exact_midnight_is_cut():
    out = normalize_dates(["2020-01-01T00:00:00Z", "2020-01-01T00:00:00.000-05:00", "2020-01-01 00:00:00", "2020-01-01T00:00:00"])
    assert out == ["2020-01-01T00:00:00Z", "2020-01-01T00:00:00.000-05:00", "2020-01-01", "2020-01-01"]


def test_code_path_scrubs_leaks_from_the_coders_rows():
    from unittest.mock import MagicMock
    from chat_nextseek.reports import outputs as outmod
    from chat_nextseek.schemas import ReportCoderOutput

    md = {"data": {"data": [{"sample_type": "D.SEQ", "samples": [
        {"metadata": {"UID": f"D.SEQ-{i}", "Name": f"lab_{i}"}} for i in range(12)]}]}}
    code = ("result = {'samples': [{'description': 'Arm: A; Lab-only remark here; SRA run: ' + "
            "s['metadata']['Name']} for g in data['data']['data'] for s in g['samples']]}")
    coder = MagicMock(return_value=ReportCoderOutput(extraction_code=code))
    out = outmod._produce_report_output(
        config=MagicMock(), user_query="q", report_type_value="GEO", reporter_context={},
        template_for_llm={"samples": [{}]}, metadata=md, report_writer_fn=MagicMock(),
        report_coder_fn=coder, log_dir="/tmp", internal_notes=["Lab-only remark here"])
    assert {r["description"] for r in out.report.samples} == {"Arm: A"}

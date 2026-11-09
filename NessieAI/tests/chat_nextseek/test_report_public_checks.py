"""Post-checks and clean-up on what goes into public repository packages (GEO / SRA / PRIDE).
Made-up records only."""
from __future__ import annotations

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
    md = _md(_s("D.1", "libA", "Lower quality, DO NOT PUBLISH, use D.9"), _s("D.2", "libB", "not for publication"),
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

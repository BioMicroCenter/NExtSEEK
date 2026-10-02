"""The curator sheet adapter (tool spec 4.3), with the earlier tool's sheet cases ported."""
import csv
import json
from pathlib import Path

import pytest

from nextseek_api.studies.sources import matching
from nextseek_api.studies.sources.sheet import (SheetError, SheetRow, read_sheet, sheet_associations,
                                                validate_rows, validate_structure)
from nextseek_api.studies.tests.conftest import FakeReader, uid

HEADERS = ["study_title", "investigation_title", "sample_uuid", "study_description"]
U2, U3 = uid(2, kind="D.SEQ"), uid(3, kind="D.SEQ")


def write_csv(path: Path, rows, headers=None):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers or HEADERS)
        writer.writerows(rows)
    return path


def row(study="Paper One", inv="Alpha Investigation", uuid=U2, desc="", number=2, **extra):
    return SheetRow(number, study, inv, uuid, desc, **extra)


def test_read_csv(tmp_path):
    rows = read_sheet(write_csv(tmp_path / "s.csv", [["Paper One", "Alpha Investigation", U2, "a description"]]))
    assert rows == [SheetRow(2, "Paper One", "Alpha Investigation", U2, "a description")]


def test_headers_are_trimmed_and_lowercased_and_sample_uid_is_read_as_sample_uuid(tmp_path):
    path = write_csv(tmp_path / "s.csv", [["Paper One", "Alpha Investigation", U2, "", "10.0000/one", "1111", "21"]],
                     headers=["  Study_Title ", "INVESTIGATION_TITLE", "Sample_UID", "Study_Description", "DOI",
                              "pmid", "seek_study_id"])
    [r] = read_sheet(path)
    assert (r.sample_uuid, r.doi, r.pmid, r.seek_study_id) == (U2, "10.0000/one", "1111", 21)


def test_both_uid_columns_are_a_duplicate_header(tmp_path):
    path = write_csv(tmp_path / "s.csv", [["Paper One", "Alpha Investigation", U2, U3]],
                     headers=["study_title", "investigation_title", "sample_uuid", "sample_uid"])
    with pytest.raises(SheetError, match="Duplicate column header"):
        read_sheet(path)


def test_read_json_and_xlsx(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps([{"study_title": "Paper One", "investigation_title": "Alpha Investigation",
                                 "sample_uuid": U2}]))
    assert read_sheet(path)[0].sample_uuid == U2
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.append(HEADERS + ["pmid"])
    book.active.append(["Paper One", "Alpha Investigation", U2, "desc", 1111])
    book.save(tmp_path / "s.xlsx")
    [r] = read_sheet(tmp_path / "s.xlsx")
    assert (r.study_title, r.pmid) == ("Paper One", "1111")


def test_a_missing_required_column_an_unknown_extension_and_a_blank_cell(tmp_path):
    with pytest.raises(SheetError, match="investigation_title"):
        read_sheet(write_csv(tmp_path / "a.csv", [["Paper One", U2]], headers=["study_title", "sample_uuid"]))
    (tmp_path / "s.txt").write_text("nope")
    with pytest.raises(SheetError, match="Unsupported"):
        read_sheet(tmp_path / "s.txt")
    with pytest.raises(SheetError, match="row 2"):
        read_sheet(write_csv(tmp_path / "b.csv", [["Paper One", "   ", U2, ""]]))


def test_a_seek_study_id_that_is_not_a_whole_number_is_refused(tmp_path):
    path = write_csv(tmp_path / "s.csv", [["Paper One", "Alpha Investigation", U2, "", "x"]],
                     headers=HEADERS + ["seek_study_id"])
    with pytest.raises(SheetError, match="seek_study_id"):
        read_sheet(path)


def test_blank_rows_are_skipped_and_row_numbers_stay_the_sheets(tmp_path):
    path = tmp_path / "s.csv"
    path.write_text("study_title,investigation_title,sample_uuid,study_description\n"
                    f"Paper One,Alpha Investigation,{U2},desc\n,,,\nPaper One,Alpha Investigation,{U3},\n")
    assert [r.row_number for r in read_sheet(path)] == [2, 4]
    path.write_text("study_title,investigation_title,sample_uuid,study_description\n,,,\n")
    with pytest.raises(SheetError, match="no data rows"):
        read_sheet(path)


def test_exact_duplicates_are_dropped_and_counted_conflicts_refused():
    deduped, dups = validate_rows([row(desc="same", number=2), row(desc="same", number=3)])
    assert (len(deduped), dups) == (1, 1)
    with pytest.raises(SheetError, match="study_description"):
        validate_rows([row(desc="first", number=2), row(uuid=U3, desc="second", number=3)])
    with pytest.raises(SheetError, match="doi"):
        validate_rows([row(doi="10.0000/a", number=2), row(uuid=U3, doi="10.0000/b", number=3)])
    deduped, _ = validate_rows([row(desc="real", number=2), row(uuid=U3, desc="", number=3)])
    assert len(deduped) == 2


def test_a_study_or_a_sample_under_two_investigations_is_refused():
    with pytest.raises(SheetError, match="multiple investigations"):
        validate_structure([row(), row(inv="Beta Investigation", uuid=U3, number=3)])
    with pytest.raises(SheetError, match="rows 2, 3"):
        validate_structure([row(study="Study A"), row(study="Study B", inv="Beta Investigation", number=3)])
    validate_structure([row(study="Study A"), row(study="Study B", number=3)])  # several studies, one investigation


def test_sheet_associations_matches_and_counts_duplicates(tmp_path, alpha):
    path = write_csv(tmp_path / "s.csv", [["Paper One", "Alpha Investigation", U2, "About"],
                                          ["Paper One", "Alpha Investigation", U2, "About"],
                                          ["Paper One", "Alpha Investigation", U3, ""],
                                          ["Paper One", "Alpha Investigation", "TIS-260101ZZZ-9", ""]])
    aset = sheet_associations(path, None, FakeReader(alpha), now="2026-01-01T00:00:00Z")
    assert aset.source == "sheet" and aset.source_ref.startswith("s.csv sha256:")
    assert aset.notes == {"duplicate_rows": 1}
    [t] = aset.targets
    assert (t.sample_ids, t.description) == ([2, 3], "About")
    assert [(u.reason, u.submitted) for u in aset.unmatched] == [(matching.SAMPLE_UID_NOT_FOUND, "TIS-260101ZZZ-9")]


def test_a_study_with_no_resolvable_sample_stops_the_plan(tmp_path, alpha):
    path = write_csv(tmp_path / "s.csv", [["Paper One", "Alpha Investigation", "TIS-260101ZZZ-9", ""]])
    with pytest.raises(SheetError, match="no resolvable samples"):
        sheet_associations(path, None, FakeReader(alpha))


def test_only_the_study_with_no_resolvable_sample_stops_the_plan(tmp_path, alpha):
    path = write_csv(tmp_path / "s.csv", [["Paper A", "Alpha Investigation", "TIS-260101ZZZ-9", ""],
                                          ["Paper B", "Alpha Investigation", "TIS-260101ZZZ-9", ""],
                                          ["Paper B", "Alpha Investigation", U2, ""]])
    with pytest.raises(SheetError) as exc:
        sheet_associations(path, None, FakeReader(alpha))
    assert len(exc.value.messages) == 1 and "'Paper A'" in exc.value.messages[0]


def test_two_studies_holding_only_one_missing_uid_both_stop_the_plan(tmp_path, alpha):
    path = write_csv(tmp_path / "s.csv", [["Paper A", "Alpha Investigation", "TIS-260101ZZZ-9", ""],
                                          ["Paper B", "Alpha Investigation", "TIS-260101ZZZ-9", ""]])
    with pytest.raises(SheetError) as exc:
        sheet_associations(path, None, FakeReader(alpha))
    assert sorted(m.split("'")[1] for m in exc.value.messages) == ["Paper A", "Paper B"]


def test_case_variants_of_one_study_title_are_one_study(tmp_path, alpha):
    path = write_csv(tmp_path / "s.csv", [["Paper One", "Alpha Investigation", U2, "About"],
                                          [" paper ONE", "Alpha Investigation", U3, ""],
                                          ["paper one", "Alpha Investigation", U2, ""]])
    aset = sheet_associations(path, None, FakeReader(alpha))
    [t] = aset.targets
    assert (t.key, t.title, t.sample_ids, t.description) == ("sheet:7:paper one", "Paper One", [2, 3], "About")
    assert aset.notes == {"duplicate_rows": 1}
    with pytest.raises(SheetError, match="study_description"):
        validate_rows([row(desc="first"), row(study="paper one", uuid=U3, desc="second", number=3)])
    with pytest.raises(SheetError, match="multiple investigations"):
        validate_structure([row(), row(study="PAPER ONE", inv="Beta Investigation", uuid=U3, number=3)])


def test_an_unknown_investigation_is_unmatched_not_created(tmp_path, alpha):
    path = write_csv(tmp_path / "s.csv", [["Paper One", "Nowhere Investigation", U2, ""]])
    aset = sheet_associations(path, None, FakeReader(alpha))
    assert aset.targets == []
    assert [u.reason for u in aset.unmatched] == [matching.INVESTIGATION_UNKNOWN]


def test_sheet_uids_are_literal_never_pub_stripped(tmp_path, alpha):
    alpha.samples[70] = {"uuid": U2 + "-PUB", "meta": {}}
    path = write_csv(tmp_path / "s.csv", [["Paper One", "Alpha Investigation", U2 + "-PUB", ""]])
    assert sheet_associations(path, None, FakeReader(alpha)).targets[0].sample_ids == [70]

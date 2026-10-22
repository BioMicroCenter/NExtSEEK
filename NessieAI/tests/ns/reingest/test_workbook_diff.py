"""A rebuilt workbook may differ from the reviewed one only by Assay rows for new assays."""
from NessieAI.ns.reingest.workbook_diff import assay_only_changes
from NessieAI.ns.upload_workbook import render_upload_workbook

ROWS = [{"json_metadata": {"Name": "a", "Parent": "D.SEQ-1"}, "assay_ids": [315]}]


def _book(tmp_path, name, rows):
    path = tmp_path / f"{name}.xlsx"
    render_upload_workbook("A.GEX", rows, str(path), mode="new", schema_fields=["Name", "Parent"])
    return str(path)


def test_added_rows_for_the_new_assay_are_the_only_allowed_change(tmp_path):
    reviewed = _book(tmp_path, "r", ROWS)
    rebuilt = _book(tmp_path, "b", [dict(ROWS[0], assay_ids=[315, 900])])
    assert assay_only_changes(reviewed, rebuilt, [900]) == []


def test_an_added_row_for_another_assay_is_refused(tmp_path):
    reviewed = _book(tmp_path, "r", ROWS)
    rebuilt = _book(tmp_path, "b", [dict(ROWS[0], assay_ids=[315, 77])])
    assert any("Assay" in r for r in assay_only_changes(reviewed, rebuilt, [900]))


def test_a_changed_sample_cell_is_refused(tmp_path):
    reviewed = _book(tmp_path, "r", ROWS)
    rebuilt = _book(tmp_path, "b", [{"json_metadata": {"Name": "b", "Parent": "D.SEQ-1"},
                                     "assay_ids": [315, 900]}])
    assert any("Samples" in r for r in assay_only_changes(reviewed, rebuilt, [900]))


def test_a_dropped_reviewed_assay_row_is_refused(tmp_path):
    reviewed = _book(tmp_path, "r", ROWS)
    rebuilt = _book(tmp_path, "b", [dict(ROWS[0], assay_ids=[900])])
    assert assay_only_changes(reviewed, rebuilt, [900]) != []


def _answered(tmp_path, name, answered_by, value="A. Curator"):
    path = tmp_path / f"{name}.xlsx"
    provenance = [{"uid": "", "attribute": "Scientist", "value": value, "origin": "answer",
                   "raw_key": "", "source_file": "", "answered_by": answered_by}]
    render_upload_workbook("A.GEX", ROWS, str(path), mode="new", provenance=provenance,
                           schema_fields=["Name", "Parent"])
    return str(path)


def test_the_answer_date_in_the_provenance_sheet_is_not_a_change(tmp_path):
    # Reviewed one day, confirmed the next: the rebuild stamps today's date.
    reviewed = _answered(tmp_path, "r", "Ann Curator on 2026-10-21")
    rebuilt = _answered(tmp_path, "b", "Ann Curator on 2026-10-22")
    assert assay_only_changes(reviewed, rebuilt, [900]) == []


def test_a_different_answerer_in_the_provenance_sheet_is_a_change(tmp_path):
    reviewed = _answered(tmp_path, "r", "Ann Curator on 2026-10-21")
    rebuilt = _answered(tmp_path, "b", "Bob Other on 2026-10-21")
    assert any("Provenance" in r for r in assay_only_changes(reviewed, rebuilt, [900]))


def test_a_changed_provenance_value_is_still_a_change(tmp_path):
    reviewed = _answered(tmp_path, "r", "Ann Curator on 2026-10-21")
    rebuilt = _answered(tmp_path, "b", "Ann Curator on 2026-10-22", value="B. Curator")
    assert any("Provenance" in r for r in assay_only_changes(reviewed, rebuilt, [900]))

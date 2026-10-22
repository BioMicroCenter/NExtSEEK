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

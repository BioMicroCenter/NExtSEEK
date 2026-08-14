"""render_upload_workbook must produce a 4-sheet workbook that round-trips cleanly
through the real batch-upload parser (the correctness oracle)."""
import json

from nextseek_api.assistant.upload_workbook import render_upload_workbook
from nextseek_api.batch_upload.convert import detect_format, parse_traditional_file


def test_render_round_trips_through_parse_traditional(tmp_path):
    rows = [
        {"json_metadata": {"Parent": "D.SEQ-220823SHA-1", "Scientist": "Marie Floryan",
                           "File_PrimaryData": "gideon_1_matrix.h5"}, "assay_ids": [12]},
        {"json_metadata": {"Parent": "D.SEQ-220823SHA-2", "Scientist": "Marie Floryan",
                           "File_PrimaryData": "gideon_2_matrix.h5"}, "assay_ids": [12]},
    ]
    out = str(tmp_path / "reingest_A.SCXP.xlsx")
    render_upload_workbook("A.SCXP", rows, out)

    # It must be recognized as the 4-sheet (traditional) format, not flat.
    assert detect_format(out) == "traditional"

    batch = parse_traditional_file(out)
    assert len(batch.rows) == 2

    r0 = batch.rows[0]
    assert r0.SampleType == "A.SCXP"
    assert not r0.UID  # blank -> server mints
    meta = json.loads(r0.json_metadata)
    assert meta["Parent"] == "D.SEQ-220823SHA-1"
    assert meta["Scientist"] == "Marie Floryan"
    assert meta["File_PrimaryData"] == "gideon_1_matrix.h5"
    assert r0.assay_ids == [12]

    r1 = batch.rows[1]
    assert json.loads(r1.json_metadata)["Parent"] == "D.SEQ-220823SHA-2"


def test_render_multi_parent_and_no_assay(tmp_path):
    # A merged/aggregate output derived from several inputs (;-delimited Parent),
    # and no assay_ids (ASSAY sheet header-only -> assay_ids == []).
    rows = [
        {"json_metadata": {"Parent": "D.SEQ-220823SHA-1;D.SEQ-220823SHA-2",
                           "Scientist": "Marie Floryan"}, "assay_ids": []},
    ]
    out = str(tmp_path / "reingest_A.SCXP_merged.xlsx")
    render_upload_workbook("A.SCXP", rows, out)

    batch = parse_traditional_file(out)
    assert len(batch.rows) == 1
    meta = json.loads(batch.rows[0].json_metadata)
    assert meta["Parent"] == "D.SEQ-220823SHA-1;D.SEQ-220823SHA-2"
    assert batch.rows[0].assay_ids == []


def test_render_rejects_empty_rows(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        render_upload_workbook("A.SCXP", [], str(tmp_path / "empty.xlsx"))

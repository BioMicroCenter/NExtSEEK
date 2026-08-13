"""granular._build_upload_xlsx: group rows by type, QA, render one workbook per type."""
import json

from nextseek_api.batch_upload.convert import parse_traditional_file
import nextseek_api.assistant.granular as g


class _Cfg:
    pass


def _rows(*specs):
    return json.dumps([
        {"SampleType": st, "json_metadata": {"Parent": parent, "Scientist": "Marie Floryan"},
         "assay_ids": aids}
        for (st, parent, aids) in specs
    ])


def test_groups_by_type_and_renders_each(tmp_path):
    rows = _rows(
        ("A.SCXP", "D.SEQ-1", [12]),
        ("A.SCXP", "D.SEQ-2", [12]),
        ("A.ALN", "D.SEQ-1", []),
    )
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1,D.SEQ-2"},
        _Cfg(), None, None, None, str(tmp_path))

    import re
    # artifact KEYS must be route-safe (download route accepts only [\w]+; no dots),
    # while the file on disk keeps the readable dotted name.
    assert set(out["saved_files"]) == {"reingest_A_SCXP", "reingest_A_ALN"}
    assert all(re.fullmatch(r"[\w]+", k) for k in out["saved_files"])
    assert out["qa"]["A.SCXP"]["disposition"] == "CLEAN"
    scxp_path = out["saved_files"]["reingest_A_SCXP"]
    assert scxp_path.endswith("reingest_A.SCXP.xlsx")
    # the rendered workbook round-trips through the real parser
    batch = parse_traditional_file(scxp_path)
    assert len(batch.rows) == 2
    assert all(r.SampleType == "A.SCXP" for r in batch.rows)


def test_hard_reject_type_is_skipped(tmp_path):
    # Unresolvable parent -> HARD_REJECT -> no workbook for that type.
    rows = _rows(("A.SCXP", "D.SEQ-does-not-exist", [12]))
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1"},
        _Cfg(), None, None, None, str(tmp_path))
    assert out["saved_files"] == {}
    assert out["qa"]["A.SCXP"]["disposition"] == "HARD_REJECT"


def test_bad_json_rows_raises_validation(tmp_path):
    import pytest
    with pytest.raises(g.OpValidationError):
        g._build_upload_xlsx({"rows": "not json"}, _Cfg(), None, None, None, str(tmp_path))

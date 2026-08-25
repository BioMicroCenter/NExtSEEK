"""The one workbook writer: README sheet first, then a sheet per sample type."""

from unittest.mock import patch

import pandas as pd
from openpyxl import load_workbook

from nextseek_api.services.sample_workbook import (
    CONTEXTDB_URL,
    build_readme_rows,
    load_sample_type_context,
    write_samples_workbook,
)

_MOD = "nextseek_api.services.sample_workbook"

CONTEXT = {
    "MUS": {"name": "Mouse", "description": "A mouse sample."},
    "TIS": {"name": "Tissue", "description": "A tissue sample."},
}


def test_readme_rows_start_with_the_header():
    rows = build_readme_rows(["MUS"], CONTEXT)
    assert rows[0] == ["Sample Type", "Name", "Description"]


def test_readme_rows_carry_name_and_description():
    rows = build_readme_rows(["MUS"], CONTEXT)
    assert rows[1] == ["MUS", "Mouse", "A mouse sample."]


def test_readme_rows_are_sorted_by_code():
    rows = build_readme_rows(["TIS", "MUS"], CONTEXT)
    assert [r[0] for r in rows[1:]] == ["MUS", "TIS"]


def test_undocumented_code_is_listed_with_blanks():
    rows = build_readme_rows(["MUS", "ZZZ"], CONTEXT)
    assert rows[2] == ["ZZZ", "", ""]


def test_readme_rows_deduplicate_codes():
    rows = build_readme_rows(["MUS", "MUS"], CONTEXT)
    assert len(rows) == 2


def test_readme_rows_drop_blank_codes():
    rows = build_readme_rows(["MUS", None, ""], CONTEXT)
    assert [r[0] for r in rows[1:]] == ["MUS"]


@patch(f"{_MOD}.Sample_types_context")
def test_load_context_maps_code_to_name_and_description(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"sample_type": "MUS", "name": "Mouse", "description": "A mouse sample."},
    ]
    assert load_sample_type_context(["MUS"]) == {
        "MUS": {"name": "Mouse", "description": "A mouse sample."}
    }


@patch(f"{_MOD}.Sample_types_context")
def test_load_context_coerces_nulls_to_empty_strings(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"sample_type": "MUS", "name": None, "description": None},
    ]
    assert load_sample_type_context(["MUS"]) == {"MUS": {"name": "", "description": ""}}


@patch(f"{_MOD}.Sample_types_context")
def test_load_context_does_not_query_for_an_empty_code_list(mock_model):
    assert load_sample_type_context([]) == {}
    mock_model.objects.filter.assert_not_called()


@patch(f"{_MOD}.Sample_types_context")
def test_load_context_survives_a_missing_table(mock_model):
    """A download must not fail because the context table is absent."""
    mock_model.objects.filter.side_effect = RuntimeError("no such table")
    assert load_sample_type_context(["MUS"]) == {}


def _df():
    return pd.DataFrame([
        {"uuid": "MUS-230101ABC-1", "Name": "m1", "Sex": "F"},
        {"uuid": "TIS-230101ABC-2", "Name": "t1", "Sex": ""},
    ])


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_is_the_first_sheet(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    assert load_workbook(out).sheetnames[0] == "README"


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_sample_type_sheets_follow_the_readme(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    assert load_workbook(out).sheetnames == ["README", "MUS", "TIS"]


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_a1_links_to_the_contextdb(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A1"].hyperlink.target == CONTEXTDB_URL


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_table_starts_at_row_3(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert [ws.cell(3, c).value for c in (1, 2, 3)] == ["Sample Type", "Name", "Description"]
    assert ws.cell(4, 1).value == "MUS"


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_helper_columns_are_dropped_from_data_sheets(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["MUS"]
    headers = [c.value for c in ws[1]]
    assert "uuid" not in headers and "sample_type" not in headers


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_all_empty_columns_are_dropped(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    assert "Sex" not in [c.value for c in load_workbook(out)["TIS"][1]]


@patch(f"{_MOD}.load_sample_type_context", return_value={})
def test_workbook_still_written_when_context_table_is_empty(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws.cell(4, 1).value == "MUS"
    assert ws.cell(4, 2).value in (None, "")


@patch(f"{_MOD}.load_sample_type_context")
def test_supplied_context_skips_the_lookup(mock_load, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out), context_by_code=CONTEXT)
    mock_load.assert_not_called()


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_dotted_sample_type_codes_survive_extraction(_ctx, tmp_path):
    """D.SEQ and friends must not be truncated to D."""
    out = tmp_path / "w.xlsx"
    df = pd.DataFrame([{"uuid": "D.SEQ-240910LAU-3", "Name": "s1"}])
    write_samples_workbook(df, str(out))
    assert "D.SEQ" in load_workbook(out).sheetnames


def test_dbtable_sample_retrieval_data_delegates_to_the_shared_writer():
    from seek.dbtable_sample import DBtable_sample

    # __init__ opens a DB connection (dmac/dbtable.py:51) and sampleRetrievalData
    # needs none of it, so skip construction rather than pulling in django_db.
    dbs = DBtable_sample.__new__(DBtable_sample)

    df = pd.DataFrame([{"uuid": "MUS-1", "json_metadata": '{"Name": "m1"}'}])
    with patch("seek.dbtable_sample.write_samples_workbook") as mock_write:
        dbs.sampleRetrievalData(df, "/tmp/unused.xlsx")
    mock_write.assert_called_once()
    assert mock_write.call_args[0][1] == "/tmp/unused.xlsx"


def test_seek_views_sample_retrieval_data_delegates_to_the_shared_writer():
    from seek import views

    df = pd.DataFrame([{"uuid": "MUS-1", "json_metadata": '{"Name": "m1"}'}])
    with patch("seek.views.admin.write_samples_workbook") as mock_write:
        views.sample_retrieval_data(df, "/tmp/unused.xlsx")
    mock_write.assert_called_once()
    assert mock_write.call_args[0][1] == "/tmp/unused.xlsx"

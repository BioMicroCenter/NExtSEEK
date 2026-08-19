"""The one workbook writer: README sheet first, then a sheet per sample type."""

from unittest.mock import patch

import pandas as pd
from openpyxl import load_workbook

from nextseek_api.services.sample_workbook import (
    CONTEXTDB_URL,
    build_readme_blocks,
    load_sample_field_context,
    load_sample_type_context,
    write_samples_workbook,
)

_MOD = "nextseek_api.services.sample_workbook"

CONTEXT = {
    "MUS": {"name": "Mouse", "description": "A mouse sample."},
    "TIS": {"name": "Tissue", "description": "A tissue sample."},
}


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


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value={})
def test_workbook_still_written_when_context_table_is_empty(_ctx, _fields, tmp_path):
    """With no sample-type context the heading is the bare code and the
    description row is empty, but the section is still there."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A3"].value == "MUS"
    assert ws["A4"].value in (None, "")


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
    with patch("seek.views.write_samples_workbook") as mock_write:
        views.sample_retrieval_data(df, "/tmp/unused.xlsx")
    mock_write.assert_called_once()
    assert mock_write.call_args[0][1] == "/tmp/unused.xlsx"


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_uses_the_global_row(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Sex", "sample_type": "", "meaning": "Sex at birth."},
    ]
    assert load_sample_field_context([("MUS", "Sex")]) == {("MUS", "Sex"): "Sex at birth."}


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_prefers_a_sample_type_override(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Name", "sample_type": "", "meaning": "Submitter's identifier."},
        {"field_name": "Name", "sample_type": "MUS", "meaning": "The animal's ear-tag ID."},
    ]
    assert load_sample_field_context([("MUS", "Name")]) == {
        ("MUS", "Name"): "The animal's ear-tag ID."
    }


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_override_does_not_leak_to_other_sample_types(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Name", "sample_type": "", "meaning": "Submitter's identifier."},
        {"field_name": "Name", "sample_type": "MUS", "meaning": "The animal's ear-tag ID."},
    ]
    result = load_sample_field_context([("MUS", "Name"), ("TIS", "Name")])
    assert result[("TIS", "Name")] == "Submitter's identifier."


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_returns_blank_for_an_undefined_field(mock_model):
    mock_model.objects.filter.return_value.values.return_value = []
    assert load_sample_field_context([("MUS", "Genotype")]) == {("MUS", "Genotype"): ""}


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_coerces_a_null_meaning_to_a_blank(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Sex", "sample_type": "", "meaning": None},
    ]
    assert load_sample_field_context([("MUS", "Sex")]) == {("MUS", "Sex"): ""}


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_does_not_query_for_an_empty_pair_list(mock_model):
    assert load_sample_field_context([]) == {}
    mock_model.objects.filter.assert_not_called()


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_survives_a_missing_table(mock_model):
    """A download must not fail because the definitions table is absent."""
    mock_model.objects.filter.side_effect = RuntimeError("no such table")
    assert load_sample_field_context([("MUS", "Sex")]) == {}


def test_blocks_carry_the_sample_type_name_and_description():
    blocks = build_readme_blocks([("MUS", ["UID"])], CONTEXT, {})
    assert blocks[0]["code"] == "MUS"
    assert blocks[0]["name"] == "Mouse"
    assert blocks[0]["description"] == "A mouse sample."


def test_blocks_keep_sheet_order_not_alphabetical_order():
    """The README is meant to be read beside the tab, left to right."""
    blocks = build_readme_blocks([("MUS", ["UID", "Sex", "Genotype"])], CONTEXT, {})
    assert [name for name, _ in blocks[0]["columns"]] == ["UID", "Sex", "Genotype"]


def test_blocks_follow_the_order_the_sheets_were_given():
    blocks = build_readme_blocks([("TIS", ["UID"]), ("MUS", ["UID"])], CONTEXT, {})
    assert [b["code"] for b in blocks] == ["TIS", "MUS"]


def test_blocks_attach_the_resolved_meaning():
    meanings = {("MUS", "Sex"): "Sex at birth."}
    blocks = build_readme_blocks([("MUS", ["Sex"])], CONTEXT, meanings)
    assert blocks[0]["columns"] == [("Sex", "Sex at birth.")]


def test_a_column_with_no_definition_is_listed_with_a_blank():
    """The README always indexes every column, so a gap is visible not silent."""
    blocks = build_readme_blocks([("MUS", ["Genotype"])], CONTEXT, {})
    assert blocks[0]["columns"] == [("Genotype", "")]


def test_an_undocumented_sample_type_still_gets_a_block():
    blocks = build_readme_blocks([("ZZZ", ["UID"])], CONTEXT, {})
    assert blocks[0] == {"code": "ZZZ", "name": "", "description": "", "columns": [("UID", "")]}


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_section_heading_is_bold_at_a3(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A3"].value == "MUS — Mouse"
    assert ws["A3"].font.bold is True


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_description_sits_under_the_heading(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A4"].value == "A mouse sample."


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_column_table_is_indented_into_b_and_c(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert [ws["B6"].value, ws["C6"].value] == ["Column", "Meaning"]
    assert ws["B6"].font.bold is True
    assert ws["B7"].value == "Name"


@patch(f"{_MOD}.load_sample_field_context",
       return_value={("MUS", "Name"): "The animal's ear-tag ID."})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_shows_the_resolved_meaning(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["C7"].value == "The animal's ear-tag ID."


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_the_second_section_starts_after_a_blank_row(_ctx, _fields, tmp_path):
    """MUS has two columns (Name, Sex) at rows 7-8, so TIS heads row 10."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A9"].value is None
    assert ws["A10"].value == "TIS — Tissue"


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_only_lists_columns_that_survive_the_empty_drop(_ctx, _fields, tmp_path):
    """TIS's Sex is empty in the fixture, so the TIS sheet drops it and the
    README must not claim a column the researcher cannot see.

    TIS heads row 10, description 11, blank 12, table header 13, so its single
    surviving column sits at B14."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["B14"].value == "Name"
    assert ws["B15"].value is None


@patch(f"{_MOD}.Sample_fields_context")
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_workbook_is_complete_when_the_definitions_table_is_missing(_ctx, mock_model, tmp_path):
    """Losing sample_fields_context costs meanings, never the download.

    This is the only Task 5 test that exercises the real loader rather than
    mocking it out, so it is what actually proves the fail-soft path end to end.
    """
    mock_model.objects.filter.side_effect = RuntimeError("no such table")
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    wb = load_workbook(out)
    assert wb.sheetnames == ["README", "MUS", "TIS"]
    ws = wb["README"]
    assert ws["B7"].value == "Name"          # still indexed
    assert ws["C7"].value in (None, "")      # meaning blank


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_field_lookup_is_asked_only_for_columns_actually_written(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    with patch(f"{_MOD}.load_sample_field_context", return_value={}) as lookup:
        write_samples_workbook(_df(), str(out))
    asked = set(lookup.call_args[0][0])
    assert ("TIS", "Sex") not in asked
    assert ("MUS", "Sex") in asked

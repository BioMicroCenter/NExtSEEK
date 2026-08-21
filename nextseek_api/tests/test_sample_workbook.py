"""The one workbook writer: README sheet first, then a sheet per sample type."""

from unittest.mock import patch

import pandas as pd
import pytest
from openpyxl import load_workbook

from nextseek_api.services.sample_workbook import (
    COLUMN_TABLE_HEADER,
    CONTEXTDB_URL,
    EXCEL_MAX_CELL_CHARS,
    CV_SHEET,
    FLOW_SHEET,
    SUMMARY_HEADER,
    build_readme_blocks,
    load_assay_titles,
    load_derivation_hops,
    load_sample_field_context,
    load_sample_type_context,
    write_samples_workbook,
)

_MOD = "nextseek_api.services.sample_workbook"


@pytest.fixture(autouse=True)
def _no_graph():
    """write_samples_workbook consults Neo4j for lineage. Tests must not reach
    for a real graph: the driver blocks rather than failing fast, which is what
    NEO4J_TIMEOUT_SECONDS bounds in production."""
    with patch(f"{_MOD}.load_derivation_hops", return_value=[]):
        yield

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
    assert ws["A4"].value == "MUS"          # summary table row
    assert ws["A7"].value == "MUS"          # section heading, bare code


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


@patch(f"{_MOD}.Sample_attributes_unique")
def test_field_context_uses_the_global_row(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Sex", "sample_type": "", "meaning": "Sex at birth."},
    ]
    assert load_sample_field_context([("MUS", "Sex")]) == {("MUS", "Sex"): "Sex at birth."}


@patch(f"{_MOD}.Sample_attributes_unique")
def test_field_context_prefers_a_sample_type_override(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Name", "sample_type": "", "meaning": "Submitter's identifier."},
        {"field_name": "Name", "sample_type": "MUS", "meaning": "The animal's ear-tag ID."},
    ]
    assert load_sample_field_context([("MUS", "Name")]) == {
        ("MUS", "Name"): "The animal's ear-tag ID."
    }


@patch(f"{_MOD}.Sample_attributes_unique")
def test_field_context_override_does_not_leak_to_other_sample_types(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Name", "sample_type": "", "meaning": "Submitter's identifier."},
        {"field_name": "Name", "sample_type": "MUS", "meaning": "The animal's ear-tag ID."},
    ]
    result = load_sample_field_context([("MUS", "Name"), ("TIS", "Name")])
    assert result[("TIS", "Name")] == "Submitter's identifier."


@patch(f"{_MOD}.Sample_attributes_unique")
def test_field_context_returns_blank_for_an_undefined_field(mock_model):
    mock_model.objects.filter.return_value.values.return_value = []
    assert load_sample_field_context([("MUS", "Genotype")]) == {("MUS", "Genotype"): ""}


@patch(f"{_MOD}.Sample_attributes_unique")
def test_field_context_coerces_a_null_meaning_to_a_blank(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Sex", "sample_type": "", "meaning": None},
    ]
    assert load_sample_field_context([("MUS", "Sex")]) == {("MUS", "Sex"): ""}


@patch(f"{_MOD}.Sample_attributes_unique")
def test_field_context_does_not_query_for_an_empty_pair_list(mock_model):
    assert load_sample_field_context([]) == {}
    mock_model.objects.filter.assert_not_called()


@patch(f"{_MOD}.Sample_attributes_unique")
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
def test_readme_section_heading_is_bold_at_a7(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A7"].value == "MUS — Mouse"
    assert ws["A7"].font.bold is True


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_description_sits_in_the_summary_table(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["C4"].value == "A mouse sample."


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_column_table_is_indented_into_b_and_c(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert [ws["B9"].value, ws["C9"].value] == ["Column", "Meaning"]
    assert ws["B9"].font.bold is True
    assert ws["B10"].value == "Name"


@patch(f"{_MOD}.load_sample_field_context",
       return_value={("MUS", "Name"): "The animal's ear-tag ID."})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_shows_the_resolved_meaning(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["C10"].value == "The animal's ear-tag ID."


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_the_second_section_starts_after_a_blank_row(_ctx, _fields, tmp_path):
    """MUS has two columns (Name, Sex) at rows 7-8, so TIS heads row 10."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A12"].value is None
    assert ws["A13"].value == "TIS — Tissue"


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
    assert ws["B16"].value == "Name"
    assert ws["B17"].value is None


@patch(f"{_MOD}.Sample_attributes_unique")
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_workbook_is_complete_when_the_definitions_table_is_missing(_ctx, mock_model, tmp_path):
    """Losing sample_attributes_unique costs meanings, never the download.

    This is the only Task 5 test that exercises the real loader rather than
    mocking it out, so it is what actually proves the fail-soft path end to end.
    """
    mock_model.objects.filter.side_effect = RuntimeError("no such table")
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    wb = load_workbook(out)
    assert wb.sheetnames == ["README", "MUS", "TIS"]
    ws = wb["README"]
    assert ws["B10"].value == "Name"         # still indexed
    assert ws["C10"].value in (None, "")     # meaning blank


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_field_lookup_is_asked_only_for_columns_actually_written(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    with patch(f"{_MOD}.load_sample_field_context", return_value={}) as lookup:
        write_samples_workbook(_df(), str(out))
    asked = set(lookup.call_args[0][0])
    assert ("TIS", "Sex") not in asked
    assert ("MUS", "Sex") in asked


@patch(f"{_MOD}.load_sample_field_context",
       return_value={("MUS", "Name"): "Line one\x0bline two."})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_a_control_character_in_a_definition_does_not_break_the_download(
    _ctx, _fields, tmp_path
):
    """openpyxl raises IllegalCharacterError on \\x0b — which is exactly what a
    line break pasted out of Word or a PDF becomes. One bad definition row must
    not cost every download of every workbook carrying that sample type."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))          # must not raise
    ws = load_workbook(out)["README"]
    assert ws["C10"].value == "Line oneline two."


@patch(f"{_MOD}.load_sample_type_context",
       return_value={"MUS": {"name": "Mouse", "description": "Desc\x07ription."},
                     "TIS": {"name": "Tissue", "description": "A tissue sample."}})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
def test_a_control_character_in_a_description_is_stripped(_fields, _ctx, tmp_path):
    """Every string the README writes goes through the same sanitizer, not just
    the meanings."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    assert load_workbook(out)["README"]["C4"].value == "Description."


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_an_over_length_definition_is_truncated_not_written_whole(_ctx, tmp_path):
    """Excel's cell limit is 32,767 characters and openpyxl does not enforce it;
    a longer value writes a file Excel reports as needing repair. `meaning` is a
    TEXT column, so a reviewer could paste well past the limit."""
    out = tmp_path / "w.xlsx"
    with patch(f"{_MOD}.load_sample_field_context",
               return_value={("MUS", "Name"): "x" * 40000}):
        write_samples_workbook(_df(), str(out))
    assert len(load_workbook(out)["README"]["C10"].value) == EXCEL_MAX_CELL_CHARS


@patch(f"{_MOD}.load_sample_field_context",
       return_value={("MUS", "Name"): "The animal's ear-tag ID."})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_the_header_cell_carries_its_definition_as_a_hover_note(_ctx, _fields, tmp_path):
    """A researcher filling the sheet in reads the header, not the README."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    header = load_workbook(out)["MUS"]["A1"]
    assert header.value == "Name"
    assert header.comment is not None
    assert "ear-tag ID" in header.comment.text


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_an_undefined_column_gets_no_empty_hover_note(_ctx, _fields, tmp_path):
    """An empty note is worse than none: it looks like a definition that failed
    to load rather than one nobody has written yet."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    assert load_workbook(out)["MUS"]["A1"].comment is None


@patch(f"{_MOD}.Sample_attributes_unique")
def test_field_context_keeps_case_variant_names_apart(mock_model):
    """SEEK has five attribute pairs differing only by case (Figure/figure,
    Bead_Coating/Bead_coating). The table stores field_name as utf8mb4_bin so
    they stay distinct; the loader must not merge them either."""
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Figure", "sample_type": "", "meaning": "Upper-case one."},
        {"field_name": "figure", "sample_type": "", "meaning": "Lower-case one."},
    ]
    result = load_sample_field_context([("D.IMG", "Figure"), ("A.IMG", "figure")])
    assert result[("D.IMG", "Figure")] == "Upper-case one."
    assert result[("A.IMG", "figure")] == "Lower-case one."


def _prov_df():
    """Two hops, one of them from a type not itself downloaded."""
    return pd.DataFrame([
        {"uuid": "D.SEQ-1", "sample_type": "D.SEQ", "Parent": "DNA-9"},
        {"uuid": "TIS-2", "sample_type": "TIS", "Parent": "MUS-3; MUS-4"},
    ])


@patch(f"{_MOD}.GraphDatabase")
def test_lineage_lookup_survives_an_unreachable_graph(mock_graph):
    mock_graph.driver.side_effect = RuntimeError("connection refused")
    assert load_derivation_hops(["D.SEQ-1"]) == []


@patch(f"{_MOD}.Samples")
def test_assay_lookup_survives_a_database_failure(mock_samples):
    """Losing the assay link must cost the labels, never the download."""
    mock_samples.objects.filter.side_effect = RuntimeError("no such table")
    assert load_assay_titles(["D.SEQ-1"]) == {}


def _cv_df():
    """A frame whose columns include two governed by GEO/SRA vocabularies."""
    return pd.DataFrame([
        {"uuid": "MUS-230101ABC-1", "Name": "m1", "DataType": "FASTQ", "LibraryDesign": "paired"},
    ])


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_a_governed_column_offers_its_vocabulary_as_a_dropdown(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_cv_df(), str(out))
    wb = load_workbook(out)
    assert CV_SHEET in wb.sheetnames
    rules = wb["MUS"].data_validations.dataValidation
    assert {r.formula1.split("!")[0].strip("'") for r in rules} == {CV_SHEET}
    assert len(rules) == 2  # DataType and LibraryDesign, not Name or uuid


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_dropdowns_warn_rather_than_reject(_ctx, _fields, tmp_path):
    """Downloaded data predates these vocabularies (RNA-seq for RNA-Seq), so a
    hard reject would fire on open for rows the researcher never touched."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_cv_df(), str(out))
    for rule in load_workbook(out)["MUS"].data_validations.dataValidation:
        assert rule.errorStyle == "warning"


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_no_vocabulary_sheet_when_no_column_is_governed(_ctx, _fields, tmp_path):
    """The fixture is Name and Sex only; an empty extra sheet would be clutter."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    assert CV_SHEET not in load_workbook(out).sheetnames


def _readme_sections(ws) -> dict[str, list[str]]:
    """Parse the README back into {heading: [column names]}, without assuming
    any row arithmetic. Used to state layout invariants against the sheet
    itself rather than against hand-computed coordinates."""
    sections: dict[str, list[str]] = {}
    current = None
    in_summary = True
    for row in range(1, ws.max_row + 1):
        a, b = ws.cell(row=row, column=1), ws.cell(row=row, column=2)
        # Section headings are the bold cells of column A. The description
        # under each one is column A too, but plain; A1's link is not bold.
        if a.value and a.font.bold:
            # The summary table's bold header is not a section; skip it and the
            # sample-type rows beneath it.
            if a.value == SUMMARY_HEADER[0]:
                in_summary = True
                continue
            in_summary = False
            current = a.value.split(" — ")[0]
            sections[current] = []
        elif in_summary:
            continue
        # Column names are the plain cells of column B. The Column/Meaning
        # table header sits in B as well, but bold.
        elif b.value and not b.font.bold and current:
            assert b.value != COLUMN_TABLE_HEADER[0]
            sections[current].append(b.value)
    return sections


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_columns_match_the_sheet_header_they_describe(_ctx, _fields, tmp_path):
    """The invariant stated directly: for every tab, what the README lists is
    exactly that tab's header row after the all-empty-column drop. TIS's Sex is
    empty in the fixture, so neither the sheet nor the README carries it."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    wb = load_workbook(out)
    sections = _readme_sections(wb["README"])
    for code in ("MUS", "TIS"):
        assert sections[code] == [c.value for c in wb[code][1]]


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_a_section_with_no_surviving_columns_has_no_table_header(_ctx, _fields, tmp_path):
    """Every TIS column is empty here, so the tab has no columns at all. A bare
    Column/Meaning header with nothing beneath it reads as a rendering bug."""
    df = pd.DataFrame([
        {"uuid": "MUS-230101ABC-1", "Name": "m1", "Sex": "F"},
        {"uuid": "TIS-230101ABC-2", "Name": "", "Sex": ""},
    ])
    out = tmp_path / "w.xlsx"
    write_samples_workbook(df, str(out))
    ws = load_workbook(out)["README"]
    assert ws["A13"].value == "TIS — Tissue"
    assert [ws.cell(row=r, column=2).value for r in (14, 15, 16)] == [None, None, None]


def _flow_df():
    """PAT -> PAV -> TIS, with TIS also downloaded."""
    return pd.DataFrame([
        {"uuid": "PAV-1", "sample_type": "PAV", "Parent": "PAT-9"},
        {"uuid": "TIS-2", "sample_type": "TIS", "Parent": "PAV-1"},
    ])


def _flow_rows(ws):
    return [[c.value for c in row if c.value is not None] for row in ws.iter_rows()
            if any(c.value is not None for c in row)]


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_the_flow_sheet_sits_directly_after_the_readme(_ctx, _fields, _assays, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_flow_df(), str(out))
    assert load_workbook(out).sheetnames[:2] == ["README", FLOW_SHEET]


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_a_chain_occupies_one_row_across_columns(_ctx, _fields, _assays, tmp_path):
    """The whole reason for a separate sheet: one flow reads left to right."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_flow_df(), str(out))
    rows = _flow_rows(load_workbook(out)[FLOW_SHEET])
    assert ["PAT", "------>", "PAV", "------>", "TIS"] in rows


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_no_flow_sheet_when_there_is_no_lineage(_ctx, _fields, _assays, tmp_path):
    """An empty sheet reads as a rendering bug."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    assert FLOW_SHEET not in load_workbook(out).sheetnames


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_the_readme_points_at_the_flow_sheet(_ctx, _fields, _assays, tmp_path):
    """A reader who never opens the tab must still learn it is there."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_flow_df(), str(out))
    text = " ".join(str(c.value) for row in load_workbook(out)["README"].iter_rows()
                    for c in row if c.value)
    assert FLOW_SHEET in text

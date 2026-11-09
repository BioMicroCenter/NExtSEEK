"""The GEO exporter inserts rows; the template's conditional-format and dropdown ranges must follow
(openpyxl leaves them at their old addresses). Made-up libraries only."""
from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.utils import range_boundaries

import chat_nextseek.reports.exporters.geo_xlsx as geo

TEMPLATE = Path(geo.__file__).parents[1] / "templates" / "GEO_template.xlsx"
HEADER_ROW, FIRST = 38, 39


def _fill(n: int, dp: int = 1, paired: int = 0):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = load_workbook(TEMPLATE)
    samples = [{"*library name": f"lib{i}", "*molecule": "total RNA", "*single or paired-end": "paired-end",
                "raw file": f"f{i}.fastq.gz"} for i in range(n)]
    protocols = {"*data processing step": [f"step {i}" for i in range(dp)]} if dp > 1 else {}
    pairs = [{"file name 1": f"p{i}_1.fq", "file name 2": f"p{i}_2.fq"} for i in range(paired)]
    geo._populate_geo_seq_workbook(wb, {"samples": samples, "protocols": protocols, "study": {},
                                        "paired_end_experiments": pairs})
    return wb["Metadata"]


def _boxes(sqref):
    return [range_boundaries(str(r)) for r in str(sqref).split()]


def _cf(ws):
    return [(str(cf.sqref), r.type) for cf in ws.conditional_formatting for r in cf.rules]


def _dv(ws):
    return [(str(dv.sqref), dv.type) for dv in ws.data_validations.dataValidation]


@pytest.mark.parametrize("n,dp,paired", [(50, 1, 0), (7, 1, 0), (50, 4, 3), (7, 3, 2)])
def test_ranges_cover_the_real_library_block(n, dp, paired):
    ws = _fill(n, dp, paired)
    last = FIRST + max(n, 14) - 1
    assert ws.cell(FIRST + n - 1, 1).value == f"lib{n - 1}"
    # dropdowns reach the last library row, on exactly the molecule (K) and layout (L) columns
    for col in "KL":
        hits = [s for s, t in _dv(ws) if t == "list" and any(b[0] == b[2] == ord(col) - 64 for b in _boxes(s))]
        assert hits, col
        assert all(b[1] == FIRST and b[3] == last for s in hits for b in _boxes(s))
    # duplicate-value rules inside the block are only on library name (A) and the raw-file columns
    raw_cols = {c for k in ("raw file", "*raw file") for c in geo._build_header_map(ws, HEADER_ROW).get(k, [])}
    for s, t in _cf(ws):
        for c1, r1, c2, r2 in _boxes(s):
            if r1 <= last and r2 >= FIRST and r1 < 54:  # touches the library block
                assert t == "duplicateValues"
                assert (c1 == c2 in (1, 2) or {c1, c2} <= raw_cols) and (r1, r2) == (FIRST, last), (s, raw_cols)


def test_rules_below_the_block_are_shifted_by_the_inserted_rows():
    ws = _fill(50)
    shift = 50 - 14
    protocols_row = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == "PROTOCOLS")
    assert protocols_row == 54 + shift
    below = [b for s, _ in _cf(ws) for b in _boxes(s) if b[1] > 60]
    assert below and all(b[1] >= protocols_row for b in below)  # nothing lands inside the library block


@pytest.mark.parametrize("n,dp,paired", [(50, 1, 0), (50, 4, 3), (7, 1, 0)])
def test_duplicate_rules_are_one_per_column_group_with_their_own_priority(n, dp, paired):
    ws = _fill(n, dp, paired)
    last = FIRST + max(n, 14) - 1
    rules = [(str(cf.sqref), r) for cf in ws.conditional_formatting for r in cf.rules]
    block = sorted(s for s, r in rules if s in {f"A{FIRST}:A{last}", f"B{FIRST}:B{last}", f"Q{FIRST}:T{last}"})
    assert block == [f"A{FIRST}:A{last}", f"B{FIRST}:B{last}", f"Q{FIRST}:T{last}"]
    assert "I14:I17" in [s for s, _ in rules]  # the study rule keeps its own copy
    assert len({id(r) for _, r in rules}) == len(rules)  # no rule object written twice
    prios = [r.priority for _, r in rules]
    assert len(set(prios)) == len(prios)
    assert len({s for s, _ in rules}) == len(rules)


@pytest.mark.parametrize("n,dp,paired", [(50, 1, 0), (50, 4, 3), (7, 1, 0)])
def test_help_prompts_follow_the_real_library_block(n, dp, paired):
    ws = _fill(n, dp, paired)
    last = FIRST + max(n, 14) - 1
    dvs = ws.data_validations.dataValidation
    assert len({id(d) for d in dvs}) == len(dvs)  # a dropdown is appended once
    sq = [str(d.sqref) for d in dvs]
    assert len(sq) == len(set(sq))
    prompts = {str(d.prompt): str(d.sqref) for d in dvs if d.prompt}
    on_block = {p: s for p, s in prompts.items() if any(b[1] == FIRST for b in _boxes(s))}
    # input-message validations on the example rows (organism, tissue, cell line, cell type, genotype, treatment, title)
    for col in "DEFGHB":
        c = ord(col) - 64
        assert any(b[0] <= c <= b[2] and (b[1], b[3]) == (FIRST, last) for s in on_block.values() for b in _boxes(s)), col
    for col in "DEFGHB":
        c = ord(col) - 64
        hits = [b for d in dvs for b in _boxes(d.sqref) if b[0] <= c <= b[2] and b[1] <= FIRST <= b[3]]
        assert len(hits) == 1, (col, hits)  # one prompt per column, no template stub left on the example rows


def test_rules_starting_at_rows_54_to_60_follow_a_50_row_block():
    ws = _fill(50, 4, 3)
    shift = 50 - 14
    starts = sorted({b[1] for s, _ in _cf(ws) for b in _boxes(s) if b[1] >= 54 + shift})
    assert 57 + shift in starts  # the template's C57:C70 / Y57:Y69 rules
    assert not [b for s, _ in _cf(ws) for b in _boxes(s) if 54 <= b[1] < 54 + shift and b[0] not in (1, 2, 17, 18, 19, 20)]


def test_ranges_below_data_processing_and_paired_rows_follow_every_insert():
    """50 libraries (+36 rows), 4 data-processing steps (+3), 3 paired-end rows (+2). The template's C57:C70 and Y57:Y69
    rules end inside the data-processing rows; its E76:AZ1999 prompt and E76:AZ76 rule sit under the paired-end rows."""
    ws = _fill(50, 4, 3)
    cf = {str(c.sqref) for c in ws.conditional_formatting}
    dv = {str(d.sqref) for d in ws.data_validations.dataValidation}
    assert "C93:C109 Y93:Y108" in cf
    assert "E115:AZ115" in cf
    assert "E115:AZ2040" in dv

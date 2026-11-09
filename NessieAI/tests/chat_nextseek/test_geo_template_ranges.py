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


def _fill(n: int):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = load_workbook(TEMPLATE)
    samples = [{"*library name": f"lib{i}", "*molecule": "total RNA", "*single or paired-end": "paired-end",
                "raw file": f"f{i}.fastq.gz"} for i in range(n)]
    geo._populate_geo_seq_workbook(wb, {"samples": samples, "protocols": {}, "study": {}})
    return wb["Metadata"]


def _boxes(sqref):
    return [range_boundaries(str(r)) for r in str(sqref).split()]


def _cf(ws):
    return [(str(cf.sqref), r.type) for cf in ws.conditional_formatting for r in cf.rules]


def _dv(ws):
    return [(str(dv.sqref), dv.type) for dv in ws.data_validations.dataValidation]


@pytest.mark.parametrize("n", [50, 7])
def test_ranges_cover_the_real_library_block(n):
    ws = _fill(n)
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
                assert (c1 == c2 == 1 or {c1, c2} <= raw_cols) and (r1, r2) == (FIRST, last), (s, raw_cols)


def test_rules_below_the_block_are_shifted_by_the_inserted_rows():
    ws = _fill(50)
    shift = 50 - 14
    protocols_row = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == "PROTOCOLS")
    assert protocols_row == 54 + shift
    below = [b for s, _ in _cf(ws) for b in _boxes(s) if b[1] > 60]
    assert below and all(b[1] >= protocols_row for b in below)  # nothing lands inside the library block

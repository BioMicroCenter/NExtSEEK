"""SEC-H03: the chat CSV exporter prefixes formula-looking strings, not numbers."""
import csv

from chat_nextseek.reports.exporters.csv import _write_csv_rows


def test_formula_prefixed_numbers_and_plain_untouched(tmp_path):
    p = tmp_path / "o.csv"
    _write_csv_rows(p, [{"a": '=HYPERLINK("x","y")', "b": "-80", "c": "+1.5e3", "d": "plain", "e": "@SUM(1)"}],
                    ["a", "b", "c", "d", "e"])
    row = next(csv.DictReader(p.open()))
    assert row == {"a": "'=HYPERLINK(\"x\",\"y\")", "b": "-80", "c": "+1.5e3", "d": "plain", "e": "'@SUM(1)"}

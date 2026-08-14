"""GEO's two mandatory study fields must never ship blank.

Observed 2026-07-24 for "write me a geo submission for D.SEQ-240422SHA-23-PUB,
D.SEQ-240422SHA-24-PUB": the workbook came out with both sample rows, all four
protocol sections, three contributors and the supplementary files populated —
but `*title` and `*summary (abstract)`, the only two REQUIRED study fields, were
empty. The submission looks finished and is not.
"""
from __future__ import annotations

from chat_nextseek.reports.exporters.geo_xlsx import (
    _select_study_summary,
    _select_study_title,
)

DESIGN = ("Single-cell RNA sequencing of Mycobacterium tuberculosis granulomas. "
          "Libraries were prepared with DropSeqTools v1.12.")


def test_explicit_values_win():
    study = {"*title": "Real title", "*summary (abstract)": "Real abstract",
             "*experimental design": DESIGN}
    assert _select_study_title(study) == "Real title"
    assert _select_study_summary(study) == "Real abstract"


def test_unstarred_keys_are_accepted():
    study = {"title": "T", "summary": "S"}
    assert _select_study_title(study) == "T"
    assert _select_study_summary(study) == "S"


def test_summary_falls_back_to_the_experimental_design():
    assert _select_study_summary({"*experimental design": DESIGN}) == DESIGN


def test_title_falls_back_to_the_first_sentence_of_the_design():
    title = _select_study_title({"*experimental design": DESIGN})
    assert title == "Single-cell RNA sequencing of Mycobacterium tuberculosis granulomas"


def test_title_falls_back_to_a_sample_title_when_there_is_no_design():
    title = _select_study_title({}, [{"*title": "scRNA-seq of granuloma 21917"}])
    assert title == "scRNA-seq of granuloma 21917"


def test_nothing_available_still_returns_none_rather_than_inventing():
    assert _select_study_title({}) is None
    assert _select_study_summary({}) is None
    assert _select_study_title(None) is None
    assert _select_study_summary(None) is None

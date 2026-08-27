"""Tests for the PRIDE exporters: submission.px (ProteomeXchange summary file)
and SDRF-Proteomics TSV. The report shape mirrors what the report writer emits
(see outputs/<run>/files/report/merged_report_PRIDE.json): a dict keyed by uid
(or "all_samples"), each value carrying report_type="PRIDE" and a `report` dict
holding project_metadata / file_mapping / sample_metadata, where CV-valued
fields are arrays of {cv_label, accession, name, value}.
"""
from __future__ import annotations

import json
from pathlib import Path


def _cv(label, acc, name, value=None):
    return {"cv_label": label, "accession": acc, "name": name, "value": value}


def _sample_pride_merged_report() -> dict:
    """A representative single-entry PRIDE merged report (PARTIAL submission)."""
    return {
        "all_samples": {
            "report_type": "PRIDE",
            "report": {
                "project_metadata": {
                    "*submitter_name": "Lauren Baugh",
                    "*submitter_email": None,
                    "*project_title": "LC-MS/MS proteomic profiling of endometrial tissue (TMT)",
                    "*project_description": "Shotgun LC-MS/MS proteomic analysis of an endometrial biopsy.",
                    "project_tag": ["Human proteome", "Biomedical"],
                    "*keywords": ["endometrium", "proteomics", "TMT", "LC-MS/MS"],
                    "*submission_type": "PARTIAL",
                    "*experiment_type": [_cv("PRIDE", "PRIDE:0000429", "Shotgun proteomics")],
                    "**reason_for_partial": "Sequest .pep.xml search output, not mzIdentML.",
                    "*species": [_cv("NEWT", "9606", "Homo sapiens")],
                    "*tissue": [_cv("BTO", "BTO:0000392", "endometrium")],
                    "cell_type": [_cv(None, None, None)],
                    "*instrument": [_cv("MS", "MS:1003028", "Orbitrap Exploris 480")],
                    "**modification": [
                        _cv("UNIMOD", "UNIMOD:737", "TMT6plex"),
                        _cv("UNIMOD", "UNIMOD:4", "Carbamidomethyl"),
                        _cv("UNIMOD", "UNIMOD:35", "Oxidation"),
                        _cv("UNIMOD", "UNIMOD:1", "Acetyl"),
                    ],
                    "additional": [_cv("PRIDE", "PRIDE:0000097", "Search engine", "Sequest")],
                    "resubmission_px": "PXD045115",
                },
                "file_mapping": [
                    {"*file_id": "1", "*file_type": "SEARCH", "*file_path": "run_01.pep.xml", "**file_mapping": []},
                    {"*file_id": "2", "*file_type": "RAW", "*file_path": "run_01.raw", "**file_mapping": []},
                ],
                "sample_metadata": [
                    {
                        "*file_id": "1",
                        "*species": [_cv("NEWT", "9606", "Homo sapiens")],
                        "*tissue": [_cv("BTO", "BTO:0000392", "endometrium")],
                        "cell_type": [_cv(None, None, None)],
                        "*instrument": [_cv("MS", "MS:1003028", "Orbitrap Exploris 480")],
                        "**modification": [_cv("UNIMOD", "UNIMOD:737", "TMT6plex")],
                        "*experimental_factor": "menstrual cycle phase = proliferative; sex = F",
                    }
                ],
            },
        }
    }


def _write_report(tmp_path: Path, data: dict) -> str:
    p = tmp_path / "merged_report_PRIDE.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


# --- submission.px exporter -------------------------------------------------

def test_px_returns_path_and_file_exists(tmp_path):
    from chat_nextseek.reports.exporters.pride_px import export_pride_report_to_px

    report_path = _write_report(tmp_path, _sample_pride_merged_report())
    out = export_pride_report_to_px(report_path, str(tmp_path))

    assert isinstance(out, list) and len(out) == 1
    p = Path(out[0])
    assert p.exists()
    assert p.suffix == ".px"


def test_px_has_mtd_metadata_lines(tmp_path):
    from chat_nextseek.reports.exporters.pride_px import export_pride_report_to_px

    out = export_pride_report_to_px(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    text = Path(out[0]).read_text(encoding="utf-8")

    # MTD key is the field name WITHOUT the * / ** requirement prefixes
    assert "MTD\tsubmitter_name\tLauren Baugh" in text
    assert "MTD\tproject_title\tLC-MS/MS proteomic profiling of endometrial tissue (TMT)" in text
    assert "MTD\tsubmission_type\tPARTIAL" in text


def test_px_keywords_serialized_comma_joined_single_line(tmp_path):
    from chat_nextseek.reports.exporters.pride_px import export_pride_report_to_px

    out = export_pride_report_to_px(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    text = Path(out[0]).read_text(encoding="utf-8")

    assert "MTD\tkeywords\tendometrium,proteomics,TMT,LC-MS/MS" in text


def test_px_cv_param_four_part_format(tmp_path):
    from chat_nextseek.reports.exporters.pride_px import export_pride_report_to_px

    out = export_pride_report_to_px(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    text = Path(out[0]).read_text(encoding="utf-8")

    assert "MTD\tspecies\t[NEWT, 9606, Homo sapiens, ]" in text
    assert "MTD\tinstrument\t[MS, MS:1003028, Orbitrap Exploris 480, ]" in text


def test_px_repeated_cv_params_become_multiple_lines(tmp_path):
    from chat_nextseek.reports.exporters.pride_px import export_pride_report_to_px

    out = export_pride_report_to_px(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    text = Path(out[0]).read_text(encoding="utf-8")

    mod_lines = [ln for ln in text.splitlines() if ln.startswith("MTD\tmodification\t")]
    assert len(mod_lines) == 4
    assert "MTD\tmodification\t[UNIMOD, UNIMOD:737, TMT6plex, ]" in text


def test_px_user_param_keeps_value_field(tmp_path):
    from chat_nextseek.reports.exporters.pride_px import export_pride_report_to_px

    out = export_pride_report_to_px(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    text = Path(out[0]).read_text(encoding="utf-8")

    assert "MTD\tadditional\t[PRIDE, PRIDE:0000097, Search engine, Sequest]" in text


def test_px_empty_cv_params_skipped(tmp_path):
    from chat_nextseek.reports.exporters.pride_px import export_pride_report_to_px

    out = export_pride_report_to_px(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    text = Path(out[0]).read_text(encoding="utf-8")

    # cell_type is an all-null CV param -> no MTD cell_type line emitted
    assert "MTD\tcell_type" not in text


def test_px_file_mapping_section(tmp_path):
    from chat_nextseek.reports.exporters.pride_px import export_pride_report_to_px

    out = export_pride_report_to_px(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    text = Path(out[0]).read_text(encoding="utf-8")

    assert "FMH\tfile_id\tfile_type\tfile_path\tfile_mapping" in text
    assert "FME\t1\tSEARCH\trun_01.pep.xml\t" in text
    assert "FME\t2\tRAW\trun_01.raw\t" in text


def test_px_sample_metadata_section(tmp_path):
    from chat_nextseek.reports.exporters.pride_px import export_pride_report_to_px

    out = export_pride_report_to_px(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    text = Path(out[0]).read_text(encoding="utf-8")

    assert "SMH\tfile_id\tspecies\ttissue\tcell_type\tdisease\tquantification\tinstrument\tmodification\texperimental_factor" in text
    sme = [ln for ln in text.splitlines() if ln.startswith("SME\t1\t")]
    assert len(sme) == 1
    assert "[NEWT, 9606, Homo sapiens, ]" in sme[0]
    assert sme[0].endswith("menstrual cycle phase = proliferative; sex = F")


def test_px_non_pride_report_returns_empty(tmp_path):
    from chat_nextseek.reports.exporters.pride_px import export_pride_report_to_px

    data = {"all_samples": {"report_type": "GEO", "report": {"study": {}}}}
    out = export_pride_report_to_px(_write_report(tmp_path, data), str(tmp_path))
    assert out == []


# --- SDRF-Proteomics exporter ----------------------------------------------

def test_sdrf_returns_path_and_extension(tmp_path):
    from chat_nextseek.reports.exporters.pride_sdrf import export_pride_report_to_sdrf

    out = export_pride_report_to_sdrf(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    assert isinstance(out, list) and len(out) == 1
    p = Path(out[0])
    assert p.exists()
    assert p.name.endswith(".sdrf.tsv")


def test_sdrf_header_has_core_columns(tmp_path):
    from chat_nextseek.reports.exporters.pride_sdrf import export_pride_report_to_sdrf

    out = export_pride_report_to_sdrf(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    header = Path(out[0]).read_text(encoding="utf-8").splitlines()[0].split("\t")

    assert header[0] == "source name"
    for col in (
        "characteristics[organism]",
        "characteristics[organism part]",
        "assay name",
        "technology type",
        "comment[instrument]",
        "comment[data file]",
    ):
        assert col in header
    assert any(c.startswith("factor value[") for c in header)


def test_sdrf_one_row_per_sample(tmp_path):
    from chat_nextseek.reports.exporters.pride_sdrf import export_pride_report_to_sdrf

    out = export_pride_report_to_sdrf(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    rows = Path(out[0]).read_text(encoding="utf-8").splitlines()
    # one header + one data row (single sample_metadata entry)
    assert len(rows) == 2


def test_sdrf_cell_values(tmp_path):
    from chat_nextseek.reports.exporters.pride_sdrf import export_pride_report_to_sdrf

    out = export_pride_report_to_sdrf(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    lines = Path(out[0]).read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    row = dict(zip(header, lines[1].split("\t")))

    assert row["characteristics[organism]"] == "Homo sapiens"
    assert row["characteristics[organism part]"] == "endometrium"
    assert row["technology type"] == "proteomic profiling by mass spectrometry"
    assert "Orbitrap Exploris 480" in row["comment[instrument]"]
    # data file resolved from file_mapping by file_id
    assert row["comment[data file]"] == "run_01.pep.xml"
    # factor value carries the experimental_factor text
    fv_key = next(c for c in header if c.startswith("factor value["))
    assert row[fv_key] == "menstrual cycle phase = proliferative; sex = F"


def test_sdrf_modification_column_present(tmp_path):
    from chat_nextseek.reports.exporters.pride_sdrf import export_pride_report_to_sdrf

    out = export_pride_report_to_sdrf(_write_report(tmp_path, _sample_pride_merged_report()), str(tmp_path))
    text = Path(out[0]).read_text(encoding="utf-8")
    assert "comment[modification parameters]" in text.splitlines()[0]
    assert "TMT6plex" in text


def test_sdrf_non_pride_returns_empty(tmp_path):
    from chat_nextseek.reports.exporters.pride_sdrf import export_pride_report_to_sdrf

    data = {"all_samples": {"report_type": "GEO", "report": {"study": {}}}}
    out = export_pride_report_to_sdrf(_write_report(tmp_path, data), str(tmp_path))
    assert out == []

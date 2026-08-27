"""PRIDE submission.px exporter.

Serializes a PRIDE report JSON (the report_writer output, shaped like
``reports/templates/pride.json``) into a ProteomeXchange *submission summary
file* (``submission.px``) — the tab-delimited, line-prefixed manifest that the
PRIDE Submission Tool consumes (MTD / FMH-FME / SMH-SME records, CV params as
``[label, accession, name, value]``).

This mirrors the geo_xlsx / sra_xlsx exporters but emits plain text instead of a
filled workbook, because PRIDE — unlike GEO/SRA — has no fillable spreadsheet
template; submission.px is the canonical artifact.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

# Project-metadata serialization order (matches reports/templates/pride.json).
_MTD_ORDER = [
    "submitter_name", "submitter_email", "submitter_affiliation",
    "lab_head_name", "lab_head_email", "lab_head_affiliation",
    "submitter_pride_login", "project_title", "project_description",
    "project_tag", "sample_processing_protocol", "data_processing_protocol",
    "other_omics_link", "keywords", "submission_type", "experiment_type",
    "reason_for_partial", "species", "tissue", "cell_type", "disease",
    "quantification", "instrument", "modification", "additional",
    "pubmed_id", "resubmission_px", "reanalysis_px",
]

# Sample-metadata (SMH/SME) column order, after file_id.
_SME_CV_COLUMNS = [
    "species", "tissue", "cell_type", "disease",
    "quantification", "instrument", "modification",
]


def _strip(key: str) -> str:
    """Drop the leading required (*) / conditional (**) markers from a field key."""
    return key.lstrip("*")


def _strip_keys(d: Mapping[str, Any]) -> dict[str, Any]:
    return {_strip(k): v for k, v in d.items()}


def _is_empty_cv(cv: Any) -> bool:
    if not isinstance(cv, Mapping):
        return True
    return not any(
        (cv.get(f) not in (None, "")) for f in ("cv_label", "accession", "name", "value")
    )


def _cv_to_str(cv: Mapping[str, Any]) -> str:
    """Serialize a CV param as the 4-part ``[label, accession, name, value]``."""
    parts = [
        str(cv.get("cv_label") or ""),
        str(cv.get("accession") or ""),
        str(cv.get("name") or ""),
        str(cv.get("value") or ""),
    ]
    return "[" + ", ".join(parts) + "]"


def _cv_cell(values: Any) -> str:
    """Join the non-empty CV params of a single column into one cell string."""
    if not isinstance(values, list):
        return ""
    return ", ".join(_cv_to_str(cv) for cv in values if not _is_empty_cv(cv))


def _mtd_lines_for(key: str, value: Any) -> list[str]:
    """Build the MTD line(s) for one project-metadata field."""
    name = _strip(key)
    lines: list[str] = []
    if value is None:
        return lines
    if isinstance(value, str):
        if value.strip():
            lines.append(f"MTD\t{name}\t{value}")
        return lines
    if isinstance(value, list):
        if not value:
            return lines
        if all(isinstance(v, Mapping) for v in value):  # CV params
            for cv in value:
                if not _is_empty_cv(cv):
                    lines.append(f"MTD\t{name}\t{_cv_to_str(cv)}")
            return lines
        # list of scalars
        scalars = [str(v) for v in value if v not in (None, "")]
        if not scalars:
            return lines
        if name == "keywords":
            lines.append(f"MTD\t{name}\t{','.join(scalars)}")
        else:
            lines.extend(f"MTD\t{name}\t{s}" for s in scalars)
        return lines
    # numbers / other scalars
    lines.append(f"MTD\t{name}\t{value}")
    return lines


def _extract_pride_reports(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    reports: list[Mapping[str, Any]] = []
    for payload in data.values():
        if not isinstance(payload, Mapping):
            continue
        rtype = (payload.get("report_type") or payload.get("report type") or "")
        if str(rtype).upper() != "PRIDE":
            continue
        report = payload.get("report")
        if isinstance(report, Mapping):
            reports.append(report)
    return reports


def _serialize_px(reports: list[Mapping[str, Any]]) -> str:
    lines: list[str] = []

    # --- MTD: project metadata (from the first report; project-level data) ---
    project = {}
    for r in reports:
        pm = r.get("project_metadata")
        if isinstance(pm, Mapping) and pm:
            project = _strip_keys(pm)
            break

    ordered_keys = [k for k in _MTD_ORDER if k in project]
    ordered_keys += [k for k in project if k not in _MTD_ORDER]
    for key in ordered_keys:
        lines.extend(_mtd_lines_for(key, project[key]))

    # --- FMH/FME: file mapping (concatenated across reports) ---
    file_rows: list[dict[str, Any]] = []
    for r in reports:
        for entry in (r.get("file_mapping") or []):
            if isinstance(entry, Mapping):
                file_rows.append(_strip_keys(entry))
    if file_rows:
        lines.append("")
        lines.append("FMH\tfile_id\tfile_type\tfile_path\tfile_mapping")
        for row in file_rows:
            mapping = row.get("file_mapping") or []
            mapping_str = ",".join(str(m) for m in mapping) if isinstance(mapping, list) else str(mapping or "")
            lines.append(
                "FME\t{}\t{}\t{}\t{}".format(
                    row.get("file_id", ""),
                    row.get("file_type", ""),
                    row.get("file_path", ""),
                    mapping_str,
                )
            )

    # --- SMH/SME: sample metadata (concatenated across reports) ---
    sample_rows: list[dict[str, Any]] = []
    for r in reports:
        for entry in (r.get("sample_metadata") or []):
            if isinstance(entry, Mapping):
                sample_rows.append(_strip_keys(entry))
    if sample_rows:
        lines.append("")
        lines.append("SMH\tfile_id\t" + "\t".join(_SME_CV_COLUMNS) + "\texperimental_factor")
        for row in sample_rows:
            cells = [str(row.get("file_id", ""))]
            cells += [_cv_cell(row.get(col)) for col in _SME_CV_COLUMNS]
            cells.append(str(row.get("experimental_factor") or ""))
            lines.append("SME\t" + "\t".join(cells))

    return "\n".join(lines) + "\n"


def export_pride_report_to_px(report_json_path: str, out_dir: str) -> list[str]:
    """Convert a PRIDE report JSON into a ``submission.px`` file.

    Returns a list with the output path, or ``[]`` if the JSON holds no PRIDE
    report.
    """
    json_path = Path(report_json_path)
    output_root = Path(out_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return []

    reports = _extract_pride_reports(data)
    if not reports:
        return []

    text = _serialize_px(reports)
    dest = output_root / "submission.px"
    dest.write_text(text, encoding="utf-8")
    return [str(dest)]

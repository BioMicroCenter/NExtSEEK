"""SDRF-Proteomics exporter.

Serializes a PRIDE report JSON (report_writer output) into an SDRF-Proteomics
``.sdrf.tsv`` — the tab-separated Sample-and-Data-Relationship file (one row per
sample-to-data-file relationship) that PRIDE accepts as an optional
``EXPERIMENTAL DESIGN`` artifact and that ``sdrf-pipelines`` validates.

Columns follow the SDRF order: sample ``characteristics[...]`` first, then the
data-file ``comment[...]`` block, then ``factor value[...]`` last.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .pride_px import _extract_pride_reports, _is_empty_cv, _strip_keys

_NA = "not available"
_TECH_TYPE = "proteomic profiling by mass spectrometry"


def _first_cv(values: Any) -> Mapping[str, Any] | None:
    if not isinstance(values, list):
        return None
    for cv in values:
        if not _is_empty_cv(cv):
            return cv
    return None


def _name(values: Any, default: str = _NA) -> str:
    cv = _first_cv(values)
    if cv is None:
        return default
    return str(cv.get("name") or default)


def _nt_ac(cv: Mapping[str, Any]) -> str:
    name = str(cv.get("name") or "")
    acc = cv.get("accession")
    return f"NT={name};AC={acc}" if acc else f"NT={name}"


def _all_cv(values: Any) -> list[Mapping[str, Any]]:
    if not isinstance(values, list):
        return []
    return [cv for cv in values if not _is_empty_cv(cv)]


def export_pride_report_to_sdrf(report_json_path: str, out_dir: str) -> list[str]:
    """Convert a PRIDE report JSON into an SDRF-Proteomics ``.sdrf.tsv`` file.

    Returns a list with the output path, or ``[]`` if the JSON holds no PRIDE
    report or no sample metadata.
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

    # file_id -> file_path, and dataset-level label fallback (quantification).
    file_paths: dict[str, str] = {}
    project_label = "label free sample"
    samples: list[dict[str, Any]] = []
    for r in reports:
        for entry in (r.get("file_mapping") or []):
            if isinstance(entry, Mapping):
                e = _strip_keys(entry)
                fid = str(e.get("file_id", ""))
                if fid:
                    file_paths[fid] = str(e.get("file_path") or "")
        pm = r.get("project_metadata")
        if isinstance(pm, Mapping):
            q = _first_cv(_strip_keys(pm).get("quantification"))
            if q is not None:
                project_label = str(q.get("name") or project_label)
        for entry in (r.get("sample_metadata") or []):
            if isinstance(entry, Mapping):
                samples.append(_strip_keys(entry))

    if not samples:
        return []

    max_mods = max((len(_all_cv(s.get("modification"))) for s in samples), default=0)

    header = [
        "source name",
        "characteristics[organism]",
        "characteristics[organism part]",
        "characteristics[cell type]",
        "characteristics[disease]",
        "assay name",
        "technology type",
        "comment[instrument]",
        "comment[label]",
    ]
    header += ["comment[modification parameters]"] * max(max_mods, 1)
    header += ["comment[data file]", "factor value[experimental factor]"]

    rows: list[list[str]] = [header]
    for s in samples:
        fid = str(s.get("file_id", ""))
        instrument = _first_cv(s.get("instrument"))
        q = _first_cv(s.get("quantification"))
        label = str(q.get("name")) if q is not None else project_label
        mods = _all_cv(s.get("modification"))
        mod_cells = [_nt_ac(m) for m in mods]
        mod_cells += [_NA] * (max(max_mods, 1) - len(mod_cells))

        row = [
            f"sample {fid}" if fid else "sample",
            _name(s.get("species")),
            _name(s.get("tissue")),
            _name(s.get("cell_type"), "not applicable"),
            _name(s.get("disease")),
            f"run {fid}" if fid else "run",
            _TECH_TYPE,
            _nt_ac(instrument) if instrument is not None else _NA,
            label,
        ]
        row += mod_cells
        row += [
            file_paths.get(fid, _NA),
            str(s.get("experimental_factor") or _NA),
        ]
        rows.append(row)

    text = "\n".join("\t".join(cell for cell in r) for r in rows) + "\n"
    stem = json_path.stem.replace("merged_report_", "").replace("merged_report", "") or "pride"
    dest = output_root / f"{stem}.sdrf.tsv"
    dest.write_text(text, encoding="utf-8")
    return [str(dest)]

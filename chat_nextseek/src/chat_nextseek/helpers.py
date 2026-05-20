from __future__ import annotations

import ast
import calendar
import csv
import json
import os
import re
import signal
import html
from copy import copy
from io import BytesIO
from urllib.parse import quote, urlparse
from zipfile import ZipFile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Any, Sequence, Callable

import requests

from .artifacts import (
    ArtifactStore,
    build_saved_report_file_manifest,
)
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from .config import ChatConfig
from .schemas import ReportWriterPlan
from .session import SessionState



# TODO (later): replace this mapping with a database-backed context/config table.

# ======================================================
# Reporter / Report Writer helpers
# ======================================================


def _extract_sra_section_reports(
    data: Mapping[str, Any],
    *,
    section_name: str,
) -> list[tuple[str, list[Mapping[str, Any]]]]:
    """Collect per-UID SRA report rows for a given section."""
    reports: list[tuple[str, list[Mapping[str, Any]]]] = []
    for uid, payload in data.items():
        if not isinstance(payload, Mapping):
            continue
        if (payload.get("report_type") or payload.get("report type") or "").upper() != "SRA":
            continue
        report = payload.get("report") or {}
        rows = report.get(section_name) if isinstance(report, Mapping) else None
        if isinstance(rows, list) and rows:
            reports.append((str(uid), [row for row in rows if isinstance(row, Mapping)]))
    return reports


def _worksheet_headers(ws: Worksheet, *, header_row: int) -> list[str]:
    """Read contiguous headers from a worksheet header row."""
    headers: list[str] = []
    col = 1
    while True:
        value = ws.cell(row=header_row, column=col).value
        if value in (None, ""):
            break
        headers.append(str(value))
        col += 1
    return headers


def _write_template_rows(
    wb,
    *,
    sheet_name: str,
    header_row: int,
    template_row: int,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Populate a row-based workbook template from ordered row mappings."""
    ws = wb[sheet_name]
    headers = _worksheet_headers(ws, header_row=header_row)

    if len(rows) > 1:
        for i in range(len(rows) - 1):
            insert_at = template_row + i + 1
            ws.insert_rows(insert_at)
            _copy_row_format(ws, template_row, insert_at)

    for row_idx, row in enumerate(rows, start=template_row):
        for col_idx, header in enumerate(headers, start=1):
            _write_cell(ws, row_idx, col_idx, row.get(header))


def _export_sra_section_to_xlsx(
    report_json_path: str,
    template_xlsx_path: str,
    out_dir: str | Path,
    *,
    section_name: str,
    sheet_name: str | None,
    header_row: int,
    template_row: int,
    filename_suffix: str,
    one_workbook_per_uid: bool,
) -> list[str]:
    """Render a row-based SRA report section into workbook copies from a template."""
    json_path = Path(report_json_path)
    template_path = Path(template_xlsx_path)
    output_root = Path(out_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return []

    template_bytes = template_path.read_bytes()
    output_paths: list[str] = []
    reports = _extract_sra_section_reports(data, section_name=section_name)

    if not reports:
        return []

    if one_workbook_per_uid:
        for uid, rows in reports:
            wb = load_workbook(BytesIO(template_bytes))
            target_sheet = sheet_name or wb.sheetnames[0]
            _write_template_rows(
                wb,
                sheet_name=target_sheet,
                header_row=header_row,
                template_row=template_row,
                rows=rows,
            )
            dest = output_root / f"{uid}_{filename_suffix}"
            wb.save(dest)
            output_paths.append(str(dest))
    else:
        merged_rows: list[Mapping[str, Any]] = []
        for _, rows in reports:
            merged_rows.extend(rows)
        if not merged_rows:
            return []
        wb = load_workbook(BytesIO(template_bytes))
        target_sheet = sheet_name or wb.sheetnames[0]
        _write_template_rows(
            wb,
            sheet_name=target_sheet,
            header_row=header_row,
            template_row=template_row,
            rows=merged_rows,
        )
        dest = output_root / f"{json_path.stem}_{filename_suffix}"
        wb.save(dest)
        output_paths.append(str(dest))

    return output_paths


def export_sra_report_to_xlsx(
    report_json_path: str,
    template_xlsx_path: str,
    out_dir: str | Path,
    *,
    one_workbook_per_uid: bool = True,
) -> list[str]:
    """
    Convert the SRA libraries report JSON into filled SRA submission workbooks.
    Returns list of output file paths.
    """
    return _export_sra_section_to_xlsx(
        report_json_path,
        template_xlsx_path,
        out_dir,
        section_name="libraries",
        sheet_name="SRA_data",
        header_row=1,
        template_row=2,
        filename_suffix="SRA_metadata_filled.xlsx",
        one_workbook_per_uid=one_workbook_per_uid,
    )


def export_sra_biosample_report_to_xlsx(
    report_json_path: str,
    template_xlsx_path: str,
    out_dir: str | Path,
    *,
    one_workbook_per_uid: bool = True,
) -> list[str]:
    """
    Convert the SRA biosamples report JSON into filled BioSample submission workbooks.
    Returns list of output file paths.
    """
    return _export_sra_section_to_xlsx(
        report_json_path,
        template_xlsx_path,
        out_dir,
        section_name="biosamples",
        sheet_name=None,
        header_row=12,
        template_row=13,
        filename_suffix="SRA_biosample_filled.xlsx",
        one_workbook_per_uid=one_workbook_per_uid,
    )


def _coerce_scalar_csv_value(value: Any) -> str:
    """Convert JSON-like values into CSV-safe scalar strings."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            return ";".join("" if item is None else str(item) for item in value)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _normalize_rows_for_csv(rows: Any) -> list[dict[str, Any]]:
    """Normalize a report section into a list of row dicts suitable for CSV export."""
    if rows is None:
        return []
    if isinstance(rows, Mapping):
        return [dict(rows)]
    if isinstance(rows, list):
        normalized: list[dict[str, Any]] = []
        for item in rows:
            if isinstance(item, Mapping):
                normalized.append(dict(item))
            elif item is not None:
                normalized.append({"value": item})
        return normalized
    return [{"value": rows}]


def _extract_report_section_rows(report: Mapping[str, Any], candidates: Sequence[str]) -> list[dict[str, Any]]:
    """Return the first matching report section that looks like tabular row data."""
    for key in candidates:
        value = report.get(key)
        rows = _normalize_rows_for_csv(value)
        if rows:
            return rows
    return []


def _ordered_csv_columns(rows: Sequence[Mapping[str, Any]], preferred: Sequence[str]) -> list[str]:
    """Build CSV column order with required columns first, then observed extras in row order."""
    columns: list[str] = []
    seen: set[str] = set()
    for col in preferred:
        if col not in seen:
            columns.append(col)
            seen.add(col)
    for row in rows:
        for key in row.keys():
            if key not in seen:
                columns.append(key)
                seen.add(key)
    return columns


def _write_csv_rows(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """Write ordered rows to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _coerce_scalar_csv_value(row.get(col)) for col in columns})
    return str(path)



# Moved to helpers_new in Phase 2 — re-exported for backward compat
from .helpers_new.prompts import load_prompt, log_usage, log_prompt  # noqa: E402,F401
from .helpers_new.json_io import _extract_required_paths, estimate_tokens_from_text, safe_parse_json  # noqa: E402,F401
from .helpers_new.text import strip_html, strip_html_recursive, load_file_for_memory, load_json_for_memory  # noqa: E402,F401
from .helpers_new.tools.memory_code import (  # noqa: E402,F401
    _json_type_name,
    _compact_json_value,
    _merge_skeletons,
    _build_skeleton_node,
    _find_record_arrays,
    build_memory_data_profile,
    _build_field_index,
    MemoryCodeSafetyError,
    MemoryCodeTimeoutError,
    _validate_memory_code,
    execute_memory_code,
)
from .helpers_new.results import slim_api_result_for_llm, collect_bundle_files, normalize_api_result_for_memory  # noqa: E402,F401
from .helpers_new.tools.neo4j import tool_neo4j_query  # noqa: E402,F401
from .helpers_new.tools.nextseek_api import (  # noqa: E402,F401
    tool_nextseek_api_request,
    _sanitize_api_row_strings,
    log_api_call,
    fix_sample_endpoint,
    build_recent_results_summary,
    _extract_total_and_rows,
    _should_retry_advanced_search,
    _split_retry_keyword,
    _has_expandable_keyword,
    _advanced_search_retry_attempts,
    _retry_advanced_search_if_empty,
)
from .helpers_new.tools.catalog_match import (  # noqa: E402,F401
    _norm_text,
    _tokenize,
    _doc_from_sampletype,
    _doc_from_assay,
    _score_pair,
    shortlist_catalog,
)
from .helpers_new.dates import (  # noqa: E402,F401
    _normalize_project_id,
    _normalize_years,
    _parse_month,
    _month_range_to_yymmdd_bounds,
    _parse_day,
    _day_range_to_yymmdd_bounds,
)
from .helpers_new.lineage import enumerate_lineage_leaves  # noqa: E402,F401
from .reports_pkg.runners import run_project_sample_report, run_project_protocols_report, run_project_published_report, run_reporter_summary  # noqa: E402,F401
from .reports_pkg.metadata import (  # noqa: E402,F401
    annotate_metadata_with_sampletypes,
    fetch_reporter_metadata,
    _scalar_for_summary,
    _entries_from_metadata,
    build_metadata_summary,
    _is_sequencing_type,
    filter_summary_to_sequencing_lineage,
    build_accession_metadata_lookup,
    filter_summary_for_deg,
)
from .reports_pkg.protocols import (  # noqa: E402,F401
    extract_protocol_refs_from_metadata,
    _request_protocol_record,
    fetch_protocols,
    _extract_docx_text,
    _extract_pdf_text,
    sanitize_protocols_for_llm,
    download_and_extract_protocol_blobs,
)
from .reports_pkg.nfcore import (  # noqa: E402,F401
    top_items,
    _extract_nfcore_samplesheet_rows,
    _accession_matches_criterion,
    _handle_nfcore_artifacts,
    _build_cohort_summary_md,
)
from .reports_pkg.outputs import persist_report_file, generate_report_outputs  # noqa: E402,F401
from .reports_pkg.templates_meta import (  # noqa: E402,F401
    load_report_template,
    normalize_report_type,
    nfcore_pipeline_from_report_type,
    get_report_template_basename,
)
from .reports_pkg.exporters.geo_xlsx import (  # noqa: E402,F401
    _copy_row_format,
    _write_cell,
    _write_list_down,
    _build_header_map,
    _normalize_geo_key,
    _normalize_sheet_label,
    _geo_get,
    _find_first_row_with_label,
    _find_sample_header_row,
    _find_paired_end_header_row,
    _select_study_summary,
    _populate_geo_seq_workbook,
    export_geo_report_to_seq_xlsx,
)

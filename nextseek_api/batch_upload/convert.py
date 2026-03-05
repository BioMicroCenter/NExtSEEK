"""Format detection, 4-sheet conversion, and multi-file merge for batch upload."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Literal, Optional, Tuple

import polars as pl
from pydantic import TypeAdapter, ValidationError

from .models import (
    AssaySheetRow,
    ConvertedBatch,
    InputRowModel,
    InstructionRow,
)
from .ontology import load_ontology_from_sheet, validate_ontology_bulk

log = logging.getLogger(__name__)

try:
    import orjson

    def _json_dumps_min(obj):
        return orjson.dumps(obj).decode("utf-8")
except ImportError:
    import json

    def _json_dumps_min(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

# Module-level TypeAdapters (compiled once at import)
_instruction_adapter: TypeAdapter[List[InstructionRow]] = TypeAdapter(List[InstructionRow])
_assay_adapter: TypeAdapter[List[AssaySheetRow]] = TypeAdapter(List[AssaySheetRow])
_input_row_adapter: TypeAdapter[List[InputRowModel]] = TypeAdapter(List[InputRowModel])

# Traditional 4-sheet names (case-insensitive match)
_TRADITIONAL_SHEETS = {"INSTRUCTIONS", "SAMPLES", "ASSAY", "ONTOLOGY"}

# Instructions sheet: Excel column name -> model field
_INSTRUCTION_COL_MAP = {
    "field": "field",
    "database field": "database_field",
    "database_field": "database_field",
    "field type": "field_type",
    "field_type": "field_type",
    "ontology": "ontology",
}

# Assay sheet: Excel column name -> model field (for Pydantic alias)
_ASSAY_COL_MAP = {
    "sampletype": "SampleType",
    "sample type": "SampleType",
    "sample_type": "SampleType",
    "assaytype": "AssayType",
    "assay type": "AssayType",
    "assay": "Assay",
    "direction": "Direction",
}


def _get_sheet_names(xlsx_path: str) -> List[str]:
    """Read Excel sheet names without loading cell data (openpyxl read_only)."""
    from openpyxl import load_workbook

    wb = load_workbook(xlsx_path, read_only=True, keep_vba=False)
    try:
        return wb.sheetnames
    finally:
        wb.close()


def detect_format(xlsx_path: str) -> Literal["traditional", "flat"]:
    """Detect whether the workbook is traditional 4-sheet or flat format."""
    names = _get_sheet_names(xlsx_path)
    upper = {s.strip().upper() for s in names}
    if _TRADITIONAL_SHEETS <= upper:
        return "traditional"
    return "flat"


def _normalize_instruction_row(row: dict) -> dict:
    """Map Excel row to InstructionRow field names."""
    out = {}
    for k, v in row.items():
        key = (k or "").strip().lower()
        if key in _INSTRUCTION_COL_MAP:
            out[_INSTRUCTION_COL_MAP[key]] = v
    return out


def _normalize_assay_row(row: dict) -> dict:
    """Map Excel row to AssaySheetRow alias names."""
    out = {}
    for k, v in row.items():
        key = (k or "").strip().lower()
        if key in _ASSAY_COL_MAP:
            out[_ASSAY_COL_MAP[key]] = v
    return out


def _read_sheet(
    xlsx_path: str,
    sheet_name: str,
    raise_if_empty: bool = False,
) -> pl.DataFrame:
    """Read one sheet by name; use actual sheet name from workbook for case match."""
    return pl.read_excel(
        xlsx_path,
        sheet_name=sheet_name,
        engine="calamine",
        raise_if_empty=raise_if_empty,
    )


def parse_traditional_file(xlsx_path: str) -> ConvertedBatch:
    """Parse a traditional 4-sheet Excel file into a ConvertedBatch of InputRowModels."""
    sheet_names = _get_sheet_names(xlsx_path)
    name_by_upper = {s.strip().upper(): s for s in sheet_names}

    def _sheet(s: str) -> str:
        return name_by_upper.get(s, s)

    # Read all 4 sheets with Polars (calamine)
    df_ins = _read_sheet(xlsx_path, _sheet("INSTRUCTIONS"), raise_if_empty=True)
    df_samples = _read_sheet(xlsx_path, _sheet("SAMPLES"), raise_if_empty=False)
    df_assay = _read_sheet(xlsx_path, _sheet("ASSAY"), raise_if_empty=False)
    df_ont = _read_sheet(xlsx_path, _sheet("ONTOLOGY"), raise_if_empty=False)

    # Bulk-validate INSTRUCTIONS
    ins_dicts = [_normalize_instruction_row(d) for d in df_ins.to_dicts()]
    instructions = _instruction_adapter.validate_python(ins_dicts)

    # Bulk-validate ASSAY
    assay_dicts = [_normalize_assay_row(d) for d in df_assay.to_dicts()]
    assay_rows = _assay_adapter.validate_python(assay_dicts)
    assay_map: Dict[str, List[int]] = {}
    for row in assay_rows:
        assay_map.setdefault(row.sample_type, []).append(row.assay_id)

    # Field mapping: SampleType -> { human_field_name -> db_attribute }
    sample_type_field_map: Dict[str, Dict[str, str]] = {}
    sample_types_order: List[str] = []
    for ins in instructions:
        st = ins.sample_type
        attr = ins.attribute_name
        sample_type_field_map.setdefault(st, {})[ins.field] = attr
        if st not in sample_types_order:
            sample_types_order.append(st)

    if not sample_types_order:
        return ConvertedBatch(
            rows=[],
            source_files=[xlsx_path],
            warnings=["No sample types found in INSTRUCTIONS sheet."],
        )

    # Ontology validation (4-sheet only): one dict per SAMPLES row, keys = db attribute names
    specs = load_ontology_from_sheet(instructions, df_ont)
    samples_dicts = df_samples.to_dicts()
    if specs:
        row_dicts_for_ont = []
        for row_dict in samples_dicts:
            mapped = {}
            for st in sample_types_order:
                field_map = sample_type_field_map[st]
                for field_name, db_attr in field_map.items():
                    val = row_dict.get(field_name)
                    if val is not None and str(val).strip():
                        mapped[db_attr] = val
            row_dicts_for_ont.append(mapped)
        ont_result = validate_ontology_bulk(row_dicts_for_ont, specs)
        if not ont_result.is_valid:
            return ConvertedBatch(
                rows=[],
                source_files=[xlsx_path],
                ontology_result=ont_result,
                warnings=[f"Ontology validation failed: {len(ont_result.violations)} violation(s)."],
            )

    # Convert SAMPLES to InputRowModel list (one record per (row, sample_type) with data)
    all_input_rows: List[dict] = []
    for row_dict in samples_dicts:
        for st in sample_types_order:
            field_map = sample_type_field_map[st]
            meta = {}
            for field_name, db_attr in field_map.items():
                val = row_dict.get(field_name)
                if val is not None and str(val).strip() != "":
                    meta[db_attr] = val
            if not meta:
                continue
            assay_ids = assay_map.get(st, [])
            all_input_rows.append({
                "UID": None,
                "SampleType": st,
                "json_metadata": _json_dumps_min(meta),
                "assay_ids": assay_ids,
            })

    try:
        validated = _input_row_adapter.validate_python(all_input_rows)
    except ValidationError as e:
        return ConvertedBatch(
            rows=[],
            source_files=[xlsx_path],
            warnings=[f"Input row validation failed: {e}"],
        )

    return ConvertedBatch(rows=validated, source_files=[xlsx_path])


def convert_file(xlsx_path: str) -> ConvertedBatch:
    """Detect format and convert one file to ConvertedBatch."""
    fmt = detect_format(xlsx_path)
    if fmt == "traditional":
        return parse_traditional_file(xlsx_path)
    # Flat: use existing stream_rows and collect into ConvertedBatch
    from .extract import stream_rows

    unknown_columns, stream = stream_rows(xlsx_path, limit=None)
    rows: List[InputRowModel] = []
    for sr in stream:
        if sr.data is not None:
            rows.append(sr.data)
    return ConvertedBatch(
        rows=rows,
        source_files=[xlsx_path],
        warnings=[f"Unknown columns (ignored): {unknown_columns}"] if unknown_columns else [],
    )


def merge_files(xlsx_paths: List[str]) -> ConvertedBatch:
    """Parse multiple files (in parallel when > 1) and merge into one ConvertedBatch."""
    if not xlsx_paths:
        return ConvertedBatch(rows=[], source_files=[], warnings=["No files provided."])

    if len(xlsx_paths) == 1:
        return convert_file(xlsx_paths[0])

    max_workers = min(max(1, (os.cpu_count() or 2) - 1), len(xlsx_paths))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        batches = list(pool.map(convert_file, xlsx_paths))

    all_rows: List[InputRowModel] = []
    all_sources: List[str] = []
    all_warnings: List[str] = []
    ontology_result = None

    for batch in batches:
        all_rows.extend(batch.rows)
        all_sources.extend(batch.source_files)
        all_warnings.extend(batch.warnings or [])
        if batch.ontology_result and not batch.ontology_result.is_valid:
            ontology_result = batch.ontology_result
            break

    return ConvertedBatch(
        rows=all_rows,
        source_files=all_sources,
        ontology_result=ontology_result,
        warnings=all_warnings,
    )

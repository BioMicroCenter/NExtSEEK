"""Stage 1: EXTRACT — Excel parsing with Polars + TypeAdapter bulk validation."""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, Iterator, List, Optional, Set

import polars as pl
from pydantic import TypeAdapter, ValidationError

from .models import InputRowModel, StreamResult

log = logging.getLogger(__name__)

# Module-level singleton: compiles validators once at import time
_input_row_adapter: TypeAdapter[List[InputRowModel]] = TypeAdapter(List[InputRowModel])

# Column mapping: lowercase Excel header -> model field name
_COLUMN_MAP = {
    "uid": "UID",
    "sampletype": "SampleType",
    "sample_type": "SampleType",
    "json_metadata": "json_metadata",
    "jsonmetadata": "json_metadata",
    "assay_ids": "assay_ids",
    "assayids": "assay_ids",
    "project_id": "project_id",
    "projectid": "project_id",
}

_REQUIRED_COLUMNS = {"uid", "sampletype", "json_metadata", "assay_ids"}


def stream_rows(xlsx_path: str, limit: Optional[int] = None) -> Iterator[StreamResult]:
    """Parse an Excel file and yield validated InputRowModel instances.

    Uses Polars with calamine engine for fast parsing and TypeAdapter
    for single-pass bulk validation.
    """
    t0 = time.perf_counter()
    log.info("EXTRACT: reading %s", xlsx_path)

    # 1. Read Excel with calamine engine (Rust-based, 3-5x faster than openpyxl)
    df = pl.read_excel(xlsx_path, engine="calamine")

    # 2. Column normalization
    col_renames = {col: col.strip().lower() for col in df.columns}
    df = df.rename(col_renames)
    normalized_cols = set(df.columns)

    # 3. Validate required columns
    missing = _REQUIRED_COLUMNS - normalized_cols
    # Also accept alternate spellings
    if "sampletype" not in normalized_cols and "sample_type" in normalized_cols:
        missing.discard("sampletype")
    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}. "
            f"Found columns: {sorted(normalized_cols)}"
        )

    # Drop completely empty rows
    df = df.drop_nulls(subset=["uid"])

    if limit is not None:
        df = df.head(limit)

    total_rows = len(df)
    log.info("EXTRACT: %d rows to process", total_rows)

    # 4. Convert to list of dicts
    rows = df.to_dicts()

    # 5. Prepare row dicts (map lowercase -> model fields)
    prepared = _prepare_row_dicts(rows, list(df.columns))

    # 6. Fast path: bulk validation
    try:
        models = _input_row_adapter.validate_python(prepared)
        elapsed = time.perf_counter() - t0
        log.info(
            "EXTRACT: fast path — all %d rows valid (%.1fs, %.0f rows/s)",
            total_rows,
            elapsed,
            total_rows / elapsed if elapsed > 0 else 0,
        )
        for idx, model in enumerate(models):
            yield StreamResult(row_index=idx, data=model, errors=[])
        return
    except ValidationError as e:
        log.info("EXTRACT: fast path failed, entering error recovery")

    # 7. Error recovery path
    failed_indices = _extract_failed_indices(e)
    error_messages = _extract_error_messages(e)

    for idx, row_dict in enumerate(prepared):
        if idx in failed_indices:
            msgs = error_messages.get(idx, [str(e)])
            yield StreamResult(row_index=idx, data=None, errors=msgs)
        else:
            # model_construct skips re-validation for known-valid rows
            model = InputRowModel.model_construct(**row_dict)
            yield StreamResult(row_index=idx, data=model, errors=[])

    elapsed = time.perf_counter() - t0
    log.info(
        "EXTRACT: error recovery — %d valid, %d failed (%.1fs)",
        total_rows - len(failed_indices),
        len(failed_indices),
        elapsed,
    )


def _coerce_json_cell(value) -> str:
    """Convert a cell value to a JSON string."""
    if value is None:
        return "{}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _prepare_row_dicts(rows: List[dict], df_columns: List[str]) -> List[dict]:
    """Map Excel columns to model fields using _COLUMN_MAP."""
    prepared = []
    for row in rows:
        mapped = {}
        for col in df_columns:
            model_field = _COLUMN_MAP.get(col, col)
            value = row.get(col)
            # Coerce json_metadata cells that are dicts/lists
            if model_field == "json_metadata":
                value = _coerce_json_cell(value)
            mapped[model_field] = value
        prepared.append(mapped)
    return prepared


def _extract_failed_indices(e: ValidationError) -> Set[int]:
    """Parse failed list positions from a bulk ValidationError."""
    failed = set()
    for error in e.errors():
        loc = error.get("loc", ())
        if loc and isinstance(loc[0], int):
            failed.add(loc[0])
    return failed


def _extract_error_messages(e: ValidationError) -> Dict[int, List[str]]:
    """Group error messages by row index."""
    by_index: Dict[int, List[str]] = {}
    for error in e.errors():
        loc = error.get("loc", ())
        if loc and isinstance(loc[0], int):
            idx = loc[0]
            field_path = ".".join(str(x) for x in loc[1:])
            msg = f"{field_path}: {error.get('msg', 'unknown error')}"
            by_index.setdefault(idx, []).append(msg)
    return by_index

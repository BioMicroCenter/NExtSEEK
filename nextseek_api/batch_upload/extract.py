"""Stage 1: EXTRACT — Excel parsing with Polars + TypeAdapter bulk validation."""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, Iterator, List, Optional, Set, Tuple

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

# Known (optional) columns that may appear in real-world spreadsheets.
# These are NOT required for successful upload, but should not be flagged as unknown.
_OPTIONAL_COLUMNS = {
    "project_id",
    "study_title",
    "study_id",
    "sop_id",
    "assay_titles",
    "mapped_assay_ids",
    "mapped_study_id",
}

# Aliases that are accepted and/or mapped by _COLUMN_MAP.
_ALIAS_COLUMNS = set(_COLUMN_MAP.keys())


def _detect_unknown_columns(normalized_cols: Set[str]) -> List[str]:
    """Return a sorted list of columns to ignore (unknown extras)."""
    known = set(_REQUIRED_COLUMNS) | set(_OPTIONAL_COLUMNS) | set(_ALIAS_COLUMNS)
    # sample_type is accepted as alias for sampletype
    known.add("sample_type")
    return sorted(normalized_cols - known)


def stream_rows(xlsx_path: str, limit: Optional[int] = None) -> Tuple[List[str], Iterator[StreamResult]]:
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

    unknown_columns = _detect_unknown_columns(normalized_cols)
    if unknown_columns:
        log.warning("EXTRACT: unknown columns ignored: %s", unknown_columns)

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

    # Drop rows only when ALL key columns are null (uid, sampletype, json_metadata)
    _drop_cols = [c for c in ["uid", "sampletype", "sample_type", "json_metadata"] if c in df.columns]
    if _drop_cols:
        df = df.filter(~pl.all_horizontal(pl.col(c).is_null() for c in _drop_cols))

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
        def _iter_fast() -> Iterator[StreamResult]:
            for idx, model in enumerate(models):
                yield StreamResult(row_index=idx, data=model, errors=[])

        return unknown_columns, _iter_fast()
    except ValidationError as e:
        log.info("EXTRACT: fast path failed, entering error recovery")
        validation_error = e

    # 7. Error recovery path: identify failed rows, bulk re-validate the rest
    failed_indices = _extract_failed_indices(validation_error)
    error_messages = _extract_error_messages(validation_error)

    # Filter out failed rows and bulk re-validate valid ones
    valid_prepared = [row for idx, row in enumerate(prepared) if idx not in failed_indices]
    valid_original_indices = [idx for idx in range(len(prepared)) if idx not in failed_indices]

    validated_models: List[InputRowModel] = []
    if valid_prepared:
        # Re-validate valid rows in bulk — runs all validators (coercion, normalization)
        validated_models = _input_row_adapter.validate_python(valid_prepared)

    # Build index -> validated model lookup
    validated_by_original_idx = dict(zip(valid_original_indices, validated_models))

    def _iter_recovery() -> Iterator[StreamResult]:
        for idx in range(len(prepared)):
            if idx in failed_indices:
                msgs = error_messages.get(idx, [str(validation_error)])
                yield StreamResult(row_index=idx, data=None, errors=msgs)
            else:
                yield StreamResult(row_index=idx, data=validated_by_original_idx[idx], errors=[])

    elapsed = time.perf_counter() - t0
    log.info(
        "EXTRACT: error recovery — %d valid, %d failed (%.1fs)",
        total_rows - len(failed_indices),
        len(failed_indices),
        elapsed,
    )
    return unknown_columns, _iter_recovery()


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

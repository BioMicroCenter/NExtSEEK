"""CSV → list[RuntimeEvalRow] loader for the HiBayes runtime-reliability pipeline.

Public surface:
    - load_runtime_eval_csv(path) -> tuple[list[RuntimeEvalRow], LoadReport]
    - LoadReport (dataclass-style Pydantic model)
    - RejectedRow (sub-model carrying query_id + serialized error)

Design references:
    - DD-01 (T03 validators run automatically on construction)
    - R-09 / OQ-5 (lowercase + strip task_family at load time)
    - R-05 (cost_usd='' → None at parse time)
    - B1 (no failure_mode cross-field re-check; exporter guarantees it)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError

from .models import RuntimeEvalRow


class RejectedRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str
    error: str


class LoadReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted: int
    rejected: list[RejectedRow]
    normalized_task_family_count: int
    warnings: list[str]


def _normalize_task_family(raw: str) -> tuple[str, bool]:
    """Apply OQ-5: lowercase + strip. Return (normalized, was_changed)."""
    norm = raw.strip().lower()
    return norm, norm != raw


def _coerce_cost_usd(cell: object) -> float | None:
    """Empty strings + NaN → None (R-05). Numeric strings/floats → float."""
    if cell is None:
        return None
    if isinstance(cell, float) and pd.isna(cell):
        return None
    if isinstance(cell, str):
        if cell.strip() == "":
            return None
        return float(cell)
    return float(cell)


def load_runtime_eval_csv(path: Path) -> tuple[list[RuntimeEvalRow], LoadReport]:
    """Read CSV at `path`, apply normalization, validate every row.

    A row whose construction raises `pydantic.ValidationError` is recorded in
    `LoadReport.rejected` and excluded from the returned row list. The function
    does NOT raise on validation failure; callers can decide whether a non-empty
    `report.rejected` is fatal.

    Raises:
        FileNotFoundError: if `path` does not exist.
        pandas.errors.EmptyDataError: if `path` is an empty file.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])

    rows: list[RuntimeEvalRow] = []
    rejected: list[RejectedRow] = []
    normalized_count = 0
    warnings: list[str] = []

    for record in df.to_dict(orient="records"):
        raw_family = record.get("task_family", "")
        norm_family, changed = _normalize_task_family(raw_family)
        if changed:
            normalized_count += 1
        record["task_family"] = norm_family

        # Subtype: empty string → None
        if record.get("task_subtype", "") == "":
            record["task_subtype"] = None

        # cost_usd: empty string → None (R-05)
        record["cost_usd"] = _coerce_cost_usd(record.get("cost_usd"))

        # Boolean coercion: pandas left these as strings 'true'/'false'
        for bool_field in ("answer_provided", "is_error", "timed_out", "runtime_success"):
            v = record.get(bool_field)
            if isinstance(v, str):
                record[bool_field] = v.strip().lower() == "true"

        # Numeric coercion for fields Pydantic v2 will not coerce from string-with-decimal
        # under strict mode. (RuntimeEvalRow inherits extra='forbid' but does not enable
        # strict; pydantic v2 default is lax on str→int|float, so this is belt-and-braces.)
        for int_field in ("tool_calls_total", "artifact_count", "is_opus"):
            v = record.get(int_field)
            if isinstance(v, str):
                record[int_field] = int(v)
        for float_field in ("latency_seconds",):
            v = record.get(float_field)
            if isinstance(v, str):
                record[float_field] = float(v)

        try:
            rows.append(RuntimeEvalRow.model_validate(record))
        except ValidationError as e:
            rejected.append(
                RejectedRow(query_id=str(record.get("query_id", "?")), error=str(e))
            )

    return rows, LoadReport(
        accepted=len(rows),
        rejected=rejected,
        normalized_task_family_count=normalized_count,
        warnings=warnings,
    )

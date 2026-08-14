"""Stage A CSV → list[ArtifactValidityRow]."""
from __future__ import annotations

import csv
from pathlib import Path

from .models import ArtifactValidityRow


# Locked-design §5.1: 29-column header in exact documented order. Pinned by
# `tests/unit/eval/test_hibayes_artifact_validity.py::test_stage_a_csv_header_29_columns_pin`.
ARTIFACT_VALIDITY_CSV_COLUMNS: list[str] = [
    "run_id",
    "query_id",
    "task_family",
    "artifact_eval_id",
    "artifact_expected",
    "expected_artifact_kind",
    "artifact_declared",
    "artifact_path",
    "artifact_basename",
    "artifact_ext",
    "runtime_success",
    "failure_mode",
    "artifact_exists",
    "artifact_accessible",
    "file_size_bytes",
    "parser_used",
    "parse_success",
    "sheet_count",
    "row_count",
    "column_count",
    "nonempty_cell_count",
    "null_cell_fraction",
    "required_fields_present",
    "required_fields_complete",
    "missing_required_fields",
    "all_required_rows_complete",
    "artifact_validity_status",
    "artifact_success",
    "validation_notes",
]


def load_artifact_validity_csv(path: Path) -> list[ArtifactValidityRow]:
    rows: list[ArtifactValidityRow] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                rows.append(
                    ArtifactValidityRow(
                        query_id=row.get("query_id", ""),
                        task_family=row.get("task_family", ""),
                        artifact_expected=row.get("artifact_expected", "False").lower() == "true",
                        artifact_success=row.get("artifact_success", "False").lower() == "true",
                        artifact_validity_status=row.get("artifact_validity_status", ""),
                    )
                )
            except Exception:  # noqa: BLE001
                continue
    return rows

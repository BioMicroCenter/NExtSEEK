"""Stage C functional-usefulness CSV → list[FunctionalUsefulnessRow]."""
from __future__ import annotations

import csv
from pathlib import Path

from .models import FunctionalUsefulnessRow


# Locked-design §5.3: 12-column header in exact documented order. Pinned by
# `tests/unit/eval/test_hibayes_functional_usefulness.py::test_functional_usefulness_csv_header_12_columns_pin`.
# This MUST equal `tools.e2e.functional_evaluator.FUNCTIONAL_USEFULNESS_HEADER_12`
# (the T2.1 producer's emitted header) — producer/consumer drift is the failure
# mode the Pass 2 reviewer flagged as D1.
FUNCTIONAL_USEFULNESS_CSV_COLUMNS: list[str] = [
    "query_id",
    "task_family",
    "expected_behavior",
    "runtime_success",
    "artifact_status",
    "outcome",
    "usefulness_score",
    "primary_issue",
    "functional_success",
    "needs_human_review",
    "review_priority",
    "rationale",
]


def load_functional_usefulness_csv(path: Path) -> list[FunctionalUsefulnessRow]:
    rows: list[FunctionalUsefulnessRow] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                rows.append(
                    FunctionalUsefulnessRow(
                        query_id=row.get("query_id", ""),
                        task_family=row.get("task_family", ""),
                        functional_success=row.get("functional_success", "False").lower()
                        == "true",
                    )
                )
            except Exception:  # noqa: BLE001
                continue
    return rows

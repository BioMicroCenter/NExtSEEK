"""T1.2 — Stage B: Functional Eval Input CSV builder.

Reads manifest.json + per-query <record_path>.record.json + hibayes_eval_rows.csv
(runtime axis output) + hibayes_artifact_validity.csv (Stage A output). Aggregates
per-query artifact_status via worst-status-wins (locked DD-24). Emits
hibayes_functional_eval_inputs.csv (12 columns per locked §5.2).

Locked-spec anchors:
- §5.2: 12-column header verbatim
- §5.2 post-table note: CSV order ≠ BAML class order — T2.1 must keyword-construct
- DD-24: worst-status-wins aggregation rule
- DD-30 + DL-021: expected_behavior derived via tools.hibayes.expected_behavior
- DD-47: four-hop parents[3] record-path resolution; canonical-layout assumption (R29)
- DD-48: read manifest as plain dict (no RawRunManifest.model_validate)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from nextseek_api.eval.enums import ArtifactStatus
from nextseek_api.eval.expected_behavior import expected_behavior_rule


__all__ = [
    "CSV_HEADER_12",
    "WORST_STATUS_ORDER",
    "aggregate_artifact_status",
    "main",
    "run_stage_b",
]


# Locked §5.2 — 12 columns in exact order. CSV order intentionally differs from BAML class order.
CSV_HEADER_12: tuple[str, ...] = (
    "query_id",
    "task_family",
    "query_text",
    "final_answer",
    "answer_provided",
    "runtime_success",
    "failure_mode",
    "artifact_expected",
    "artifact_status",
    "artifact_kind",
    "declared_artifact_count",
    "expected_behavior",
)


# Locked DD-24 — worst→best, NotExpected dropped from aggregation.
WORST_STATUS_ORDER: tuple[ArtifactStatus, ...] = (
    ArtifactStatus.RuntimeFailed,
    ArtifactStatus.Missing,
    ArtifactStatus.Inaccessible,
    ArtifactStatus.Unreadable,
    ArtifactStatus.SchemaInvalid,
    ArtifactStatus.Incomplete,
    ArtifactStatus.PartialAfterFailure,
    ArtifactStatus.Indeterminate,
    ArtifactStatus.Valid,
)


def aggregate_artifact_status(
    statuses: list[ArtifactStatus],
) -> ArtifactStatus | None:
    """Locked DD-24: worst-status-wins across non-NotExpected statuses.

    Returns None if all statuses are NotExpected (or input is empty).
    """
    filtered = [s for s in statuses if s != ArtifactStatus.NotExpected]
    if not filtered:
        return None
    rank = {s: i for i, s in enumerate(WORST_STATUS_ORDER)}
    return min(filtered, key=lambda s: rank.get(s, 999))


def _read_runtime_csv(path: Path) -> dict[str, dict[str, str]]:
    """Build a per-query lookup: query_id → {task_family, runtime_success, failure_mode}."""
    by_qid: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            qid = row.get("query_id", "")
            by_qid[qid] = {
                "task_family": row.get("task_family", ""),
                "runtime_success": row.get("runtime_success", ""),
                "failure_mode": row.get("failure_mode", ""),
            }
    return by_qid


def _read_artifact_csv(path: Path) -> dict[str, dict[str, Any]]:
    """Build a per-query aggregate of Stage A rows.

    Returns: query_id → {
        artifact_expected: bool,
        artifact_kind: str,
        declared_count: int,
        statuses: list[ArtifactStatus],
    }
    """
    by_qid: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            qid = row.get("query_id", "")
            entry = by_qid.setdefault(
                qid,
                {
                    "artifact_expected": False,
                    "artifact_kind": "",
                    "declared_count": 0,
                    "statuses": [],
                },
            )
            entry["artifact_expected"] = (
                entry["artifact_expected"]
                or row.get("artifact_expected") in ("True", "true", "1")
            )
            if row.get("expected_artifact_kind"):
                entry["artifact_kind"] = row["expected_artifact_kind"]
            if row.get("artifact_declared") in ("True", "true", "1"):
                entry["declared_count"] += 1
            status_str = row.get("artifact_validity_status", "")
            if status_str:
                try:
                    entry["statuses"].append(ArtifactStatus(status_str))
                except ValueError:
                    # Skip unknown status strings; will fall through to None aggregate.
                    pass
    return by_qid


def _resolve_record_path(manifest_path: Path, record_path_str: str) -> Path:
    """Locked DD-47: four-hop parents[3] resolution.

    record_path is relative to repo_root = Path(manifest_path).resolve().parents[3].
    Canonical-layout assumption: manifest at evidence/headless/<run>/manifest.json
    """
    repo_root = manifest_path.resolve().parents[3]
    resolved = repo_root / record_path_str
    if not resolved.is_file():
        raise FileNotFoundError(
            f"Stage B failed to resolve record_path. "
            f"manifest.json={manifest_path} record_path={record_path_str} "
            f"resolved={resolved}. Per locked DD-47 the manifest must live at "
            f"evidence/headless/<run>/manifest.json (default --output-dir invocation)."
        )
    return resolved


def run_stage_b(
    *,
    manifest_path: Path,
    runtime_csv_path: Path,
    artifact_csv_path: Path,
    out_csv_path: Path,
) -> int:
    """Run Stage B end-to-end. Returns process exit code."""
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Stage B requires --manifest-path; not found at {manifest_path}"
        )
    if not runtime_csv_path.is_file():
        raise FileNotFoundError(
            f"Stage B requires hibayes_eval_rows.csv; not found at {runtime_csv_path}"
        )
    if not artifact_csv_path.is_file():
        raise FileNotFoundError(
            f"Stage B requires hibayes_artifact_validity.csv; not found at {artifact_csv_path}"
        )

    with manifest_path.open("r", encoding="utf-8") as fh:
        manifest: dict[str, Any] = json.load(fh)

    runtime_by_qid = _read_runtime_csv(runtime_csv_path)
    artifact_by_qid = _read_artifact_csv(artifact_csv_path)

    rows: list[list[Any]] = []
    for s in manifest.get("summaries", []):
        qid: str = s.get("query_id", "")
        query_text: str = s.get("query_text", "")
        record_path_str: str = s.get("record_path", "")
        if record_path_str:
            record_path = _resolve_record_path(manifest_path, record_path_str)
            with record_path.open("r", encoding="utf-8") as fh:
                record: dict[str, Any] = json.load(fh)
            final_answer = record.get("final_answer", "")
        else:
            final_answer = ""

        rt = runtime_by_qid.get(qid, {})
        task_family = rt.get("task_family") or "Unsupported"
        runtime_success = rt.get("runtime_success", "")
        failure_mode = rt.get("failure_mode", "")

        av = artifact_by_qid.get(qid, {})
        artifact_expected = bool(av.get("artifact_expected", False))
        artifact_kind = av.get("artifact_kind", "")
        declared_count = int(av.get("declared_count", 0))
        statuses: list[ArtifactStatus] = av.get("statuses", [])
        agg_status = aggregate_artifact_status(statuses)

        try:
            expected_behavior = expected_behavior_rule(task_family).value
        except KeyError as e:
            # Fail loud per T0.2 contract (task-02-enums-rule.md lines 446-449):
            # "Raises: KeyError: if `task_family` is not one of the 22 canonical
            # strings in FAMILIES_22. Callers MUST handle unknown families
            # explicitly rather than relying on a silent default."
            # An empty expected_behavior cell would propagate silently through
            # Stage C and into Stage D's functional-axis posterior with no signal.
            raise ValueError(
                f"Stage B encountered unknown task_family={task_family!r} "
                f"(qid={qid!r}, runtime-csv-row). T0.2 docstring requires callers "
                f"handle unknown families explicitly. Add the family to FAMILIES_22 "
                f"via T0.2, or fix the source data."
            ) from e

        answer_provided = bool(s.get("answer_provided", False))

        rows.append(
            [
                qid,
                task_family,
                query_text,
                final_answer,
                answer_provided,
                runtime_success,
                failure_mode,
                artifact_expected,
                agg_status.value if agg_status else "",
                artifact_kind,
                declared_count,
                expected_behavior,
            ]
        )

    out_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with out_csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEADER_12)
        for row in rows:
            w.writerow(row)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.hibayes.functional_inputs",
        description=(
            "Stage B — HiBayes Functional Eval Input CSV builder. "
            "Joins manifest + runtime CSV + artifact CSV + per-query record.json "
            "into a 12-column CSV consumed by Stage C."
        ),
    )
    parser.add_argument("--manifest-path", required=True, type=Path)
    parser.add_argument(
        "--runtime-csv",
        type=Path,
        default=Path("data/hibayes_eval_rows.csv"),
    )
    parser.add_argument(
        "--artifact-csv",
        type=Path,
        default=Path("out/hibayes_artifact_validity.csv"),
    )
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("out/hibayes_functional_eval_inputs.csv"),
    )
    args = parser.parse_args(argv)
    return run_stage_b(
        manifest_path=args.manifest_path,
        runtime_csv_path=args.runtime_csv,
        artifact_csv_path=args.artifact_csv,
        out_csv_path=args.out_csv,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

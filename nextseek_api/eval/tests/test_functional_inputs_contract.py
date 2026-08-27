"""tools/hibayes/tests/test_functional_inputs.py — pinning tests for T1.2 Stage B."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from nextseek_api.eval.enums import ArtifactStatus
from nextseek_api.eval.functional_inputs import (
    CSV_HEADER_12,
    WORST_STATUS_ORDER,
    aggregate_artifact_status,
    run_stage_b,
)


EXPECTED_12_COLUMNS = [
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
]


def test_csv_header_12_is_exact_locked_design_order() -> None:
    """Locked §5.2: 12 columns in exact order. Any reorder or omission is a defect."""
    assert list(CSV_HEADER_12) == EXPECTED_12_COLUMNS


def test_csv_header_12_length_is_12() -> None:
    assert len(CSV_HEADER_12) == 12


def test_csv_header_differs_from_baml_class_order() -> None:
    """Locked §5.2 post-table note: CSV column order intentionally differs from BAML.

    This test pins the divergence so a future refactor that "fixes" the order
    silently is caught.
    """
    baml_field_order = [
        "task_family",
        "query_text",
        "final_answer",
        "answer_provided",
        "runtime_success",
        "failure_mode",
        "expected_behavior",  # position 7 in BAML
        "artifact_expected",
        "artifact_status",
        "artifact_kind",
        "declared_artifact_count",
    ]
    # CSV order: expected_behavior at position 12 (last), not position 7.
    csv_non_id_cols = [c for c in CSV_HEADER_12 if c != "query_id"]
    assert csv_non_id_cols != baml_field_order


# -----------------------------------------------------------------------------
# Worst-status-wins aggregation (locked DD-24)
# -----------------------------------------------------------------------------

def test_worst_status_order_is_locked_canonical() -> None:
    """DD-24: ordering (worst→best): RuntimeFailed > Missing > Inaccessible > Unreadable >
    SchemaInvalid > Incomplete > PartialAfterFailure > Indeterminate > Valid.
    NotExpected dropped from aggregation."""
    expected_order = [
        ArtifactStatus.RuntimeFailed,
        ArtifactStatus.Missing,
        ArtifactStatus.Inaccessible,
        ArtifactStatus.Unreadable,
        ArtifactStatus.SchemaInvalid,
        ArtifactStatus.Incomplete,
        ArtifactStatus.PartialAfterFailure,
        ArtifactStatus.Indeterminate,
        ArtifactStatus.Valid,
    ]
    assert list(WORST_STATUS_ORDER) == expected_order


def test_aggregate_artifact_status_picks_worst_non_not_expected() -> None:
    """DD-24: aggregate picks the worst-ranked non-NotExpected status."""
    statuses = [ArtifactStatus.Valid, ArtifactStatus.Missing, ArtifactStatus.NotExpected]
    assert aggregate_artifact_status(statuses) == ArtifactStatus.Missing


def test_aggregate_artifact_status_all_not_expected_returns_none() -> None:
    """DD-24: when only NotExpected rows are present, aggregate returns None."""
    statuses = [ArtifactStatus.NotExpected, ArtifactStatus.NotExpected]
    assert aggregate_artifact_status(statuses) is None


def test_aggregate_artifact_status_runtime_failed_dominates() -> None:
    """DD-24: RuntimeFailed is the worst rank (index 0); always wins over any other status."""
    statuses = [
        ArtifactStatus.Valid,
        ArtifactStatus.RuntimeFailed,
        ArtifactStatus.SchemaInvalid,
    ]
    assert aggregate_artifact_status(statuses) == ArtifactStatus.RuntimeFailed


def test_aggregate_artifact_status_empty_returns_none() -> None:
    """An empty list aggregates to None (no rows for this query)."""
    assert aggregate_artifact_status([]) is None


# -----------------------------------------------------------------------------
# run_stage_b: file-existence prerequisites
# -----------------------------------------------------------------------------

def test_run_stage_b_errors_on_missing_manifest(tmp_path: Path) -> None:
    """Stage B errors when --manifest-path is absent."""
    with pytest.raises(FileNotFoundError, match="manifest"):
        run_stage_b(
            manifest_path=tmp_path / "missing.json",
            runtime_csv_path=tmp_path / "rows.csv",
            artifact_csv_path=tmp_path / "av.csv",
            out_csv_path=tmp_path / "out.csv",
        )


def test_run_stage_b_errors_on_missing_runtime_csv(tmp_path: Path) -> None:
    """Stage B errors when hibayes_eval_rows.csv is absent."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"run_id": "x", "summaries": []}))
    with pytest.raises(FileNotFoundError, match="hibayes_eval_rows.csv"):
        run_stage_b(
            manifest_path=manifest_path,
            runtime_csv_path=tmp_path / "missing.csv",
            artifact_csv_path=tmp_path / "missing.csv",
            out_csv_path=tmp_path / "out.csv",
        )


def test_run_stage_b_errors_on_missing_artifact_csv(tmp_path: Path) -> None:
    """Stage B errors when hibayes_artifact_validity.csv is absent."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"run_id": "x", "summaries": []}))
    runtime_csv = tmp_path / "rows.csv"
    runtime_csv.write_text("query_id,task_family,runtime_success,failure_mode\n")
    with pytest.raises(FileNotFoundError, match="hibayes_artifact_validity.csv"):
        run_stage_b(
            manifest_path=manifest_path,
            runtime_csv_path=runtime_csv,
            artifact_csv_path=tmp_path / "missing.csv",
            out_csv_path=tmp_path / "out.csv",
        )


# -----------------------------------------------------------------------------
# run_stage_b: end-to-end smoke (single query happy path)
# -----------------------------------------------------------------------------

def _seed_inputs(tmp_path: Path, qid: str, task_family: str, query_text: str) -> tuple[Path, Path, Path]:
    # manifest
    manifest = {
        "run_id": "smoke",
        "summaries": [
            {
                "query_id": qid,
                "query_text": query_text,
                "answer_provided": True,
                "is_error": False,
                "timed_out": False,
                "artifacts": [],
                "record_path": f"evidence/headless/smoke/{qid}.record.json",
            }
        ],
    }
    headless_dir = tmp_path / "evidence" / "headless" / "smoke"
    headless_dir.mkdir(parents=True)
    manifest_path = headless_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    record = {"query_id": qid, "final_answer": "ok"}
    (headless_dir / f"{qid}.record.json").write_text(json.dumps(record))
    # runtime CSV
    runtime_csv = tmp_path / "rows.csv"
    runtime_csv.write_text(
        "query_id,task_family,runtime_success,failure_mode\n"
        f"{qid},{task_family},True,none\n"
    )
    # artifact CSV (1 row, NotExpected for non-Report families)
    artifact_csv = tmp_path / "av.csv"
    artifact_csv.write_text(
        "query_id,task_family,artifact_expected,artifact_validity_status,expected_artifact_kind,artifact_declared\n"
        f"{qid},{task_family},False,NotExpected,NONE_EXPECTED,False\n"
    )
    return manifest_path, runtime_csv, artifact_csv


def test_run_stage_b_emits_12_column_csv(tmp_path: Path) -> None:
    """Happy path: a Search-Basic query produces a single 12-column row."""
    manifest_path, runtime_csv, artifact_csv = _seed_inputs(
        tmp_path, qid="Search-Basic-1", task_family="Search-Basic", query_text="find X"
    )
    out_csv = tmp_path / "fei.csv"
    exit_code = run_stage_b(
        manifest_path=manifest_path,
        runtime_csv_path=runtime_csv,
        artifact_csv_path=artifact_csv,
        out_csv_path=out_csv,
    )
    assert exit_code == 0
    with out_csv.open("r", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = list(reader)
    assert header == EXPECTED_12_COLUMNS
    assert len(rows) == 1
    row = rows[0]
    by_col = dict(zip(header, row))
    assert by_col["query_id"] == "Search-Basic-1"
    assert by_col["task_family"] == "Search-Basic"
    assert by_col["query_text"] == "find X"
    assert by_col["final_answer"] == "ok"
    assert by_col["expected_behavior"] == "AnswerDirectly"


def test_run_stage_b_resolves_record_path_via_parents_3(tmp_path: Path) -> None:
    """DD-47 four-hop parents[3] resolution: record file at evidence/headless/<run>/<qid>.record.json."""
    qid = "Memory-1"
    manifest_path, runtime_csv, artifact_csv = _seed_inputs(
        tmp_path, qid=qid, task_family="Memory", query_text="recall last"
    )
    out_csv = tmp_path / "fei.csv"
    exit_code = run_stage_b(
        manifest_path=manifest_path,
        runtime_csv_path=runtime_csv,
        artifact_csv_path=artifact_csv,
        out_csv_path=out_csv,
    )
    assert exit_code == 0
    # Verify final_answer made it from the record.json into the CSV.
    with out_csv.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        row = next(reader)
    assert row["final_answer"] == "ok"
    assert row["expected_behavior"] == "UsePriorContext"


def test_run_stage_b_errors_on_missing_record_path(tmp_path: Path) -> None:
    """DD-47 step 3: missing record.json fails loud with both manifest_path and record_path named."""
    qid = "Search-Basic-1"
    manifest_path, runtime_csv, artifact_csv = _seed_inputs(
        tmp_path, qid=qid, task_family="Search-Basic", query_text="find X"
    )
    # Delete the record.json — manifest still references it.
    (manifest_path.parent / f"{qid}.record.json").unlink()
    with pytest.raises(FileNotFoundError) as exc_info:
        run_stage_b(
            manifest_path=manifest_path,
            runtime_csv_path=runtime_csv,
            artifact_csv_path=artifact_csv,
            out_csv_path=tmp_path / "fei.csv",
        )
    # Error message must name both the manifest_path and the failing record_path per DD-47.
    msg = str(exc_info.value)
    assert "manifest.json" in msg or str(manifest_path) in msg
    assert f"{qid}.record.json" in msg


# -----------------------------------------------------------------------------
# Unknown task_family: fail loud per T0.2 contract (task-02-enums-rule.md L446-449)
# -----------------------------------------------------------------------------

def test_run_stage_b_raises_value_error_on_unknown_task_family(tmp_path: Path) -> None:
    """T0.2 contract: callers MUST handle unknown families explicitly rather
    than relying on a silent default. Stage B re-raises as ValueError naming
    the offending task_family and qid so the failure is visible in Stage C/D
    rather than propagating an empty `expected_behavior` cell silently."""
    qid = "Unknown-Family-Query-1"
    bad_family = "DefinitelyNotARealFamily"
    # Seed inputs but with a task_family that is NOT in FAMILIES_22.
    manifest = {
        "run_id": "smoke",
        "summaries": [
            {
                "query_id": qid,
                "query_text": "...",
                "answer_provided": True,
                "is_error": False,
                "timed_out": False,
                "artifacts": [],
                "record_path": f"evidence/headless/smoke/{qid}.record.json",
            }
        ],
    }
    headless_dir = tmp_path / "evidence" / "headless" / "smoke"
    headless_dir.mkdir(parents=True)
    manifest_path = headless_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    (headless_dir / f"{qid}.record.json").write_text(json.dumps({"final_answer": "x"}))
    runtime_csv = tmp_path / "rows.csv"
    runtime_csv.write_text(
        "query_id,task_family,runtime_success,failure_mode\n"
        f"{qid},{bad_family},True,none\n"
    )
    artifact_csv = tmp_path / "av.csv"
    artifact_csv.write_text(
        "query_id,task_family,artifact_expected,artifact_validity_status,"
        "expected_artifact_kind,artifact_declared\n"
        f"{qid},{bad_family},False,NotExpected,NONE_EXPECTED,False\n"
    )
    with pytest.raises(ValueError) as exc_info:
        run_stage_b(
            manifest_path=manifest_path,
            runtime_csv_path=runtime_csv,
            artifact_csv_path=artifact_csv,
            out_csv_path=tmp_path / "fei.csv",
        )
    msg = str(exc_info.value)
    assert bad_family in msg
    assert qid in msg
    # Confirm the underlying cause chain points at the T0.2 KeyError.
    assert isinstance(exc_info.value.__cause__, KeyError)


# -----------------------------------------------------------------------------
# main() CLI entry point — coverage gate reachability (D1 fix)
# -----------------------------------------------------------------------------

def test_main_invokes_run_stage_b(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`main()` parses argv and delegates to `run_stage_b` with the four path args.

    Mocks `run_stage_b` so the test exercises only the argparse + delegation
    surface in `main()`. Required for the 95% coverage gate to be reachable.
    """
    from nextseek_api.eval import functional_inputs as fi

    captured: dict[str, Path] = {}

    def fake_run_stage_b(
        *,
        manifest_path: Path,
        runtime_csv_path: Path,
        artifact_csv_path: Path,
        out_csv_path: Path,
    ) -> int:
        captured["manifest_path"] = manifest_path
        captured["runtime_csv_path"] = runtime_csv_path
        captured["artifact_csv_path"] = artifact_csv_path
        captured["out_csv_path"] = out_csv_path
        return 0

    monkeypatch.setattr(fi, "run_stage_b", fake_run_stage_b)

    rc = fi.main(
        [
            "--manifest-path",
            str(tmp_path / "manifest.json"),
            "--runtime-csv",
            str(tmp_path / "rows.csv"),
            "--artifact-csv",
            str(tmp_path / "av.csv"),
            "--out-csv",
            str(tmp_path / "out.csv"),
        ]
    )
    assert rc == 0
    assert captured["manifest_path"] == tmp_path / "manifest.json"
    assert captured["runtime_csv_path"] == tmp_path / "rows.csv"
    assert captured["artifact_csv_path"] == tmp_path / "av.csv"
    assert captured["out_csv_path"] == tmp_path / "out.csv"


@pytest.mark.parametrize("expected_rc", [0, 1, 2])
def test_main_returns_run_stage_b_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expected_rc: int
) -> None:
    """`main()` returns whatever exit code `run_stage_b` returns (no remapping)."""
    from nextseek_api.eval import functional_inputs as fi

    monkeypatch.setattr(fi, "run_stage_b", lambda **_: expected_rc)
    rc = fi.main(
        [
            "--manifest-path",
            str(tmp_path / "manifest.json"),
            "--runtime-csv",
            str(tmp_path / "rows.csv"),
            "--artifact-csv",
            str(tmp_path / "av.csv"),
            "--out-csv",
            str(tmp_path / "out.csv"),
        ]
    )
    assert rc == expected_rc

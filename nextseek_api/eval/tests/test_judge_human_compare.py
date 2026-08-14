"""Focused hermetic tests for the capped judge-versus-human comparison harness."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from nextseek_api.eval.judge_human_compare import (
    ABSOLUTE_MAX_ARMS,
    ComparisonError,
    build_sample_plan,
    execute_sample_plan,
    load_authenticated_bundle,
    main,
)
from nextseek_api.eval.judge_models import (
    FunctionalEvaluation,
    FunctionalOutcome,
    PrimaryIssue,
    ReviewPriority,
)


INPUT_MEMBER = "testquestions/set3_final/hibayes/hibayes_functional_eval_inputs.csv"
HUMAN_NS_MEMBER = "testquestions/set3_final/hibayes/hibayes_functional_usefulness_human_ns.csv"
HUMAN_CC_MEMBER = "testquestions/set3_final/hibayes/hibayes_functional_usefulness_human_cc.csv"


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def _bundle(tmp_path: Path) -> tuple[Path, Path, str, str]:
    input_rows: list[dict[str, str]] = []
    humans: dict[str, list[dict[str, str]]] = {"ns": [], "cc": []}
    specs = [
        ("alpha.one", "family_a", "ns", "false"),
        ("beta.two", "family_b", "ns", "true"),
        ("gamma.three", "family_c", "cc", "false"),
        ("delta.four", "family_d", "cc", "true"),
        ("epsilon.five", "family_e", "ns", "true"),
        ("zeta.six", "family_f", "cc", "false"),
        # Authenticated but ineligible: an execution plan must never select it.
        ("missing.answer", "family_g", "cc", "false"),
    ]
    for query_id, family, arm, human_success in specs:
        arm_id = f"{query_id}::{arm}"
        input_rows.append(
            {
                "query_id": arm_id,
                "task_family": family,
                "query_text": f"Question {query_id}?",
                "final_answer": "" if query_id == "missing.answer" else f"Answer {query_id}",
                "answer_provided": "false" if query_id == "missing.answer" else "true",
                "runtime_success": "true",
                "failure_mode": "none",
                "artifact_expected": "false",
                "artifact_status": "NotExpected",
                "artifact_kind": "NONE_EXPECTED",
                "declared_artifact_count": "0",
                "expected_behavior": "AnswerDirectly",
            }
        )
        humans[arm].append(
            {
                "query_id": query_id,
                "task_family": family,
                "functional_success": human_success,
                "grade_key": arm_id,
                "arm": arm,
            }
        )

    members = {
        INPUT_MEMBER: _csv_bytes(input_rows),
        HUMAN_NS_MEMBER: _csv_bytes(humans["ns"]),
        HUMAN_CC_MEMBER: _csv_bytes(humans["cc"]),
    }
    archive = tmp_path / "testquestions.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    archive_bytes = archive.read_bytes()
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    manifest = {
        "laptop_root": "/fixture/testquestions",
        "source_archive": {
            "filename": "testquestions.zip",
            "checksum": archive_sha,
            "checksum_method": "sha256",
            "bytes": len(archive_bytes),
        },
        "files": [
            {
                "path_laptop": f"/fixture/{name}",
                "checksum": hashlib.sha256(payload).hexdigest(),
                "checksum_method": "sha256",
                "bytes": len(payload),
            }
            for name, payload in members.items()
        ],
    }
    manifest_path = tmp_path / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return archive, manifest_path, archive_sha, manifest_sha


def _evaluation(success: bool) -> FunctionalEvaluation:
    return FunctionalEvaluation(
        outcome=(
            FunctionalOutcome.FullySatisfied
            if success
            else FunctionalOutcome.NotSatisfied
        ),
        usefulness_score=4 if success else 0,
        primary_issue=PrimaryIssue.NoIssue if success else PrimaryIssue.Other,
        needs_human_review=False,
        review_priority=ReviewPriority.Low,
        rationale="fixture judgment",
    )


def test_dry_run_makes_zero_calls_and_stratifies_sample(tmp_path: Path) -> None:
    archive, manifest, archive_sha, manifest_sha = _bundle(tmp_path)
    bundle = load_authenticated_bundle(
        archive, manifest, archive_sha256=archive_sha, manifest_sha256=manifest_sha
    )
    plan = build_sample_plan(bundle, max_arms=4)
    called = 0

    def evaluator(*_args: object) -> FunctionalEvaluation:
        nonlocal called
        called += 1
        return _evaluation(True)

    report = execute_sample_plan(plan, bundle, evaluator=evaluator, execute_provider=False)
    selected = report["selected_arms"]
    assert called == 0
    assert report["actual_call_count"] == 0
    assert report["estimated_call_count"] == 12
    assert {(row["arm"], row["human_functional_success"]) for row in selected} == {
        ("ns", False),
        ("ns", True),
        ("cc", False),
        ("cc", True),
    }
    assert len({row["task_family"] for row in selected}) >= 2
    assert all(row["arm_id"] != "missing.answer::cc" for row in selected)


def test_cap_enforced_before_any_call(tmp_path: Path) -> None:
    archive, manifest, archive_sha, manifest_sha = _bundle(tmp_path)
    bundle = load_authenticated_bundle(
        archive, manifest, archive_sha256=archive_sha, manifest_sha256=manifest_sha
    )
    with pytest.raises(ComparisonError, match="absolute cap"):
        build_sample_plan(bundle, max_arms=ABSOLUTE_MAX_ARMS + 1)


def test_execute_calls_exactly_three_per_arm_and_compares_aggregate_only(tmp_path: Path) -> None:
    archive, manifest, archive_sha, manifest_sha = _bundle(tmp_path)
    bundle = load_authenticated_bundle(
        archive, manifest, archive_sha256=archive_sha, manifest_sha256=manifest_sha
    )
    plan = build_sample_plan(bundle, max_arms=4)
    calls: list[tuple[str, int]] = []

    def evaluator(arm_id: str, call_index: int, _input: object) -> FunctionalEvaluation:
        calls.append((arm_id, call_index))
        human = next(
            row["human_functional_success"]
            for row in plan["selected_arms"]
            if row["arm_id"] == arm_id
        )
        # Individual votes may disagree; comparison must use DD-44 aggregate only.
        return _evaluation(not human if call_index == 0 else human)

    report = execute_sample_plan(
        plan,
        bundle,
        evaluator=evaluator,
        execute_provider=True,
        attempt_store_root=tmp_path / "attempts",
    )
    assert len(calls) == 12
    for row in plan["selected_arms"]:
        assert [i for arm_id, i in calls if arm_id == row["arm_id"]] == [0, 1, 2]
    assert report["actual_call_count"] == 12
    assert report["agreement_count"] == 4
    assert report["disagreements"] == []
    assert all(item["agreement"] for item in report["comparisons"])


def test_tampered_archive_member_refused(tmp_path: Path) -> None:
    archive, manifest, archive_sha, manifest_sha = _bundle(tmp_path)
    with archive.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ComparisonError, match="archive SHA-256"):
        load_authenticated_bundle(
            archive, manifest, archive_sha256=archive_sha, manifest_sha256=manifest_sha
        )


def test_cli_execution_requires_immutable_sample_manifest_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, manifest, archive_sha, manifest_sha = _bundle(tmp_path)
    sample_path = tmp_path / "sample.json"
    assert main(
        [
            "--archive",
            str(archive),
            "--delivery-manifest",
            str(manifest),
            "--archive-sha256",
            archive_sha,
            "--manifest-sha256",
            manifest_sha,
            "--output",
            str(sample_path),
        ]
    ) == 0
    monkeypatch.setattr(
        "nextseek_api.eval.judge_human_compare._provider_evaluator",
        lambda *_args: _evaluation(True),
    )
    with pytest.raises(ComparisonError, match="sample manifest SHA-256"):
        main(
            [
                "--archive",
                str(archive),
                "--delivery-manifest",
                str(manifest),
                "--archive-sha256",
                archive_sha,
                "--manifest-sha256",
                manifest_sha,
                "--execute-provider",
                "--sample-manifest",
                str(sample_path),
                "--sample-manifest-sha256",
                "0" * 64,
                "--attempt-store",
                str(tmp_path / "attempts"),
                "--output",
                str(tmp_path / "result.json"),
            ]
        )


def test_execution_refuses_selected_arm_with_missing_final_answer(tmp_path: Path) -> None:
    archive, manifest, archive_sha, manifest_sha = _bundle(tmp_path)
    bundle = load_authenticated_bundle(
        archive, manifest, archive_sha256=archive_sha, manifest_sha256=manifest_sha
    )
    plan = build_sample_plan(bundle, max_arms=4)
    plan["selected_arms"][0]["arm_id"] = "missing.answer::cc"
    with pytest.raises(ComparisonError, match="changed|final answer"):
        execute_sample_plan(
            plan,
            bundle,
            evaluator=lambda *_args: _evaluation(True),
            execute_provider=True,
            attempt_store_root=tmp_path / "attempts",
        )

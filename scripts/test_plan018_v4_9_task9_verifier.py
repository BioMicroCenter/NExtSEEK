"""Focused tests for the Plan 018 V4-9 final verifier/cold-review gate."""
from __future__ import annotations

import json
from pathlib import Path

import plan018_v4_9_task9_verifier as gate


def _cold_review(*, verdict: str = "PASS", task_7: str = "PASS") -> str:
    grades = [f"| Task {index} | {task_7 if index == 7 else 'PASS'} | evidence |" for index in range(10)]
    return "\n".join(
        [
            "# Plan 018 V4-9 cold outcome review",
            "reviewer_kind: cold_subagent",
            "subagent_id: /root/plan018_v4_9_final_cold",
            "parent_transcript_id: unknown",
            "prompt_verbatim: true",
            "Any prior implementer-written artifact at this path is VOID.",
            "",
            "> " + gate.COLD_PROMPT,
            "",
            "| Task | Grade | Why |",
            "| --- | --- | --- |",
            *grades,
            "",
            f"Final verdict: {verdict}",
        ]
    ) + "\n"


def test_validator_inventory_covers_every_required_final_gate() -> None:
    names = [spec.name for spec in gate.VALIDATORS]

    assert names == [
        "task1-owned-surface",
        "task2-coverage",
        "task3-coverage",
        "task4-coverage",
        "task5-mutation",
        "task6-replay",
        "task7-deploy-recovery",
        "task8-operational",
        "global-coverage",
    ]
    assert len(names) == len(set(names))
    assert all(spec.success_marker for spec in gate.VALIDATORS)


def test_current_tree_is_honestly_red_for_missing_task8_and_task9() -> None:
    pre = gate.static_preflight_errors(gate.ROOT)
    final = gate.final_errors(gate.ROOT)

    assert any("Task 8 evidence" in error for error in pre)
    assert any("Task 9 verifier evidence" in error for error in final)
    assert any("cold outcome review" in error for error in final)
    assert not any("critical module misses" in error for error in pre)


def test_provenance_bearing_cold_pass_grades_every_task() -> None:
    assert gate.cold_review_errors(_cold_review()) == []


def test_cold_review_refuses_missing_provenance_wrong_task_grade_and_nonpass() -> None:
    text = _cold_review(verdict="PARTIAL", task_7="PARTIAL").replace(
        "subagent_id: /root/plan018_v4_9_final_cold\n", ""
    )
    errors = gate.cold_review_errors(text)

    assert any("subagent_id" in error for error in errors)
    assert any("Task 7" in error for error in errors)
    assert any("final verdict" in error for error in errors)


def test_cold_review_requires_exact_verbatim_prompt_and_void_statement() -> None:
    text = _cold_review().replace(gate.COLD_PROMPT, gate.COLD_PROMPT + " changed")
    text = text.replace("Any prior implementer-written artifact at this path is VOID.\n", "")
    errors = gate.cold_review_errors(text)

    assert any("verbatim prompt" in error for error in errors)
    assert any("VOID" in error for error in errors)


def test_prerequisite_gate_rejects_nonpass_and_missing_migration_membership(tmp_path: Path) -> None:
    evidence = json.loads((gate.ROOT / gate.PREREQUISITE).read_text())
    evidence["verifier_results"]["V4-7"]["gate"] = "FAIL"
    evidence["migration_lineage"]["v4_8_membership"] = ""
    path = tmp_path / gate.PREREQUISITE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(evidence))

    errors = gate.prerequisite_errors(tmp_path)
    assert any("V4-7" in error for error in errors)
    assert any("migration" in error for error in errors)


def test_run_evidence_rejects_missing_command_and_tampered_control(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    control = root / "scripts" / "control.py"
    control.write_text("original\n")
    payload = gate.synthetic_run_evidence(
        head="1" * 40,
        tree="2" * 40,
        controls={"scripts/control.py": gate.sha256(control)},
    )
    payload["commands"] = payload["commands"][:-1]
    evidence = root / gate.RUN_EVIDENCE
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps(payload))
    control.write_text("tampered\n")

    errors = gate.run_evidence_errors(root, check_static=False)
    assert any("validator command inventory" in error for error in errors)
    assert any("control hash drift" in error for error in errors)


def test_status_surfaces_require_task8_task9_and_cold_pass(tmp_path: Path) -> None:
    directory = tmp_path / gate.SDD_DIR
    directory.mkdir(parents=True)
    (directory / "progress.md").write_text("Task 0: complete\nTask 8: complete\n")
    for index in range(10):
        (directory / f"task-{index}-report.md").write_text(f"Task {index}\nStatus: PASS\n")

    errors = gate.status_surface_errors(tmp_path)
    assert any("progress" in error for error in errors)
    (directory / "progress.md").write_text(
        "\n".join([*(f"Task {index}: complete" for index in range(10)), "Cold outcome review: PASS"]) + "\n"
    )
    assert gate.status_surface_errors(tmp_path) == []

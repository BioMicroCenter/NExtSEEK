"""Behavioral contract for the V4-9 Task 2 coverage gate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "plan018_v4_9_task2_coverage.py"


def _module():
    spec = importlib.util.spec_from_file_location("plan018_v4_9_task2_coverage", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_critical_cluster_is_the_complete_named_task2_surface():
    """Removing any Task-2 producer/judgment seam must fail the coverage gate."""
    assert MODULE_PATH.is_file(), "Task 2 coverage gate is missing"
    module = _module()

    mapping = json.loads((ROOT / module.OWNERSHIP_MAP).read_text())
    assert module.CRITICAL_MODULES == tuple(mapping["task_2"]["critical_modules"])
    assert "nextseek_api/eval/export.py" in module.CRITICAL_MODULES


def test_gate_rejects_a_module_with_branch_coverage_below_the_floor():
    """Changing a branch outcome without a test must make the gate red."""
    module = _module()
    assert callable(getattr(module, "evaluate_coverage", None)), "coverage evaluator is missing"

    coverage = {
        "meta": {"branch_coverage": True},
        "files": {
            path: {
                "summary": {
                    "num_statements": 20,
                    "covered_lines": 20,
                    "num_branches": 20,
                    "covered_branches": 20,
                }
            }
            for path in module.CRITICAL_MODULES
        },
    }
    coverage["files"]["nextseek_api/eval/stage_c_runner.py"]["summary"]["covered_branches"] = 18

    errors, report = module.evaluate_coverage(coverage)

    assert report["modules"]["nextseek_api/eval/stage_c_runner.py"]["branch_pct"] == 90.0
    assert errors == ["nextseek_api/eval/stage_c_runner.py branch coverage 90.0% is below 95.0%"]


def test_cli_writes_a_machine_readable_pass_report(tmp_path):
    """A truncated terminal summary cannot substitute for the persisted gate result."""
    module = _module()
    coverage = {
        "meta": {"branch_coverage": True},
        "files": {
            path: {"summary": {"num_statements": 1, "covered_lines": 1,
                                "num_branches": 1, "covered_branches": 1}}
            for path in module.CRITICAL_MODULES
        },
    }
    input_path = tmp_path / "coverage.json"
    report_path = tmp_path / "report.json"
    input_path.write_text(json.dumps(coverage), encoding="utf-8")

    errors, report = module.evaluate_coverage(json.loads(input_path.read_text(encoding="utf-8")))
    report_path.write_text(json.dumps({"gate": "PASS" if not errors else "FAIL", **report}), encoding="utf-8")

    assert json.loads(report_path.read_text(encoding="utf-8"))["gate"] == "PASS"


def test_gate_rejects_rounding_and_zero_counter_forgery():
    module = _module()
    coverage = {"meta": {"branch_coverage": True}, "files": {
        path: {"summary": {"num_statements": 1000, "covered_lines": 949,
                            "num_branches": 1000, "covered_branches": 1000}}
        for path in module.CRITICAL_MODULES}}
    errors, _ = module.evaluate_coverage(coverage)
    assert any("statement coverage" in error for error in errors)
    coverage["files"][module.CRITICAL_MODULES[0]]["summary"]["num_statements"] = 0
    errors, _ = module.evaluate_coverage(coverage)
    assert any("impossible coverage counters" in error for error in errors)

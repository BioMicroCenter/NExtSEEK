"""Adversarial tests for the Plan 018 V4-9 global critical coverage gate."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "plan018_v4_9_global_coverage.py"
REPORT = ROOT / "evidence" / "plan018-v4-9-global-coverage.json"


def _module():
    spec = importlib.util.spec_from_file_location("plan018_v4_9_global_coverage", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _covered(statements: int = 20, branches: int = 20) -> dict[str, int]:
    return {
        "statements": statements,
        "covered_statements": statements,
        "branches": branches,
        "covered_branches": branches,
    }


def test_critical_union_is_exact_disjoint_and_source_derived():
    module = _module()
    groups = module.critical_groups(ROOT)
    modules = tuple(path for paths in groups.values() for path in paths)

    assert len(modules) == 47
    assert len(modules) == len(set(modules))
    assert "nextseek_api/eval/stage_c_runner.py" in groups["task2"]
    assert "nextseek_api/eval/fit/v14/quality_model.py" in groups["task3"]
    assert "nextseek_api/cc_assistant/router.py" in groups["task4"]
    assert groups["task7"] == (
        "nextseek_api/eval/deploy_record.py",
        "nextseek_api/eval/mixed_version_recovery.py",
    )
    assert module.inventory_errors(ROOT, groups) == []


def test_floor_uses_exact_counters_not_rounded_display_values():
    module = _module()
    files = {"critical.py": _covered(statements=1000)}
    files["critical.py"]["covered_statements"] = 949

    errors, _ = module.evaluate(files, ("critical.py",))

    assert errors == ["critical.py statement coverage 94.9% is below 95.0%"]


def test_missing_duplicate_and_zero_denominator_fail_closed():
    module = _module()

    errors, _ = module.evaluate({"a.py": _covered()}, ("a.py", "b.py"))
    assert errors == ["missing coverage for b.py"]

    errors, _ = module.evaluate({"a.py": _covered()}, ("a.py", "a.py"))
    assert errors == ["duplicate critical module: a.py"]

    errors, _ = module.evaluate(
        {"a.py": _covered(statements=0, branches=0)}, ("a.py",)
    )
    assert errors == ["critical module has no executable statements: a.py"]


def test_checked_in_global_report_reproduces_and_validates():
    module = _module()
    checked_in = json.loads(REPORT.read_text())

    assert module.build_report(ROOT) == checked_in
    assert module.validation_errors(ROOT) == []
    assert checked_in["gate"] == "PASS"
    assert checked_in["aggregate"]["statement_pct"] >= 95.0
    assert checked_in["aggregate"]["branch_pct"] >= 95.0

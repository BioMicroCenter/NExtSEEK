"""Adversarial contract tests for the V4-9 Task-3 coverage gate."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "plan018_v4_9_task3_coverage.py"


def _module():
    spec = importlib.util.spec_from_file_location("task3_coverage", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _coverage(module, *, statements=20, covered_statements=20, branches=20, covered_branches=20):
    return {
        "meta": {"branch_coverage": True},
        "files": {
            path: {
                "summary": {
                    "num_statements": statements,
                    "covered_lines": covered_statements,
                    "num_branches": branches,
                    "covered_branches": covered_branches,
                }
            }
            for path in module.critical_modules(ROOT)
        },
    }


def test_critical_modules_derive_from_task3_ownership():
    module = _module()
    ownership = json.loads((ROOT / module.OWNERSHIP).read_text(encoding="utf-8"))
    assert module.critical_modules(ROOT) == tuple(ownership["critical_modules"])
    assert "nextseek_api/eval/fit/v14/quality_model.py" in ownership["critical_modules"]
    assert "nextseek_api/eval/generation_store.py" in ownership["critical_modules"]
    assert all("vendor/" not in path for path in ownership["critical_modules"])


def test_gate_rejects_one_module_below_branch_floor_without_rounding_it_up():
    module = _module()
    coverage = _coverage(module, branches=1000, covered_branches=1000)
    target = "nextseek_api/eval/fit/v14/quality_model.py"
    coverage["files"][target]["summary"]["covered_branches"] = 949
    errors, report = module.evaluate_coverage(ROOT, coverage)
    assert report["modules"][target]["branch_pct"] == 94.9
    assert errors == [f"{target} branch coverage 94.9% is below 95.0%"]


def test_gate_rejects_missing_module_and_nonbranch_collection():
    module = _module()
    coverage = _coverage(module)
    coverage["files"].pop(next(iter(coverage["files"])))
    errors, _ = module.evaluate_coverage(ROOT, coverage)
    assert any("missing coverage" in error for error in errors)
    assert any("not every declared critical module" in error for error in errors)

    coverage["meta"]["branch_coverage"] = False
    errors, report = module.evaluate_coverage(ROOT, coverage)
    assert errors == ["coverage was not collected with branch coverage"]
    assert report == {}


def test_gate_accepts_exact_floor_and_aggregates_integer_counters():
    module = _module()
    coverage = _coverage(
        module,
        statements=20,
        covered_statements=19,
        branches=20,
        covered_branches=19,
    )
    errors, report = module.evaluate_coverage(ROOT, coverage)
    assert errors == []
    assert report["aggregate"]["statement_pct"] == 95.0
    assert report["aggregate"]["branch_pct"] == 95.0

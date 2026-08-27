from __future__ import annotations

import json
from pathlib import Path

from scripts import plan018_v4_9_task8_coverage as coverage


def _write_fixture(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(coverage, "MODULES", ("startup/example.py",))
    monkeypatch.setattr(coverage, "TESTS", ("startup/tests/test_example.py",))
    (tmp_path / "startup").mkdir()
    (tmp_path / "startup/example.py").write_text("answer = 42\n")
    (tmp_path / coverage.RAW).parent.mkdir(parents=True)
    (tmp_path / coverage.RAW).write_text(
        json.dumps(
            {
                "files": {
                    "startup/example.py": {
                        "summary": {
                            "num_statements": 20,
                            "covered_lines": 19,
                            "num_branches": 20,
                            "covered_branches": 19,
                        }
                    }
                }
            }
        )
    )
    (tmp_path / coverage.JUNIT).write_text(
        '<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0" time="0.1"/></testsuites>'
    )


def test_build_and_validate_source_bound_task8_coverage(tmp_path: Path, monkeypatch) -> None:
    _write_fixture(tmp_path, monkeypatch)
    evidence = coverage.build_evidence(tmp_path)
    assert evidence["gate"] == "PASS"
    assert evidence["modules"]["startup/example.py"]["branch_pct"] == 95.0
    (tmp_path / coverage.EVIDENCE).write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    )
    assert coverage.validation_errors(tmp_path) == []

    (tmp_path / "startup/example.py").write_text("answer = 43\n")
    assert "stale or not reproducible" in " ".join(
        coverage.validation_errors(tmp_path)
    )


def test_task8_coverage_rejects_subfloor_branch_result(tmp_path: Path, monkeypatch) -> None:
    _write_fixture(tmp_path, monkeypatch)
    raw = json.loads((tmp_path / coverage.RAW).read_text())
    raw["files"]["startup/example.py"]["summary"]["covered_branches"] = 18
    (tmp_path / coverage.RAW).write_text(json.dumps(raw))

    evidence = coverage.build_evidence(tmp_path)

    assert evidence["gate"] == "FAIL"
    assert "branch coverage" in " ".join(evidence["errors"])

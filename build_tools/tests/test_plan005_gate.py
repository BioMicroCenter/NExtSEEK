"""Focused tests for build_tools.plan005_gate."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from build_tools.plan005_gate import (
    GateError,
    assert_clean_coverage_config,
    combined_branch_enabled_percent,
    construct_null_coverage_config,
    count_oracle_sites,
    enumerate_production_py,
    parse_junit_node_results,
    require_base_nodes_present,
    run_gate,
)


def _coverage_json(files: dict) -> dict:
    return {"meta": {"branch_coverage": True}, "files": files}


def _file_summary(*, covered_lines, num_statements, covered_branches, num_branches):
    return {
        "summary": {
            "covered_lines": covered_lines,
            "num_statements": num_statements,
            "covered_branches": covered_branches,
            "num_branches": num_branches,
        }
    }


def test_enumerate_production_py_excludes_tests(tmp_path: Path):
    root = tmp_path / "cc_assistant"
    (root / "op_registry").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "op_registry" / "models.py").write_text("x=1\n", encoding="utf-8")
    (root / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    files = enumerate_production_py(root)
    assert [p.name for p in files] == ["models.py"]


def test_combined_percent_ignores_test_file_coverage(tmp_path: Path):
    root = tmp_path / "cc_assistant"
    root.mkdir()
    (root / "mod.py").write_text("x=1\n", encoding="utf-8")
    payload = _coverage_json(
        {
            str(root / "mod.py"): _file_summary(
                covered_lines=9, num_statements=10, covered_branches=9, num_branches=10
            ),
            str(root / "tests" / "test_x.py"): _file_summary(
                covered_lines=100, num_statements=100, covered_branches=100, num_branches=100
            ),
        }
    )
    percent = combined_branch_enabled_percent(payload, source_root=root)
    assert percent == 90.0


def test_combined_percent_missing_production_file_is_red(tmp_path: Path):
    root = tmp_path / "cc_assistant"
    root.mkdir()
    (root / "mod.py").write_text("x=1\n", encoding="utf-8")
    payload = _coverage_json({})
    with pytest.raises(GateError, match="missing from coverage JSON"):
        combined_branch_enabled_percent(payload, source_root=root)


def test_combined_percent_line_and_branch_cross_threshold(tmp_path: Path):
    root = tmp_path / "cc_assistant"
    root.mkdir()
    (root / "a.py").write_text("x=1\n", encoding="utf-8")
    payload = _coverage_json(
        {
            str(root / "a.py"): _file_summary(
                covered_lines=95, num_statements=100, covered_branches=0, num_branches=0
            )
        }
    )
    assert combined_branch_enabled_percent(payload, source_root=root) == 95.0
    payload["files"][str(root / "a.py")] = _file_summary(
        covered_lines=90, num_statements=100, covered_branches=4, num_branches=10
    )
    # (90+4)/(100+10) = 94/110 < 95
    assert combined_branch_enabled_percent(payload, source_root=root) < 95


def test_clean_config_rejects_omit_exclude_source_and_branch_off():
    assert_clean_coverage_config(construct_null_coverage_config())
    with pytest.raises(GateError, match="omit"):
        assert_clean_coverage_config({**construct_null_coverage_config(), "omit": ["x.py"]})
    with pytest.raises(GateError, match="exclude_also"):
        assert_clean_coverage_config({**construct_null_coverage_config(), "exclude_also": ["pragma"]})
    with pytest.raises(GateError, match="source narrowing"):
        assert_clean_coverage_config(
            {**construct_null_coverage_config(), "source": ["nextseek_api.cc_assistant.op_registry"]}
        )
    with pytest.raises(GateError, match="branch"):
        assert_clean_coverage_config({**construct_null_coverage_config(), "branch": False})
    with pytest.raises(GateError, match="config_file"):
        assert_clean_coverage_config({**construct_null_coverage_config(), "config_file": ".coveragerc"})


def test_parse_junit_and_duplicate_conflict(tmp_path: Path):
    xml = """<?xml version="1.0"?>
<testsuite>
  <testcase classname="pkg.tests.test_a" name="test_one" time="0.01"/>
  <testcase classname="pkg.tests.test_a" name="test_two" time="0.01">
    <skipped message="xfail: later"/>
  </testcase>
</testsuite>
"""
    path = tmp_path / "cc-assistant.junit.xml"
    path.write_text(xml, encoding="utf-8")
    results = parse_junit_node_results(path)
    assert results["pkg.tests.test_a::test_one"] == "passed"
    assert results["pkg.tests.test_a::test_two"] == "xfail"
    dup = tmp_path / "dup.xml"
    dup.write_text(
        """<?xml version="1.0"?><testsuite>
        <testcase classname="c" name="t"/>
        <testcase classname="c" name="t"><failure/></testcase>
        </testsuite>""",
        encoding="utf-8",
    )
    with pytest.raises(GateError, match="duplicate conflicting"):
        parse_junit_node_results(dup)


def test_count_oracle_sites_detects_reduction():
    before = "def test_a():\n    assert 1\n    with pytest.raises(ValueError):\n        raise ValueError()\n    pytest.fail('x')\n"
    after = "def test_a():\n    assert 1\n"
    assert count_oracle_sites(after) < count_oracle_sites(before)


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    src = repo / "nextseek_api" / "cc_assistant"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("x = 1\n", encoding="utf-8")
    tests = src / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text("def test_mod():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    return repo


def _junit(path: Path, classname: str, name: str, skipped: bool = False) -> None:
    skip = "<skipped message='skip'/>" if skipped else ""
    path.write_text(
        f"""<?xml version="1.0"?><testsuite>
        <testcase classname="{classname}" name="{name}">{skip}</testcase>
        </testsuite>""",
        encoding="utf-8",
    )


def test_run_gate_missing_base_node_and_skip(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    source = repo / "nextseek_api" / "cc_assistant"
    cov = tmp_path / "cov.json"
    cov.write_text(
        json.dumps(
            _coverage_json(
                {
                    str(source / "mod.py"): _file_summary(
                        covered_lines=10,
                        num_statements=10,
                        covered_branches=10,
                        num_branches=10,
                    )
                }
            )
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "base.xml"
    _junit(baseline, "nextseek_api.cc_assistant.tests.test_mod", "test_mod")
    final = tmp_path / "cc-assistant.junit.xml"
    _junit(final, "nextseek_api.cc_assistant.tests.test_other", "test_other")
    with pytest.raises(GateError, match="deleted or renamed"):
        run_gate(
            coverage_json_path=cov,
            junit_paths=[final],
            baseline_junit=baseline,
            source_root=source,
            repo_root=repo,
            base=base,
            min_total=95,
        )
    _junit(final, "nextseek_api.cc_assistant.tests.test_mod", "test_mod", skipped=True)
    with pytest.raises(GateError, match="skipped"):
        run_gate(
            coverage_json_path=cov,
            junit_paths=[final],
            baseline_junit=baseline,
            source_root=source,
            repo_root=repo,
            base=base,
            min_total=95,
        )


def test_run_gate_unclassified_test_and_assert_reduction(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    stray = repo / "nextseek_api" / "eval" / "tests"
    stray.mkdir(parents=True)
    (stray / "test_stray.py").write_text("def test_stray():\n    assert True\n", encoding="utf-8")
    source = repo / "nextseek_api" / "cc_assistant"
    cov = tmp_path / "cov.json"
    cov.write_text(
        json.dumps(
            _coverage_json(
                {
                    str(source / "mod.py"): _file_summary(
                        covered_lines=10,
                        num_statements=10,
                        covered_branches=10,
                        num_branches=10,
                    )
                }
            )
        ),
        encoding="utf-8",
    )
    # stray is not under scanned prefixes, so won't be in added_or_changed from those prefixes.
    # Add a cc_assistant test with reduced asserts instead.
    test_mod = source / "tests" / "test_mod.py"
    test_mod.write_text("def test_mod():\n    x = 1\n", encoding="utf-8")
    baseline = tmp_path / "base.xml"
    _junit(baseline, "nextseek_api.cc_assistant.tests.test_mod", "test_mod")
    final = tmp_path / "cc-assistant.junit.xml"
    _junit(final, "nextseek_api.cc_assistant.tests.test_mod", "test_mod")
    with pytest.raises(GateError, match="reduced assert"):
        run_gate(
            coverage_json_path=cov,
            junit_paths=[final],
            baseline_junit=baseline,
            source_root=source,
            repo_root=repo,
            base=base,
            min_total=95,
        )


def test_run_gate_rejects_threshold_reduction(tmp_path: Path):
    repo = _git_repo(tmp_path)
    source = repo / "nextseek_api" / "cc_assistant"
    cov = tmp_path / "cov.json"
    cov.write_text("{}", encoding="utf-8")
    with pytest.raises(GateError, match="threshold reduction"):
        run_gate(
            coverage_json_path=cov,
            junit_paths=[],
            baseline_junit=tmp_path / "missing.xml",
            source_root=source,
            repo_root=repo,
            base="HEAD",
            min_total=90,
        )


def test_run_gate_rejects_added_pragma(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    source = repo / "nextseek_api" / "cc_assistant"
    (source / "mod.py").write_text("x = 1  # pragma: no cover\n", encoding="utf-8")
    cov = tmp_path / "cov.json"
    cov.write_text(
        json.dumps(
            _coverage_json(
                {
                    str(source / "mod.py"): _file_summary(
                        covered_lines=10,
                        num_statements=10,
                        covered_branches=10,
                        num_branches=10,
                    )
                }
            )
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "base.xml"
    _junit(baseline, "nextseek_api.cc_assistant.tests.test_mod", "test_mod")
    final = tmp_path / "cc-assistant.junit.xml"
    _junit(final, "nextseek_api.cc_assistant.tests.test_mod", "test_mod")
    with pytest.raises(GateError, match="pragma"):
        run_gate(
            coverage_json_path=cov,
            junit_paths=[final],
            baseline_junit=baseline,
            source_root=source,
            repo_root=repo,
            base=base,
            min_total=95,
        )


def _cov_and_source(tmp_path: Path):
    repo = _git_repo(tmp_path)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    source = repo / "nextseek_api" / "cc_assistant"
    cov = tmp_path / "cov.json"
    cov.write_text(
        json.dumps(
            _coverage_json(
                {
                    str(source / "mod.py"): _file_summary(
                        covered_lines=10,
                        num_statements=10,
                        covered_branches=10,
                        num_branches=10,
                    )
                }
            )
        ),
        encoding="utf-8",
    )
    return repo, base, source, cov


def test_preexisting_skip_is_allowed(tmp_path: Path):
    repo, base, source, cov = _cov_and_source(tmp_path)
    baseline = tmp_path / "base.xml"
    _junit(baseline, "nextseek_api.cc_assistant.tests.test_mod", "test_mod", skipped=True)
    final = tmp_path / "cc-assistant.junit.xml"
    _junit(final, "nextseek_api.cc_assistant.tests.test_mod", "test_mod", skipped=True)
    outcome = run_gate(
        coverage_json_path=cov,
        junit_paths=[final],
        baseline_junit=baseline,
        source_root=source,
        repo_root=repo,
        base=base,
        min_total=95,
    )
    assert outcome["min_total"] == 95


@pytest.mark.parametrize("final_status", ["skipped", "xfail"])
def test_known_red_base_node_must_be_green(final_status: str):
    with pytest.raises(GateError, match="known-red base node must pass"):
        require_base_nodes_present(
            baseline_results={"pkg.tests.test_base::test_known_red": "failed"},
            final_results={"pkg.tests.test_base::test_known_red": final_status},
        )


def test_new_skip_is_red(tmp_path: Path):
    repo, base, source, cov = _cov_and_source(tmp_path)
    (source / "tests" / "test_new.py").write_text(
        "def test_new():\n    assert True\n", encoding="utf-8"
    )
    baseline = tmp_path / "base.xml"
    _junit(baseline, "nextseek_api.cc_assistant.tests.test_mod", "test_mod")
    final = tmp_path / "cc-assistant.junit.xml"
    final.write_text(
        """<?xml version="1.0"?><testsuite>
        <testcase classname="nextseek_api.cc_assistant.tests.test_mod" name="test_mod"/>
        <testcase classname="nextseek_api.cc_assistant.tests.test_new" name="test_new">
          <skipped message="skip"/>
        </testcase>
        </testsuite>""",
        encoding="utf-8",
    )
    with pytest.raises(GateError, match="new skip/xfail"):
        run_gate(
            coverage_json_path=cov,
            junit_paths=[final],
            baseline_junit=baseline,
            source_root=source,
            repo_root=repo,
            base=base,
            min_total=95,
        )


def test_missing_new_node_is_red(tmp_path: Path):
    repo, base, source, cov = _cov_and_source(tmp_path)
    (source / "tests" / "test_new.py").write_text(
        "def test_new():\n    assert True\n", encoding="utf-8"
    )
    baseline = tmp_path / "base.xml"
    _junit(baseline, "nextseek_api.cc_assistant.tests.test_mod", "test_mod")
    final = tmp_path / "cc-assistant.junit.xml"
    _junit(final, "nextseek_api.cc_assistant.tests.test_mod", "test_mod")
    with pytest.raises(GateError, match="new collected node missing"):
        run_gate(
            coverage_json_path=cov,
            junit_paths=[final],
            baseline_junit=baseline,
            source_root=source,
            repo_root=repo,
            base=base,
            min_total=95,
        )

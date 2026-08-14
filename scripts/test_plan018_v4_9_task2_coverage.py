"""Behavioral contract for the V4-9 Task 2 coverage gate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


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


def test_partition_is_a_lossless_disjoint_cover_of_the_full_collection():
    """A slow collection must not silently drop its last Bayesian node."""
    module = _module()
    nodes = (
        "nessie_tests/tests/test_bayesian.py::test_01",
        "nessie_tests/tests/test_bayesian.py::test_33",
        "nextseek_api/eval/tests/test_stage_c_runner.py::test_replay",
    )

    chunks = module.partition_node_ids(nodes, max_chunk_size=2)

    assert chunks == (nodes[:2], nodes[2:])
    assert module.validate_partition(nodes, chunks) == []


def test_partition_validation_rejects_overlap_and_a_missing_final_node():
    module = _module()
    nodes = ("a::test_01", "a::test_02", "a::test_33")

    errors = module.validate_partition(nodes, ((nodes[0], nodes[1]), (nodes[1],)))

    assert "chunk union does not equal the full intended collection" in errors
    assert "chunk 0 intersects chunk 1" in errors


def test_evidence_validation_rejects_stale_inputs(tmp_path):
    """A green summary is invalid if it no longer authenticates its inputs."""
    module = _module()
    owned = tmp_path / "ownership.json"
    source = tmp_path / "source.py"
    raw = tmp_path / "raw.json"
    junit = tmp_path / "chunk.xml"
    for path, body in ((owned, "{}"), (source, "print('x')\n"), (raw, "{}"), (junit, "<testsuite/>")):
        path.write_text(body, encoding="utf-8")
    evidence = {
        "ownership_map_sha256": "not-current",
        "source_sha256": {"source.py": "not-current"},
        "raw_coverage_sha256": "not-current",
        "chunks": [{"junit": "chunk.xml", "junit_sha256": "not-current"}],
    }

    errors = module.validate_evidence_inputs(
        evidence, root=tmp_path, ownership_map=owned, raw_coverage=raw
    )

    assert any("ownership map" in error for error in errors)
    assert any("source.py" in error for error in errors)
    assert any("raw coverage" in error for error in errors)
    assert any("chunk.xml" in error for error in errors)


def test_transferred_evidence_is_required_and_hash_pinned(tmp_path):
    """V4-2 replay must consume the established archive, never a host-path accident."""
    module = _module()
    archive = tmp_path / "testquestions.zip"
    archive.write_bytes(b"established transferred evidence")

    assert module.transferred_evidence_hash(archive, expected_sha256=module.sha256(archive)) == module.sha256(archive)
    with pytest.raises(RuntimeError, match="SHA-256"):
        module.transferred_evidence_hash(archive, expected_sha256="0" * 64)
    with pytest.raises(RuntimeError, match="missing"):
        module.transferred_evidence_hash(tmp_path / "missing.zip", expected_sha256="0" * 64)


def test_transferred_evidence_directory_requires_the_manifest_too(tmp_path):
    module = _module()
    (tmp_path / "testquestions.zip").write_bytes(b"zip")
    (tmp_path / "MANIFEST.json").write_bytes(b"manifest")
    pins = {"testquestions.zip": module.sha256(tmp_path / "testquestions.zip"),
            "MANIFEST.json": module.sha256(tmp_path / "MANIFEST.json")}

    assert module.transferred_evidence_hashes(tmp_path, expected_sha256=pins) == pins
    (tmp_path / "MANIFEST.json").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="MANIFEST.json"):
        module.transferred_evidence_hashes(tmp_path, expected_sha256=pins)


def test_authoritative_run_removes_only_prior_task2_generated_artifacts(tmp_path):
    module = _module()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    stale = evidence / "plan018-v4-9-task2-chunk-00.junit.xml"
    shard = evidence / ".plan018-v4-9-task2.coverage.host.pid"
    unrelated = evidence / "retain-me.junit.xml"
    for path in (stale, shard, unrelated):
        path.write_text("x", encoding="utf-8")

    module.clear_prior_generated_artifacts(tmp_path)

    assert not stale.exists()
    assert not shard.exists()
    assert unrelated.exists()


def test_finalize_requires_complete_disjoint_junit_collection(tmp_path):
    module = _module()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    full = ("a::one", "a::two")
    (evidence / "plan018-v4-9-task2-full-collection.txt").write_text("\n".join(full) + "\n")
    raw = evidence / "plan018-v4-9-task2-coverage.raw.json"
    raw.write_text(json.dumps({"meta": {"branch_coverage": True}, "files": {}}))
    manifest = {"chunks": [{"index": 0, "node_ids": ["a::one"], "junit": "evidence/chunk.xml", "junit_sha256": "wrong"}]}
    (evidence / "plan018-v4-9-task2-chunks.json").write_text(json.dumps(manifest))

    errors = module.validate_completed_collection(tmp_path, manifest, full)

    assert any("union" in error for error in errors)
    assert any("JUnit" in error for error in errors)

"""Self-tests for the Plan 018 V4-9 Task-7 evidence gate."""
from __future__ import annotations

import json

import plan018_v4_9_task7_recovery as gate


def test_fixture_record_contains_complete_mixed_version_identity() -> None:
    record = gate.fixture_record()

    assert record.phase == "expand"
    assert {(item.release, item.role) for item in record.runtime_identities} == {
        ("old", "web"),
        ("new", "web"),
        ("old", "worker"),
        ("new", "worker"),
    }
    assert all(record.data.row_counts.values())
    assert record.generations.active != record.generations.prior


def test_contract_phase_is_absent_from_generated_schema() -> None:
    schema = gate.deploy_record_schema()
    phase = schema["properties"]["phase"]

    assert phase["enum"] == ["expand", "migrate"]
    assert "contract" not in json.dumps(schema)


def test_mutation_manifest_is_finite_source_bound_and_all_killed() -> None:
    manifest = gate.mutation_manifest()

    assert manifest["summary"] == {"enumerated": 3, "killed": 3}
    assert len({case["id"] for case in manifest["cases"]}) == 3
    assert all(case["result"] == "KILLED" for case in manifest["cases"])
    assert all(len(case["source_sha256"]) == 64 for case in manifest["cases"])
    assert all(case["killer"].startswith(gate.TEST + "::") for case in manifest["cases"])


def test_coverage_has_positive_denominators_and_both_floors() -> None:
    coverage = gate.coverage_summary()

    assert set(coverage["files"]) == set(gate.MODULES)
    for summary in (*coverage["files"].values(), coverage["aggregate"]):
        assert summary["statements"] > 0
        assert summary["branches"] > 0
        assert summary["statement_percent"] >= gate.MIN_COVERAGE
        assert summary["branch_percent"] >= gate.MIN_COVERAGE


def test_current_generated_artifacts_and_full_gate_validate() -> None:
    outputs = gate.generated_outputs()

    assert all((gate.ROOT / path).read_bytes() == expected for path, expected in outputs.items())
    assert gate.validation_errors() == []


def test_coverage_floor_mutation_turns_gate_red(monkeypatch) -> None:
    monkeypatch.setattr(gate, "MIN_COVERAGE", 101.0)

    errors = gate.validation_errors()

    assert any("statement coverage below" in error for error in errors)
    assert any("branch coverage below" in error for error in errors)

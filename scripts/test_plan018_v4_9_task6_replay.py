from __future__ import annotations

from pathlib import Path

import pytest

import plan018_v4_9_task6_replay as gate


def test_delivery_validation_refuses_missing_before_parsing(tmp_path: Path):
    with pytest.raises(gate.GateError, match="required transferred evidence missing"):
        gate.verify_delivery(tmp_path)


def test_delivery_validation_refuses_wrong_hash_and_size(tmp_path: Path):
    for name in gate.DELIVERY_FILES:
        (tmp_path / name).write_bytes(b"not-authenticated")
    with pytest.raises(gate.GateError, match="identity mismatch"):
        gate.verify_delivery(tmp_path)


def test_control_inventory_is_positive_and_current():
    assert gate.CONTROL_FILES
    assert all((gate.ROOT / path).is_file() for path in gate.CONTROL_FILES)
    assert gate.MAX_WALL_S == 300.0


def test_missing_results_are_red_after_authentication(monkeypatch, tmp_path: Path):
    authenticated = {
        name: {"size": value["size"], "sha256": value["sha256"]}
        for name, value in gate.DELIVERY_FILES.items()
    }
    monkeypatch.setattr(gate, "verify_delivery", lambda delivery: authenticated)
    errors = gate.validation_errors(tmp_path, tmp_path / "delivery")
    assert any("missing Task 6 artifacts" in error for error in errors)


def test_junit_parser_refuses_missing_source_identity(tmp_path: Path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuites><testsuite><testcase classname="" name="test_name" />'
        "</testsuite></testsuites>"
    )
    with pytest.raises(gate.GateError, match="omitted its source identity"):
        gate.junit_counts(junit)

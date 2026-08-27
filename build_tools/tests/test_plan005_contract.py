"""Done-8 contract tests for the Plan 005 Task 12 CI protocol."""
from __future__ import annotations

from copy import deepcopy

import pytest

from build_tools.plan005_closeout import (
    COVERAGE_MIN_TOTAL,
    COMMAND_TIMEOUT_SECONDS,
    EVIDENCE_PARENT,
    IMMUTABLE_NEXTSEEK_IMAGE,
    ProtocolError,
    protocol_rows,
    validate_protocol_rows,
)
from build_tools.plan005_record import RecordError, refuse_mutable_image


def _rows():
    return protocol_rows()


def test_contract_rejects_threshold_reduction():
    rows = _rows()
    argv = rows[15]["argv_template"]
    idx = argv.index("--min-total")
    argv[idx + 1] = "90"
    with pytest.raises(ProtocolError, match="95 percent"):
        validate_protocol_rows(rows)
    report = [row for row in _rows() if row["id"] == "14-coverage-report"][0]
    report["argv_template"] = [
        tok.replace("--fail-under=95", "--fail-under=80") for tok in report["argv_template"]
    ]
    with pytest.raises(ProtocolError, match="fail-under"):
        validate_protocol_rows(_rows()[:13] + [report] + _rows()[14:])


def test_contract_rejects_branch_removal():
    rows = _rows()
    cov = next(row for row in rows if row["id"] == "12-coverage-run")
    cov["argv_template"] = [tok for tok in cov["argv_template"] if tok != "--branch"]
    with pytest.raises(ProtocolError, match="branch"):
        validate_protocol_rows(rows)


def test_contract_rejects_source_narrowing():
    rows = _rows()
    cov = next(row for row in rows if row["id"] == "12-coverage-run")
    cov["argv_template"] = [
        tok.replace(
            "--source=nextseek_api.cc_assistant",
            "--source=nextseek_api.cc_assistant.op_registry",
        )
        for tok in cov["argv_template"]
    ]
    with pytest.raises(ProtocolError, match="narrow"):
        validate_protocol_rows(rows)


def test_contract_rejects_extra_ignores():
    rows = _rows()
    cov = next(row for row in rows if row["id"] == "12-coverage-run")
    cov["argv_template"].extend(["--ignore", "nextseek_api/cc_assistant/tests/test_extra.py"])
    with pytest.raises(ProtocolError, match="ignores"):
        validate_protocol_rows(rows)


def test_contract_rejects_network_enablement():
    rows = _rows()
    export = next(row for row in rows if row["id"] == "02-export-check")
    argv = export["argv_template"]
    argv[argv.index("none")] = "bridge"
    with pytest.raises(ProtocolError, match="network"):
        validate_protocol_rows(rows)


def test_contract_rejects_pytest_cov_and_xdist():
    rows = _rows()
    cov = next(row for row in rows if row["id"] == "12-coverage-run")
    cov["argv_template"].append("--cov")
    with pytest.raises(ProtocolError, match="pytest-cov"):
        validate_protocol_rows(rows)
    rows = _rows()
    cov = next(row for row in rows if row["id"] == "12-coverage-run")
    cov["argv_template"].extend(["-n", "auto"])
    with pytest.raises(ProtocolError, match="xdist"):
        validate_protocol_rows(rows)


def test_contract_rejects_timeout_inflation():
    rows = _rows()
    cov = next(row for row in rows if row["id"] == "12-coverage-run")
    cov["argv_template"].extend(["--timeout", str(COMMAND_TIMEOUT_SECONDS + 60)])
    # inflation is detected on --timeout= form; also pin the constant
    assert COMMAND_TIMEOUT_SECONDS == 600
    cov["argv_template"][-2] = f"--timeout={COMMAND_TIMEOUT_SECONDS + 60}"
    with pytest.raises(ProtocolError, match="timeout inflation"):
        validate_protocol_rows(rows)


def test_contract_rejects_mutable_image_tags():
    rows = _rows()
    export = next(row for row in rows if row["id"] == "02-export-check")
    export["image"] = "nextseek-nextseek:latest"
    with pytest.raises(ProtocolError, match="immutable sha256"):
        validate_protocol_rows(rows)
    with pytest.raises(RecordError, match="mutable"):
        refuse_mutable_image(["docker", "run", "--network", "none", "nextseek-nextseek:latest", "true"])
    assert IMMUTABLE_NEXTSEEK_IMAGE.startswith("sha256:")


def test_contract_rejects_repository_local_evidence_output():
    rows = _rows()
    cov = next(row for row in rows if row["id"] == "12-coverage-run")
    cov["declared_output_namespace"] = "/home/taishajo/work/NExtSEEK-plan005/evidence"
    with pytest.raises(ProtocolError, match="repository-local"):
        validate_protocol_rows(rows)
    assert EVIDENCE_PARENT.startswith("/home/taishajo/work/state/plan005/execution")
    assert COVERAGE_MIN_TOTAL == 95

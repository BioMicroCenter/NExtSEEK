"""Hermetic unit tests for the PURE-LOGIC pieces of
``cc_matrix_gate_harness.py`` (Task 15's RUN_REALSTACK-gated capability-gate
harness): argv construction, matrix-row shaping, and the executor env/
run-kwargs builders. No Docker, no network, no DB, no spend -- these are
plain-function unit tests, independent of whether the harness is ever
executed live.
"""
from __future__ import annotations

import json

import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.tests import cc_matrix_gate_harness as gate
from nextseek_api.cc_assistant.tests.validate_cc_acceptance import (
    SHARED_CRED_KEYS,
    matrix_executor_name,
)
from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import BIN_OPS


# ==========================================================================
# build_op_argv
# ==========================================================================

@pytest.mark.parametrize("op", ["nextseek-entity-extract", "nextseek-parse", "nextseek-graph", "nextseek-plan"])
def test_query_only_ops_argv(op):
    assert gate.build_op_argv(op, query="find rna-seq samples") == [op, "--query", "find rna-seq samples"]


@pytest.mark.parametrize("op", ["nextseek-entity-extract", "nextseek-parse", "nextseek-graph", "nextseek-plan"])
def test_query_only_ops_require_query(op):
    with pytest.raises(ValueError):
        gate.build_op_argv(op)


def test_nextseek_query_always_passes_json_flag():
    """--json is REQUIRED for the excerpt to be parseable JSON matching the
    op's allowlist -- the bin's default (no --json) form prints only the
    extracted .reply string."""
    argv = gate.build_op_argv("nextseek-query", query="what projects exist?")
    assert argv == ["nextseek-query", "--query", "what projects exist?", "--json"]


def test_api_read_argv():
    argv = gate.build_op_argv("nextseek-api-read", parser_plan='{"endpoint": "/samples/"}')
    assert argv == ["nextseek-api-read", "--parser-plan", '{"endpoint": "/samples/"}']


def test_api_read_requires_parser_plan():
    with pytest.raises(ValueError):
        gate.build_op_argv("nextseek-api-read")


def test_api_write_unconfirmed_leg_omits_confirmed_write_flag():
    argv = gate.build_op_argv("nextseek-api-write", parser_plan="{}")
    assert "--confirmed-write" not in argv


def test_api_write_confirmed_leg_includes_flag():
    argv = gate.build_op_argv("nextseek-api-write", parser_plan="{}", confirmed_write=True)
    assert argv[-1] == "--confirmed-write" or "--confirmed-write" in argv


def test_report_argv():
    argv = gate.build_op_argv("nextseek-report", mode="samples", project="1-sandbox")
    assert argv == ["nextseek-report", "--mode", "samples", "--project", "1-sandbox"]


def test_report_requires_mode_and_project():
    with pytest.raises(ValueError):
        gate.build_op_argv("nextseek-report", mode="samples")
    with pytest.raises(ValueError):
        gate.build_op_argv("nextseek-report", project="1-sandbox")


def test_generate_submission_argv():
    argv = gate.build_op_argv("nextseek-generate-submission", submission_type="GEO", uids="S0001,S0002")
    assert argv == ["nextseek-generate-submission", "--type", "GEO", "--uids", "S0001,S0002"]


def test_unknown_op_rejected():
    with pytest.raises(ValueError):
        gate.build_op_argv("nextseek-not-a-real-op", query="x")


def test_build_op_argv_covers_every_bin_op():
    """Every one of the 9 real bin ops must be dispatchable (no silent gaps
    the matrix harness would crash on at gate-run time)."""
    kwargs_for_op = {
        "nextseek-entity-extract": {"query": "q"},
        "nextseek-parse": {"query": "q"},
        "nextseek-graph": {"query": "q"},
        "nextseek-plan": {"query": "q"},
        "nextseek-query": {"query": "q"},
        "nextseek-api-read": {"parser_plan": "{}"},
        "nextseek-api-write": {"parser_plan": "{}"},
        "nextseek-report": {"mode": "samples", "project": "1-sandbox"},
        "nextseek-generate-submission": {"submission_type": "GEO", "uids": "S1"},
    }
    assert set(kwargs_for_op) == set(BIN_OPS)
    for op, kwargs in kwargs_for_op.items():
        argv = gate.build_op_argv(op, **kwargs)
        assert argv[0] == op
        assert all(isinstance(a, str) for a in argv)


# ==========================================================================
# gate_executor_environment / build_gate_executor_run_kwargs
# ==========================================================================

def test_gate_executor_environment_goes_through_build_agent_environment():
    """iter-3 M-1: the harness must not hand-assemble the env -- it must
    call cc_engine.build_agent_environment (proven here by checking every
    key that function contributes is present, plus the harness's own
    additive idle-mode flag)."""
    hostile_source = {
        "AWS_BEARER_TOKEN_BEDROCK": "ABSK-SHOULD-NEVER-APPEAR",
        "NEO4J_PASSWORD": "shared-secret",
        "NEXTSEEK_BASE_URL": "http://127.0.0.1:8000",
    }
    env = gate.gate_executor_environment(
        api_user="gateuser", api_pass="gatepass", source=hostile_source,
    )
    expected = cc_engine.build_agent_environment(
        source=hostile_source, api_user="gateuser", api_pass="gatepass", path_mappings={},
    )
    for k, v in expected.items():
        assert env[k] == v
    assert env["DMAC_RUNTIME_MODE"] == "idle"
    # No shared cred key the builder itself excludes can appear via the
    # harness's additive step either.
    for key in SHARED_CRED_KEYS:
        assert key not in env


def test_gate_executor_environment_carries_no_shared_creds_even_from_hostile_source():
    env = gate.gate_executor_environment(
        api_user="u", api_pass="p",
        source={"AWS_BEARER_TOKEN_BEDROCK": "x", "MYSQL_PASSWORD": "y", "GCP_API_KEY": "z"},
    )
    for key in SHARED_CRED_KEYS:
        assert key not in env


def test_build_gate_executor_run_kwargs_shape():
    env = {"NEXTSEEK_SIDECAR_HOST": "nextseek-sidecar", "DMAC_RUNTIME_MODE": "idle"}
    kwargs = gate.build_gate_executor_run_kwargs(run_id="deadbeef", image="dmac-assistant:poc", environment=env)
    assert kwargs["name"] == matrix_executor_name("deadbeef") == "dmac-cc-matrix-deadbeef"
    assert kwargs["image"] == "dmac-assistant:poc"
    assert kwargs["environment"] is env
    assert kwargs["network"] == cc_engine.DEFAULT_NETWORK
    assert kwargs["labels"]["nextseek.cc.run"] == "deadbeef"
    assert kwargs["labels"]["nextseek.cc.matrix"] == "1"
    assert kwargs["detach"] is True


# ==========================================================================
# make_matrix_row
# ==========================================================================

def test_make_matrix_row_success_excerpts_stdout():
    result = gate.ExecResult(exit_code=0, stdout='{"op": "entity", "result": {}}', stderr="", wall_secs=1.234567)
    row = gate.make_matrix_row(
        "nextseek-entity-extract", result=result, container_id="cid1",
        container_name="dmac-cc-matrix-run1", image="dmac-assistant:poc", transport="sidecar",
    )
    assert row["op"] == "nextseek-entity-extract"
    assert row["exit_code"] == 0
    assert json.loads(row["excerpt"]) == {"op": "entity", "result": {}}
    assert row["container_id"] == "cid1"
    assert row["container_name"] == "dmac-cc-matrix-run1"
    assert row["image"] == "dmac-assistant:poc"
    assert row["wall_secs"] == pytest.approx(1.235, abs=1e-3)
    assert "published_path" not in row


def test_make_matrix_row_failure_excerpts_stderr():
    result = gate.ExecResult(
        exit_code=5, stdout="", stderr='{"error": {"code": "WRITE_BLOCKED", "message": "x"}}', wall_secs=0.5,
    )
    row = gate.make_matrix_row(
        "nextseek-api-write", result=result, container_id="cid1",
        container_name="dmac-cc-matrix-run1", image="dmac-assistant:poc", transport="sidecar",
    )
    assert row["exit_code"] == 5
    assert "WRITE_BLOCKED" in row["excerpt"]


def test_make_matrix_row_carries_published_path_when_given():
    result = gate.ExecResult(exit_code=0, stdout='{"op": "report", "result": {}}', stderr="", wall_secs=2.0)
    row = gate.make_matrix_row(
        "nextseek-report", result=result, container_id="cid1", container_name="dmac-cc-matrix-run1",
        image="dmac-assistant:poc", transport="sidecar",
        published_path="/dmac/users/1-sandbox/gateuser/scratch/nextseek-artifacts/report.xlsx",
    )
    assert row["published_path"].endswith("report.xlsx")


def test_transport_for_op_matches_wire_topology():
    assert gate.TRANSPORT_FOR_OP["nextseek-query"] == "viewset"
    assert gate.TRANSPORT_FOR_OP["nextseek-plan"] == "viewset"
    for op in BIN_OPS:
        if op not in ("nextseek-query", "nextseek-plan"):
            assert gate.TRANSPORT_FOR_OP[op] == "sidecar"


# ==========================================================================
# Sweep command / post-sweep scan command construction
# ==========================================================================

def test_build_sweep_command_matches_the_documented_invocation_form():
    argv = gate.build_sweep_command(
        nextseek_container="nextseek", user_id="gateuser", api_user="gateuser", project="1-sandbox",
    )
    assert argv == [
        "docker", "exec", "nextseek", "/app/.venv/bin/python", "manage.py", "cc_sweep_staging",
        "--user-id", "gateuser", "--api-user", "gateuser", "--project", "1-sandbox",
    ]


def test_build_ps_name_filter_command():
    argv = gate.build_ps_name_filter_command("nextseek_nginx")
    assert argv == ["docker", "ps", "--filter", "name=nextseek_nginx", "--format", "{{.Names}}"]


def test_build_post_sweep_scan_command_mounts_readonly():
    argv = gate.build_post_sweep_scan_command(volume="dmac-cc-users")
    assert argv[0] == "docker"
    assert "-v" in argv
    assert "dmac-cc-users:/v:ro" in argv


# ==========================================================================
# images_json_cc_image
# ==========================================================================

def test_images_json_cc_image_reads_the_pinned_key(tmp_path):
    (tmp_path / "images.json").write_text(json.dumps({"cc-agent": "dmac-assistant:poc"}), encoding="utf-8")
    assert gate.images_json_cc_image(tmp_path) == "dmac-assistant:poc"


def test_images_json_cc_image_missing_file_returns_none(tmp_path):
    assert gate.images_json_cc_image(tmp_path) is None


def test_images_json_cc_image_missing_key_returns_none(tmp_path):
    (tmp_path / "images.json").write_text(json.dumps({"nextseek": "x"}), encoding="utf-8")
    assert gate.images_json_cc_image(tmp_path) is None


# ==========================================================================
# write_json / write_text
# ==========================================================================

def test_write_json_and_text_roundtrip(tmp_path):
    gate.write_json(tmp_path / "a.json", {"x": 1})
    assert json.loads((tmp_path / "a.json").read_text()) == {"x": 1}
    gate.write_text(tmp_path / "b.txt", "hello\n")
    assert (tmp_path / "b.txt").read_text() == "hello\n"


# ==========================================================================
# Import-safety: module-level constants agree with the validator's contract
# ==========================================================================

def test_harness_bin_ops_matches_validator_bin_ops():
    assert gate.BIN_OPS == BIN_OPS


def test_harness_exposes_all_public_names():
    for name in gate.__all__:
        assert hasattr(gate, name), name

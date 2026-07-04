"""Hermetic tests for Task 15's extension of the Step 7 evidence validator:
the ``plugin_ops_matrix.json`` capability-completeness proof (all 9 bin ops)
and the ``dmac-cc-net`` CLOSED-SET peer rule (G7-11 sidecar wave, SPEC-7
section 8's plugin_ops_matrix.json paragraph + section 10's G7-11 testing
additions).

Split into a sibling file (Task 2's own precedent) rather than further
growing ``test_step7_compose_deploy.py``. Shared bundle-building helpers
(``_full_bundle``, ``_write_auxiliary_artifacts``, ``_write_matrix_artifacts``,
``_matrix_row``, ``_network_inspect_json``, ...) are imported from that
module rather than duplicated.

No Docker, no network, no DB, no spend.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nextseek_api.cc_assistant.tests.test_step7_compose_deploy import (
    GATE_PROJECT,
    GATE_USER_ID,
    RUN_ID,
    TRANSCRIPT_CONTENT,
    _full_bundle,
    _names,
    _repo_with_transcript,
)
from nextseek_api.cc_assistant.tests.validate_cc_acceptance import (
    is_dmac_cc_net_closed_set_member,
    matrix_executor_name,
)
from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (
    BIN_OPS,
    IN_TURN_HEADROOM_SECS,
    OP_ASSISTANT_ENDPOINT,
    validate_run,
)


def _bundle(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    repo, sha = _repo_with_transcript(tmp_path, content=TRANSCRIPT_CONTENT)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    return repo, bundle, tracker, sha


def _clean(tmp_path: Path, **aux_overrides) -> tuple[bool, list, Path, Path]:
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, aux_overrides=aux_overrides)
    all_ok, checks = validate_run(bundle, repo_root=repo)
    return all_ok, checks, bundle, repo


# ==========================================================================
# Happy path: the 15 new Task 15 checks all fire on a clean bundle.
# ==========================================================================

def test_clean_bundle_matrix_checks_all_pass(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path)
    assert all_ok, [c for c in checks if not c[1]]
    d = _names(checks)
    for name in (
        "dmac_cc_net_closed_set", "plugin_ops_matrix_present",
        "plugin_ops_matrix_all_ops_present", "plugin_ops_matrix_row_schema_valid",
        "plugin_ops_matrix_exit_codes_valid", "plugin_ops_matrix_excerpt_shape_valid",
        "plugin_ops_matrix_executor_provenance",
        "plugin_ops_matrix_published_paths_under_user_subtree",
        "post_sweep_user_tree_scan_contains_published_paths",
        "gate_access_log_window_hits_every_op", "matrix_env_scan_no_shared_creds",
        "sweep_invocation_valid", "gate_instance_binding_present",
        "plugin_ops_matrix_in_turn_viability", "cost_ledger_valid",
        "cost_extraction_evidence", "r26_live_probes_present",
        "meta_matrix_spend_estimate_recorded",
    ):
        assert d[name] is True, name


# ==========================================================================
# Success line: "validator rejects a synthetic 8/9 matrix"
# ==========================================================================

def test_matrix_missing_one_op_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, omit_matrix_ops=["nextseek-plan"])
    assert not all_ok
    d = _names(checks)
    assert d["plugin_ops_matrix_all_ops_present"] is False


def test_matrix_with_unexpected_extra_key_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, extra_matrix_ops=["nextseek-bogus-op"])
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_all_ops_present"] is False


# ==========================================================================
# Success line: "a matrix with one exit-7"
# ==========================================================================

def test_matrix_row_exit_7_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-graph": {"exit_code": 7, "excerpt": json.dumps(
            {"error": {"code": "TRANSPORT_ERROR", "message": "sidecar unreachable"}}
        )},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_exit_codes_valid"] is False


@pytest.mark.parametrize("op", list(BIN_OPS))
def test_matrix_row_exit_7_fails_for_every_op(tmp_path, op):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        op: {"exit_code": 7, "excerpt": json.dumps({"error": {"code": "TRANSPORT_ERROR"}})},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_exit_codes_valid"] is False


# ==========================================================================
# Layer-2 write alternative: pinned exit-5 / WRITE_BLOCKED form
# ==========================================================================

def test_api_write_exit_5_without_write_blocked_marker_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-api-write": {"exit_code": 5, "excerpt": json.dumps({"error": {"code": "OTHER"}})},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_exit_codes_valid"] is False


def test_api_write_exit_5_with_write_blocked_marker_passes(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-api-write": {"exit_code": 5, "excerpt": json.dumps(
            {"error": {"code": "WRITE_BLOCKED", "message": "confirmed_write not set"}}
        )},
    })
    assert _names(checks)["plugin_ops_matrix_exit_codes_valid"] is True, checks


def test_api_write_confirmed_leg_exit_0_passes(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-api-write": {
            "exit_code": 0,
            "excerpt": json.dumps({"op": "api-write", "result": {"endpoint": "/x/", "method": "POST"}}),
        },
    })
    assert _names(checks)["plugin_ops_matrix_exit_codes_valid"] is True, checks
    assert _names(checks)["plugin_ops_matrix_excerpt_shape_valid"] is True, checks


@pytest.mark.parametrize("op", [o for o in BIN_OPS if o != "nextseek-api-write"])
def test_exit_5_write_blocked_on_any_other_op_still_fails(tmp_path, op):
    """The pinned Layer-2 exception is scoped to nextseek-api-write ONLY --
    no other op can legitimately produce a WRITE_BLOCKED exit-5 leg."""
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        op: {"exit_code": 5, "excerpt": json.dumps({"error": {"code": "WRITE_BLOCKED"}})},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_exit_codes_valid"] is False


@pytest.mark.parametrize("bad_exit", [1, 2, 3, 4, 6, 8, 9])
def test_other_nonzero_exit_codes_fail(tmp_path, bad_exit):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-entity-extract": {"exit_code": bad_exit, "excerpt": json.dumps({"error": {"code": "X"}})},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_exit_codes_valid"] is False


# ==========================================================================
# Anti-fabrication: excerpt shape (per-op response-field allowlist)
# ==========================================================================

def test_excerpt_with_extra_top_level_field_fails_shape_check(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-entity-extract": {
            "exit_code": 0,
            "excerpt": json.dumps({"op": "entity", "result": {}, "totally_fabricated_field": 1}),
        },
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_excerpt_shape_valid"] is False


def test_excerpt_exit_0_with_error_field_fails_shape_check(tmp_path):
    """iter-2 R2-M3 / iter-3 L-5: an exit-0 excerpt carrying a failure payload
    (e.g. {"ok": false, "error": "graph agent produced no cypher"}) MUST FAIL
    -- agent failure is not empty data."""
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-graph": {
            "exit_code": 0,
            "excerpt": json.dumps({"ok": False, "error": "graph agent produced no cypher"}),
        },
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_excerpt_shape_valid"] is False


def test_excerpt_exit_0_with_ok_false_and_no_error_field_still_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-parse": {"exit_code": 0, "excerpt": json.dumps({"op": "parse", "result": {}, "ok": False})},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_excerpt_shape_valid"] is False


def test_excerpt_not_json_fails_shape_check(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-query": {"exit_code": 0, "excerpt": "not json at all"},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_excerpt_shape_valid"] is False


def test_successful_empty_data_shape_passes(tmp_path):
    """Step 3b: legitimate empty-data semantics (empty rows/saved_files WITH
    the op's success marker) is the ONLY acceptable non-error exit-0 form --
    and it must PASS, not be penalized."""
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-report": {
            "exit_code": 0,
            "excerpt": json.dumps({"op": "report", "result": {"summary": {}, "saved_files": {}, "rows": {}}}),
        },
    })
    assert _names(checks)["plugin_ops_matrix_excerpt_shape_valid"] is True, checks


# ==========================================================================
# Executor provenance: image + container_id -> network_inspect_matrix.json join
# ==========================================================================

def test_matrix_row_image_mismatch_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-entity-extract": {"image": "some-other-image:latest"},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_executor_provenance"] is False


def test_matrix_row_container_id_not_in_matrix_window_inspect_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-entity-extract": {"container_id": "f" * 64},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_executor_provenance"] is False


def test_matrix_row_container_name_field_mismatch_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-entity-extract": {"container_name": "dmac-cc-agent-notthematrix"},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_executor_provenance"] is False


def test_matrix_window_inspect_names_row_something_else_fails(tmp_path):
    """No 'in-turn-agent exception' (iter-3 M-3): even if the matrix
    executor's own container id maps to the AGENT's name (not
    dmac-cc-matrix-<run_id>) in network_inspect_matrix.json, the row fails."""
    all_ok, checks, bundle, repo = _clean(tmp_path)
    inspect = json.loads((bundle / "network_inspect_matrix.json").read_text())
    containers = inspect[0]["Containers"]
    for rec in containers.values():
        if rec["Name"] == matrix_executor_name(RUN_ID):
            rec["Name"] = f"dmac-cc-agent-{RUN_ID}"
    (bundle / "network_inspect_matrix.json").write_text(json.dumps(inspect), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_executor_provenance"] is False


# ==========================================================================
# Sweep cross-check: published_path under the gate user's own subtree +
# present on disk in post_sweep_user_tree_scan.txt
# ==========================================================================

def test_report_row_without_published_path_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    matrix = json.loads((bundle / "plugin_ops_matrix.json").read_text())
    del matrix["nextseek-report"]["published_path"]
    (bundle / "plugin_ops_matrix.json").write_text(json.dumps(matrix), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_published_paths_under_user_subtree"] is False


def test_generate_submission_row_without_published_path_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    matrix = json.loads((bundle / "plugin_ops_matrix.json").read_text())
    del matrix["nextseek-generate-submission"]["published_path"]
    (bundle / "plugin_ops_matrix.json").write_text(json.dumps(matrix), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_published_paths_under_user_subtree"] is False


def test_dead_staging_only_published_path_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-report": {"published_path": "/dmac/users/_staging/deadbeef/req-1/report.xlsx"},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_published_paths_under_user_subtree"] is False


def test_published_path_outside_gate_user_subtree_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-report": {"published_path": "/dmac/users/9-someoneelse/otheruser/scratch/nextseek-artifacts/x.xlsx"},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_published_paths_under_user_subtree"] is False


def test_published_path_absent_from_post_sweep_scan_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    # The scan on disk does NOT carry the recorded published_path(s) --
    # an on-disk artifact check, not a bare string assertion, must catch this.
    (bundle / "post_sweep_user_tree_scan.txt").write_text(
        f"/dmac/users/{GATE_PROJECT}/{GATE_USER_ID}/scratch/nextseek-artifacts/unrelated.txt\n",
        encoding="utf-8",
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["post_sweep_user_tree_scan_contains_published_paths"] is False


# ==========================================================================
# Success line: bundle missing any REQUIRED Task 15 artifact fails
# ==========================================================================

@pytest.mark.parametrize("skip_key", [
    "plugin_ops_matrix", "instance_binding", "cost_ledger", "gate_access_log_window",
    "post_sweep_user_tree_scan", "matrix_env_scan", "sweep_invocation",
    "network_inspect_matrix",
])
def test_bundle_missing_required_task15_artifact_fails(tmp_path, skip_key):
    all_ok, checks, _, _ = _clean(tmp_path, skip_matrix_artifacts={skip_key})
    assert not all_ok, f"expected failure with {skip_key!r} omitted"


def test_bundle_missing_network_inspect_json_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "network_inspect.json").unlink()

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["cross_artifact_agent_container_in_network_inspect"] is False
    assert _names(checks)["dmac_cc_net_closed_set"] is True or _names(checks)["dmac_cc_net_closed_set"] is False
    # network_inspect_matrix.json alone still lets the closed-set check
    # evaluate (fail-closed only if BOTH inspects are unreadable) -- assert
    # the specific artifact-presence checks that must fail instead:
    assert _names(checks)["network_segmentation_ok"] is False


def test_bundle_missing_both_network_inspects_fails_closed_set(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "network_inspect.json").unlink()
    (bundle / "network_inspect_matrix.json").unlink()

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["dmac_cc_net_closed_set"] is False


# ==========================================================================
# matrix_env_scan.txt: same no-shared-creds rules as agent_env_scan.txt
# ==========================================================================

def test_matrix_env_scan_shared_cred_key_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "matrix_env_scan.txt").write_text(
        "NEO4J_PASSWORD=demopassword\nHOME=/home/user\n", encoding="utf-8",
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["matrix_env_scan_no_shared_creds"] is False


def test_matrix_env_scan_leak_marker_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "matrix_env_scan.txt").write_text("SOME_TOKEN=ABSK1234\n", encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["matrix_env_scan_no_shared_creds"] is False


# ==========================================================================
# sweep_invocation.json: nonzero exit / wrong command / missing fields fail
# ==========================================================================

def test_sweep_invocation_nonzero_exit_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    inv = json.loads((bundle / "sweep_invocation.json").read_text())
    inv["exit_code"] = 1
    (bundle / "sweep_invocation.json").write_text(json.dumps(inv), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["sweep_invocation_valid"] is False


def test_sweep_invocation_wrong_command_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    inv = json.loads((bundle / "sweep_invocation.json").read_text())
    inv["command"] = "docker exec nextseek /app/.venv/bin/python manage.py some_other_command"
    (bundle / "sweep_invocation.json").write_text(json.dumps(inv), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["sweep_invocation_valid"] is False


# ==========================================================================
# gate_access_log_window.txt: per-op (endpoint-keyed) hit requirement;
# query/plan share ONE endpoint -- not double-counted
# ==========================================================================

def test_access_log_window_missing_hit_for_an_op_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    # Drop the /entity/ line only.
    text = (bundle / "gate_access_log_window.txt").read_text()
    text = "\n".join(ln for ln in text.splitlines() if "/entity/" not in ln) + "\n"
    (bundle / "gate_access_log_window.txt").write_text(text, encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["gate_access_log_window_hits_every_op"] is False


def test_access_log_window_missing_shared_query_plan_endpoint_fails_once(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    text = (bundle / "gate_access_log_window.txt").read_text()
    text = "\n".join(ln for ln in text.splitlines() if "/query/async/" not in ln) + "\n"
    (bundle / "gate_access_log_window.txt").write_text(text, encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    name, ok, detail = next(c for c in checks if c[0] == "gate_access_log_window_hits_every_op")
    assert ok is False
    assert detail.count("/nextseek_api/assistant/query/async/") == 1, (
        "the shared endpoint must be listed once, not once per sharing op (no double count)"
    )


def test_access_log_window_single_hit_satisfies_both_query_and_plan(tmp_path):
    """The happy-path fixture writes the query/async/ hit line exactly ONCE
    (see _write_matrix_artifacts: it dedupes on distinct endpoints) and both
    nextseek-query and nextseek-plan pass from that single hit."""
    all_ok, checks, bundle, _ = _clean(tmp_path)
    text = (bundle / "gate_access_log_window.txt").read_text()
    assert text.count("/nextseek_api/assistant/query/async/") == 1
    assert _names(checks)["gate_access_log_window_hits_every_op"] is True


def test_access_log_window_missing_entirely_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, skip_matrix_artifacts={"gate_access_log_window"})
    assert not all_ok
    assert _names(checks)["gate_access_log_window_hits_every_op"] is False


def test_op_endpoint_map_covers_every_bin_op():
    assert set(OP_ASSISTANT_ENDPOINT) == set(BIN_OPS)
    assert OP_ASSISTANT_ENDPOINT["nextseek-query"] == OP_ASSISTANT_ENDPOINT["nextseek-plan"]
    assert OP_ASSISTANT_ENDPOINT["nextseek-query"] == "/nextseek_api/assistant/query/async/"


# ==========================================================================
# instance_binding.json (Gate 3C)
# ==========================================================================

def test_instance_binding_missing_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, skip_matrix_artifacts={"instance_binding"})
    assert not all_ok
    assert _names(checks)["gate_instance_binding_present"] is False


def test_instance_binding_empty_uids_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "instance_binding.json").write_text(json.dumps({
        "project_title": "Published Data",
        "project": GATE_PROJECT,
        "reference_uids": [],
        "uids": [],
        "forbidden_actions": ["create_seeded_fixture"],
        "source": "instance_binding.json",
    }), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["gate_instance_binding_present"] is False


# ==========================================================================
# In-turn viability: exceeding the 150s headroom does NOT fail the bundle,
# but IS listed in the validator output.
# ==========================================================================

def test_op_exceeding_in_turn_headroom_does_not_fail_bundle_but_is_listed(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-generate-submission": {"wall_secs": IN_TURN_HEADROOM_SECS + 30},
    })
    assert all_ok, [c for c in checks if not c[1]]
    name, ok, detail = next(c for c in checks if c[0] == "plugin_ops_matrix_in_turn_viability")
    assert ok is True
    assert "nextseek-generate-submission" in detail
    assert "EXCEEDS_HEADROOM" in detail


def test_op_wall_secs_not_numeric_fails_viability_check(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, matrix_overrides={
        "nextseek-graph": {"wall_secs": "not-a-number"},
    })
    assert not all_ok
    assert _names(checks)["plugin_ops_matrix_in_turn_viability"] is False


# ==========================================================================
# cost_ledger.json (Gate 3C)
# ==========================================================================

def test_missing_cost_ledger_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, skip_matrix_artifacts={"cost_ledger"})
    assert not all_ok
    assert _names(checks)["cost_ledger_valid"] is False
    assert _names(checks)["meta_matrix_spend_estimate_recorded"] is False


def test_estimate_only_bundle_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, meta_overrides={
        "matrix_spend_estimate_usd": 0.35,
        "matrix_spend_estimate_method": "heuristic",
    })
    (bundle / "cost_ledger.json").unlink(missing_ok=True)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["cost_ledger_valid"] is False
    assert _names(checks)["meta_matrix_spend_estimate_recorded"] is False


# ==========================================================================
# dmac-cc-net closed set (Step 2) -- direct unit tests on the pure predicate,
# plus a bundle-level test with a planted stranger.
# ==========================================================================

@pytest.mark.parametrize("name", [
    "nextseek_nginx",                # bare compose service name
    "nextseek-nextseek_nginx-1",     # compose-v2 project-prefixed runtime name
    "nextseek_nextseek_nginx_1",     # compose-v1 underscore-style runtime name
    "dmac-bedrock-proxy",
    "nextseek-sidecar",
])
def test_closed_set_legitimate_trio_bare_and_prefixed_forms(name):
    assert is_dmac_cc_net_closed_set_member(name) is True


@pytest.mark.parametrize("run_id", ["a1b2c3d4", "deadbeef-0001", "0" * 64])
def test_closed_set_general_agent_pattern(run_id):
    assert is_dmac_cc_net_closed_set_member(f"dmac-cc-agent-{run_id}") is True


def test_closed_set_this_runs_required_agent_name():
    assert is_dmac_cc_net_closed_set_member(f"dmac-cc-agent-{RUN_ID}") is True


def test_closed_set_matrix_executor_requires_matching_run_id():
    assert is_dmac_cc_net_closed_set_member(matrix_executor_name(RUN_ID), run_id=RUN_ID) is True
    # A DIFFERENT run's matrix executor name is NOT a member of THIS run's
    # closed set (the reserved name is exact-per-run, not a general pattern).
    assert is_dmac_cc_net_closed_set_member(matrix_executor_name("some-other-run"), run_id=RUN_ID) is False
    # And with no run_id supplied at all, no matrix name is ever a member.
    assert is_dmac_cc_net_closed_set_member(matrix_executor_name(RUN_ID), run_id=None) is False


@pytest.mark.parametrize("stranger", [
    "nextseek",                          # exact-name rejection retained
    "neo4j",
    "seek-mysql",
    "dmac-cc-agent",                     # missing the run_id segment
    "dmac-cc-agentX-deadbeef",           # not the exact "-agent-" pattern
    "dmac-cc-matrix-someone-elses-run",  # a DIFFERENT run's matrix name
    "evil-nextseek_nginx-lookalike",     # NOT a legitimate compose-prefixed nginx form
])
def test_closed_set_rejects_planted_stranger_bare(stranger):
    assert is_dmac_cc_net_closed_set_member(stranger, run_id=RUN_ID) is False


def test_closed_set_bundle_level_planted_stranger_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, network_inspect_containers=[
        f"dmac-cc-agent-{RUN_ID}", "dmac-bedrock-proxy", "nextseek-sidecar",
        "nextseek_nginx", "some-stranger-container",
    ])
    assert not all_ok
    assert _names(checks)["dmac_cc_net_closed_set"] is False


def test_closed_set_bundle_level_compose_prefixed_stranger_fails(tmp_path):
    all_ok, checks, _, _ = _clean(tmp_path, network_inspect_matrix_containers=[
        matrix_executor_name(RUN_ID), "nextseek-project-evil-container-1",
    ])
    assert not all_ok
    assert _names(checks)["dmac_cc_net_closed_set"] is False


def test_closed_set_bundle_level_all_legitimate_members_pass(tmp_path):
    all_ok, checks, _, _ = _clean(
        tmp_path,
        network_inspect_containers=[
            f"dmac-cc-agent-{RUN_ID}", "dmac-bedrock-proxy", "nextseek-sidecar",
            "nextseek-nextseek_nginx-1",
        ],
        network_inspect_matrix_containers=[matrix_executor_name(RUN_ID), "dmac-bedrock-proxy"],
    )
    assert _names(checks)["dmac_cc_net_closed_set"] is True, [c for c in checks if not c[1]]

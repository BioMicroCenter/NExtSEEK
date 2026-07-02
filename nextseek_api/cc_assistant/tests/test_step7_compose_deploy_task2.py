"""Hermetic tests for PLAN-7 Task 2's extension of the Step 7 evidence
validator: every check named in the Task 2 brief's "Step 2: Implement the
validator" paragraph beyond the Task 1 preflight/step3_deploy_gate subset
(covered in ``test_step7_compose_deploy.py``).

Split into a sibling file (rather than further growing
``test_step7_compose_deploy.py``, which already carries the Task 1 suite +
the shared ``_full_bundle`` fixture machinery) per the Task 2 brief's
explicit allowance. Shared bundle-building helpers are imported from that
module rather than duplicated.

No Docker, no network, no DB, no spend. Git IS used against real throwaway
``tmp_path`` repos (available hermetically) to independently re-verify the
live-gate transcript content, exactly as Task 1 does.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nextseek_api.cc_assistant.tests.test_step7_compose_deploy import (
    FOREIGN_TOKENS,
    LIVE_SENTINEL,
    OWN_MARKER,
    RUN_ID,
    TRANSCRIPT_CONTENT,
    _full_bundle,
    _make_git_repo,
    _names,
    _pass_bundle,
    _repo_with_transcript,
    _write_auxiliary_artifacts,
    _write_meta_full,
    _write_tracker,
)
from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (
    EXPECTED_VOLUME_BASE_NAMES,
    validate_run,
)


def _bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (repo, bundle, tracker) with a real git repo carrying the
    committed live-gate transcript, an empty bundle dir, and a tracker path."""
    repo, sha = _repo_with_transcript(tmp_path, content=TRANSCRIPT_CONTENT)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    return repo, bundle, tracker, sha


# ==========================================================================
# deploy_commit format + deploy_commit == meta.json.repo_commit
# ==========================================================================

def test_deploy_commit_format_valid_passes_on_real_sha(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["deploy_commit_format_valid"] is True


@pytest.mark.parametrize("bad", ["not-a-sha", "a" * 39, "A" * 40, "g" * 40, ""])
def test_deploy_commit_format_invalid_fails(tmp_path, bad):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, gate_overrides={"deploy_commit": bad})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    d = _names(checks)
    assert not all_ok
    assert d["deploy_commit_format_valid"] is False


def test_deploy_commit_matches_meta_repo_commit_passes(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert all_ok, checks
    assert _names(checks)["deploy_commit_matches_meta_repo_commit"] is True


def test_deploy_commit_mismatched_meta_repo_commit_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha,
                 meta_overrides={"repo_commit": "f" * 40})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["deploy_commit_matches_meta_repo_commit"] is False


# ==========================================================================
# Live transcript content markers (byte-identical PLAN-3 Task 13 Step 8
# allowlist)
# ==========================================================================

def test_transcript_markers_all_present_on_clean_bundle(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    d = _names(checks)
    assert d["transcript_migration_marker_present"] is True
    assert d["transcript_cc_upload_marker_present"] is True
    assert d["transcript_cc_traces_marker_present"] is True
    assert all_ok, checks


def test_transcript_migration_marker_accepts_showmigrations_form(tmp_path):
    """The idempotency-robust OR: an already-applied DB's `showmigrations`
    stdout ("[X] 0007_ccsessiontranscript") must ALSO satisfy the gate, not
    just the fresh-migrate "Applying nextseek_api.0007" form."""
    content = (
        b"[X] 0007_ccsessiontranscript\n"
        b"cc_assistant.upload\n"
        b'{"cc_traces": []}\n'
    )
    repo, sha = _make_git_repo(tmp_path, {
        "nextseek_api/cc_assistant/evidence/3-ui-based-io-live/live_gate_transcript.txt": content,
    })
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["transcript_migration_marker_present"] is True
    assert all_ok, checks


def test_transcript_missing_migration_marker_fails(tmp_path):
    content = b"cc_assistant.upload\ncc_traces excerpt\n"  # no migration marker at all
    repo, sha = _make_git_repo(tmp_path, {
        "nextseek_api/cc_assistant/evidence/3-ui-based-io-live/live_gate_transcript.txt": content,
    })
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["transcript_migration_marker_present"] is False


def test_transcript_missing_cc_upload_marker_fails(tmp_path):
    content = b"Applying nextseek_api.0007_ccsessiontranscript... OK\ncc_traces excerpt\n"
    repo, sha = _make_git_repo(tmp_path, {
        "nextseek_api/cc_assistant/evidence/3-ui-based-io-live/live_gate_transcript.txt": content,
    })
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["transcript_cc_upload_marker_present"] is False


def test_transcript_missing_cc_traces_marker_fails(tmp_path):
    content = b"Applying nextseek_api.0007_ccsessiontranscript... OK\ncc_assistant.upload\n"
    repo, sha = _make_git_repo(tmp_path, {
        "nextseek_api/cc_assistant/evidence/3-ui-based-io-live/live_gate_transcript.txt": content,
    })
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["transcript_cc_traces_marker_present"] is False


def test_transcript_command_substrings_not_required(tmp_path):
    """PLAN-3 Task 13 Step 8 contracts only stdout/stderr + exit codes, not
    echoed command lines -- a transcript with NO command text and NO
    exit-code substring, but the three allowlisted markers, must still pass."""
    content = (
        b"Applying nextseek_api.0007_ccsessiontranscript... OK\n"
        b"cc_assistant.upload\n"
        b'{"cc_traces": [{"turn": 1, "role": "user"}]}\n'
    )
    repo, sha = _make_git_repo(tmp_path, {
        "nextseek_api/cc_assistant/evidence/3-ui-based-io-live/live_gate_transcript.txt": content,
    })
    assert b"migrate nextseek_api 0007" not in content
    assert b"inspect registered" not in content
    assert b"exit-code" not in content
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert all_ok, checks


# ==========================================================================
# Supplementary handoff parse (SRS fields + step3 done / sha match)
# ==========================================================================

def test_supplementary_handoff_absent_is_fine(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)  # user_signoff_handoff_path defaults None

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["supplementary_handoff_valid"] is True
    assert all_ok, checks


def test_supplementary_handoff_citing_step3_done_passes(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    handoff = repo / "handoffs" / "2026-07-01-step7.json"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(json.dumps({
        "report_meta": {"schema_version": "handoff/v1"},
        "annotation_file": {"annotations": [{"step3_status": "done"}]},
    }), encoding="utf-8")
    _full_bundle(bundle, tracker, deploy_commit=sha,
                 gate_overrides={"user_signoff_handoff_path": str(handoff)})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["supplementary_handoff_valid"] is True
    assert all_ok, checks


def test_supplementary_handoff_matching_integration_plan_sha256_passes(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    tracker_sha = _write_tracker(tracker, "done")
    handoff = repo / "handoffs" / "2026-07-01-step7.json"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(json.dumps({
        "report_meta": {"schema_version": "handoff/v1"},
        "annotation_file": {"integration_plan_sha256": tracker_sha},
    }), encoding="utf-8")
    _full_bundle(bundle, tracker, deploy_commit=sha,
                 gate_overrides={"user_signoff_handoff_path": str(handoff)})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["supplementary_handoff_valid"] is True
    assert all_ok, checks


def test_supplementary_handoff_missing_srs_schema_version_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    handoff = repo / "handoffs" / "not-srs.json"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(json.dumps({"step3_status": "done"}), encoding="utf-8")
    _full_bundle(bundle, tracker, deploy_commit=sha,
                 gate_overrides={"user_signoff_handoff_path": str(handoff)})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["supplementary_handoff_valid"] is False


def test_supplementary_handoff_neither_done_nor_sha_match_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    handoff = repo / "handoffs" / "2026-07-01-step7.json"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(json.dumps({
        "report_meta": {"schema_version": "handoff/v1"},
        "annotation_file": {"annotations": [{"step3_status": "in_progress"}]},
    }), encoding="utf-8")
    _full_bundle(bundle, tracker, deploy_commit=sha,
                 gate_overrides={"user_signoff_handoff_path": str(handoff)})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["supplementary_handoff_valid"] is False


def test_supplementary_handoff_path_unreadable_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha,
                 gate_overrides={"user_signoff_handoff_path": "handoffs/does-not-exist.json"})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["supplementary_handoff_valid"] is False


# ==========================================================================
# host_label locked enum
# ==========================================================================

@pytest.mark.parametrize("host_label", ["dev-vm", "nextseek-dev"])
def test_host_label_dev_smoke_values_valid(tmp_path, host_label):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label=host_label)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["host_label_enum_valid"] is True
    assert all_ok, checks


@pytest.mark.parametrize("host_label", ["MBP", "taishajo-mbp", "linux-dev-vm", "prod", "", "Dev-Vm"])
def test_host_label_rejects_everything_else(tmp_path, host_label):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label=host_label)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["host_label_enum_valid"] is False


# ==========================================================================
# MBP greenfield pre-bootstrap volume/network scans + volume-name prefix
# resolution + greenfield_exception
# ==========================================================================

def test_mbp_pre_bootstrap_scans_clean_pass(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="mbp",
                 aux_overrides={"pre_bootstrap": True})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    d = _names(checks)
    assert d["mbp_pre_bootstrap_volumes_absent"] is True
    assert d["mbp_pre_bootstrap_network_absent"] is True
    assert all_ok, checks


def test_mbp_missing_pre_bootstrap_files_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="mbp")  # pre_bootstrap defaults False

    all_ok, checks = validate_run(bundle, repo_root=repo)
    d = _names(checks)
    assert not all_ok
    assert d["mbp_pre_bootstrap_volumes_absent"] is False
    assert d["mbp_pre_bootstrap_network_absent"] is False


def test_mbp_pre_existing_seek_filestore_volume_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="mbp",
                 aux_overrides={"pre_bootstrap": True, "pre_existing_volume_names": ["seek-filestore"]})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["mbp_pre_bootstrap_volumes_absent"] is False


def test_mbp_pre_existing_dmac_cc_users_volume_fails(tmp_path):
    """dmac-cc-users is in EXPECTED_VOLUME_BASE_NAMES (Task 6 additive
    membership) -- a pre-existing one on a supposedly-greenfield MBP must
    also fail the gate."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    assert "dmac-cc-users" in EXPECTED_VOLUME_BASE_NAMES
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="mbp",
                 aux_overrides={"pre_bootstrap": True, "pre_existing_volume_names": ["dmac-cc-users"]})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["mbp_pre_bootstrap_volumes_absent"] is False


def test_mbp_pre_existing_dmac_cc_net_network_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="mbp",
                 aux_overrides={"pre_bootstrap": True, "pre_existing_network_names": ["dmac-cc-net"]})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["mbp_pre_bootstrap_network_absent"] is False


def test_mbp_greenfield_exception_with_handoff_ref_overrides_pre_existing_volume(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(
        bundle, tracker, deploy_commit=sha, host_label="mbp",
        meta_overrides={
            "greenfield_exception": True,
            "greenfield_exception_handoff_path": "handoffs/2026-07-01-greenfield.json",
        },
        aux_overrides={"pre_bootstrap": True, "pre_existing_volume_names": ["seek-filestore"]},
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["mbp_pre_bootstrap_volumes_absent"] is True
    assert all_ok, checks


def test_mbp_greenfield_exception_flag_without_handoff_ref_does_not_override(tmp_path):
    """greenfield_exception alone (no handoff_path) is not sufficient --
    both are required per the brief ("greenfield_exception + handoff ref")."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(
        bundle, tracker, deploy_commit=sha, host_label="mbp",
        meta_overrides={"greenfield_exception": True, "greenfield_exception_handoff_path": None},
        aux_overrides={"pre_bootstrap": True, "pre_existing_volume_names": ["seek-filestore"]},
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["mbp_pre_bootstrap_volumes_absent"] is False


def test_mbp_volume_prefix_resolution_from_instance_json(tmp_path):
    """When startup/.instance.json records a non-default prefix, the expected
    volume names must be prefix-adjusted (Task 10 Step 0's documented
    prefix-adjusted oracle) -- a bare-name pre-existing volume must NOT
    false-positive-fail when the instance is prefixed, but a
    prefix-matching one must still fail the gate."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    (repo / "startup").mkdir(parents=True, exist_ok=True)
    (repo / "startup" / ".instance.json").write_text(
        json.dumps({"name": "sandbox", "prefix": "sandbox-", "ports": {}, "compose_project_name": "x", "created": "now"}),
        encoding="utf-8",
    )
    # Bare "seek-filestore" pre-exists, but this instance is prefixed
    # "sandbox-" -- the actual expected name "sandbox-seek-filestore" is
    # absent, so the gate must PASS (no false positive on the bare name).
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="mbp",
                 aux_overrides={"pre_bootstrap": True, "pre_existing_volume_names": ["seek-filestore"]})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["mbp_pre_bootstrap_volumes_absent"] is True
    assert all_ok, checks


def test_mbp_volume_prefix_resolution_catches_prefixed_pre_existing_volume(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    (repo / "startup").mkdir(parents=True, exist_ok=True)
    (repo / "startup" / ".instance.json").write_text(
        json.dumps({"name": "sandbox", "prefix": "sandbox-", "ports": {}, "compose_project_name": "x", "created": "now"}),
        encoding="utf-8",
    )
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="mbp",
                 aux_overrides={"pre_bootstrap": True, "pre_existing_volume_names": ["sandbox-seek-filestore"]})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["mbp_pre_bootstrap_volumes_absent"] is False


# ==========================================================================
# Independent Docker Engine/API floor re-parse (unconditional, real floor)
# ==========================================================================

def _bundle_with_docker_version_summary(tmp_path, version_summary: str, flag: bool = True):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    preflight = _full_bundle(bundle, tracker, deploy_commit=sha,
                              gate_overrides={"docker_engine_meets_subpath_floor": flag})
    preflight["docker_version_summary"] = version_summary
    (bundle / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")
    return repo, bundle


def test_engine_floor_passes_when_engine_26_api_145_and_flag_true(tmp_path):
    repo, bundle = _bundle_with_docker_version_summary(
        tmp_path, "Engine:\n Version: 26.0.0\n API version: 1.45 (minimum version 1.24)\n"
    )
    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["docker_engine_floor_independent"] is True
    assert all_ok, checks


def test_engine_floor_fails_when_engine_below_26_even_if_flag_claims_true(tmp_path):
    """The core anti-forgery behavior: a hand-edited preflight.json claiming
    the floor bool is true must NOT sail through if the recorded version
    text itself parses below the real floor."""
    repo, bundle = _bundle_with_docker_version_summary(
        tmp_path, "Engine:\n Version: 25.9.9\n API version: 1.47 (minimum version 1.24)\n", flag=True
    )
    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["docker_engine_floor_independent"] is False


def test_engine_floor_fails_when_api_below_145_even_if_engine_high(tmp_path):
    repo, bundle = _bundle_with_docker_version_summary(
        tmp_path, "Engine:\n Version: 27.0.0\n API version: 1.44 (minimum version 1.24)\n", flag=True
    )
    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["docker_engine_floor_independent"] is False


def test_engine_floor_fails_when_flag_is_false_even_if_version_high(tmp_path):
    repo, bundle = _bundle_with_docker_version_summary(
        tmp_path, "Engine:\n Version: 27.0.0\n API version: 1.47 (minimum version 1.24)\n", flag=False
    )
    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["docker_engine_floor_independent"] is False


# ==========================================================================
# Compose floor: CONDITIONAL on compose_config.json using `subpath:` syntax
# ==========================================================================

def test_compose_floor_not_required_when_no_subpath_syntax_even_if_version_low(tmp_path):
    """This plan mounts the whole dmac-cc-users volume with no YAML subpath
    syntax -- a host whose compose file never uses it must NOT be rejected
    on the Compose floor alone, even with an old Compose plugin recorded."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    preflight = _full_bundle(
        bundle, tracker, deploy_commit=sha,
        gate_overrides={"docker_compose_meets_subpath_floor": False},
    )
    preflight["docker_compose_version"] = "Docker Compose version v2.10.0"
    (bundle / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")
    # compose_config.json (written by _full_bundle) has no "subpath" key.

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["docker_compose_floor_conditional"] is True
    assert all_ok, checks


def test_compose_floor_required_and_enforced_when_subpath_syntax_used(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    preflight = _full_bundle(
        bundle, tracker, deploy_commit=sha,
        gate_overrides={"docker_compose_meets_subpath_floor": True},
    )
    preflight["docker_compose_version"] = "Docker Compose version v2.29.1"
    (bundle / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")
    compose_config = json.loads((bundle / "compose_config.json").read_text())
    compose_config["services"]["nextseek"]["volumes"] = [
        {"type": "volume", "source": "dmac-cc-users", "target": "/dmac/users", "volume": {"subpath": "proj/user"}}
    ]
    (bundle / "compose_config.json").write_text(json.dumps(compose_config), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["docker_compose_floor_conditional"] is True
    assert all_ok, checks


def test_compose_floor_fails_when_subpath_syntax_used_but_version_too_low(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    preflight = _full_bundle(
        bundle, tracker, deploy_commit=sha,
        gate_overrides={"docker_compose_meets_subpath_floor": True},
    )
    preflight["docker_compose_version"] = "Docker Compose version v2.10.0"  # below 2.26 floor
    (bundle / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")
    compose_config = json.loads((bundle / "compose_config.json").read_text())
    compose_config["services"]["nextseek"]["volumes"] = [
        {"type": "volume", "source": "dmac-cc-users", "volume": {"subpath": "proj/user"}}
    ]
    (bundle / "compose_config.json").write_text(json.dumps(compose_config), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["docker_compose_floor_conditional"] is False


def test_compose_floor_fails_when_subpath_syntax_used_but_flag_false(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    preflight = _full_bundle(
        bundle, tracker, deploy_commit=sha,
        gate_overrides={"docker_compose_meets_subpath_floor": False},
    )
    preflight["docker_compose_version"] = "Docker Compose version v2.29.1"
    (bundle / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")
    compose_config = json.loads((bundle / "compose_config.json").read_text())
    compose_config["services"]["nextseek"]["volumes"] = [
        {"type": "volume", "source": "dmac-cc-users", "volume": {"subpath": "proj/user"}}
    ]
    (bundle / "compose_config.json").write_text(json.dumps(compose_config), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["docker_compose_floor_conditional"] is False


# ==========================================================================
# Compose topology / image+service status / cc_runner_available / forced-CC
# ==========================================================================

def test_compose_topology_missing_dmac_cc_net_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    compose_config = json.loads((bundle / "compose_config.json").read_text())
    del compose_config["networks"]["dmac-cc-net"]
    (bundle / "compose_config.json").write_text(json.dumps(compose_config), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["compose_topology_recorded"] is False


def test_compose_topology_legacy_srv_dmac_bind_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    compose_config = json.loads((bundle / "compose_config.json").read_text())
    compose_config["services"]["nextseek"]["volumes"] = ["/srv/dmac/users:/dmac/users"]
    (bundle / "compose_config.json").write_text(json.dumps(compose_config), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["compose_topology_recorded"] is False


def test_image_service_status_missing_images_json_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "images.json").unlink()

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["image_service_status_recorded"] is False


def test_cc_runner_available_false_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "cc_runner_available.json").write_text(json.dumps([False, "docker daemon unreachable"]))

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["cc_runner_available_ok"] is False


def test_forced_cc_is_error_true_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    result = json.loads((bundle / "forced_cc_result.json").read_text())
    result["is_error"] = True
    (bundle / "forced_cc_result.json").write_text(json.dumps(result), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["forced_cc_success"] is False


# ==========================================================================
# forced_cc_result.json.cost <= meta.json.budget_cap_usd (default 2.0)
# ==========================================================================

def test_cost_exactly_at_default_cap_passes(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, aux_overrides={"cost": 2.0})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["forced_cc_cost_within_budget"] is True
    assert all_ok, checks


def test_cost_over_default_cap_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, aux_overrides={"cost": 2.01})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["forced_cc_cost_within_budget"] is False


def test_cost_over_custom_budget_cap_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, aux_overrides={"cost": 0.6},
                 meta_overrides={"budget_cap_usd": 0.5})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["forced_cc_cost_within_budget"] is False


def test_cost_zero_without_exception_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, aux_overrides={"cost": 0.0})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["forced_cc_cost_positive_unless_zero_cost_exception"] is False


def test_cost_zero_with_documented_exception_passes(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, aux_overrides={"cost": 0.0},
                 meta_overrides={"zero_cost_exception": True})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["forced_cc_cost_positive_unless_zero_cost_exception"] is True
    assert all_ok, checks


# ==========================================================================
# forced_cc_result.json.run_id == meta.json.run_id
# ==========================================================================

def test_forced_cc_run_id_matches_meta_passes(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["forced_cc_result_run_id_matches_meta"] is True
    assert all_ok, checks


def test_forced_cc_run_id_mismatch_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    result = json.loads((bundle / "forced_cc_result.json").read_text())
    result["run_id"] = "some-other-run-id"
    (bundle / "forced_cc_result.json").write_text(json.dumps(result), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["forced_cc_result_run_id_matches_meta"] is False


# ==========================================================================
# proxy_invoke_recorded: pinned to the allowed Opus model id (Task 16 debt
# fix -- the pre-fix `_INVOKE_200_GENERIC_RE` accepted an invoke-200 for ANY
# model id, not just the one the bedrock-proxy allowlist actually permits).
# ==========================================================================

def test_proxy_invoke_recorded_passes_for_the_allowed_opus_model(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["proxy_invoke_recorded"] is True
    assert all_ok, checks


def test_proxy_invoke_recorded_rejects_non_allowed_model_id(tmp_path):
    """A well-formed invoke->200 line for a model OTHER than the pinned
    allowed Opus id must NOT satisfy this check -- a generic any-model regex
    would wrongly accept it (the exact pre-fix behavior this test guards
    against)."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "proxy_log_window.txt").write_text(
        f"[2026-07-01T12:00:00Z] run_id={RUN_ID} "
        f"POST /model/us.anthropic.claude-sonnet-4-5/invoke -> 200\n",
        encoding="utf-8",
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["proxy_invoke_recorded"] is False


# ==========================================================================
# Cross-artifact correlation: run_id in proxy log; agent container in
# network_inspect.json
# ==========================================================================

def test_cross_artifact_run_id_missing_from_proxy_log_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "proxy_log_window.txt").write_text(
        "POST /model/us.anthropic.claude-sonnet-4-5/invoke -> 200\n", encoding="utf-8"
    )  # no run_id anywhere

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["cross_artifact_run_id_in_proxy_log"] is False


def test_cross_artifact_agent_container_missing_from_network_inspect_fails(tmp_path):
    """A genuine (well-formed, Task-15-shape) network_inspect.json whose
    containers simply do NOT include the bundle's own agent name must fail --
    not merely a malformed-shape artifact."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(
        bundle, tracker, deploy_commit=sha,
        aux_overrides={"network_inspect_containers": ["dmac-bedrock-proxy"]},
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["cross_artifact_agent_container_in_network_inspect"] is False


def test_cross_artifact_agent_container_malformed_network_inspect_shape_fails(tmp_path):
    """The pre-Task-15 ``{"containers": [<name>, ...]}`` list-of-strings shape
    is no longer accepted at all (fail-closed on the wrong shape)."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "network_inspect.json").write_text(json.dumps({"containers": ["bedrock-proxy"]}), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["cross_artifact_agent_container_in_network_inspect"] is False
    assert _names(checks)["network_segmentation_ok"] is False


# ==========================================================================
# Network segmentation / agent env de-credentialing / proxy token logging
# ==========================================================================

def test_network_segmentation_backend_peer_present_fails(tmp_path):
    """A genuine (well-formed) network_inspect.json carrying a real backend
    peer (neo4j) alongside the legitimate agent must fail."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(
        bundle, tracker, deploy_commit=sha,
        aux_overrides={"network_inspect_containers": [f"dmac-cc-agent-{RUN_ID}", "neo4j"]},
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["network_segmentation_ok"] is False


def test_agent_env_shared_cred_key_leaked_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "agent_env_scan.txt").write_text("NEO4J_PASSWORD=FAKE-NOT-REAL-1234\n", encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["agent_env_decredentialed"] is False


def test_proxy_token_logged_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "proxy_log_window.txt").write_text(
        f"run_id={RUN_ID} Authorization: Bearer ABSK-FAKE-NOT-REAL\n", encoding="utf-8"
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["proxy_token_not_logged"] is False


# ==========================================================================
# migration_policy conditionality
# ==========================================================================

def test_migration_policy_required_when_dev_vm_had_host_bind_data(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="dev-vm",
                 had_host_bind_data=True, migration_policy=None)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["migration_policy_conditionality"] is False


def test_migration_policy_present_when_dev_vm_had_host_bind_data_passes(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="dev-vm",
                 had_host_bind_data=True, migration_policy="one-time-copy-then-wipe")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["migration_policy_conditionality"] is True
    assert all_ok, checks


def test_migration_policy_optional_on_greenfield_dev_vm(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="dev-vm",
                 had_host_bind_data=False, migration_policy=None)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["migration_policy_conditionality"] is True
    assert all_ok, checks


def test_migration_policy_forbidden_on_mbp(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="mbp",
                 migration_policy="some-policy",
                 aux_overrides={"pre_bootstrap": True})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["migration_policy_conditionality"] is False


def test_migration_policy_absent_on_mbp_passes(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="mbp",
                 migration_policy=None, aux_overrides={"pre_bootstrap": True})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["migration_policy_conditionality"] is True
    assert all_ok, checks


# ==========================================================================
# pre_turn_seed_scan.txt / subpath_isolation_scan.txt paired oracle
# ==========================================================================

def test_pre_turn_seed_scan_missing_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "pre_turn_seed_scan.txt").unlink()

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["pre_turn_seed_scan_contains_foreign_tokens"] is False


def test_pre_turn_seed_scan_missing_a_foreign_token_fails(tmp_path):
    """An unseeded volume (or a partially-seeded one) must turn this scan
    RED before the turn -- this is what stops an unseeded whole-volume leak
    from passing green via a vacuous in-turn foreign-absent check."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "pre_turn_seed_scan.txt").write_text("/v/otherproj/input/nope\n", encoding="utf-8")  # no "bob", no SENTINEL_FOREIGN

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["pre_turn_seed_scan_contains_foreign_tokens"] is False


def test_subpath_isolation_scan_missing_own_marker_fails(tmp_path):
    """A hand-written empty stub (missing the own marker) must fail."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "subpath_isolation_scan.txt").write_text(f"/data/scratch/{LIVE_SENTINEL}\n", encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["subpath_isolation_scan_valid"] is False


def test_subpath_isolation_scan_missing_live_sentinel_fails(tmp_path):
    """A stale/clean scan reused from a different run (no live sentinel) must
    fail the anti-substitution binding."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "subpath_isolation_scan.txt").write_text(f"/data/input/{OWN_MARKER}/foo\n", encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["subpath_isolation_scan_valid"] is False


@pytest.mark.parametrize("leak_line", [
    "/v/otherproj/bob/input/SENTINEL_FOREIGN",
    "/v/ otherproj /somewhere",
    "/v/ bob /somewhere",
])
def test_subpath_isolation_scan_foreign_token_present_fails(tmp_path, leak_line):
    """The REQUIRED cross-user isolation gate: any foreign token/path in the
    in-turn scan (a whole-volume Subpath="" leak) must fail, even alongside
    the correct own_marker/live_sentinel."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "subpath_isolation_scan.txt").write_text(
        f"/data/input/{OWN_MARKER}/foo\n/data/scratch/{LIVE_SENTINEL}\n{leak_line}\n", encoding="utf-8"
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["subpath_isolation_scan_valid"] is False


def test_old_non_recursive_ls_style_scan_without_own_marker_or_sentinel_fails(tmp_path):
    """The old non-recursive `ls` + slash-bearing matcher is rejected: a
    top-level-only listing (no own_marker, no live_sentinel) must fail even
    though it shows no foreign token either."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "subpath_isolation_scan.txt").write_text("proj1\nproj2\n", encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["subpath_isolation_scan_valid"] is False


def test_meta_tokens_not_pairwise_disjoint_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha, meta_overrides={"own_marker": "bob"})

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["meta_tokens_pairwise_disjoint"] is False


def test_meta_tokens_pairwise_disjoint_passes_on_clean_bundle(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["meta_tokens_pairwise_disjoint"] is True
    assert all_ok, checks


# ==========================================================================
# Legacy filename rejection
# ==========================================================================

@pytest.mark.parametrize("legacy_name", ["forced_result.json", "proxy_log.txt", "network.json"])
def test_legacy_filename_anywhere_in_bundle_rejected(tmp_path, legacy_name):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / legacy_name).write_text("legacy artifact\n", encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["no_legacy_artifact_filenames"] is False


def test_legacy_filename_nested_subdir_still_rejected(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    nested = bundle / "old" / "sub"
    nested.mkdir(parents=True)
    (nested / "network.json").write_text("{}", encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["no_legacy_artifact_filenames"] is False


def test_no_legacy_filenames_passes_on_clean_bundle(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["no_legacy_artifact_filenames"] is True
    assert all_ok, checks


# ==========================================================================
# Secret scan negative controls (SPEC-7 section 9 / Task 2 brief Step 3)
# ==========================================================================

@pytest.mark.parametrize("secret_line", [
    "AWS_BEARER_TOKEN_BEDROCK=FAKE-NOT-REAL-abcdef123456",
    "Authorization: Bearer FAKE-NOT-REAL-token\n",
    "token=ABSK-FAKE-NOT-REAL-xyz\n",
    "GCP_API_KEY=FAKE-NOT-REAL-gcpkey123\n",
    "MYSQL_PASSWORD=FAKE-NOT-REAL-mysqlpw\n",
    "NEO4J_PASSWORD=FAKE-NOT-REAL-neo4jpw\n",
    "logged in as demo/demopassword\n",
    "SECRET_KEY = 'django-insecure-FAKE-NOT-REAL-0123456789abcdef'\n",
    "NEXTSEEK_PASSWORD=FAKE-NOT-REAL-plaintext\n",
])
def test_secret_scan_negative_controls_fail(tmp_path, secret_line):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "extra_note.txt").write_text(secret_line, encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["secret_scan_clean"] is False


def test_nextseek_password_redacted_value_passes(tmp_path):
    """The documented exception: only the logged-in user's own credential,
    value masked -- NEXTSEEK_PASSWORD=*** must NOT trip the scanner."""
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "extra_note.txt").write_text("NEXTSEEK_PASSWORD=***REDACTED***\n", encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["secret_scan_clean"] is True
    assert all_ok, checks


def test_secret_scan_clean_on_default_bundle(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["secret_scan_clean"] is True
    assert _names(checks)["secret_scan_report_present"] is True
    assert all_ok, checks


def test_secret_scan_report_missing_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "secret_scan_report.json").unlink()

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["secret_scan_report_present"] is False


# ==========================================================================
# Screenshot review requirement
# ==========================================================================

def _write_fake_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\nfake-not-a-real-png")


def test_no_screenshots_no_review_required(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["screenshot_review_recorded"] is True
    assert all_ok, checks


def test_screenshot_without_any_review_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    _write_fake_png(bundle / "step7_ui.png")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["screenshot_review_recorded"] is False


def test_screenshot_with_ocr_review_recorded_passes(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    _write_fake_png(bundle / "step7_ui.png")
    (bundle / "secret_scan_report.json").write_text(json.dumps({
        "clean": True,
        "screenshots": {"step7_ui.png": {"method": "ocr", "result": "clean"}},
    }), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["screenshot_review_recorded"] is True
    assert all_ok, checks


def test_screenshot_with_manual_review_recorded_passes(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    _write_fake_png(bundle / "step7_ui.png")
    (bundle / "secret_scan_report.json").write_text(json.dumps({
        "clean": True,
        "screenshots": {"step7_ui.png": {"method": "manual_review", "reviewer": "taishajo"}},
    }), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["screenshot_review_recorded"] is True
    assert all_ok, checks


def test_screenshot_review_entry_for_wrong_file_still_fails(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    _write_fake_png(bundle / "step7_ui.png")
    (bundle / "secret_scan_report.json").write_text(json.dumps({
        "clean": True,
        "screenshots": {"a_different_screenshot.png": {"method": "ocr"}},
    }), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert not all_ok
    assert _names(checks)["screenshot_review_recorded"] is False


# ==========================================================================
# Markdown-only evidence rejection
# ==========================================================================

def test_markdown_only_bundle_rejected(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "README.md").write_text("# Step 7 evidence\n\nSee reproduction steps.\n", encoding="utf-8")
    (bundle / "notes.md").write_text("some notes\n", encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=tmp_path)

    assert not all_ok
    assert _names(checks)["not_markdown_only_bundle"] is False


def test_bundle_with_non_markdown_evidence_not_rejected_by_markdown_check(tmp_path):
    repo, bundle, tracker, sha = _bundle(tmp_path)
    _full_bundle(bundle, tracker, deploy_commit=sha)
    (bundle / "README.md").write_text("# notes\n", encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)
    assert _names(checks)["not_markdown_only_bundle"] is True
    assert all_ok, checks

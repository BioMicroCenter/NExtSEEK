"""Hermetic tests for the Step 7 (compose-native deploy) preflight collector
and its ``validate_step7_compose_deploy`` gate.

Purpose (PLAN-7 Task 1): prove, over synthetic bundles only (no docker socket,
no network), that the preflight guard actually catches stale planning-session
file state before Step 7 ever touches the running stack.

No Docker and no network are used for the preflight/validator suite above.
Docker facts are injected via a fake ``DockerProbe``; collector git facts via
a fake ``GitProbe``. The validator's independent transcript re-check IS
exercised against real throwaway git repos built under ``tmp_path`` (git is
available in the hermetic test container; ``git init/add/commit`` needs no
network), because the whole point of that check is that a hand-edited
``preflight.json`` cannot forge it.

PLAN-7 Task 5 (compose-native topology) ADDS a second suite at the bottom of
this file that DOES shell out to the real ``docker compose ... config`` CLI
(client-side only -- no daemon socket, no network egress -- see the harness
mount note in ``.superpowers/sdd/progress.md``) against a throwaway tmp-path
copy of the repo's actual ``docker-compose.yml``, plus hermetic
(no-docker-module-required) tests for ``cc_engine.cc_runner_available()``'s
detail strings.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.tests.step7_preflight_collector import (
    CC_ENV_KEYS,
    DOCKER_API_SUBPATH_FLOOR,
    DOCKER_ENGINE_SUBPATH_FLOOR,
    DockerProbe,
    GitProbe,
    LIVE_GATE_TRANSCRIPT_REL,
    _deploy_md_has_old_bootstrap,
    _parse_cc_env_keys,
    _parse_compose,
    collect_preflight,
    compose_meets_subpath_floor,
    engine_meets_subpath_floor,
    read_tracker_step3_status,
    resolve_integration_plan_path,
    sha256_file,
)
from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (
    LIVE_EVIDENCE_PATH_LITERAL,
    main as validator_main,
    validate_run,
)

PORT_SOURCE_COMMIT = "b" * 40
# Byte-identical allowlist markers (PLAN-3 Task 13 Step 8 / Task 2 brief): the
# migration marker (fresh-migrate stdout form), `cc_assistant.upload`
# (registered-task name), `cc_traces` (Step 6 GET …?include=turns JSON key).
TRANSCRIPT_CONTENT = (
    b"Applying nextseek_api.0007_ccsessiontranscript... OK\n"
    b"cc_assistant.upload\n"
    b'{"cc_traces": [{"turn": 1}]}\n'
)

# --------------------------------------------------------------------------
# Task 2: a comprehensive, all-artifacts-present bundle builder. Task 1's
# `_pass_bundle` only wrote preflight.json (+ a minimal meta.json via
# `_write_meta`) because Task 1 only implemented the preflight/step3_deploy_gate
# checks. Task 2 extends CHECKS with ~30 more checks spanning the rest of the
# SPEC-7 section-8 bundle, so a bundle that should now fully pass
# (`all_ok is True`) must carry every one of those artifacts too.
# --------------------------------------------------------------------------

RUN_ID = "run-20260701-0001"
OWN_MARKER = f"OWN_{RUN_ID}"
LIVE_SENTINEL = "LIVE_SENT-feed1234"
FOREIGN_TOKENS = ["SENTINEL_FOREIGN", "otherproj", "bob"]


def _write_meta_full(bundle_dir: Path, *, run_id: str = RUN_ID, repo_commit: str,
                      host_label: str = "dev-vm", budget_cap_usd: float = 2.0,
                      own_marker: str = OWN_MARKER, live_sentinel: str = LIVE_SENTINEL,
                      foreign_tokens: list[str] | None = None,
                      migration_policy: str | None = None,
                      greenfield_exception: bool = False,
                      greenfield_exception_handoff_path: str | None = None,
                      zero_cost_exception: bool = False,
                      **extra) -> None:
    meta = {
        "run_id": run_id,
        "repo_commit": repo_commit,
        "repo_branch": "cc-step7-compose-native",
        "host_label": host_label,
        "timestamp": "2026-07-01T12:00:00Z",
        "verifier_version": "1",
        "budget_cap_usd": budget_cap_usd,
        "foreign_tokens": FOREIGN_TOKENS if foreign_tokens is None else foreign_tokens,
        "own_marker": own_marker,
        "live_sentinel": live_sentinel,
        "migration_policy": migration_policy,
        "greenfield_exception": greenfield_exception,
        "greenfield_exception_handoff_path": greenfield_exception_handoff_path,
        "zero_cost_exception": zero_cost_exception,
    }
    meta.update(extra)
    (bundle_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _write_auxiliary_artifacts(bundle_dir: Path, *, run_id: str = RUN_ID,
                                own_marker: str = OWN_MARKER, live_sentinel: str = LIVE_SENTINEL,
                                foreign_tokens: list[str] | None = None,
                                cost: float = 0.05, is_error: bool = False,
                                sentinel: str = LIVE_SENTINEL,
                                pre_bootstrap: bool = False,
                                pre_existing_volume_names: list[str] | None = None,
                                pre_existing_network_names: list[str] | None = None,
                                network_inspect_containers: list[str] | None = None) -> None:
    """Write every SPEC-7 section 8 artifact Task 2 checks beyond preflight.json
    + meta.json, with values that make a clean, fully-passing bundle."""
    foreign_tokens = FOREIGN_TOKENS if foreign_tokens is None else foreign_tokens
    bundle_dir.mkdir(parents=True, exist_ok=True)

    compose_config = {
        "services": {
            "nextseek": {"image": "nextseek:dev"},
            "nextseek_nginx": {"image": "nginx:1"},
            "bedrock-proxy": {"image": "bedrock-proxy:dev"},
        },
        "networks": {"dmac-cc-net": {"driver": "bridge"}},
        "volumes": {"dmac-cc-users": {"external": True}, "seek-filestore": {"external": True}},
    }
    (bundle_dir / "compose_config.json").write_text(json.dumps(compose_config), encoding="utf-8")
    (bundle_dir / "compose_services.txt").write_text(
        "nextseek       Up 2 minutes\nbedrock-proxy  Up 2 minutes\n", encoding="utf-8"
    )
    (bundle_dir / "docker_ps.txt").write_text(
        "CONTAINER ID   IMAGE           STATUS\nabc123         nextseek:dev    Up 2 minutes\n", encoding="utf-8"
    )
    (bundle_dir / "images.json").write_text(
        json.dumps({"nextseek": "nextseek:dev", "bedrock-proxy": "bedrock-proxy:dev"}), encoding="utf-8"
    )
    containers = (
        [f"cc-agent-{run_id}", "bedrock-proxy"]
        if network_inspect_containers is None else network_inspect_containers
    )
    (bundle_dir / "network_inspect.json").write_text(
        json.dumps({"containers": containers}), encoding="utf-8"
    )
    (bundle_dir / "cc_runner_available.json").write_text(json.dumps([True, "ok"]), encoding="utf-8")
    (bundle_dir / "forced_cc_result.json").write_text(json.dumps({
        "run_id": run_id, "is_error": is_error, "sentinel": sentinel, "cost": cost,
    }), encoding="utf-8")
    (bundle_dir / "proxy_log_window.txt").write_text(
        f"[2026-07-01T12:00:00Z] run_id={run_id} "
        f"POST /model/us.anthropic.claude-sonnet-4-5/invoke -> 200\n",
        encoding="utf-8",
    )
    (bundle_dir / "agent_env_scan.txt").write_text(
        "NEXTSEEK_CC_IMAGE=nextseek-cc:dev\nHOME=/home/agent\nPATH=/usr/bin\n", encoding="utf-8"
    )
    (bundle_dir / "pre_turn_seed_scan.txt").write_text(
        "/v/otherproj/bob/input/SENTINEL_FOREIGN\n", encoding="utf-8"
    )
    (bundle_dir / "subpath_isolation_scan.txt").write_text(
        f"/data/input/{own_marker}/foo.txt\n/data/scratch/{live_sentinel}\n", encoding="utf-8"
    )
    (bundle_dir / "secret_scan_report.json").write_text(
        json.dumps({"clean": True, "screenshots": {}}), encoding="utf-8"
    )
    if pre_bootstrap:
        vol_lines = ["DRIVER    VOLUME NAME"] + [f"local     {n}" for n in (pre_existing_volume_names or [])]
        (bundle_dir / "pre_bootstrap_docker_volume_ls.txt").write_text(
            "\n".join(vol_lines) + "\n", encoding="utf-8"
        )
        net_lines = ["NETWORK ID     NAME      DRIVER"] + [
            f"abc123         {n}       bridge" for n in (pre_existing_network_names or [])
        ]
        (bundle_dir / "pre_bootstrap_docker_network_ls.txt").write_text(
            "\n".join(net_lines) + "\n", encoding="utf-8"
        )


def _full_bundle(bundle_dir: Path, tracker_path: Path, deploy_commit: str,
                  *, host_label: str = "dev-vm", run_id: str = RUN_ID,
                  had_host_bind_data: bool = False, migration_policy: str | None = None,
                  gate_overrides: dict | None = None, meta_overrides: dict | None = None,
                  aux_overrides: dict | None = None) -> dict:
    """The one-stop "clean bundle" builder for Task 2: preflight.json (via
    `_pass_bundle`, with a realistic multi-line docker_version_summary so the
    independent Engine/API floor parse passes) + meta.json + every auxiliary
    section-8 artifact. Returns the written preflight dict."""
    gate_overrides = dict(gate_overrides or {})
    gate_overrides.setdefault("had_host_bind_data", had_host_bind_data)
    # `deploy_commit` is also a positional param of this function (used below
    # for meta.json.repo_commit and the bundle's real git commit); allow
    # gate_overrides to independently override just the *recorded* gate value
    # (e.g. to test a malformed deploy_commit) without a duplicate-keyword
    # crash against the positional arg.
    pass_bundle_deploy_commit = gate_overrides.pop("deploy_commit", deploy_commit)
    preflight = _pass_bundle(bundle_dir, tracker_path, pass_bundle_deploy_commit, **gate_overrides)
    preflight["docker_version_summary"] = (
        "Docker version 27.0.0, build abc123\n"
        "Engine:\n Version: 27.0.0\n API version: 1.47 (minimum version 1.24)\n"
    )
    (bundle_dir / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")

    meta_kwargs = {"run_id": run_id, "repo_commit": deploy_commit, "host_label": host_label,
                   "migration_policy": migration_policy}
    meta_kwargs.update(meta_overrides or {})
    _write_meta_full(bundle_dir, **meta_kwargs)
    _write_auxiliary_artifacts(bundle_dir, run_id=run_id, **(aux_overrides or {}))
    return preflight


# --------------------------------------------------------------------------
# Real throwaway git repos (tmp_path; no network, no global config needed)
# --------------------------------------------------------------------------

def _git(repo_dir: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir),
         "-c", "user.email=test@test", "-c", "user.name=test",
         *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def _make_git_repo(root: Path, files: dict[str, bytes]) -> tuple[Path, str]:
    """git init a repo at root/gitrepo with `files` committed; return (repo, HEAD sha)."""
    repo = root / "gitrepo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")
    return repo, _git(repo, "rev-parse", "HEAD")


def _repo_with_transcript(tmp_path: Path, content: bytes = TRANSCRIPT_CONTENT) -> tuple[Path, str]:
    return _make_git_repo(tmp_path, {LIVE_GATE_TRANSCRIPT_REL: content})


# --------------------------------------------------------------------------
# Synthetic bundle builders
# --------------------------------------------------------------------------

def _mk_tracker(status: str = "done") -> dict:
    return {"steps": [{"id": "3", "name": "UI-based I/O", "status": status, "substeps": []}]}


def _write_tracker(path: Path, status: str = "done") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_mk_tracker(status)), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pass_bundle(bundle_dir: Path, tracker_path: Path, deploy_commit: str,
                 **gate_overrides) -> dict:
    """Write a synthetic clean preflight.json bundle. Returns the dict written."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    tracker_sha = _write_tracker(tracker_path, "done")

    gate = {
        "integration_plan_path": str(tracker_path),
        "tracker_step3_status": "done",
        "integration_plan_sha256": tracker_sha,
        "canonical_integration_plan_sha256": tracker_sha,
        "live_gate_transcript_committed": True,
        "deploy_commit": deploy_commit,
        # None by default: check_supplementary_handoff_valid treats an absent
        # handoff as trivially fine (transcript commit is the hard gate; the
        # handoff is supplementary). Tests exercising a *recorded* handoff
        # path set this explicitly.
        "user_signoff_handoff_path": None,
        "live_evidence_path": LIVE_EVIDENCE_PATH_LITERAL,
        "pre_step3_snapshot_tag": "nextseek-nextseek:pre-step3",
        "docker_engine_meets_subpath_floor": True,
        "docker_compose_meets_subpath_floor": True,
        "port_source_path": "/home/x/work/dmac-assistant",
        "port_source_commit": PORT_SOURCE_COMMIT,
        "had_host_bind_data": False,
    }
    gate.update(gate_overrides)

    preflight = {
        "branch": "cc-step7-compose-native",
        "commit": deploy_commit,
        "dirty": False,
        "file_hashes": {
            "docker-compose.yml": {"path": "docker-compose.yml", "exists": True, "sha256": "1" * 64},
            "docker/nextseek.env.example": {
                "path": "docker/nextseek.env.example", "exists": True, "sha256": "2" * 64,
            },
            "DEPLOY.md": {"path": "nextseek_api/cc_assistant/DEPLOY.md", "exists": True, "sha256": "3" * 64},
        },
        "compose_services": ["nextseek", "nextseek_nginx", "db", "neo4j"],
        "compose_networks": [],
        "cc_env_keys": [],
        "deploy_md_has_old_bootstrap": True,
        "docker_version_summary": "Docker version 27.0.0, build abc123",
        "docker_info_summary": "Server Version: 27.0.0",
        "docker_compose_version": "Docker Compose version v2.29.1",
        "step3_deploy_gate": gate,
    }
    (bundle_dir / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")
    return preflight


def _write_meta(bundle_dir: Path, host_label: str = "linux-dev-vm") -> None:
    (bundle_dir / "meta.json").write_text(json.dumps({"host_label": host_label}), encoding="utf-8")


def _names(checks):
    return {name: ok for name, ok, _ in checks}


ALL_CHECK_NAMES = {
    "preflight_json_present", "branch_and_commit_recorded", "required_file_hashes_present",
    "step3_gate_fields_present", "tracker_path_not_arbitrary", "tracker_step3_done",
    "live_evidence_path_literal", "live_gate_transcript_committed",
    # Task 2 additions:
    "deploy_commit_format_valid", "deploy_commit_matches_meta_repo_commit",
    "transcript_migration_marker_present", "transcript_cc_upload_marker_present",
    "transcript_cc_traces_marker_present", "supplementary_handoff_valid",
    "host_label_enum_valid", "mbp_pre_bootstrap_volumes_absent", "mbp_pre_bootstrap_network_absent",
    "docker_engine_floor_independent", "docker_compose_floor_conditional",
    "compose_topology_recorded", "image_service_status_recorded", "cc_runner_available_ok",
    "forced_cc_success", "forced_cc_cost_within_budget",
    "forced_cc_cost_positive_unless_zero_cost_exception", "forced_cc_result_run_id_matches_meta",
    "proxy_invoke_recorded", "network_segmentation_ok", "agent_env_decredentialed",
    "proxy_token_not_logged", "cross_artifact_run_id_in_proxy_log",
    "cross_artifact_agent_container_in_network_inspect", "migration_policy_conditionality",
    "pre_turn_seed_scan_contains_foreign_tokens", "subpath_isolation_scan_valid",
    "foreign_tokens_canonical_set",
    "meta_tokens_pairwise_disjoint", "no_legacy_artifact_filenames", "secret_scan_report_present",
    "secret_scan_clean", "screenshot_review_recorded", "not_markdown_only_bundle",
}


# --------------------------------------------------------------------------
# Validator: happy path
# --------------------------------------------------------------------------

def test_clean_bundle_passes(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)

    failed = [c for c in checks if not c[1]]
    assert all_ok, f"expected all pass, failed: {failed}"
    assert set(n for n, _, _ in checks) == ALL_CHECK_NAMES
    assert len(checks) == len(ALL_CHECK_NAMES)  # all named checks distinct


def test_clean_bundle_without_meta_json_fails_only_task2_checks_that_need_it(tmp_path):
    """meta.json was optional in the Task-1-only preflight/step3_deploy_gate
    subset (the original 8 checks below still degrade gracefully to False
    rather than raising), but Task 2 adds many checks (host_label enum,
    run_id correlation, cost cap, isolation-scan oracle, …) that legitimately
    require it -- so the FULL bundle can no longer pass without meta.json.
    This replaces the old `test_clean_bundle_passes_even_without_meta_json`."""
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit=sha)
    # no meta.json written at all

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    d = _names(checks)
    # the original Task-1 checks still evaluate cleanly (no crash) and pass,
    # since none of them require meta.json in the non-MBP path:
    for name in ("preflight_json_present", "branch_and_commit_recorded",
                 "required_file_hashes_present", "step3_gate_fields_present",
                 "deploy_commit_format_valid", "tracker_path_not_arbitrary",
                 "tracker_step3_done", "live_evidence_path_literal",
                 "live_gate_transcript_committed"):
        assert d[name] is True, f"{name} should still pass without meta.json"
    # but Task 2's meta.json-dependent checks correctly fail rather than crash:
    assert d["host_label_enum_valid"] is False
    assert d["deploy_commit_matches_meta_repo_commit"] is False


# --------------------------------------------------------------------------
# Validator: missing / hand-truncated preflight.json
# --------------------------------------------------------------------------

def test_missing_preflight_json_fails(tmp_path):
    repo, _sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    d = _names(checks)
    assert d["preflight_json_present"] is False
    # every other check must degrade gracefully (no exception) rather than crash
    assert set(d) == ALL_CHECK_NAMES


def test_hand_truncated_json_fails(tmp_path):
    repo, _sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    # Simulate a preflight.json cut off mid-write (invalid JSON).
    (bundle / "preflight.json").write_text('{"branch": "cc-step7", "commit": "a1b2', encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["preflight_json_present"] is False


def test_missing_branch_or_commit_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    preflight = _pass_bundle(bundle, tracker, deploy_commit=sha)
    del preflight["branch"]
    (bundle / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["branch_and_commit_recorded"] is False


def test_missing_required_file_hash_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    preflight = _pass_bundle(bundle, tracker, deploy_commit=sha)
    del preflight["file_hashes"]["DEPLOY.md"]
    (bundle / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["required_file_hashes_present"] is False


def test_empty_file_hash_value_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    preflight = _pass_bundle(bundle, tracker, deploy_commit=sha)
    preflight["file_hashes"]["docker-compose.yml"]["sha256"] = None
    (bundle / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["required_file_hashes_present"] is False


# --------------------------------------------------------------------------
# Validator: step3_deploy_gate structural checks
# --------------------------------------------------------------------------

def test_missing_step3_gate_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    preflight = _pass_bundle(bundle, tracker, deploy_commit=sha)
    del preflight["step3_deploy_gate"]
    (bundle / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    d = _names(checks)
    assert d["step3_gate_fields_present"] is False
    assert d["tracker_path_not_arbitrary"] is False
    assert d["tracker_step3_done"] is False
    assert d["live_evidence_path_literal"] is False
    assert d["live_gate_transcript_committed"] is False


def test_hand_truncated_gate_missing_deploy_commit_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    preflight = _pass_bundle(bundle, tracker, deploy_commit=sha)
    del preflight["step3_deploy_gate"]["deploy_commit"]
    (bundle / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    d = _names(checks)
    assert d["step3_gate_fields_present"] is False
    # transcript cannot be re-verified without a deploy_commit
    assert d["live_gate_transcript_committed"] is False


# --------------------------------------------------------------------------
# Validator: the core anti-staleness behavior — tracker is RE-READ live
# --------------------------------------------------------------------------

def test_stale_recorded_done_but_live_tracker_regressed_fails(tmp_path):
    """The whole point of Task 1: a preflight.json that recorded "done" at
    collection time must NOT be trusted if the tracker file has since
    regressed (or was always wrong) — the validator re-reads it live."""
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit=sha)  # gate records "done" (stale claim)
    _write_meta(bundle)

    # Mutate the tracker file after collection to simulate staleness.
    _write_tracker(tracker, "in_progress")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["tracker_step3_done"] is False


def test_recorded_not_done_but_live_tracker_now_done_passes(tmp_path):
    """Symmetric case: even if the collector snapshot recorded a non-"done"
    status, a validation-time re-read of a now-"done" tracker must pass —
    the validator trusts live state, not the recorded string."""
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(bundle, tracker, deploy_commit=sha,
                 gate_overrides={"tracker_step3_status": "in_progress"})
    # tracker file on disk still says "done" (written by _pass_bundle inside _full_bundle)

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert all_ok, checks


def test_tracker_status_not_done_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit=sha)
    _write_tracker(tracker, "blocked")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["tracker_step3_done"] is False


def test_missing_tracker_file_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit=sha)
    tracker.unlink()  # collected, then the tracker vanished

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["tracker_step3_done"] is False


# --------------------------------------------------------------------------
# Validator: live_evidence_path + INDEPENDENT git re-check of the transcript
# --------------------------------------------------------------------------

def test_committed_nonempty_transcript_at_deploy_commit_passes(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path, content=TRANSCRIPT_CONTENT)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert _names(checks)["live_gate_transcript_committed"] is True
    assert all_ok, checks


def test_committed_but_empty_transcript_blob_fails(tmp_path):
    """`git cat-file -e` passes zero-byte blobs; the gate must not."""
    repo, sha = _repo_with_transcript(tmp_path, content=b"")
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["live_gate_transcript_committed"] is False


def test_transcript_absent_from_commit_fails(tmp_path):
    repo, sha = _make_git_repo(tmp_path, {"README.md": b"no transcript here\n"})
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit=sha)

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["live_gate_transcript_committed"] is False


def test_nonexistent_deploy_commit_fails(tmp_path):
    repo, _sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit="c" * 40)  # not a commit in repo

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["live_gate_transcript_committed"] is False


def test_hand_edited_recorded_true_but_git_disagrees_fails(tmp_path):
    """The hand-edit case: a forged preflight.json claiming
    live_gate_transcript_committed=true must NOT sail through — the
    validator re-checks git at deploy_commit regardless of the bool."""
    repo, sha = _make_git_repo(tmp_path, {"README.md": b"transcript never committed\n"})
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit=sha,
                 live_gate_transcript_committed=True)  # forged claim

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["live_gate_transcript_committed"] is False


def test_recorded_bool_false_fails_even_if_git_agrees_transcript_exists(tmp_path):
    """The recorded bool must be the literal True AND git must agree; a
    collector that recorded false is a failed gate even on a good repo."""
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit=sha,
                 live_gate_transcript_committed=False)

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["live_gate_transcript_committed"] is False


def test_recorded_bool_wrong_type_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit=sha,
                 live_gate_transcript_committed="true")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["live_gate_transcript_committed"] is False


def test_wrong_live_evidence_path_literal_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit=sha,
                 live_evidence_path="some/other/evidence/dir/")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["live_evidence_path_literal"] is False


def test_handoff_json_is_not_a_substitute_for_committed_transcript(tmp_path):
    """User decision 2026-06-30: a handoff-only fallback is rejected. Even
    with a plausible-looking user_signoff_handoff_path, an uncommitted
    transcript must still fail the gate."""
    repo, sha = _make_git_repo(tmp_path, {"README.md": b"no transcript\n"})
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _pass_bundle(bundle, tracker, deploy_commit=sha,
                 user_signoff_handoff_path="handoffs/2026-06-30-signoff.json")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["live_gate_transcript_committed"] is False


# --------------------------------------------------------------------------
# Validator: MBP-only in-bundle tracker snapshot exception
# --------------------------------------------------------------------------

def test_tracker_path_inside_bundle_rejected_on_non_mbp_host(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    snapshot = bundle / "integration_plan_snapshot.json"
    _pass_bundle(bundle, snapshot, deploy_commit=sha)  # tracker path INSIDE the bundle
    _write_meta(bundle, host_label="linux-dev-vm")  # not MBP

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    d = _names(checks)
    assert d["tracker_path_not_arbitrary"] is False
    assert d["tracker_step3_done"] is False


def test_tracker_path_inside_bundle_rejected_with_no_meta_json(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    snapshot = bundle / "integration_plan_snapshot.json"
    _pass_bundle(bundle, snapshot, deploy_commit=sha)
    # no meta.json at all -> host_label defaults to "" -> not MBP

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["tracker_path_not_arbitrary"] is False


@pytest.mark.parametrize("host_label", ["taishajo-mbp", "MBP.local", "MacBook-Pro-2.local"])
def test_mbp_snapshot_exception_satisfied_on_quasi_mbp_labels(tmp_path, host_label):
    """Task 1's in-bundle tracker-snapshot exception uses a fuzzy MBP_HOST_LABEL_RE
    ("taishajo-mbp", "MBP.local", etc.) predating Task 2's LOCKED host_label
    enum (exact "mbp" only). Both coexist: these quasi-MBP labels still
    satisfy the narrow snapshot-bypass (the two checks asserted below), but
    the bundle as a whole no longer passes `all_ok` under Task 2's stricter
    host_label_enum_valid check -- that is expected, not a regression."""
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    snapshot = bundle / "integration_plan_snapshot.json"
    _pass_bundle(bundle, snapshot, deploy_commit=sha)
    _write_meta(bundle, host_label=host_label)

    all_ok, checks = validate_run(bundle, repo_root=repo)

    d = _names(checks)
    assert d["tracker_path_not_arbitrary"] is True
    assert d["tracker_step3_done"] is True
    assert d["host_label_enum_valid"] is False  # locked enum requires exact "mbp"


def test_mbp_snapshot_exception_passes_fully_on_exact_mbp_label(tmp_path):
    """The exact locked-enum spelling "mbp" satisfies BOTH the legacy fuzzy
    snapshot exception AND the new locked host_label enum, so (given the
    MBP-required pre-bootstrap scans showing no pre-existing volumes/network)
    the full bundle passes end to end."""
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    snapshot = bundle / "integration_plan_snapshot.json"
    _full_bundle(bundle, snapshot, deploy_commit=sha, host_label="mbp",
                 aux_overrides={"pre_bootstrap": True})

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert all_ok, checks


def test_mbp_snapshot_wrong_basename_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    wrong_name = bundle / "integration-plan-copy.json"  # not the exact required basename
    _pass_bundle(bundle, wrong_name, deploy_commit=sha)
    _write_meta(bundle, host_label="taishajo-mbp")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["tracker_path_not_arbitrary"] is False


def test_mbp_snapshot_missing_canonical_sha_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    snapshot = bundle / "integration_plan_snapshot.json"
    _pass_bundle(bundle, snapshot, deploy_commit=sha,
                 canonical_integration_plan_sha256=None)
    _write_meta(bundle, host_label="taishajo-mbp")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["tracker_path_not_arbitrary"] is False


def test_mbp_snapshot_tampered_sha_mismatch_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    snapshot = bundle / "integration_plan_snapshot.json"
    _pass_bundle(bundle, snapshot, deploy_commit=sha)
    _write_meta(bundle, host_label="taishajo-mbp")

    # Tamper the snapshot AFTER collection without updating integration_plan_sha256.
    snapshot.write_text(json.dumps(_mk_tracker("done")).replace("done", "done "), encoding="utf-8")

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    d = _names(checks)
    assert d["tracker_path_not_arbitrary"] is False
    assert d["tracker_step3_done"] is False


def test_mbp_host_but_tracker_path_outside_bundle_still_validates_normally(tmp_path):
    """MBP exception only *permits* an in-bundle snapshot; it doesn't force
    one. A normal external tracker path must still work on an MBP host
    (exact locked-enum host_label "mbp", with the MBP-required pre-bootstrap
    scans showing no pre-existing volumes/network)."""
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(bundle, tracker, deploy_commit=sha, host_label="mbp",
                 aux_overrides={"pre_bootstrap": True})

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert all_ok, checks


# --------------------------------------------------------------------------
# Final-review Fix 1: exact-name `nextseek` peer detection in
# check_network_segmentation_ok (drifted host with the app container itself
# joined to dmac-cc-net must fail; the legitimately dual-homed nginx entry
# -- both the bare compose service name and the compose-project-prefixed
# runtime form -- must still pass; existing backend-peer stem rejection
# must stay covered).
# --------------------------------------------------------------------------

def test_network_segmentation_fails_when_exact_nextseek_container_attached(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(
        bundle, tracker, deploy_commit=sha,
        aux_overrides={"network_inspect_containers": ["nextseek", "bedrock-proxy", f"cc-agent-{RUN_ID}"]},
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["network_segmentation_ok"] is False


@pytest.mark.parametrize("nginx_name", ["nextseek_nginx", "nextseek-nextseek_nginx-1"])
def test_network_segmentation_passes_for_legitimately_dual_homed_nginx_names(tmp_path, nginx_name):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(
        bundle, tracker, deploy_commit=sha,
        aux_overrides={"network_inspect_containers": [nginx_name, "bedrock-proxy", f"cc-agent-{RUN_ID}"]},
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert _names(checks)["network_segmentation_ok"] is True, checks


@pytest.mark.parametrize("peer_name", ["neo4j", "seek-mysql", "seek", "mysql"])
def test_network_segmentation_still_fails_for_existing_backend_peer_stems(tmp_path, peer_name):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(
        bundle, tracker, deploy_commit=sha,
        aux_overrides={"network_inspect_containers": [peer_name, "bedrock-proxy", f"cc-agent-{RUN_ID}"]},
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["network_segmentation_ok"] is False


# --------------------------------------------------------------------------
# Final-review Fix 2: canonical foreign-token set assertion. The pre-turn
# seed-scan check only proves presence of whatever meta.json.foreign_tokens
# lists; without pinning that list to the canonical set, a harness could
# seed non-canonical tokens and pass pre-turn while a real leak (of the
# actual pinned FOREIGN_TOKEN_GREP_RE tokens) stays invisible in-turn.
# --------------------------------------------------------------------------

def test_foreign_tokens_missing_one_canonical_token_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(
        bundle, tracker, deploy_commit=sha,
        meta_overrides={"foreign_tokens": ["SENTINEL_FOREIGN", "otherproj"]},
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["foreign_tokens_canonical_set"] is False


def test_foreign_tokens_extra_noncanonical_token_fails(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    extra = ["SENTINEL_FOREIGN", "otherproj", "bob", "harness_seeded_token"]
    _full_bundle(
        bundle, tracker, deploy_commit=sha,
        meta_overrides={"foreign_tokens": extra},
    )

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert not all_ok
    assert _names(checks)["foreign_tokens_canonical_set"] is False


def test_foreign_tokens_exact_canonical_set_passes(tmp_path):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(bundle, tracker, deploy_commit=sha)  # default FOREIGN_TOKENS is canonical

    all_ok, checks = validate_run(bundle, repo_root=repo)

    assert _names(checks)["foreign_tokens_canonical_set"] is True, checks


# --------------------------------------------------------------------------
# Validator CLI
# --------------------------------------------------------------------------

def test_cli_main_reports_pass_and_exit_zero(tmp_path, capsys):
    repo, sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    tracker = tmp_path / "tracker" / "integration-plan.json"
    _full_bundle(bundle, tracker, deploy_commit=sha)

    rc = validator_main(["prog", str(bundle), str(repo)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "ALL CHECKS PASSED" in out


def test_cli_main_reports_fail_and_exit_one(tmp_path, capsys):
    repo, _sha = _repo_with_transcript(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    rc = validator_main(["prog", str(bundle), str(repo)])
    out = capsys.readouterr().out

    assert rc == 1
    assert "STEP 7 PREFLIGHT GATE FAILED" in out


def test_cli_main_usage_error(capsys):
    rc = validator_main(["prog"])
    assert rc == 2


# ==========================================================================
# Collector unit tests
# ==========================================================================

def _fake_git(commit: str = "a" * 40, branch="cc-step7-compose-native", dirty=False,
              transcript_size: int | None = len(TRANSCRIPT_CONTENT)) -> GitProbe:
    def cat_file_size(commit_, path_):
        if path_ == LIVE_GATE_TRANSCRIPT_REL and commit_ == commit:
            return transcript_size
        return None
    return GitProbe(branch=branch, commit=commit, dirty=dirty, cat_file_size=cat_file_size)


def _fake_docker() -> DockerProbe:
    return DockerProbe(
        version_summary=(
            "Docker version 27.0.0, build abc123\n"
            "Engine:\n Version: 27.0.0\n API version: 1.47 (minimum version 1.24)\n"
        ),
        info_summary="Server Version: 27.0.0",
        compose_version="Docker Compose version v2.29.1",
        engine_meets_subpath_floor=True,
        compose_meets_subpath_floor=True,
    )


def test_resolve_integration_plan_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("INTEGRATION_PLAN_PATH", "/custom/path/plan.json")
    p = resolve_integration_plan_path(tmp_path / "repo")
    assert str(p) == "/custom/path/plan.json"


def test_resolve_integration_plan_path_default_is_repo_relative(monkeypatch):
    monkeypatch.delenv("INTEGRATION_PLAN_PATH", raising=False)
    repo_root = Path("/home/someuser/work/NExtSEEK")
    p = resolve_integration_plan_path(repo_root)
    assert str(p) == str(repo_root / ".." / "state" / "integration-plan.json")
    # never a baked-in absolute home path other than what was passed in
    assert "/home/taishajo" not in str(p)


def test_read_tracker_step3_status_finds_step_3(tmp_path):
    tracker = tmp_path / "plan.json"
    tracker.write_text(json.dumps({
        "steps": [
            {"id": "0", "status": "done"},
            {"id": "3", "status": "done", "substeps": [{"id": "3a", "status": "done"}]},
            {"id": "4", "status": "not_started"},
        ]
    }), encoding="utf-8")

    assert read_tracker_step3_status(tracker) == "done"


def test_read_tracker_step3_status_missing_file_returns_none(tmp_path):
    assert read_tracker_step3_status(tmp_path / "nope.json") is None


def test_read_tracker_step3_status_malformed_json_returns_none(tmp_path):
    tracker = tmp_path / "plan.json"
    tracker.write_text("{not json", encoding="utf-8")
    assert read_tracker_step3_status(tracker) is None


def test_sha256_file_matches_known_hash(tmp_path):
    f = tmp_path / "x.txt"
    f.write_bytes(b"hello world")
    assert sha256_file(f) == hashlib.sha256(b"hello world").hexdigest()


def test_sha256_file_missing_returns_none(tmp_path):
    assert sha256_file(tmp_path / "nope.txt") is None


# --------------------------------------------------------------------------
# Collector: real Docker Engine/API subpath floor (Task 2 assigned review
# item -- replaces the old (25,0,0)-only placeholder with the plan-pinned
# real floor: Engine >=26 AND API >=v1.45).
# --------------------------------------------------------------------------

def test_engine_subpath_floor_constant_is_the_real_pinned_floor():
    assert DOCKER_ENGINE_SUBPATH_FLOOR == (26, 0, 0)
    assert DOCKER_API_SUBPATH_FLOOR == (1, 45)


def test_engine_meets_subpath_floor_true_when_engine_and_api_both_meet_floor():
    text = "Engine:\n Version: 26.0.0\n API version: 1.45 (minimum version 1.24)\n"
    assert engine_meets_subpath_floor(text) is True


def test_engine_meets_subpath_floor_false_when_engine_below_26_even_with_good_api():
    text = "Engine:\n Version: 25.9.9\n API version: 1.47 (minimum version 1.24)\n"
    assert engine_meets_subpath_floor(text) is False


def test_engine_meets_subpath_floor_false_when_api_below_145_even_with_good_engine():
    text = "Engine:\n Version: 27.0.0\n API version: 1.44 (minimum version 1.24)\n"
    assert engine_meets_subpath_floor(text) is False


def test_engine_meets_subpath_floor_false_when_unparseable():
    assert engine_meets_subpath_floor("<unavailable: docker not found>") is False


def test_compose_meets_subpath_floor_true_at_exact_floor():
    assert compose_meets_subpath_floor("Docker Compose version v2.26.0") is True


def test_compose_meets_subpath_floor_false_below_floor():
    assert compose_meets_subpath_floor("Docker Compose version v2.10.0") is False


def test_parse_compose_services_and_networks(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        "  nextseek:\n"
        "    image: nextseek\n"
        "  db:\n"
        "    image: mysql\n"
        "networks:\n"
        "  dmac-cc-net:\n"
        "    driver: bridge\n",
        encoding="utf-8",
    )
    services, networks = _parse_compose(compose)
    assert services == ["db", "nextseek"]
    assert networks == ["dmac-cc-net"]


def test_parse_compose_missing_file_returns_empty(tmp_path):
    services, networks = _parse_compose(tmp_path / "nope.yml")
    assert services == []
    assert networks == []


def test_parse_cc_env_keys_detects_known_keys(tmp_path):
    env_file = tmp_path / "nextseek.env.example"
    env_file.write_text(
        "SEEK_HOST=seek\n"
        "NEXTSEEK_CC_IMAGE=dmac-assistant:poc\n"
        "DMAC_USER_ROOT=/srv/dmac/users\n",
        encoding="utf-8",
    )
    keys = _parse_cc_env_keys(env_file)
    assert keys == ["NEXTSEEK_CC_IMAGE", "DMAC_USER_ROOT"]
    assert set(keys) <= set(CC_ENV_KEYS)


def test_parse_cc_env_keys_none_present(tmp_path):
    env_file = tmp_path / "nextseek.env.example"
    env_file.write_text("SEEK_HOST=seek\n", encoding="utf-8")
    assert _parse_cc_env_keys(env_file) == []


def test_deploy_md_has_old_bootstrap_detects_marker(tmp_path):
    deploy = tmp_path / "DEPLOY.md"
    deploy.write_text("Phase A\n\n```bash\ndocker network create dmac-cc-net\n```\n", encoding="utf-8")
    assert _deploy_md_has_old_bootstrap(deploy) is True


def test_deploy_md_has_old_bootstrap_false_when_absent(tmp_path):
    deploy = tmp_path / "DEPLOY.md"
    deploy.write_text("Everything is compose-native now.\n", encoding="utf-8")
    assert _deploy_md_has_old_bootstrap(deploy) is False


def test_deploy_md_has_old_bootstrap_false_when_missing_file(tmp_path):
    assert _deploy_md_has_old_bootstrap(tmp_path / "nope.md") is False


# --------------------------------------------------------------------------
# Collector: live_gate_transcript_committed truth table (size-based)
# --------------------------------------------------------------------------

def _build_fake_repo(repo_root: Path) -> None:
    (repo_root / "docker").mkdir(parents=True)
    (repo_root / "nextseek_api" / "cc_assistant").mkdir(parents=True)
    (repo_root / "docker-compose.yml").write_text(
        "services:\n  nextseek:\n    image: nextseek\n  db:\n    image: mysql\n", encoding="utf-8"
    )
    (repo_root / "docker" / "nextseek.env.example").write_text("SEEK_HOST=seek\n", encoding="utf-8")
    (repo_root / "nextseek_api" / "cc_assistant" / "DEPLOY.md").write_text(
        "Phase A\ndocker network create dmac-cc-net\n", encoding="utf-8"
    )
    (repo_root / "nextseek_api" / "cc_assistant" / "SPEC-3-ui-based-io.md").write_text("spec", encoding="utf-8")
    (repo_root / "nextseek_api" / "cc_assistant" / "PLAN-3-ui-based-io.md").write_text("plan", encoding="utf-8")


def _collect(tmp_path, git: GitProbe):
    repo_root = tmp_path / "repo"
    if not repo_root.exists():
        _build_fake_repo(repo_root)
    tracker = tmp_path / "state" / "integration-plan.json"
    _write_tracker(tracker, "done")
    return collect_preflight(
        repo_root=repo_root, git=git, docker=_fake_docker(),
        env={"INTEGRATION_PLAN_PATH": str(tracker)},
        port_source_path="/x", port_source_commit=PORT_SOURCE_COMMIT, had_host_bind_data=False,
    )


def test_collect_preflight_transcript_committed_true_on_nonempty_blob(tmp_path):
    preflight = _collect(tmp_path, _fake_git(transcript_size=57))
    assert preflight["step3_deploy_gate"]["live_gate_transcript_committed"] is True


def test_collect_preflight_transcript_committed_false_on_empty_blob(tmp_path):
    """A committed but ZERO-BYTE transcript must not count as committed
    evidence (`cat-file -e` would pass it; the size probe must not)."""
    preflight = _collect(tmp_path, _fake_git(transcript_size=0))
    assert preflight["step3_deploy_gate"]["live_gate_transcript_committed"] is False


def test_collect_preflight_transcript_committed_false_when_absent(tmp_path):
    preflight = _collect(tmp_path, _fake_git(transcript_size=None))
    assert preflight["step3_deploy_gate"]["live_gate_transcript_committed"] is False


# --------------------------------------------------------------------------
# Collector end-to-end: builds a real preflight.json over a synthetic repo
# tree + real temp git repo, then feeds it straight into the validator
# (Task 1 success condition).
# --------------------------------------------------------------------------

def test_collect_preflight_end_to_end_produces_valid_json_and_passes_validator(tmp_path):
    repo_root = tmp_path / "repo"
    _build_fake_repo(repo_root)
    git_repo, sha = _repo_with_transcript(tmp_path)
    tracker = tmp_path / "state" / "integration-plan.json"
    _write_tracker(tracker, "done")

    preflight = collect_preflight(
        repo_root=repo_root,
        git=_fake_git(commit=sha),
        docker=_fake_docker(),
        env={"INTEGRATION_PLAN_PATH": str(tracker)},
        port_source_path=str(tmp_path / "dmac-assistant"),
        port_source_commit=PORT_SOURCE_COMMIT,
        had_host_bind_data=False,
        pre_step3_snapshot_tag="nextseek-nextseek:pre-step3",
        canonical_integration_plan_sha256=sha256_file(tracker),
    )

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    serialized = json.dumps(preflight)  # must be JSON-serializable
    (bundle / "preflight.json").write_text(serialized, encoding="utf-8")

    # Success condition: valid JSON with all required fields, including
    # hashes for docker-compose.yml, docker/nextseek.env.example, DEPLOY.md,
    # SPEC-3-ui-based-io.md, PLAN-3-ui-based-io.md (all present here).
    reloaded = json.loads(serialized)
    assert reloaded["branch"] == "cc-step7-compose-native"
    assert reloaded["commit"] == sha
    for key in ("docker-compose.yml", "docker/nextseek.env.example", "DEPLOY.md",
                "SPEC-3-ui-based-io.md", "PLAN-3-ui-based-io.md"):
        assert reloaded["file_hashes"][key]["sha256"], key

    gate = reloaded["step3_deploy_gate"]
    assert gate["tracker_step3_status"] == "done"
    assert gate["live_gate_transcript_committed"] is True
    assert gate["deploy_commit"] == sha
    assert gate["integration_plan_path"] == str(tracker)

    # collect_preflight() only produces preflight.json (Task 1's scope); Task
    # 2 extends the validator with checks over the REST of the section-8
    # bundle, so round it out with meta.json + auxiliary artifacts here too.
    _write_meta_full(bundle, repo_commit=sha)
    _write_auxiliary_artifacts(bundle)

    all_ok, checks = validate_run(bundle, repo_root=git_repo)
    assert all_ok, checks


def test_collect_preflight_optional_docs_absent_when_missing(tmp_path):
    repo_root = tmp_path / "repo"
    _build_fake_repo(repo_root)
    (repo_root / "nextseek_api" / "cc_assistant" / "SPEC-3-ui-based-io.md").unlink()
    (repo_root / "nextseek_api" / "cc_assistant" / "PLAN-3-ui-based-io.md").unlink()
    tracker = tmp_path / "state" / "integration-plan.json"
    _write_tracker(tracker, "done")

    preflight = collect_preflight(
        repo_root=repo_root, git=_fake_git(), docker=_fake_docker(),
        env={"INTEGRATION_PLAN_PATH": str(tracker)},
        port_source_path="/x", port_source_commit=PORT_SOURCE_COMMIT, had_host_bind_data=False,
    )

    assert "SPEC-3-ui-based-io.md" not in preflight["file_hashes"]
    assert "PLAN-3-ui-based-io.md" not in preflight["file_hashes"]
    # required ones are still there
    for key in ("docker-compose.yml", "docker/nextseek.env.example", "DEPLOY.md"):
        assert preflight["file_hashes"][key]["sha256"]


def test_collect_preflight_default_integration_plan_path_is_repo_relative(tmp_path, monkeypatch):
    monkeypatch.delenv("INTEGRATION_PLAN_PATH", raising=False)
    repo_root = tmp_path / "repo"
    _build_fake_repo(repo_root)
    # No tracker written anywhere -- default path resolves nowhere, and that
    # must be handled gracefully (None status), never raise.

    preflight = collect_preflight(
        repo_root=repo_root, git=_fake_git(), docker=_fake_docker(),
        port_source_path="/x", port_source_commit=PORT_SOURCE_COMMIT, had_host_bind_data=False,
    )

    gate = preflight["step3_deploy_gate"]
    assert gate["tracker_step3_status"] is None
    assert gate["integration_plan_sha256"] is None
    assert str(repo_root) in gate["integration_plan_path"] or ".." in gate["integration_plan_path"]


# --------------------------------------------------------------------------
# default_git_probe against a REAL temp git repo (git available hermetically)
# --------------------------------------------------------------------------

def test_default_git_probe_reports_real_repo_state_and_blob_sizes(tmp_path):
    from nextseek_api.cc_assistant.tests.step7_preflight_collector import default_git_probe

    repo, sha = _repo_with_transcript(tmp_path, content=TRANSCRIPT_CONTENT)
    probe = default_git_probe(repo)

    assert probe.commit == sha
    assert probe.dirty is False
    assert probe.cat_file_size(sha, LIVE_GATE_TRANSCRIPT_REL) == len(TRANSCRIPT_CONTENT)
    assert probe.cat_file_size(sha, "no/such/file.txt") is None
    assert probe.cat_file_size("f" * 40, LIVE_GATE_TRANSCRIPT_REL) is None

    # dirty detection
    (repo / "scratch.txt").write_text("uncommitted", encoding="utf-8")
    assert default_git_probe(repo).dirty is True


# ==========================================================================
# PLAN-7 Task 5: real ``docker compose -f docker-compose.yml config``
# topology tests. Parses the ACTUAL compose CLI's subprocess output over a
# throwaway tmp-path copy of the repo's real docker-compose.yml -- never a
# hand-edited golden fixture (Task 5 brief mandate).
# ==========================================================================

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


def _write_compose_env_files(tmp_root: Path, repo_root: Path = REPO_ROOT) -> None:
    """Synthesize the gitignored ``env_file:`` targets ``docker compose
    config`` needs to just exist and be parseable. None of their CONTENT is
    topology-relevant except the bedrock-proxy one, whose two keys are seeded
    from the committed ``.example`` placeholder template (empty values, never
    a real token) so the fixture matches a genuinely fresh checkout.

    Deliberately NOT synthesized (verified empirically -- see
    ``_real_compose_config``'s docstring): ``docker/nginx.conf`` and the
    ``docker/cc-runtime/`` / ``docker/bedrock-proxy/`` build-context
    directories. ``docker compose config`` does not validate bind-mount host
    paths or build-context existence, only ``env_file:`` existence.
    """
    (tmp_root / "docker").mkdir(parents=True, exist_ok=True)
    (tmp_root / "docker" / "db.env").write_text("", encoding="utf-8")
    (tmp_root / "docker" / "nextseek.env").write_text("", encoding="utf-8")
    proxy_dir = tmp_root / "docker" / "bedrock-proxy"
    proxy_dir.mkdir(parents=True, exist_ok=True)
    example = repo_root / "docker" / "bedrock-proxy" / "proxy-secret.env.example"
    proxy_secret_text = example.read_text(encoding="utf-8") if example.is_file() else ""
    (proxy_dir / "proxy-secret.env").write_text(proxy_secret_text, encoding="utf-8")


def _real_compose_config(tmp_path: Path, repo_root: Path = REPO_ROOT) -> dict:
    """Run the REAL ``docker compose -f docker-compose.yml config`` subprocess
    against a throwaway copy of the repo's actual ``docker-compose.yml`` and
    return the parsed JSON.

    Never mutates the repo tree and never hand-edits the copied YAML's
    content -- only a byte-for-byte copy of the committed file plus
    synthesized (gitignored-in-the-real-repo) env files live under
    ``tmp_path``. Fails (raises/asserts), never skips, if the ``docker``/
    ``docker compose`` CLI is unavailable or config resolution errors for any
    other reason -- the zero-skip standard for this suite.
    """
    compose_dst = tmp_path / "docker-compose.yml"
    compose_dst.write_bytes((repo_root / "docker-compose.yml").read_bytes())
    _write_compose_env_files(tmp_path, repo_root)

    proc = subprocess.run(
        ["docker", "compose", "-f", str(compose_dst), "config", "--format", "json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"docker compose config failed (rc={proc.returncode}):\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout)


def _expected_cc_image_default() -> str:
    """Mirror ``cc_engine.DEFAULT_IMAGE``'s own computation exactly (same env
    var, same literal fallback) so the alignment check is genuine rather than
    two independently-guessed constants that happen to match today."""
    return os.environ.get("NEXTSEEK_CC_IMAGE", "dmac-assistant:poc")


def test_compose_config_includes_bedrock_proxy_service(tmp_path):
    cfg = _real_compose_config(tmp_path)
    assert "bedrock-proxy" in cfg["services"]


def test_compose_config_bedrock_proxy_container_name_pinned(tmp_path):
    cfg = _real_compose_config(tmp_path)
    assert cfg["services"]["bedrock-proxy"]["container_name"] == "dmac-bedrock-proxy"


def test_compose_config_bedrock_proxy_has_no_host_ports_key(tmp_path):
    """R-8/gate G5: the proxy's :8080 must be reachable only on dmac-cc-net,
    never published to the host."""
    cfg = _real_compose_config(tmp_path)
    assert "ports" not in cfg["services"]["bedrock-proxy"]


def test_compose_config_bedrock_proxy_build_context_is_docker_bedrock_proxy(tmp_path):
    cfg = _real_compose_config(tmp_path)
    ctx = cfg["services"]["bedrock-proxy"]["build"]["context"]
    assert ctx.rstrip("/").endswith("docker/bedrock-proxy")
    assert "cc-runner" not in ctx
    assert "cc-runtime" not in ctx


def test_compose_config_bedrock_proxy_attached_only_to_dmac_cc_net(tmp_path):
    """The proxy must be reachable only on dmac-cc-net -- never the default
    stack network (db/seek/solr/neo4j must not be able to reach it either)."""
    cfg = _real_compose_config(tmp_path)
    nets = set(cfg["services"]["bedrock-proxy"].get("networks") or {})
    assert nets == {"dmac-cc-net"}


def test_compose_config_includes_cc_image_build_target(tmp_path):
    cfg = _real_compose_config(tmp_path)
    assert "cc-agent" in cfg["services"]


def test_compose_config_cc_image_build_context_is_docker_cc_runtime_not_cc_runner(tmp_path):
    """G7-3: the compose CC image build target must point at the canonical
    ``docker/cc-runtime/`` -- never the lean, explicitly non-production
    ``docker/cc-runner/`` proof image."""
    cfg = _real_compose_config(tmp_path)
    ctx = cfg["services"]["cc-agent"]["build"]["context"]
    assert ctx.rstrip("/").endswith("docker/cc-runtime")
    assert "cc-runner" not in ctx


def test_compose_config_cc_image_tag_matches_nextseek_cc_image_default(tmp_path):
    """Required for ``cc_runner_available()``: a fresh ``docker compose
    build`` must tag the image under the exact name ``NEXTSEEK_CC_IMAGE``
    resolves to by default, or the runner-availability probe never finds it."""
    cfg = _real_compose_config(tmp_path)
    assert cfg["services"]["cc-agent"]["image"] == _expected_cc_image_default()


def test_compose_config_cc_agent_has_no_network_attachment(tmp_path):
    """The compose-declared build stanza is never meant to run as a reachable
    service -- it carries no network attachment at all (the real per-turn
    sibling containers join dmac-cc-net via docker-py at spawn time, not
    through this stanza)."""
    cfg = _real_compose_config(tmp_path)
    assert not (cfg["services"]["cc-agent"].get("networks") or {})


def test_compose_config_dmac_cc_net_network_present_with_pinned_literal_name(tmp_path):
    """cc_engine.DEFAULT_NETWORK expects the literal (unprefixed) name
    ``dmac-cc-net`` -- compose's default project-prefixing behavior must be
    overridden via ``networks.dmac-cc-net.name``."""
    cfg = _real_compose_config(tmp_path)
    assert "dmac-cc-net" in cfg["networks"]
    assert cfg["networks"]["dmac-cc-net"]["name"] == "dmac-cc-net"


def test_compose_config_dmac_cc_net_is_compose_managed_not_external(tmp_path):
    """An ``external: true`` network requires a pre-existing manual ``docker
    network create`` before ``up`` -- the primary deploy path must not
    require that."""
    cfg = _real_compose_config(tmp_path)
    assert not cfg["networks"]["dmac-cc-net"].get("external")


def test_compose_config_nginx_dual_homed_default_and_dmac_cc_net(tmp_path):
    cfg = _real_compose_config(tmp_path)
    nets = set(cfg["services"]["nextseek_nginx"].get("networks") or {})
    assert nets == {"default", "dmac-cc-net"}


def test_compose_config_nextseek_itself_not_on_dmac_cc_net(tmp_path):
    """OI-3 segmentation: only ``nextseek_nginx`` is dual-homed. ``nextseek``
    (which holds the Docker socket mount used to spawn CC siblings) must
    never itself gain L3 reach onto the segmented net."""
    cfg = _real_compose_config(tmp_path)
    nets = set(cfg["services"]["nextseek"].get("networks") or {})
    assert "dmac-cc-net" not in nets


@pytest.mark.parametrize("backend_service", ["db", "seek", "seek_workers", "solr", "neo4j"])
def test_compose_config_backend_services_not_on_dmac_cc_net(tmp_path, backend_service):
    cfg = _real_compose_config(tmp_path)
    nets = set(cfg["services"][backend_service].get("networks") or {})
    assert "dmac-cc-net" not in nets


# --------------------------------------------------------------------------
# Task 6 (G7-10): CC user trees in the dmac-cc-users named external volume.
# Real `docker compose config` over the committed YAML — never a hand-edited
# golden fixture.
# --------------------------------------------------------------------------

def _nextseek_volume_mounts(cfg: dict) -> list[dict]:
    return cfg["services"]["nextseek"].get("volumes") or []


def test_compose_config_nextseek_mounts_dmac_cc_users_volume_at_dmac_users(tmp_path):
    cfg = _real_compose_config(tmp_path)
    cc_mounts = [v for v in _nextseek_volume_mounts(cfg)
                 if v.get("target") == "/dmac/users"]
    assert len(cc_mounts) == 1, cc_mounts
    v = cc_mounts[0]
    assert v["type"] == "volume"
    assert v["source"] == "dmac-cc-users"


def test_compose_config_nextseek_has_no_srv_dmac_users_host_bind(tmp_path):
    """Negative guard: the pre-G7-10 host bind `/srv/dmac/users:/dmac/users`
    must never be reintroduced as the primary CC store."""
    cfg = _real_compose_config(tmp_path)
    for v in _nextseek_volume_mounts(cfg):
        assert v.get("source") != "/srv/dmac/users", v
        assert not (v.get("type") == "bind" and v.get("target") == "/dmac/users"), v


def test_compose_config_declares_dmac_cc_users_external_like_seek_filestore(tmp_path):
    cfg = _real_compose_config(tmp_path)
    vols = cfg.get("volumes") or {}
    assert "dmac-cc-users" in vols
    assert vols["dmac-cc-users"].get("external") is True
    # same pattern as the existing six external volumes
    assert vols["seek-filestore"].get("external") is True


def test_compose_yaml_text_never_mentions_srv_dmac_users():
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "/srv/dmac/users" not in text
    assert "dmac-cc-users:/dmac/users" in text


# --------------------------------------------------------------------------
# Task 13 (G7-11): NS shared-cred sidecar compose service. Real
# `docker compose config` over the committed YAML -- never a hand-edited
# golden fixture (Task 5 precedent, carried into Task 13).
# --------------------------------------------------------------------------

SIDECAR_CLIENT_FILE = (
    REPO_ROOT / "docker" / "cc-runtime" / "build_context" / "plugins"
    / "nextseek" / "bin" / "_sidecar_client.py"
)


def _duration_to_seconds(value) -> float:
    """Parse a Go-style duration string (`docker compose config`'s rendering
    of a healthcheck ``start_period``/``interval``/``timeout``, e.g. "20s",
    "1m30s") into total seconds. Also accepts a bare int/float (already
    seconds) for robustness across compose versions."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    total = 0.0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)(h|m|s|ms)", str(value)):
        amount = float(amount)
        total += {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}[unit] * amount
    return total


def test_compose_config_includes_nextseek_sidecar_service(tmp_path):
    cfg = _real_compose_config(tmp_path)
    assert "nextseek-sidecar" in cfg["services"]


def test_compose_config_sidecar_build_context_is_docker_ns_sidecar(tmp_path):
    cfg = _real_compose_config(tmp_path)
    ctx = cfg["services"]["nextseek-sidecar"]["build"]["context"]
    assert ctx.rstrip("/").endswith("docker/ns-sidecar")


def test_compose_config_sidecar_container_name_pinned(tmp_path):
    cfg = _real_compose_config(tmp_path)
    assert cfg["services"]["nextseek-sidecar"]["container_name"] == "nextseek-sidecar"


def test_compose_config_sidecar_service_name_matches_agent_client_default():
    """The compose SERVICE name is the load-bearing identity: whatever
    `_sidecar_client.py`'s NEXTSEEK_SIDECAR_HOST falls back to must be the
    exact compose service key -- that's the Docker DNS alias agents actually
    resolve (`container_name` is a host-side identity only, see the
    bedrock-proxy precedent above)."""
    text = SIDECAR_CLIENT_FILE.read_text(encoding="utf-8")
    m = re.search(r'os\.environ\.get\("NEXTSEEK_SIDECAR_HOST",\s*"([^"]+)"\)', text)
    assert m, "could not find NEXTSEEK_SIDECAR_HOST default in _sidecar_client.py"
    assert m.group(1) == "nextseek-sidecar"


def test_compose_config_sidecar_attached_only_to_dmac_cc_net(tmp_path):
    cfg = _real_compose_config(tmp_path)
    nets = set(cfg["services"]["nextseek-sidecar"].get("networks") or {})
    assert nets == {"dmac-cc-net"}


def test_compose_config_sidecar_has_no_host_ports_key(tmp_path):
    """The WS port must be reachable only inside dmac-cc-net, never published
    to the host."""
    cfg = _real_compose_config(tmp_path)
    assert "ports" not in cfg["services"]["nextseek-sidecar"]


def test_compose_config_sidecar_healthcheck_present_with_cold_start_tolerance(tmp_path):
    """The sidecar's healthcheck GETs through nginx -> Django, so it is
    legitimately unhealthy until the whole stack finishes booting --
    start_period + retries must encode that so Task 9/10 bring-up evidence
    doesn't capture a red-herring 'unhealthy'."""
    cfg = _real_compose_config(tmp_path)
    hc = cfg["services"]["nextseek-sidecar"].get("healthcheck")
    assert hc is not None
    assert hc.get("test")
    assert hc.get("retries", 0) >= 6
    # `docker compose config --format json` renders durations back out as Go
    # duration strings (e.g. "20s", "1m30s"), not nanoseconds.
    assert _duration_to_seconds(hc.get("start_period")) >= 20


def test_compose_config_sidecar_env_carries_only_three_nonsecret_keys(tmp_path):
    cfg = _real_compose_config(tmp_path)
    env = cfg["services"]["nextseek-sidecar"].get("environment") or {}
    assert set(env) == {"NEXTSEEK_BASE_URL", "SIDECAR_STAGING_DIR", "SIDECAR_WS_PORT"}


def test_compose_config_sidecar_base_url_pinned_to_nginx_literal(tmp_path):
    """Do NOT assert equality with the agent's rewritten URL --
    `_rewrite_loopback_url` passes non-loopback URLs through unchanged, so
    agent URL != sidecar URL is legitimate; only the sidecar's own literal is
    pinned here."""
    cfg = _real_compose_config(tmp_path)
    env = cfg["services"]["nextseek-sidecar"]["environment"]
    assert env["NEXTSEEK_BASE_URL"] == "http://nextseek_nginx"


def test_compose_config_sidecar_no_dmac_cc_users_mount(tmp_path):
    """Task 13 ships with NO dmac-cc-users mount (the placeholder option is
    deleted, iter-1 M-2). Task 14 lands the `_staging` subpath mount and
    flips this test (documented two-state, Task 3/4 precedent)."""
    cfg = _real_compose_config(tmp_path)
    vols = cfg["services"]["nextseek-sidecar"].get("volumes") or []
    assert not any(v.get("source") == "dmac-cc-users" for v in vols)


# --------------------------------------------------------------------------
# Task 13: nginx access_log must not silently ride on the nginx-image-baked
# default -- a hermetic guard against a future conf edit that drops it.
# --------------------------------------------------------------------------

NGINX_CONF_FILE = REPO_ROOT / "docker" / "nginx.conf"


def test_nginx_conf_has_explicit_access_log_directive():
    text = NGINX_CONF_FILE.read_text(encoding="utf-8")
    assert re.search(r"access_log\s+\S", text), (
        "docker/nginx.conf must declare an explicit access_log directive "
        "(never rely on the compiled-in default)"
    )


# --------------------------------------------------------------------------
# Task 5 Step 3: service-name (`bedrock-proxy`) vs container-name
# (`dmac-bedrock-proxy`) must never be conflated. These lock in -- with a
# real cross-file check -- what cc_engine.py / test_cc_realstack.py already
# do correctly, so a future global find-and-replace cannot silently reconflate
# the two.
# --------------------------------------------------------------------------

REALSTACK_FILE = REPO_ROOT / "nextseek_api" / "cc_assistant" / "tests" / "test_cc_realstack.py"
CC_ENGINE_FILE = REPO_ROOT / "nextseek_api" / "cc_assistant" / "cc_engine.py"


def test_cc_engine_bedrock_proxy_default_url_uses_service_dns_not_container_name():
    """In-network URLs must resolve the compose SERVICE name ``bedrock-proxy``
    (a Docker DNS alias only inside dmac-cc-net) -- never the host-side
    CONTAINER name ``dmac-bedrock-proxy``, which is not a network alias and
    does not resolve from inside a sibling container."""
    text = CC_ENGINE_FILE.read_text(encoding="utf-8")
    assert "http://bedrock-proxy:8080" in text
    assert "http://dmac-bedrock-proxy:8080" not in text


def test_realstack_host_side_inspection_uses_container_name_not_service_name():
    """Host-side ``docker logs``/``docker inspect``/``docker network
    inspect`` must key off the CONTAINER name ``dmac-bedrock-proxy``
    (overridable via ``DMAC_PROXY_CONTAINER``) -- the compose service name
    ``bedrock-proxy`` is not a valid host-side ``docker inspect`` target."""
    text = REALSTACK_FILE.read_text(encoding="utf-8")
    assert 'os.environ.get("DMAC_PROXY_CONTAINER", "dmac-bedrock-proxy")' in text


def test_compose_bedrock_proxy_container_name_matches_realstack_default(tmp_path):
    """Cross-file lock: whatever literal ``container_name`` root compose pins
    for ``bedrock-proxy`` must be the exact default
    ``test_cc_realstack.py``'s ``PROXY_CONTAINER`` falls back to -- otherwise
    a migrated realstack run can never find the live proxy container by its
    default name."""
    cfg = _real_compose_config(tmp_path)
    compose_container_name = cfg["services"]["bedrock-proxy"]["container_name"]
    text = REALSTACK_FILE.read_text(encoding="utf-8")
    m = re.search(r'os\.environ\.get\("DMAC_PROXY_CONTAINER",\s*"([^"]+)"\)', text)
    assert m, "could not find the PROXY_CONTAINER default in test_cc_realstack.py"
    assert m.group(1) == compose_container_name


# ==========================================================================
# PLAN-7 Task 5: cc_runner_available() detail strings cite `docker compose
# build` + NEXTSEEK_CC_IMAGE -- never the old standalone `make image-build` /
# manual sidecar bring-up language. Hermetic: a fake `docker` module is
# injected into sys.modules so no real Docker daemon or docker-py install is
# required (cc_runner_available() does a local `import docker`).
# ==========================================================================

def _fake_docker_module(*, ping_exc=None, image_exc=None, network_exc=None):
    mod = types.ModuleType("docker")

    class _Images:
        def get(self, name):
            if image_exc is not None:
                raise image_exc
            return object()

    class _Networks:
        def get(self, name):
            if network_exc is not None:
                raise network_exc
            return object()

    class _Client:
        def __init__(self):
            self.images = _Images()
            self.networks = _Networks()

        def ping(self):
            if ping_exc is not None:
                raise ping_exc

    mod.from_env = lambda: _Client()
    return mod


def test_cc_runner_available_ok_when_daemon_image_and_network_all_present(monkeypatch):
    monkeypatch.setitem(sys.modules, "docker", _fake_docker_module())
    ok, detail = cc_engine.cc_runner_available()
    assert ok is True
    assert detail == "ok"


def test_cc_runner_available_daemon_unreachable_detail_unaffected(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "docker",
        _fake_docker_module(ping_exc=ConnectionError("connection refused")),
    )
    ok, detail = cc_engine.cc_runner_available()
    assert ok is False
    assert "docker daemon unreachable" in detail


def test_cc_runner_available_image_missing_detail_excludes_make_image_build(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "docker",
        _fake_docker_module(image_exc=LookupError("no such image")),
    )
    ok, detail = cc_engine.cc_runner_available()
    assert ok is False
    assert "make image-build" not in detail
    assert "docker compose build" in detail
    assert "NEXTSEEK_CC_IMAGE" in detail


def test_cc_runner_available_network_missing_detail_excludes_make_image_build_and_cites_compose(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "docker",
        _fake_docker_module(network_exc=LookupError("no such network")),
    )
    ok, detail = cc_engine.cc_runner_available()
    assert ok is False
    assert "make image-build" not in detail
    assert "docker compose" in detail
    assert "NEXTSEEK_CC_NETWORK" in detail

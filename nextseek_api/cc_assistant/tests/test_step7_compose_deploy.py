"""Hermetic tests for the Step 7 (compose-native deploy) preflight collector
and its ``validate_step7_compose_deploy`` gate.

Purpose (PLAN-7 Task 1): prove, over synthetic bundles only (no docker socket,
no network), that the preflight guard actually catches stale planning-session
file state before Step 7 ever touches the running stack.

No Docker and no network are used. Docker facts are injected via a fake
``DockerProbe``; collector git facts via a fake ``GitProbe``. The validator's
independent transcript re-check IS exercised against real throwaway git repos
built under ``tmp_path`` (git is available in the hermetic test container;
``git init/add/commit`` needs no network), because the whole point of that
check is that a hand-edited ``preflight.json`` cannot forge it.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

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
                                pre_existing_network_names: list[str] | None = None) -> None:
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
    (bundle_dir / "network_inspect.json").write_text(
        json.dumps({"containers": [f"cc-agent-{run_id}", "bedrock-proxy"]}), encoding="utf-8"
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

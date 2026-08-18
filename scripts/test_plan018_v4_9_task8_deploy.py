"""Fail-closed self-tests for the Plan 018 V4-9 Task-8 evidence gate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import plan018_v4_9_task8_deploy as gate


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _valid_bundle(tmp_path: Path) -> tuple[Path, Path, dict]:
    root = tmp_path / "repo"
    evidence_dir = root / "evidence" / "task8"
    evidence_dir.mkdir(parents=True)

    deploy_record = json.loads((gate.ROOT / gate.TASK7_FIXTURE).read_text())
    deploy_record_path = evidence_dir / "deploy-record.json"
    _write_json(deploy_record_path, deploy_record)

    ledger_path = evidence_dir / "commands.jsonl"
    entries = []
    for seq, phase in enumerate(gate.REQUIRED_PHASES, 1):
        argv = ["task8-control", phase]
        if phase == "forward":
            argv = ["./startup.sh", "rebuild", "--source-tree", "/isolated/origin-dev"]
        entries.append(
            {
                "seq": seq,
                "action": f"{phase}-{seq}",
                "phase": phase,
                "daemon": "host_read_only" if phase in {"preflight", "snapshot"} else "isolated",
                "effect": "read_only" if phase in {"preflight", "snapshot"} else "isolated_mutation",
                "argv": argv,
                "returncode": 0,
                "stdout_sha256": "1" * 64,
                "stderr_sha256": "2" * 64,
                "duration_s": 0.1,
            }
        )
    ledger_path.write_text("".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries))

    runbook_log = evidence_dir / "runbook-results.json"
    _write_json(runbook_log, {"redacted": True, "checks": "PASS"})
    host_before = evidence_dir / "host-before.json"
    host_after = evidence_dir / "host-after.json"
    host_snapshot = {"containers": ["existing-a", "existing-b"], "images": ["image-a"]}
    _write_json(host_before, host_snapshot)
    _write_json(host_after, host_snapshot)

    backup = tmp_path / "private" / "task8.sql"
    backup.parent.mkdir()
    backup.write_bytes(b"-- disposable migration-aware backup\n")
    backup.chmod(0o600)

    old_sha = "1" * 40
    new_sha = "2" * 40
    prior = "sha256:" + "a" * 64
    candidate = "sha256:" + "b" * 64
    prewrite_ids = ["pre-1", "pre-2"]
    postwrite_ids = ["post-1", "post-2"]
    payload = {
        "schema": gate.SCHEMA,
        "gate": "PASS",
        "authorization": {
            "approved": True,
            "approved_at": "2026-08-18T18:00:00Z",
            "conversation_ref": "maintainer-current-conversation",
            "scope_sha256": gate.AUTHORIZATION_SCOPE_SHA256,
        },
        "source": {
            "branch": "origin/dev",
            "remote_sha": new_sha,
            "deployed_sha": new_sha,
            "prior_sha": old_sha,
            "clean": True,
            "ahead": 0,
            "behind": 0,
            "diff_sha256": gate.EMPTY_SHA256,
        },
        "isolation": {
            "daemon_kind": "rootless_dockerd",
            "socket_path": "/tmp/plan018-task8/docker.sock",
            "data_root": "/tmp/plan018-task8/data",
            "daemon_id": "task8-daemon-001",
            "compose_project": "nextseek",
            "network": "dmac-cc-net",
            "host_snapshot_before": {
                "path": str(host_before.relative_to(root)), "sha256": _sha(host_before)
            },
            "host_snapshot_after": {
                "path": str(host_after.relative_to(root)), "sha256": _sha(host_after)
            },
            "host_unchanged": True,
        },
        "resources": {
            "max_cpus": 2,
            "max_memory_bytes": 4 * 1024**3,
            "disk_free_before_bytes": 20 * 1024**3,
            "disk_free_after_bytes": 6 * 1024**3,
            "disk_peak_delta_bytes": 14 * 1024**3,
            "minimum_reserve_bytes": 4 * 1024**3,
        },
        "deploy_record": {
            "path": str(deploy_record_path.relative_to(root)),
            "sha256": _sha(deploy_record_path),
            "schema_validated": True,
        },
        "backup": {
            "path": str(backup),
            "mode": "0600",
            "sha256": _sha(backup),
            "size_bytes": backup.stat().st_size,
            "migration_aware": True,
            "migration_range": ["0019_merge_attribute_async_turn_ledger"],
            "tables": ["django_migrations", "nextseek_api_evalgeneration"],
            "checksum_verified": True,
            "restore_probe": "PASS",
        },
        "images": {
            "prior": {"tag": "nextseek-nextseek:pre-task8", "image_id": prior},
            "candidate": {"tag": "nextseek-nextseek:latest", "image_id": candidate},
            "rollback_tag": {
                "tag": "nextseek-nextseek:pre-task8",
                "image_id": prior,
                "verified": True,
            },
            "registry": {
                "tag": "ghcr.io/biomicrocenter/nextseek:baseline-20260818-22222222",
                "digest": "sha256:" + "c" * 64,
                "baked_secret_gate": "PASS",
                "push": "PASS",
                "private_package": True,
                "credential_path": gate.GHCR_ENV_PATH,
                "credential_mode": "0600",
            },
        },
        "command_ledger": {
            "path": str(ledger_path.relative_to(root)),
            "sha256": _sha(ledger_path),
            "count": len(entries),
            "failed": 0,
            "forbidden": 0,
        },
        "forward": {
            "checks": {name: True for name in gate.REQUIRED_FORWARD_CHECKS},
            "prewrite_ids": prewrite_ids,
            "postwrite_ids": postwrite_ids,
            "retained_ids_after_forward": prewrite_ids + postwrite_ids,
        },
        "recovery": {
            "actions": list(gate.SAFE_RECOVERY_ACTIONS),
            "checks": {name: True for name in gate.REQUIRED_RECOVERY_CHECKS},
            "active_generation": deploy_record["generations"]["prior"],
            "image_id": prior,
            "retained_ids_after_recovery": prewrite_ids + postwrite_ids,
        },
        "oi3": {"checks": {name: True for name in gate.REQUIRED_OI3_CHECKS}},
        "external_effects": {
            "provider_calls": 0,
            "paid_resources": False,
            "live_database": False,
            "production_deployment": False,
            "existing_host_stack_mutated": False,
            "disposable_database": True,
            "private_registry_pushes": 1,
        },
        "artifacts_sha256": {
            str(deploy_record_path.relative_to(root)): _sha(deploy_record_path),
            str(ledger_path.relative_to(root)): _sha(ledger_path),
            str(runbook_log.relative_to(root)): _sha(runbook_log),
            str(host_before.relative_to(root)): _sha(host_before),
            str(host_after.relative_to(root)): _sha(host_after),
        },
        "wall_s": 900.0,
        "wall_cap_s": gate.MAX_WALL_S,
    }
    evidence_path = root / gate.EVIDENCE
    _write_json(evidence_path, payload)
    return root, evidence_path, payload


def test_valid_synthetic_bundle_passes(tmp_path: Path) -> None:
    root, evidence_path, _ = _valid_bundle(tmp_path)

    assert gate.validation_errors(root, evidence_path) == []


def test_missing_real_artifact_is_red_and_no_placeholder_is_committed(tmp_path: Path) -> None:
    assert gate.validation_errors(tmp_path, tmp_path / "missing.json") == [
        f"missing Task 8 evidence: {tmp_path / 'missing.json'}"
    ]
    assert not (gate.ROOT / gate.EVIDENCE).exists()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p["source"].__setitem__("deployed_sha", "4" * 40), "source is not exact committed origin/dev"),
        (lambda p: p["isolation"].__setitem__("socket_path", "/var/run/docker.sock"), "host Docker socket"),
        (lambda p: p["images"]["rollback_tag"].__setitem__("image_id", "sha256:" + "d" * 64), "rollback tag"),
        (lambda p: p["forward"].__setitem__("retained_ids_after_forward", ["pre-1"]), "forward retained-write"),
        (lambda p: p["recovery"].__setitem__("actions", list(reversed(gate.SAFE_RECOVERY_ACTIONS))), "safe recovery order"),
        (lambda p: p["external_effects"].__setitem__("provider_calls", 1), "external-effect boundary"),
    ],
)
def test_critical_identity_and_recovery_mutations_turn_gate_red(
    tmp_path: Path, mutation, message: str
) -> None:
    root, evidence_path, payload = _valid_bundle(tmp_path)
    mutation(payload)
    _write_json(evidence_path, payload)

    assert any(message in error for error in gate.validation_errors(root, evidence_path))


def test_backup_must_be_private_outside_repo_and_restore_verified(tmp_path: Path) -> None:
    root, evidence_path, payload = _valid_bundle(tmp_path)
    inside = root / "evidence" / "task8" / "leaked.sql"
    inside.write_bytes(b"secret")
    inside.chmod(0o644)
    payload["backup"].update(
        path=str(inside), mode="0644", sha256=_sha(inside), size_bytes=inside.stat().st_size,
        restore_probe="FAIL",
    )
    _write_json(evidence_path, payload)

    errors = gate.validation_errors(root, evidence_path)
    assert any("backup must be outside" in error for error in errors)
    assert any("mode 0600" in error for error in errors)
    assert any("restore probe" in error for error in errors)


def test_command_ledger_rejects_host_mutation_shell_and_missing_phase(tmp_path: Path) -> None:
    root, evidence_path, payload = _valid_bundle(tmp_path)
    ledger = root / payload["command_ledger"]["path"]
    entries = [json.loads(line) for line in ledger.read_text().splitlines()]
    entries[2]["daemon"] = "host_read_only"
    entries[2]["effect"] = "isolated_mutation"
    entries[2]["argv"] = ["sh", "-c", "docker system prune"]
    entries[-1]["phase"] = "snapshot"
    ledger.write_text("".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries))
    payload["command_ledger"]["sha256"] = _sha(ledger)
    payload["artifacts_sha256"][payload["command_ledger"]["path"]] = _sha(ledger)
    _write_json(evidence_path, payload)

    errors = gate.validation_errors(root, evidence_path)
    assert any("host-read-only ledger entry mutates" in error for error in errors)
    assert any("shell or forbidden command" in error for error in errors)
    assert any("command phases" in error for error in errors)


def test_noncanonical_project_is_rejected_because_startup_would_skip_ghcr(tmp_path: Path) -> None:
    root, evidence_path, payload = _valid_bundle(tmp_path)
    payload["isolation"]["compose_project"] = "plan018-v4-9-task8"
    _write_json(evidence_path, payload)

    assert any("wrong isolated identity" in error for error in gate.validation_errors(root, evidence_path))


def test_artifact_hash_and_resource_bounds_are_fail_closed(tmp_path: Path) -> None:
    root, evidence_path, payload = _valid_bundle(tmp_path)
    artifact = root / next(iter(payload["artifacts_sha256"]))
    artifact.write_bytes(artifact.read_bytes() + b"tamper")
    payload["resources"]["disk_free_after_bytes"] = 1
    payload["wall_s"] = gate.MAX_WALL_S + 1
    _write_json(evidence_path, payload)

    errors = gate.validation_errors(root, evidence_path)
    assert any("artifact hash drift" in error for error in errors)
    assert any("disk reserve" in error for error in errors)
    assert any("wall cap" in error for error in errors)

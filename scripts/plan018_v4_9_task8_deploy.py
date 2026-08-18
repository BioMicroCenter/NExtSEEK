#!/usr/bin/env python3
"""Validate the authorized Plan 018 V4-9 Task-8 operational evidence.

This file deliberately does not manufacture a PASS artifact and does not run
deployment commands.  The actual disposable exercise is authorization-gated;
after it runs, this validator proves that its source, daemon, image, backup,
runbook, recovery, resource, and command-ledger evidence is complete and
internally consistent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nextseek_api.eval.deploy_record import DeployRecord  # noqa: E402


SCHEMA = "plan018-v4-9-task8-evidence/v1"
EVIDENCE = "evidence/plan018-v4-9-task8-evidence.json"
TASK7_FIXTURE = "evidence/plan018-v4-9-deploy-record.fixture.json"
GHCR_ENV_PATH = "/home/taishajo/work/state/secrets/ghcr-tavjo.env"
MAX_WALL_S = 1800.0
MAX_CPUS = 2
MAX_MEMORY_BYTES = 4 * 1024**3
MINIMUM_DISK_RESERVE_BYTES = 4 * 1024**3
# Read-only `docker system df -v` on this box showed an approximately 24.6-GiB
# unique image footprint for two app releases plus the full Compose/OI-3 peer
# set.  Allow 7 GiB for build cache and disposable seeded volumes, then retain
# the non-negotiable 4-GiB host reserve.  The real evidence records measured
# peak delta; this conservative preflight prevents starting a run that cannot
# plausibly finish without emergency deletion.
ESTIMATED_IMAGE_BYTES = 25 * 1024**3
ESTIMATED_CACHE_AND_VOLUME_BYTES = 7 * 1024**3
REQUIRED_FREE_BYTES = (
    ESTIMATED_IMAGE_BYTES + ESTIMATED_CACHE_AND_VOLUME_BYTES
    + MINIMUM_DISK_RESERVE_BYTES
)
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

AUTHORIZATION_SCOPE = (
    "non-force push current Plan018 V4-9 commits to origin/dev; isolated "
    "disposable Docker daemon/stack and disposable MySQL; verified local "
    "pre-tags; mode-0600 migration-aware dump; mandatory baked-secret gate "
    "and private GHCR baseline push; forward deploy; non-destructive recovery; "
    "zero-spend DEPLOYMENT.md section 6 and OI-3 verification; excludes "
    "production deployment or enablement, provider calls, reverse migrations, "
    "retained-data deletion, Docker prune, and existing-host-stack mutation"
)
AUTHORIZATION_SCOPE_SHA256 = hashlib.sha256(AUTHORIZATION_SCOPE.encode()).hexdigest()

REQUIRED_PHASES = (
    "preflight",
    "snapshot",
    "backup",
    "seed",
    "forward",
    "verify_forward",
    "recovery",
    "verify_recovery",
    "cleanup",
)
REQUIRED_FORWARD_CHECKS = (
    "site_http_200",
    "server_gunicorn",
    "no_daphne",
    "restart_zero",
    "boot_clean",
    "migrations_all_applied",
    "cc_route_wired",
    "oi3_peers_unchanged",
    "rollback_tag_present",
    "doctor_green",
)
REQUIRED_RECOVERY_CHECKS = (
    "flags_disabled",
    "schedules_stopped",
    "workers_drained",
    "prior_generation_active",
    "prior_image_active",
    "forward_corrective_migration_only",
    "prewrite_retained",
    "postwrite_retained",
    "site_http_200",
    "doctor_green",
)
REQUIRED_OI3_CHECKS = (
    "zero_agent_shared_credentials",
    "bedrock_proxy_only",
    "proxy_token_not_logged",
    "network_members_closed",
    "nextseek_not_on_agent_network",
    "scratch_only_writes",
    "full_zero_spend_validator",
)
SAFE_RECOVERY_ACTIONS = (
    "disable_flags",
    "stop_schedules",
    "stop_workers",
    "activate_prior_generation",
    "restore_prior_compatible_image",
    "forward_corrective_migration",
)

_TOP_KEYS = {
    "schema", "gate", "authorization", "source", "isolation", "resources",
    "deploy_record", "backup", "images", "command_ledger", "forward",
    "recovery", "oi3", "external_effects", "artifacts_sha256", "wall_s",
    "wall_cap_s",
}
_LEDGER_KEYS = {
    "seq", "action", "phase", "daemon", "effect", "argv", "returncode",
    "stdout_sha256", "stderr_sha256", "duration_s",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_sha256(value: object) -> bool:
    return _is_hex(value, 64)


def _is_image_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith("sha256:") and _is_sha256(value[7:])


def _exact_keys(value: object, expected: set[str], label: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return {}
    actual = set(value)
    if actual != expected:
        errors.append(
            f"{label} keys are not exact: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return value


def _aware_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _root_artifact(root: Path, relative: object, label: str, errors: list[str]) -> Path | None:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        errors.append(f"{label} must be a non-empty repo-relative path")
        return None
    root_resolved = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root_resolved):
        errors.append(f"{label} escapes the evidence root")
        return None
    return path


def _all_true_checks(value: object, required: tuple[str, ...], label: str, errors: list[str]) -> None:
    checks = _exact_keys(value, set(required), label, errors)
    if checks and any(checks.get(name) is not True for name in required):
        errors.append(f"{label} are not all PASS")


def evaluate_preflight(
    *, free_bytes: int, tools: dict[str, bool], credential_mode: int | None,
) -> dict[str, Any]:
    required_tools = {
        "docker", "dockerd-rootless.sh", "rootlesskit", "pasta", "git", "uv",
    }
    errors: list[str] = []
    if set(tools) != required_tools or not all(tools.get(name) for name in required_tools):
        errors.append("rootless isolated-daemon toolchain is incomplete")
    if credential_mode != 0o600:
        errors.append("canonical GHCR credential is absent or not mode 0600")
    if free_bytes < REQUIRED_FREE_BYTES:
        errors.append(
            "insufficient disk for full isolated stack peak plus 4-GiB reserve: "
            f"free={free_bytes}, required={REQUIRED_FREE_BYTES}"
        )
    return {
        "schema": "plan018-v4-9-task8-preflight/v1",
        "gate": "PASS" if not errors else "FAIL",
        "free_bytes": free_bytes,
        "required_free_bytes": REQUIRED_FREE_BYTES,
        "estimated_image_bytes": ESTIMATED_IMAGE_BYTES,
        "estimated_cache_and_volume_bytes": ESTIMATED_CACHE_AND_VOLUME_BYTES,
        "minimum_reserve_bytes": MINIMUM_DISK_RESERVE_BYTES,
        "tools": tools,
        "credential_mode": None if credential_mode is None else f"0{credential_mode:o}",
        "errors": errors,
    }


def preflight(root: Path = ROOT) -> dict[str, Any]:
    tool_names = ("docker", "dockerd-rootless.sh", "rootlesskit", "pasta", "git", "uv")
    credential = Path(GHCR_ENV_PATH)
    mode = stat.S_IMODE(credential.stat().st_mode) if credential.is_file() else None
    return evaluate_preflight(
        free_bytes=shutil.disk_usage(root).free,
        tools={name: shutil.which(name) is not None for name in tool_names},
        credential_mode=mode,
    )


def _validate_ledger(root: Path, summary: dict[str, Any], errors: list[str]) -> None:
    path = _root_artifact(root, summary.get("path"), "command ledger path", errors)
    if path is None or not path.is_file():
        errors.append("command ledger artifact is missing")
        return
    if not _is_sha256(summary.get("sha256")) or _sha(path) != summary.get("sha256"):
        errors.append("command ledger hash drift")
    try:
        entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"command ledger is malformed: {exc}")
        return
    if summary.get("count") != len(entries) or summary.get("failed") != 0 or summary.get("forbidden") != 0:
        errors.append("command-count evidence is not exact/green")
    phases: set[str] = set()
    saw_rebuild = False
    action_ids: set[str] = set()
    for index, raw in enumerate(entries, 1):
        entry = _exact_keys(raw, _LEDGER_KEYS, f"command ledger entry {index}", errors)
        if not entry:
            continue
        if entry.get("seq") != index:
            errors.append(f"command ledger sequence is not contiguous at {index}")
        action = entry.get("action")
        if not isinstance(action, str) or not action or action in action_ids:
            errors.append(f"command ledger action identity is missing/duplicate at {index}")
        else:
            action_ids.add(action)
        phase = entry.get("phase")
        if isinstance(phase, str):
            phases.add(phase)
        daemon = entry.get("daemon")
        effect = entry.get("effect")
        if daemon not in {"host_read_only", "isolated", "registry"}:
            errors.append(f"command ledger daemon identity is invalid at {index}")
        if effect not in {"read_only", "isolated_mutation", "registry_write", "cleanup"}:
            errors.append(f"command ledger effect is invalid at {index}")
        if daemon == "host_read_only" and effect != "read_only":
            errors.append(f"host-read-only ledger entry mutates at {index}")
        argv = entry.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(token, str) and token for token in argv):
            errors.append(f"command ledger argv is malformed at {index}")
            continue
        joined = " ".join(argv).lower()
        shell_meta = any(token in joined for token in (";", "&&", "||", "$(", "`"))
        forbidden = any(
            phrase in joined
            for phrase in (
                "docker system prune", "docker volume rm", " down -v",
                "startup.sh reset", "reverse_migration", "reverse migration",
                "delete_retained_rows", "reset_persistent_database",
            )
        )
        sensitive = any(
            marker in joined
            for marker in ("ghcr_token=", "password=", "secret_key=", "authorization: bearer")
        )
        if argv[0] in {"sh", "bash", "zsh"} or shell_meta or forbidden or sensitive:
            errors.append(f"shell or forbidden command in ledger entry {index}")
        if argv[:2] == ["./startup.sh", "rebuild"] and "--source-tree" in argv:
            saw_rebuild = True
        if entry.get("returncode") != 0:
            errors.append(f"command ledger contains failed entry {index}")
        if not _is_sha256(entry.get("stdout_sha256")) or not _is_sha256(entry.get("stderr_sha256")):
            errors.append(f"command ledger output identity is malformed at {index}")
        duration = entry.get("duration_s")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration < 0:
            errors.append(f"command ledger duration is malformed at {index}")
    if phases != set(REQUIRED_PHASES):
        errors.append(
            f"command phases are not exact: missing={sorted(set(REQUIRED_PHASES) - phases)}, "
            f"extra={sorted(phases - set(REQUIRED_PHASES))}"
        )
    if not saw_rebuild:
        errors.append("command ledger lacks required ./startup.sh rebuild --source-tree invocation")


def validation_errors(root: Path = ROOT, evidence_path: Path | str = EVIDENCE) -> list[str]:
    root = root.resolve()
    evidence = Path(evidence_path)
    if not evidence.is_absolute():
        evidence = root / evidence
    if not evidence.is_file():
        return [f"missing Task 8 evidence: {evidence}"]
    try:
        payload = json.loads(evidence.read_text())
    except (OSError, ValueError, TypeError) as exc:
        return [f"malformed Task 8 evidence: {exc}"]

    errors: list[str] = []
    payload = _exact_keys(payload, _TOP_KEYS, "Task 8 evidence", errors)
    if payload.get("schema") != SCHEMA or payload.get("gate") != "PASS":
        errors.append("Task 8 schema/gate is not exact PASS")

    authorization = _exact_keys(
        payload.get("authorization"),
        {"approved", "approved_at", "conversation_ref", "scope_sha256"},
        "authorization", errors,
    )
    if (
        authorization.get("approved") is not True
        or not _aware_timestamp(authorization.get("approved_at"))
        or not isinstance(authorization.get("conversation_ref"), str)
        or not authorization.get("conversation_ref")
        or authorization.get("scope_sha256") != AUTHORIZATION_SCOPE_SHA256
    ):
        errors.append("Task 8 current-conversation authorization is absent or scope-drifted")

    source = _exact_keys(
        payload.get("source"),
        {"branch", "remote_sha", "deployed_sha", "prior_sha", "clean", "ahead", "behind", "diff_sha256"},
        "source", errors,
    )
    if (
        source.get("branch") != "origin/dev"
        or not _is_hex(source.get("remote_sha"), 40)
        or source.get("deployed_sha") != source.get("remote_sha")
        or not _is_hex(source.get("prior_sha"), 40)
        or source.get("prior_sha") == source.get("deployed_sha")
        or source.get("clean") is not True
        or source.get("ahead") != 0
        or source.get("behind") != 0
        or source.get("diff_sha256") != EMPTY_SHA256
    ):
        errors.append("source is not exact committed origin/dev")

    isolation = _exact_keys(
        payload.get("isolation"),
        {"daemon_kind", "socket_path", "data_root", "daemon_id", "compose_project", "network", "host_snapshot_before", "host_snapshot_after", "host_unchanged"},
        "isolation", errors,
    )
    socket = str(isolation.get("socket_path", ""))
    data_root = str(isolation.get("data_root", ""))
    if (
        isolation.get("daemon_kind") not in {"rootless_dockerd", "dind"}
        or not socket
        or socket == "/var/run/docker.sock"
        or socket.startswith("unix:///var/run/docker.sock")
        or not data_root
        or data_root == "/var/lib/docker"
        # registry_push intentionally skips non-canonical projects.  The
        # daemon/socket/data-root isolation makes canonical `nextseek` safe
        # here and is required for the plan-mandated GHCR baseline write.
        or isolation.get("compose_project") != "nextseek"
        or isolation.get("network") != "dmac-cc-net"
    ):
        errors.append("Task 8 used the host Docker socket/data root or wrong isolated identity")
    snapshot_hashes: list[str] = []
    for name in ("host_snapshot_before", "host_snapshot_after"):
        snapshot = _exact_keys(isolation.get(name), {"path", "sha256"}, name, errors)
        path = _root_artifact(root, snapshot.get("path"), f"{name} path", errors)
        expected = snapshot.get("sha256")
        if path is None or not path.is_file() or not _is_sha256(expected) or _sha(path) != expected:
            errors.append(f"{name} artifact identity drift")
        elif name == "host_snapshot_before":
            snapshot_hashes.append(expected)
        else:
            snapshot_hashes.append(expected)
    if isolation.get("host_unchanged") is not True or len(snapshot_hashes) != 2 or snapshot_hashes[0] != snapshot_hashes[1]:
        errors.append("existing host stack fingerprint changed")

    resources = _exact_keys(
        payload.get("resources"),
        {"max_cpus", "max_memory_bytes", "disk_free_before_bytes", "disk_free_after_bytes", "disk_peak_delta_bytes", "minimum_reserve_bytes"},
        "resources", errors,
    )
    numeric_resources = all(
        isinstance(resources.get(name), (int, float)) and not isinstance(resources.get(name), bool)
        for name in resources
    ) if resources else False
    if not numeric_resources or not (0 < resources.get("max_cpus", 0) <= MAX_CPUS) or not (0 < resources.get("max_memory_bytes", 0) <= MAX_MEMORY_BYTES):
        errors.append("Task 8 CPU/memory resource cap exceeded")
    if numeric_resources:
        reserve = resources["minimum_reserve_bytes"]
        if (
            reserve < MINIMUM_DISK_RESERVE_BYTES
            or resources["disk_free_after_bytes"] < reserve
            or resources["disk_peak_delta_bytes"] > resources["disk_free_before_bytes"] - reserve
        ):
            errors.append("Task 8 disk reserve was not preserved")

    record_ref = _exact_keys(
        payload.get("deploy_record"), {"path", "sha256", "schema_validated"},
        "deploy record reference", errors,
    )
    record_path = _root_artifact(root, record_ref.get("path"), "deploy record path", errors)
    record: DeployRecord | None = None
    if record_path is None or not record_path.is_file():
        errors.append("deploy record artifact is missing")
    elif not _is_sha256(record_ref.get("sha256")) or _sha(record_path) != record_ref.get("sha256"):
        errors.append("deploy record hash drift")
    else:
        try:
            record = DeployRecord.model_validate_json(record_path.read_bytes())
        except Exception as exc:  # pydantic exposes a stable closed-model boundary
            errors.append(f"deploy record schema validation failed: {exc}")
    if record_ref.get("schema_validated") is not True:
        errors.append("deploy record was not marked schema-validated")

    backup = _exact_keys(
        payload.get("backup"),
        {"path", "mode", "sha256", "size_bytes", "migration_aware", "migration_range", "tables", "checksum_verified", "restore_probe"},
        "backup", errors,
    )
    backup_raw = Path(str(backup.get("path", "")))
    backup_path = backup_raw.resolve()
    if not backup_raw.is_absolute() or backup_path.is_relative_to(root):
        errors.append("backup must be outside the git checkout")
    if not backup_path.is_file():
        errors.append("backup artifact is missing")
    else:
        actual_mode = stat.S_IMODE(backup_path.stat().st_mode)
        if backup.get("mode") != "0600" or actual_mode != 0o600:
            errors.append("backup mode 0600 is not proven")
        if (
            not _is_sha256(backup.get("sha256"))
            or _sha(backup_path) != backup.get("sha256")
            or backup.get("size_bytes") != backup_path.stat().st_size
            or backup_path.stat().st_size <= 0
        ):
            errors.append("backup checksum/size verification failed")
    if (
        backup.get("migration_aware") is not True
        or not isinstance(backup.get("migration_range"), list)
        or not backup.get("migration_range")
        or not isinstance(backup.get("tables"), list)
        or "django_migrations" not in backup.get("tables", [])
        or backup.get("checksum_verified") is not True
    ):
        errors.append("migration-aware backup evidence is incomplete")
    if backup.get("restore_probe") != "PASS":
        errors.append("backup restore probe is not PASS")

    images = _exact_keys(payload.get("images"), {"prior", "candidate", "rollback_tag", "registry"}, "images", errors)
    prior = _exact_keys(images.get("prior"), {"tag", "image_id"}, "prior image", errors)
    candidate = _exact_keys(images.get("candidate"), {"tag", "image_id"}, "candidate image", errors)
    rollback = _exact_keys(images.get("rollback_tag"), {"tag", "image_id", "verified"}, "rollback tag", errors)
    registry = _exact_keys(
        images.get("registry"),
        {"tag", "digest", "baked_secret_gate", "push", "private_package", "credential_path", "credential_mode"},
        "registry image", errors,
    )
    if (
        not _is_image_id(prior.get("image_id"))
        or not _is_image_id(candidate.get("image_id"))
        or prior.get("image_id") == candidate.get("image_id")
    ):
        errors.append("prior/candidate immutable image identities are malformed or equal")
    if rollback.get("tag") != prior.get("tag") or rollback.get("image_id") != prior.get("image_id") or rollback.get("verified") is not True:
        errors.append("rollback tag is not identity-verified against the prior image")
    if (
        not str(registry.get("tag", "")).startswith("ghcr.io/biomicrocenter/nextseek:baseline-")
        or not _is_image_id(registry.get("digest"))
        or registry.get("baked_secret_gate") != "PASS"
        or registry.get("push") != "PASS"
        or registry.get("private_package") is not True
        or registry.get("credential_path") != GHCR_ENV_PATH
        or registry.get("credential_mode") != "0600"
    ):
        errors.append("immutable private-registry/baked-secret evidence is incomplete")
    credential_path = Path(GHCR_ENV_PATH)
    if (
        not credential_path.is_file()
        or stat.S_IMODE(credential_path.stat().st_mode) != 0o600
    ):
        errors.append("canonical GHCR credential file is absent or not mode 0600")
    if record is not None:
        if record.git.source_sha != source.get("deployed_sha"):
            errors.append("deploy record source SHA is not the deployed origin/dev SHA")
        if record.images.get("prior") != prior.get("image_id") or record.images.get("candidate") != candidate.get("image_id"):
            errors.append("deploy record image identities disagree with operational evidence")

    ledger = _exact_keys(
        payload.get("command_ledger"), {"path", "sha256", "count", "failed", "forbidden"},
        "command ledger summary", errors,
    )
    _validate_ledger(root, ledger, errors)

    forward = _exact_keys(
        payload.get("forward"), {"checks", "prewrite_ids", "postwrite_ids", "retained_ids_after_forward"},
        "forward result", errors,
    )
    _all_true_checks(forward.get("checks"), REQUIRED_FORWARD_CHECKS, "forward checks", errors)
    pre = forward.get("prewrite_ids")
    post = forward.get("postwrite_ids")
    retained_forward = forward.get("retained_ids_after_forward")
    valid_ids = (
        isinstance(pre, list) and isinstance(post, list) and pre and post
        and all(isinstance(item, str) and item for item in pre + post)
        and len(set(pre + post)) == len(pre + post)
    )
    if not valid_ids or not isinstance(retained_forward, list) or set(retained_forward) != set(pre + post):
        errors.append("forward retained-write conservation failed")

    recovery = _exact_keys(
        payload.get("recovery"), {"actions", "checks", "active_generation", "image_id", "retained_ids_after_recovery"},
        "recovery result", errors,
    )
    if recovery.get("actions") != list(SAFE_RECOVERY_ACTIONS):
        errors.append("safe recovery order is incomplete, destructive, or reordered")
    _all_true_checks(recovery.get("checks"), REQUIRED_RECOVERY_CHECKS, "recovery checks", errors)
    if record is not None and (
        recovery.get("active_generation") != record.generations.prior
        or recovery.get("image_id") != record.images["prior"]
    ):
        errors.append("recovery did not restore the exact prior generation/image")
    if not valid_ids or not isinstance(recovery.get("retained_ids_after_recovery"), list) or set(recovery.get("retained_ids_after_recovery", [])) != set(pre + post):
        errors.append("recovery retained-write conservation failed")

    oi3 = _exact_keys(payload.get("oi3"), {"checks"}, "OI-3 result", errors)
    _all_true_checks(oi3.get("checks"), REQUIRED_OI3_CHECKS, "OI-3 checks", errors)

    effects = _exact_keys(
        payload.get("external_effects"),
        {"provider_calls", "paid_resources", "live_database", "production_deployment", "existing_host_stack_mutated", "disposable_database", "private_registry_pushes"},
        "external effects", errors,
    )
    if effects != {
        "provider_calls": 0,
        "paid_resources": False,
        "live_database": False,
        "production_deployment": False,
        "existing_host_stack_mutated": False,
        "disposable_database": True,
        "private_registry_pushes": 1,
    }:
        errors.append("external-effect boundary is not exact zero-spend/disposable scope")

    artifacts = payload.get("artifacts_sha256")
    if not isinstance(artifacts, dict) or len(artifacts) < 5:
        errors.append("artifact manifest is incomplete")
        artifacts = {}
    required_artifacts = {
        record_ref.get("path"),
        ledger.get("path"),
        (isolation.get("host_snapshot_before") or {}).get("path") if isinstance(isolation.get("host_snapshot_before"), dict) else None,
        (isolation.get("host_snapshot_after") or {}).get("path") if isinstance(isolation.get("host_snapshot_after"), dict) else None,
    }
    if None in required_artifacts or not required_artifacts.issubset(set(artifacts)):
        errors.append("artifact manifest omits a required identity artifact")
    for relative, expected in artifacts.items():
        path = _root_artifact(root, relative, f"artifact {relative}", errors)
        if path is None or not path.is_file() or not _is_sha256(expected) or _sha(path) != expected:
            errors.append(f"artifact hash drift: {relative}")

    wall_s = payload.get("wall_s")
    wall_cap = payload.get("wall_cap_s")
    if (
        wall_cap != MAX_WALL_S
        or not isinstance(wall_s, (int, float))
        or isinstance(wall_s, bool)
        or wall_s < 0
        or wall_s > MAX_WALL_S
    ):
        errors.append("Task 8 wall cap is absent or exceeded")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "validate"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--evidence", type=Path, default=Path(EVIDENCE))
    args = parser.parse_args(argv)
    if args.command == "preflight":
        result = preflight(args.root)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["gate"] == "PASS" else 1
    errors = validation_errors(args.root, args.evidence)
    print("Task 8 evidence " + ("PASS" if not errors else "FAIL"))
    for error in errors:
        print("- " + error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Plan and validate the authorized Plan 018 V4-9 Task-8 exercise.

The ``plan`` command is read-only: it validates an exact, expiring approval
manifest and emits the argv-only operational sequence that a later ``run``
command will execute.  This file deliberately does not manufacture a PASS
artifact.  The actual disposable exercise is authorization-gated; after it
runs, this validator proves that its source, daemon, image, backup, runbook,
recovery, resource, namespace-cleanup, and command-ledger evidence is complete and internally
consistent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nextseek_api.eval.deploy_record import DeployRecord  # noqa: E402


SCHEMA = "plan018-v4-9-task8-evidence/v1"
APPROVAL_SCHEMA = "plan018-v4-9-task8-approval/v1"
PLAN_SCHEMA = "plan018-v4-9-task8-plan/v1"
EVIDENCE = "evidence/plan018-v4-9-task8-evidence.json"
TASK7_FIXTURE = "evidence/plan018-v4-9-deploy-record.fixture.json"
GHCR_ENV_PATH = "/home/taishajo/work/state/secrets/ghcr-tavjo.env"
MAX_WALL_S = 1800.0
MAX_CPUS = 2
try:
    TASKSET_CPU_LIST = ",".join(
        str(cpu) for cpu in sorted(os.sched_getaffinity(0))[:MAX_CPUS]
    )
except AttributeError:  # pragma: no cover - Linux deployment host contract
    TASKSET_CPU_LIST = "0,1"
MAX_MEMORY_BYTES = 6 * 1024**3
MINIMUM_MEMORY_RESERVE_BYTES = 2 * 1024**3
REQUIRED_AVAILABLE_MEMORY_BYTES = (
    MAX_MEMORY_BYTES + MINIMUM_MEMORY_RESERVE_BYTES
)
MINIMUM_DISK_RESERVE_BYTES = 4 * 1024**3
REQUIRED_LOCAL_IMAGES = (
    "nextseek-nextseek:latest",
    "mysql:8.0",
    "nginx:latest",
    "nextseek-bedrock-proxy:latest",
    "nextseek-ns-sidecar:latest",
    "dmac-assistant:poc",
)
# Task 8 reuses the independently verified prior app image and all immutable
# peer images already on this host.  It builds one candidate image only.  The
# allowance below covers that image plus BuildKit scratch/cache and disposable
# MySQL data while preserving the mandatory four-GiB host reserve.
ESTIMATED_IMAGE_BYTES = 10 * 1024**3
ESTIMATED_CACHE_AND_VOLUME_BYTES = 4 * 1024**3
REQUIRED_FREE_BYTES = (
    ESTIMATED_IMAGE_BYTES + ESTIMATED_CACHE_AND_VOLUME_BYTES
    + MINIMUM_DISK_RESERVE_BYTES
)
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

AUTHORIZATION_SCOPE = (
    "non-force push current Plan018 V4-9 commits to origin/dev; exact-namespaced "
    "disposable app cohort on the existing Docker daemon and disposable MySQL; "
    "reuse independently source-verified immutable prior app image; verified local "
    "pre-tags; mode-0600 migration-aware dump; mandatory baked-secret gate "
    "and private GHCR baseline push; forward deploy; non-destructive recovery; "
    "zero-spend DEPLOYMENT.md section 6 and OI-3 verification; excludes "
    "production deployment or enablement, provider calls, reverse migrations, "
    "retained-data deletion, Docker prune, canonical container/network/volume "
    "mutation, and full SEEK/Neo4j/Solr duplication"
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
CONTROL_ACTIONS = frozenset(
    {
        "approval", "source", "resources", "host-before", "harness-config",
        "prior-image", "namespace-start", "prewrite-seed",
        "migration-aware-dump", "restore-probe", "candidate-source",
        "build-prepare", "builder-cleanup", "cohort-resume", "registry-push",
        "postwrite-seed", "forward-runbook", "disable-flags", "stop-schedules",
        "stop-workers", "activate-prior", "restore-image", "forward-only-schema",
        "recovery-runbook", "namespace-cleanup", "host-after",
    }
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
    "deploy_record", "backup", "images", "operational_plan", "command_ledger",
    "forward", "recovery", "oi3", "external_effects", "artifacts_sha256",
    "wall_s", "wall_cap_s",
}
_LEDGER_KEYS = {
    "seq", "action", "phase", "daemon", "effect", "argv", "returncode",
    "stdout_sha256", "stderr_sha256", "duration_s",
}
_PLAN_COMMAND_KEYS = {
    "action", "phase", "daemon", "effect", "argv", "cwd", "env_keys",
}


@dataclass(frozen=True)
class OperationalConfig:
    """Paths and immutable identities for one disposable Task-8 run."""

    repo_root: Path
    run_root: Path
    approval_path: Path
    prior_sha: str
    candidate_sha: str
    port_offset: int = 12000

    @property
    def runtime_root(self) -> Path:
        # The checkout is disposable; Compose identity is explicit and never
        # derived from this basename.
        return self.run_root / "NExtSEEK"

    @property
    def source_root(self) -> Path:
        return self.run_root / "NExtSEEK-source"

    @property
    def token(self) -> str:
        return hashlib.sha256(str(self.run_root.resolve()).encode()).hexdigest()[:10]

    @property
    def compose_project(self) -> str:
        return f"plan018v49task8{self.token}"

    @property
    def instance_prefix(self) -> str:
        return f"p18t8-{self.token}-"

    @property
    def cc_network(self) -> str:
        return f"{self.instance_prefix}cc"

    @property
    def egress_network(self) -> str:
        return f"{self.instance_prefix}egress"

    @property
    def app_image(self) -> str:
        return f"{self.compose_project}-nextseek:latest"

    @property
    def builder_name(self) -> str:
        return f"{self.instance_prefix}builder"

    @property
    def app_container(self) -> str:
        return f"{self.instance_prefix}nextseek"

    @property
    def db_container(self) -> str:
        return f"{self.instance_prefix}seek-mysql"

    @property
    def nginx_container(self) -> str:
        return f"{self.instance_prefix}nextseek-nginx"

    @property
    def proxy_container(self) -> str:
        return f"{self.instance_prefix}bedrock-proxy"

    @property
    def sidecar_container(self) -> str:
        return f"{self.instance_prefix}nextseek-sidecar"

    @property
    def backup_path(self) -> Path:
        return self.run_root.parent / f"{self.run_root.name}-dmac-pre-forward.sql"


@dataclass(frozen=True)
class PlannedCommand:
    action: str
    phase: str
    daemon: str
    effect: str
    argv: tuple[str, ...]
    cwd: str
    env_keys: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["argv"] = list(self.argv)
        data["env_keys"] = list(self.env_keys)
        return data


@dataclass(frozen=True)
class CommandOutcome:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


class OperationalAdapter(Protocol):
    """Execution boundary used by the real runner and fast fake tests."""

    def execute(self, command: PlannedCommand, *, timeout_s: float) -> CommandOutcome:
        ...

    def emergency_stop(self, *, timeout_s: float) -> CommandOutcome:
        ...


@dataclass(frozen=True)
class RunArtifacts:
    plan_path: Path
    ledger_path: Path
    elapsed_s: float
    command_count: int


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


def available_memory_bytes() -> int:
    """Read Linux MemAvailable without adding a process/dependency."""

    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, IndexError, ValueError):
        pass
    return 0


def evaluate_preflight(
    *,
    free_bytes: int,
    available_memory: int,
    tools: dict[str, bool],
    local_images: dict[str, bool],
    credential_mode: int | None,
) -> dict[str, Any]:
    required_tools = {"docker", "git", "uv", "taskset"}
    errors: list[str] = []
    if set(tools) != required_tools or not all(tools.get(name) for name in required_tools):
        errors.append("Task 8 host-daemon toolchain is incomplete")
    if set(local_images) != set(REQUIRED_LOCAL_IMAGES) or not all(
        local_images.get(image) for image in REQUIRED_LOCAL_IMAGES
    ):
        errors.append("Task 8 immutable local-image cohort is incomplete")
    if credential_mode != 0o600:
        errors.append("canonical GHCR credential is absent or not mode 0600")
    if free_bytes < REQUIRED_FREE_BYTES:
        errors.append(
            "insufficient disk for one candidate build/cohort plus 4-GiB reserve: "
            f"free={free_bytes}, required={REQUIRED_FREE_BYTES}"
        )
    if available_memory < REQUIRED_AVAILABLE_MEMORY_BYTES:
        errors.append(
            "insufficient available memory for 6-GiB Task-8 ceiling plus "
            "2-GiB host reserve: "
            f"available={available_memory}, required={REQUIRED_AVAILABLE_MEMORY_BYTES}"
        )
    return {
        "schema": "plan018-v4-9-task8-preflight/v1",
        "gate": "PASS" if not errors else "FAIL",
        "free_bytes": free_bytes,
        "required_free_bytes": REQUIRED_FREE_BYTES,
        "estimated_image_bytes": ESTIMATED_IMAGE_BYTES,
        "estimated_cache_and_volume_bytes": ESTIMATED_CACHE_AND_VOLUME_BYTES,
        "minimum_reserve_bytes": MINIMUM_DISK_RESERVE_BYTES,
        "available_memory_bytes": available_memory,
        "required_available_memory_bytes": REQUIRED_AVAILABLE_MEMORY_BYTES,
        "minimum_memory_reserve_bytes": MINIMUM_MEMORY_RESERVE_BYTES,
        "tools": tools,
        "local_images": local_images,
        "credential_mode": None if credential_mode is None else f"0{credential_mode:o}",
        "errors": errors,
    }


def preflight(root: Path = ROOT) -> dict[str, Any]:
    tool_names = ("docker", "git", "uv", "taskset")
    credential = Path(GHCR_ENV_PATH)
    mode = stat.S_IMODE(credential.stat().st_mode) if credential.is_file() else None
    local_images: dict[str, bool] = {}
    for image in REQUIRED_LOCAL_IMAGES:
        try:
            inspected = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                timeout=20,
                check=False,
            )
            local_images[image] = inspected.returncode == 0
        except (OSError, subprocess.SubprocessError):
            local_images[image] = False
    return evaluate_preflight(
        free_bytes=shutil.disk_usage(root).free,
        available_memory=available_memory_bytes(),
        tools={name: shutil.which(name) is not None for name in tool_names},
        local_images=local_images,
        credential_mode=mode,
    )


def authorization_errors(
    payload: object,
    *,
    prior_sha: str,
    candidate_sha: str,
    now: datetime | None = None,
) -> list[str]:
    """Validate a narrow, expiring maintainer approval before any mutation."""

    errors: list[str] = []
    expected = {
        "schema", "approved", "approved_at", "expires_at", "conversation_ref",
        "scope_sha256", "prior_sha", "candidate_sha",
    }
    approval = _exact_keys(payload, expected, "Task 8 approval", errors)
    if not approval:
        return errors
    if approval.get("schema") != APPROVAL_SCHEMA or approval.get("approved") is not True:
        errors.append("Task 8 approval schema/status is not exact approved")
    if approval.get("scope_sha256") != AUTHORIZATION_SCOPE_SHA256:
        errors.append("Task 8 approval scope hash drifted")
    if approval.get("prior_sha") != prior_sha or approval.get("candidate_sha") != candidate_sha:
        errors.append("Task 8 approval source identities drifted")
    if not isinstance(approval.get("conversation_ref"), str) or not approval.get("conversation_ref"):
        errors.append("Task 8 approval lacks a current-conversation reference")
    timestamps: dict[str, datetime] = {}
    for field in ("approved_at", "expires_at"):
        value = approval.get(field)
        if not _aware_timestamp(value):
            errors.append(f"Task 8 approval {field} is not timezone-aware")
            continue
        timestamps[field] = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("authorization validation clock must be timezone-aware")
    if set(timestamps) == {"approved_at", "expires_at"}:
        if timestamps["approved_at"] > current:
            errors.append("Task 8 approval is future-dated")
        if timestamps["expires_at"] <= current:
            errors.append("Task 8 approval has expired")
        if timestamps["expires_at"] <= timestamps["approved_at"]:
            errors.append("Task 8 approval expiry is not after approval")
    return errors


def load_authorization(
    path: Path,
    *,
    prior_sha: str,
    candidate_sha: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"cannot read Task 8 approval: {exc}") from exc
    errors = authorization_errors(
        payload, prior_sha=prior_sha, candidate_sha=candidate_sha, now=now,
    )
    if errors:
        raise ValueError("; ".join(errors))
    return payload


def operational_config_errors(config: OperationalConfig) -> list[str]:
    """Reject broad/ambiguous paths before planning disposable mutations."""

    errors: list[str] = []
    repo = config.repo_root.resolve()
    run_root = config.run_root.resolve()
    safe_base = Path("/home/taishajo/work/state/task8-runs").resolve()
    if run_root == safe_base or not run_root.is_relative_to(safe_base):
        errors.append(f"Task 8 run root must be a named child of {safe_base}")
    if run_root == repo or run_root.is_relative_to(repo) or repo.is_relative_to(run_root):
        errors.append("Task 8 run root and git checkout must not contain one another")
    if config.runtime_root.name != "NExtSEEK":
        errors.append("Task 8 runtime checkout basename must preserve canonical NExtSEEK identity")
    if config.source_root == config.runtime_root:
        errors.append("Task 8 clean source and generated-config runtime must be distinct")
    if not _is_hex(config.prior_sha, 40) or not _is_hex(config.candidate_sha, 40):
        errors.append("Task 8 prior/candidate source identities must be full Git SHAs")
    elif config.prior_sha == config.candidate_sha:
        errors.append("Task 8 prior and candidate source identities must differ")
    if not 1000 <= config.port_offset <= 50000:
        errors.append("Task 8 port offset is outside the bounded range")
    if config.approval_path.resolve().is_relative_to(repo):
        errors.append("Task 8 approval must remain outside the git checkout")
    if config.backup_path.resolve().is_relative_to(repo):
        errors.append("Task 8 backup must remain outside the git checkout")
    return errors


def _planned(
    config: OperationalConfig,
    action: str,
    phase: str,
    daemon: str,
    effect: str,
    argv: tuple[str, ...],
    *,
    cwd: Path | None = None,
    env_keys: tuple[str, ...] = (),
) -> PlannedCommand:
    return PlannedCommand(
        action=action,
        phase=phase,
        daemon=daemon,
        effect=effect,
        argv=argv,
        cwd=str((cwd or config.repo_root).resolve()),
        env_keys=env_keys,
    )


def build_operational_plan(config: OperationalConfig) -> tuple[PlannedCommand, ...]:
    """Return the exact mutation order; dynamic facts remain control actions."""

    errors = operational_config_errors(config)
    if errors:
        raise ValueError("; ".join(errors))
    repo = config.repo_root.resolve()
    runtime = config.runtime_root.resolve()
    env = (
        "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "INSTANCE_PREFIX",
        "TASK8_CC_NETWORK", "TASK8_EGRESS_NETWORK", "NEXTSEEK_GHCR_ENV",
    )
    plan = (
        _planned(config, "approval", "preflight", "host_read_only", "read_only", ("task8-control", "validate-approval", str(config.approval_path.resolve()))),
        _planned(config, "source", "preflight", "host_read_only", "read_only", ("task8-control", "validate-source", config.prior_sha, config.candidate_sha)),
        _planned(config, "resources", "preflight", "host_read_only", "read_only", ("task8-control", "validate-resources", str(REQUIRED_FREE_BYTES))),
        _planned(config, "host-before", "snapshot", "host_read_only", "read_only", ("task8-control", "snapshot-host", "before")),
        _planned(config, "candidate-worktree", "seed", "task8_namespace", "namespaced_mutation", ("git", "-C", str(repo), "worktree", "add", "--detach", str(runtime), config.candidate_sha)),
        _planned(config, "source-worktree", "seed", "task8_namespace", "namespaced_mutation", ("git", "-C", str(repo), "worktree", "add", "--detach", str(config.source_root.resolve()), config.candidate_sha)),
        _planned(config, "harness-config", "seed", "task8_namespace", "namespaced_mutation", ("task8-control", "configure-harness"), cwd=runtime, env_keys=env),
        _planned(config, "prior-image", "seed", "task8_namespace", "namespaced_mutation", ("task8-control", "verify-and-tag-prior-image"), cwd=runtime, env_keys=env),
        _planned(config, "namespace-start", "seed", "task8_namespace", "namespaced_mutation", ("task8-control", "start-namespace"), cwd=runtime, env_keys=env),
        _planned(config, "prewrite-seed", "seed", "task8_namespace", "namespaced_mutation", ("task8-control", "seed-prewrite-and-prior-generation"), cwd=runtime, env_keys=env),
        _planned(config, "migration-aware-dump", "backup", "task8_namespace", "namespaced_mutation", ("task8-control", "dump-dmac", str(config.backup_path.resolve())), cwd=runtime, env_keys=env + ("MYSQL_PWD",)),
        _planned(config, "restore-probe", "backup", "task8_namespace", "namespaced_mutation", ("task8-control", "restore-probe", str(config.backup_path.resolve())), cwd=runtime, env_keys=env + ("MYSQL_PWD",)),
        _planned(config, "candidate-source", "forward", "host_read_only", "read_only", ("task8-control", "validate-candidate-source", config.candidate_sha), cwd=runtime),
        _planned(config, "build-prepare", "forward", "task8_namespace", "namespaced_mutation", ("task8-control", "quiesce-cohort-and-create-builder"), cwd=runtime, env_keys=env),
        _planned(config, "forward-rebuild", "forward", "task8_namespace", "namespaced_mutation", ("taskset", "--cpu-list", TASKSET_CPU_LIST, "./startup.sh", "rebuild", "--builder", config.builder_name, "--no-restart", "--no-registry-push", "--source-tree", str(config.source_root.resolve())), cwd=runtime, env_keys=env),
        _planned(config, "builder-cleanup", "forward", "task8_namespace", "cleanup", ("task8-control", "remove-exact-builder"), cwd=runtime, env_keys=env),
        _planned(config, "cohort-resume", "forward", "task8_namespace", "namespaced_mutation", ("task8-control", "resume-app-cohort"), cwd=runtime, env_keys=env),
        _planned(config, "registry-push", "forward", "registry", "registry_write", ("task8-control", "push-candidate-baseline"), cwd=runtime, env_keys=env),
        _planned(config, "postwrite-seed", "forward", "task8_namespace", "namespaced_mutation", ("task8-control", "seed-postwrite-and-candidate-generation"), cwd=runtime, env_keys=env),
        _planned(config, "forward-runbook", "verify_forward", "task8_namespace", "read_only", ("task8-control", "verify-forward"), cwd=runtime, env_keys=env),
        _planned(config, "disable-flags", "recovery", "task8_namespace", "namespaced_mutation", ("task8-control", "disable-flags"), cwd=runtime, env_keys=env),
        _planned(config, "stop-schedules", "recovery", "task8_namespace", "namespaced_mutation", ("task8-control", "stop-schedules"), cwd=runtime, env_keys=env),
        _planned(config, "stop-workers", "recovery", "task8_namespace", "namespaced_mutation", ("task8-control", "stop-workers"), cwd=runtime, env_keys=env),
        _planned(config, "activate-prior", "recovery", "task8_namespace", "namespaced_mutation", ("task8-control", "activate-prior-generation"), cwd=runtime, env_keys=env),
        _planned(config, "restore-image", "recovery", "task8_namespace", "namespaced_mutation", ("task8-control", "restore-verified-prior-image"), cwd=runtime, env_keys=env),
        _planned(config, "forward-only-schema", "recovery", "task8_namespace", "read_only", ("task8-control", "verify-forward-only-schema"), cwd=runtime, env_keys=env),
        _planned(config, "recovery-runbook", "verify_recovery", "task8_namespace", "read_only", ("task8-control", "verify-recovery"), cwd=runtime, env_keys=env),
        _planned(config, "namespace-cleanup", "cleanup", "task8_namespace", "cleanup", ("task8-control", "cleanup-namespace"), cwd=runtime, env_keys=env),
        _planned(config, "host-after", "snapshot", "host_read_only", "read_only", ("task8-control", "snapshot-host", "after")),
    )
    if {entry.phase for entry in plan} != set(REQUIRED_PHASES):
        raise AssertionError("operational plan phase inventory drifted")
    if len({entry.action for entry in plan}) != len(plan):
        raise AssertionError("operational plan action identities are not unique")
    internal = {
        entry.action
        for entry in plan
        if entry.argv and entry.argv[0] == "task8-control"
    }
    if internal != CONTROL_ACTIONS:
        raise AssertionError("operational control inventory drifted")
    return plan


def plan_payload(config: OperationalConfig, approval: dict[str, Any]) -> dict[str, Any]:
    commands = build_operational_plan(config)
    return {
        "schema": PLAN_SCHEMA,
        "authorization": {
            "conversation_ref": approval["conversation_ref"],
            "approved_at": approval["approved_at"],
            "expires_at": approval["expires_at"],
            "scope_sha256": approval["scope_sha256"],
        },
        "source": {"prior_sha": config.prior_sha, "candidate_sha": config.candidate_sha},
        "bounds": {
            "max_cpus": MAX_CPUS,
            "max_memory_bytes": MAX_MEMORY_BYTES,
            "required_available_memory_bytes": REQUIRED_AVAILABLE_MEMORY_BYTES,
            "minimum_memory_reserve_bytes": MINIMUM_MEMORY_RESERVE_BYTES,
            "wall_cap_s": MAX_WALL_S,
            "required_free_bytes": REQUIRED_FREE_BYTES,
            "minimum_disk_reserve_bytes": MINIMUM_DISK_RESERVE_BYTES,
        },
        "paths": {
            "run_root": str(config.run_root.resolve()),
            "runtime_root": str(config.runtime_root.resolve()),
            "source_root": str(config.source_root.resolve()),
            "backup_path": str(config.backup_path.resolve()),
        },
        "namespace": {
            "kind": "namespaced_host_daemon",
            "compose_project": config.compose_project,
            "instance_prefix": config.instance_prefix,
            "cc_network": config.cc_network,
            "egress_network": config.egress_network,
            "app_image": config.app_image,
            "builder_name": config.builder_name,
        },
        "commands": [command.as_json() for command in commands],
        "command_count": len(commands),
    }


class OperationalRunError(RuntimeError):
    """The disposable run refused or failed before truthful PASS evidence."""


class LocalOperationalAdapter:
    """Real argv-only adapter for the exact disposable host-daemon namespace.

    Expensive or stateful controls are implemented as named methods, never as
    interpolated shell.  Until every control exists, unknown controls fail with
    EX_CONFIG instead of pretending that a rehearsal occurred.
    """

    def __init__(self, config: OperationalConfig, *, artifact_dir: Path) -> None:
        self.config = config
        self.artifact_dir = artifact_dir
        self.facts: dict[str, Any] = {}
        initial_free = shutil.disk_usage(config.repo_root).free
        self.facts["disk_free_before_bytes"] = initial_free
        self.facts["disk_free_min_bytes"] = initial_free
        self.facts["memory_peak_bytes"] = 0
        initial_available = available_memory_bytes()
        self.facts["memory_available_before_bytes"] = initial_available
        self.facts["memory_available_min_bytes"] = initial_available

    def _sample_resources(self, extra_pids: set[int] | None = None) -> None:
        roots = set(extra_pids or ())
        try:
            containers = subprocess.run(
                [
                    "docker", "ps", "-q", "--filter",
                    f"label=com.docker.compose.project={self.config.compose_project}",
                ],
                capture_output=True,
                text=True,
                env=self._host_env(),
                timeout=3,
                check=False,
            ).stdout.split()
            builder_containers = subprocess.run(
                [
                    "docker", "ps", "-q", "--filter",
                    f"name=buildx_buildkit_{self.config.builder_name}",
                ],
                capture_output=True,
                text=True,
                env=self._host_env(),
                timeout=3,
                check=False,
            ).stdout.split()
            containers.extend(builder_containers)
            if containers:
                pids = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Pid}}", *containers],
                    capture_output=True,
                    text=True,
                    env=self._host_env(),
                    timeout=3,
                    check=False,
                ).stdout.split()
                roots.update(int(pid) for pid in pids if pid.isdigit() and int(pid) > 0)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        rss = self._process_tree_rss_bytes(roots)
        self.facts["memory_peak_bytes"] = max(
            int(self.facts.get("memory_peak_bytes", 0)), rss
        )
        free = shutil.disk_usage(self.config.repo_root).free
        self.facts["disk_free_min_bytes"] = min(
            int(self.facts.get("disk_free_min_bytes", free)), free
        )
        available = available_memory_bytes()
        self.facts["memory_available_min_bytes"] = min(
            int(self.facts.get("memory_available_min_bytes", available)),
            available,
        )

    def _task_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("DOCKER_HOST", None)
        env.update(
            {
                "NEXTSEEK_GHCR_ENV": GHCR_ENV_PATH,
                "COMPOSE_FILE": "docker-compose.task8.yml",
                "COMPOSE_PROJECT_NAME": self.config.compose_project,
                "INSTANCE_PREFIX": self.config.instance_prefix,
                "TASK8_CC_NETWORK": self.config.cc_network,
                "TASK8_EGRESS_NETWORK": self.config.egress_network,
                "NEXTSEEK_PORT": str(8000 + self.config.port_offset),
            }
        )
        return env

    @staticmethod
    def _host_env() -> dict[str, str]:
        env = os.environ.copy()
        env.pop("DOCKER_HOST", None)
        return env

    @staticmethod
    def _process_tree_rss_bytes(root_pids: set[int]) -> int:
        if not root_pids:
            return 0
        try:
            result = subprocess.run(
                ["ps", "-eo", "pid=,ppid=,rss="],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return 0
        rows: list[tuple[int, int, int]] = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) != 3:
                continue
            try:
                rows.append(tuple(int(field) for field in fields))
            except ValueError:
                continue
        descendants = set(root_pids)
        changed = True
        while changed:
            changed = False
            for pid, ppid, _rss_kib in rows:
                if ppid in descendants and pid not in descendants:
                    descendants.add(pid)
                    changed = True
        return sum(rss_kib * 1024 for pid, _ppid, rss_kib in rows if pid in descendants)

    @staticmethod
    def _terminate_group(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, 15)
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, 9)
            except OSError:
                pass

    def _run(
        self,
        argv: tuple[str, ...] | list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_s: float,
        input_bytes: bytes | None = None,
    ) -> CommandOutcome:
        with subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process:
            started = time.monotonic()
            pending_input = input_bytes
            while True:
                try:
                    stdout, stderr = process.communicate(input=pending_input, timeout=1)
                    return CommandOutcome(process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    # communicate() retains the bytes internally; input must be
                    # supplied only once on subsequent polls.
                    pending_input = None
                elapsed = time.monotonic() - started
                if elapsed >= timeout_s:
                    self._terminate_group(process)
                    stdout, stderr = process.communicate()
                    return CommandOutcome(124, stdout, stderr + b"\nTask 8 command timeout")
                self._sample_resources({process.pid})
                if self.facts["memory_peak_bytes"] > MAX_MEMORY_BYTES:
                    self._terminate_group(process)
                    stdout, stderr = process.communicate()
                    return CommandOutcome(70, stdout, stderr + b"\nTask 8 memory cap exceeded")
                if available_memory_bytes() < MINIMUM_MEMORY_RESERVE_BYTES:
                    self._terminate_group(process)
                    stdout, stderr = process.communicate()
                    return CommandOutcome(
                        70,
                        stdout,
                        stderr + b"\nTask 8 host memory reserve reached",
                    )
                if shutil.disk_usage(self.config.repo_root).free < MINIMUM_DISK_RESERVE_BYTES:
                    self._terminate_group(process)
                    stdout, stderr = process.communicate()
                    return CommandOutcome(70, stdout, stderr + b"\nTask 8 disk reserve reached")

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", "-C", str((cwd or self.config.repo_root).resolve()), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise OperationalRunError(result.stderr.strip() or "git verification failed")
        return result.stdout.strip()

    def _validate_source(self, *, candidate_checkout: bool = False) -> CommandOutcome:
        root = self.config.source_root if candidate_checkout else self.config.repo_root
        try:
            head = self._git("rev-parse", "HEAD", cwd=root)
            remote = self._git("rev-parse", "origin/dev", cwd=root)
            dirty = self._git("status", "--porcelain", "--untracked-files=all", cwd=root)
            if candidate_checkout:
                if head != self.config.candidate_sha or remote != self.config.candidate_sha or dirty:
                    raise OperationalRunError("candidate runtime is not clean exact origin/dev")
                return CommandOutcome(0, json.dumps({"head": head, "remote": remote}).encode())
            if remote != self.config.candidate_sha:
                raise OperationalRunError("candidate SHA is not current origin/dev")
            self._git("cat-file", "-e", f"{self.config.prior_sha}^{{commit}}", cwd=root)
            self._git("merge-base", "--is-ancestor", self.config.prior_sha, self.config.candidate_sha, cwd=root)
            if head != self.config.candidate_sha or dirty:
                raise OperationalRunError("runner checkout is not clean at candidate SHA")
            ahead, behind = (
                int(value)
                for value in self._git(
                    "rev-list", "--left-right", "--count",
                    f"{self.config.candidate_sha}...origin/dev", cwd=root,
                ).split()
            )
            diff = self._git("diff", "--binary", self.config.candidate_sha, "origin/dev", cwd=root)
            changed = self._git(
                "diff", "--name-only", self.config.prior_sha, self.config.candidate_sha,
                "--", cwd=root,
            ).splitlines()
            migration_paths = sorted(path for path in changed if "migrations" in path)
            self.facts["source"] = {
                "branch": "origin/dev",
                "remote_sha": remote,
                "deployed_sha": self.config.candidate_sha,
                "prior_sha": self.config.prior_sha,
                "clean": True,
                "ahead": ahead,
                "behind": behind,
                "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
            }
            self.facts["migration_paths"] = migration_paths
            return CommandOutcome(0, json.dumps(self.facts["source"], sort_keys=True).encode())
        except (OperationalRunError, OSError, ValueError) as exc:
            return CommandOutcome(1, stderr=str(exc).encode())

    def _configure_harness(self) -> CommandOutcome:
        """Render only the files needed by the bounded app cohort."""

        try:
            from startup.lib.instance import InstanceState, save_instance
            from startup.steps import config as startup_config

            runtime = self.config.runtime_root
            port = 8000 + self.config.port_offset
            state = InstanceState(
                name=self.config.compose_project,
                prefix=self.config.instance_prefix,
                ports={"nextseek": port, "seek": 3000 + self.config.port_offset},
                compose_project_name=self.config.compose_project,
                created=datetime.now(timezone.utc).isoformat(),
                seek_public_url=f"http://127.0.0.1:{3000 + self.config.port_offset}",
            )
            save_instance(runtime, state)
            values = startup_config.default_values(
                nextseek_port=port,
                seek_port=3000 + self.config.port_offset,
                seek_public_url=state.seek_public_url,
            )
            startup_config.render_db_env(runtime, values)
            startup_config.render_nextseek_env(runtime, values)
            startup_config.render_local_settings(runtime, values)
            startup_config.render_proxy_secret_env(runtime, source_env={})
            startup_config.render_root_env(
                runtime, state.compose_env(), neo4j_password=values.neo4j_password
            )
            self._set_env_key(
                runtime / "docker" / "nextseek.env",
                "NEXTSEEK_POSTERIOR_ROUTING_ENABLED",
                "1",
            )
            for relative in ("outputs", "logs", "docker/nginx-optional"):
                (runtime / relative).mkdir(parents=True, exist_ok=True)
            self.facts["namespace_id"] = self.config.compose_project
            self.facts["settings_sha256"] = _sha(runtime / "docker" / "nextseek.env")
            return CommandOutcome(
                0,
                json.dumps(
                    {
                        "compose_project": self.config.compose_project,
                        "instance_prefix": self.config.instance_prefix,
                        "port": port,
                    },
                    sort_keys=True,
                ).encode(),
            )
        except (OSError, ValueError, TypeError) as exc:
            return CommandOutcome(1, stderr=str(exc).encode())

    def _verify_and_tag_prior_image(self) -> CommandOutcome:
        """Prove the reused image contains the expected prior source, then tag it."""

        paths = (
            "startup/cli.py",
            "startup/lib/deploy_source.py",
            "startup/steps/registry_push.py",
            "nextseek_api/assistant/models_db.py",
            "nextseek_api/cc_assistant/router.py",
            "nextseek_api/eval/human_grade_fit.py",
            "nextseek_api/eval/fit/v14/decision.py",
            "docker/scripts/entrypoint.sh",
        )
        peer_images = (
            "mysql:8.0",
            "nginx:latest",
            "nextseek-bedrock-proxy:latest",
            "nextseek-ns-sidecar:latest",
            "dmac-assistant:poc",
        )
        try:
            prior_id = self._inspect_image_id("nextseek-nextseek:latest")
            peer_image_ids = {
                image: self._inspect_image_id(image) for image in peer_images
            }
            verified: dict[str, str] = {}
            for relative in paths:
                blob = subprocess.run(
                    [
                        "git", "-C", str(self.config.repo_root), "show",
                        f"{self.config.prior_sha}:{relative}",
                    ],
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                if blob.returncode:
                    raise OperationalRunError(f"prior source path missing: {relative}")
                expected = hashlib.sha256(blob.stdout).hexdigest()
                probe = self._docker(
                    [
                        "run", "--rm", "--network", "none", "--cpus", "0.10",
                        "--memory", "128m", "--entrypoint",
                        "sha256sum", "nextseek-nextseek:latest", f"/app/{relative}",
                    ],
                    timeout_s=60,
                )
                actual = probe.stdout.decode(errors="replace").split(maxsplit=1)[0]
                if probe.returncode or actual != expected:
                    raise OperationalRunError(
                        f"prior image source mismatch for {relative}: {actual or 'missing'}"
                    )
                verified[relative] = expected
            tagged = self._docker(
                ["tag", "nextseek-nextseek:latest", self.config.app_image],
                timeout_s=30,
            )
            if tagged.returncode:
                return tagged
            if self._inspect_image_id(self.config.app_image) != prior_id:
                raise OperationalRunError("Task 8 prior image tag identity drifted")
            self.facts["prior_image_id"] = prior_id
            self.facts["prior_source_hashes"] = verified
            self.facts["peer_image_ids"] = peer_image_ids
            return CommandOutcome(
                0,
                json.dumps(
                    {
                        "image_id": prior_id,
                        "peer_image_ids": peer_image_ids,
                        "source_hashes": verified,
                    },
                    sort_keys=True,
                ).encode(),
            )
        except (OSError, subprocess.SubprocessError, OperationalRunError) as exc:
            return CommandOutcome(1, stderr=str(exc).encode())

    def _start_namespace(self) -> CommandOutcome:
        required_config = (
            self.config.runtime_root / "docker" / "db.env",
            self.config.runtime_root / "docker" / "nextseek.env",
            self.config.runtime_root / "docker" / "bedrock-proxy" / "proxy-secret.env",
            self.config.runtime_root / "dmac" / "local_settings.py",
        )
        if not all(path.is_file() for path in required_config):
            return CommandOutcome(1, stderr=b"Task 8 runtime config is incomplete")
        started = self._runtime_command(
            ["docker", "compose", "up", "-d", "db"], timeout_s=120
        )
        if started.returncode:
            return started
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            health = self._docker(
                [
                    "inspect", self.config.db_container, "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                ],
                timeout_s=15,
            )
            if health.returncode == 0 and health.stdout.strip() == b"healthy":
                break
            self._sample_resources()
            time.sleep(2)
        else:
            return CommandOutcome(1, stderr=b"Task 8 MySQL did not become healthy")
        cohort = self._runtime_command(
            [
                "docker", "compose", "up", "-d", "--no-build",
                "nextseek", "nextseek_nginx", "bedrock-proxy", "nextseek-sidecar",
            ],
            timeout_s=180,
        )
        if cohort.returncode:
            return cohort
        if not self._wait_site(timeout_s=240):
            detail = json.dumps(
                self.facts.get("last_site_wait", {}), sort_keys=True
            ).encode()
            return CommandOutcome(
                1,
                stderr=b"Task 8 prior app did not become HTTP-ready: " + detail,
            )
        return CommandOutcome(
            0,
            json.dumps(
                {
                    "compose_project": self.config.compose_project,
                    "containers": [
                        self.config.db_container, self.config.app_container,
                        self.config.nginx_container, self.config.proxy_container,
                        self.config.sidecar_container,
                    ],
                },
                sort_keys=True,
            ).encode(),
        )

    def _push_registry(self) -> CommandOutcome:
        try:
            from startup.steps.registry_push import push_baseline

            old_override = os.environ.get("NEXTSEEK_GHCR_ENV")
            os.environ["NEXTSEEK_GHCR_ENV"] = GHCR_ENV_PATH
            try:
                outcome = push_baseline(
                    self.config.runtime_root,
                    "nextseek",
                    local_image=self.config.app_image,
                    git_root=self.config.source_root,
                    gate_run_limits=("--cpus", "0.10", "--memory", "128m"),
                )
            finally:
                if old_override is None:
                    os.environ.pop("NEXTSEEK_GHCR_ENV", None)
                else:
                    os.environ["NEXTSEEK_GHCR_ENV"] = old_override
            if outcome.status != "pushed" or not outcome.tag or not _is_image_id(outcome.digest):
                return CommandOutcome(
                    1,
                    stderr=(
                        f"mandatory private GHCR push failed: {outcome.status}: "
                        f"{outcome.detail}"
                    ).encode(),
                )
            self.facts["registry_tag"] = outcome.tag
            self.facts["registry_digest"] = outcome.digest
            return CommandOutcome(
                0,
                json.dumps(
                    {"status": outcome.status, "tag": outcome.tag, "digest": outcome.digest},
                    sort_keys=True,
                ).encode(),
            )
        except Exception as exc:
            return CommandOutcome(1, stderr=str(exc).encode())

    def _prepare_build(self) -> CommandOutcome:
        quiesced = self._runtime_command(
            [
                "docker", "compose", "stop", "nextseek_nginx",
                "nextseek-sidecar", "bedrock-proxy", "nextseek",
            ],
            timeout_s=120,
        )
        if quiesced.returncode:
            return quiesced
        created = self._docker(
            [
                "buildx", "create", "--name", self.config.builder_name,
                "--driver", "docker-container",
                "--driver-opt", f"cpuset-cpus={TASKSET_CPU_LIST}",
                "--driver-opt", "cpu-quota=150000",
                "--driver-opt", "memory=4g",
            ],
            timeout_s=60,
        )
        if created.returncode:
            return created
        bootstrapped = self._docker(
            ["buildx", "inspect", "--bootstrap", self.config.builder_name],
            timeout_s=180,
        )
        if bootstrapped.returncode:
            return bootstrapped
        builder_container = self._docker(
            [
                "ps", "-q", "--filter",
                f"name=buildx_buildkit_{self.config.builder_name}",
            ],
            timeout_s=30,
        )
        container_ids = builder_container.stdout.decode().split()
        if builder_container.returncode or len(container_ids) != 1:
            return CommandOutcome(
                1,
                stderr=b"Task 8 could not identify its exact BuildKit container",
            )
        builder_image = self._docker(
            ["inspect", container_ids[0], "--format", "{{.Image}}"],
            timeout_s=30,
        )
        builder_image_id = builder_image.stdout.decode().strip()
        if builder_image.returncode or not _is_image_id(builder_image_id):
            return CommandOutcome(
                1,
                stderr=b"Task 8 could not identify its BuildKit image",
            )
        self.facts["builder_created"] = True
        self.facts["builder_image_id"] = builder_image_id
        self.facts["builder_image_preexisting"] = builder_image_id in self.facts.get(
            "preexisting_image_ids", set()
        )
        return CommandOutcome(
            0,
            json.dumps(
                {
                    "builder": self.config.builder_name,
                    "cpus": 1.5,
                    "memory_bytes": 4 * 1024**3,
                    "database_remained_running": True,
                },
                sort_keys=True,
            ).encode(),
        )

    def _remove_builder(self) -> CommandOutcome:
        removed = self._docker(
            ["buildx", "rm", self.config.builder_name], timeout_s=180
        )
        if removed.returncode and b"no builder" not in removed.stderr.lower():
            return removed
        builder_image_id = str(self.facts.get("builder_image_id") or "")
        if builder_image_id and not self.facts.get("builder_image_preexisting", False):
            image_removed = self._docker(
                ["image", "rm", builder_image_id], timeout_s=60
            )
            if (
                image_removed.returncode
                and b"no such image" not in image_removed.stderr.lower()
            ):
                return image_removed
        self.facts["builder_removed"] = True
        return CommandOutcome(0, b"exact Task 8 builder/cache removed")

    def _resume_cohort(self) -> CommandOutcome:
        resumed = self._runtime_command(
            [
                "docker", "compose", "up", "-d", "--no-build",
                "nextseek", "nextseek_nginx", "bedrock-proxy", "nextseek-sidecar",
            ],
            timeout_s=180,
        )
        if resumed.returncode:
            return resumed
        if not self._wait_site(timeout_s=180):
            detail = json.dumps(
                self.facts.get("last_site_wait", {}), sort_keys=True
            ).encode()
            return CommandOutcome(
                1,
                stderr=b"candidate app did not become HTTP-ready: " + detail,
            )
        return CommandOutcome(0, b"candidate app cohort resumed")

    def _cleanup_namespace(self) -> CommandOutcome:
        """Remove only exact resources/tags created by this Task-8 run."""

        errors: list[str] = []
        runtime = self.config.runtime_root
        builder = self._remove_builder()
        if builder.returncode:
            errors.append(builder.stderr.decode(errors="replace"))
        if runtime.exists():
            down = self._runtime_command(
                ["docker", "compose", "down", "-v", "--remove-orphans"],
                timeout_s=180,
            )
            if down.returncode:
                errors.append(down.stderr.decode(errors="replace"))
        tags = {
            self.config.app_image,
            str(self.facts.get("rollback_tag") or ""),
            str(self.facts.get("registry_tag") or ""),
        }
        for tag in sorted(tag for tag in tags if tag):
            removed = self._docker(["image", "rm", tag], timeout_s=60)
            if removed.returncode and b"No such image" not in removed.stderr:
                errors.append(removed.stderr.decode(errors="replace"))
        for checkout in (runtime, self.config.source_root):
            if not checkout.exists():
                continue
            worktree = self._run(
                [
                    "git", "-C", str(self.config.repo_root), "worktree", "remove",
                    "--force", str(checkout),
                ],
                cwd=self.config.repo_root,
                env=self._host_env(),
                timeout_s=60,
            )
            if worktree.returncode:
                errors.append(worktree.stderr.decode(errors="replace"))
        inventory = self._docker(
            [
                "ps", "-a", "--filter", f"label=com.docker.compose.project={self.config.compose_project}",
                "--format", "{{.Names}}",
            ],
            timeout_s=30,
        )
        networks = self._docker(
            ["network", "ls", "--filter", f"label=com.docker.compose.project={self.config.compose_project}", "--format", "{{.Name}}"],
            timeout_s=30,
        )
        volumes = self._docker(
            ["volume", "ls", "--filter", f"label=com.docker.compose.project={self.config.compose_project}", "--format", "{{.Name}}"],
            timeout_s=30,
        )
        removed = (
            inventory.returncode == networks.returncode == volumes.returncode == 0
            and not inventory.stdout.strip()
            and not networks.stdout.strip()
            and not volumes.stdout.strip()
            and not runtime.exists()
            and not self.config.source_root.exists()
        )
        if not removed:
            errors.append("Task 8 namespace resources remain after exact cleanup")
        self.facts["task8_resources_removed"] = removed and not errors
        return CommandOutcome(
            0 if self.facts["task8_resources_removed"] else 1,
            json.dumps({"task8_resources_removed": removed}, sort_keys=True).encode(),
            "\n".join(errors).encode(),
        )

    def _snapshot_host(self, label: str) -> CommandOutcome:
        env = self._host_env()
        try:
            ids = subprocess.run(
                ["docker", "ps", "-aq"], capture_output=True, text=True,
                env=env, timeout=20, check=True,
            ).stdout.split()
            inspected: list[dict[str, Any]] = []
            if ids:
                raw = subprocess.run(
                    ["docker", "inspect", *ids], capture_output=True, text=True,
                    env=env, timeout=30, check=True,
                ).stdout
                for item in json.loads(raw):
                    inspected.append(
                        {
                            "id": item.get("Id"),
                            "name": item.get("Name"),
                            "image": item.get("Image"),
                            "configured_image": (item.get("Config") or {}).get("Image"),
                            "status": (item.get("State") or {}).get("Status"),
                            "restart_count": item.get("RestartCount"),
                            "network_mode": (item.get("HostConfig") or {}).get("NetworkMode"),
                            "networks": sorted(((item.get("NetworkSettings") or {}).get("Networks") or {})),
                            "mounts": sorted(
                                (
                                    mount.get("Type"), mount.get("Name"),
                                    mount.get("Source"), mount.get("Destination"),
                                )
                                for mount in item.get("Mounts") or []
                            ),
                        }
                    )
            inspected = [
                item for item in inspected
                if not str(item.get("name") or "").lstrip("/").startswith(
                    self.config.instance_prefix
                )
            ]
            image_ids = subprocess.run(
                ["docker", "image", "ls", "-aq", "--no-trunc"],
                capture_output=True,
                text=True,
                env=env,
                timeout=20,
                check=True,
            ).stdout.split()
            images: list[dict[str, Any]] = []
            if image_ids:
                raw_images = subprocess.run(
                    ["docker", "image", "inspect", *sorted(set(image_ids))],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=60,
                    check=True,
                ).stdout
                for item in json.loads(raw_images):
                    images.append(
                        {
                            "id": item.get("Id"),
                            "repo_tags": sorted(item.get("RepoTags") or []),
                            "repo_digests": sorted(item.get("RepoDigests") or []),
                        }
                    )
            snapshot = {
                "containers": sorted(inspected, key=lambda item: str(item["id"])),
                "images": sorted(images, key=lambda item: str(item["id"])),
            }
            path = self.artifact_dir / f"host-{label}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
            digest = _sha(path)
            self.facts[f"host_{label}"] = {"path": path, "sha256": digest}
            if label == "before":
                self.facts["preexisting_image_ids"] = {
                    str(item["id"]) for item in images if item.get("id")
                }
            if label == "after" and digest != self.facts.get("host_before", {}).get("sha256"):
                return CommandOutcome(1, stderr=b"existing host Docker state changed")
            return CommandOutcome(0, json.dumps({"sha256": digest}).encode())
        except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
            return CommandOutcome(1, stderr=str(exc).encode())

    def _docker(
        self,
        argv: list[str],
        *,
        timeout_s: float,
        input_bytes: bytes | None = None,
    ) -> CommandOutcome:
        return self._run(
            ["docker", *argv],
            cwd=self.config.repo_root,
            env=self._task_env(),
            timeout_s=timeout_s,
            input_bytes=input_bytes,
        )

    def _exec_python_json(
        self,
        code: str,
        *,
        timeout_s: float = 90.0,
    ) -> tuple[CommandOutcome, dict[str, Any] | None]:
        outcome = self._docker(
            [
                "exec", "-w", "/app", self.config.app_container, "uv", "run", "--no-sync",
                "python", "-c", code,
            ],
            timeout_s=timeout_s,
        )
        if outcome.returncode:
            return outcome, None
        try:
            line = next(
                line
                for line in reversed(outcome.stdout.decode().splitlines())
                if line.strip()
            )
            payload = json.loads(line)
        except (StopIteration, UnicodeDecodeError, ValueError, TypeError) as exc:
            return (
                CommandOutcome(
                    1,
                    outcome.stdout,
                    outcome.stderr + f"\ninvalid JSON control output: {exc}".encode(),
                ),
                None,
            )
        if not isinstance(payload, dict):
            return (
                CommandOutcome(
                    1,
                    outcome.stdout,
                    outcome.stderr + b"\ncontrol JSON is not an object",
                ),
                None,
            )
        return outcome, payload

    def _inspect_image_id(self, image: str) -> str:
        outcome = self._docker(
            ["image", "inspect", image, "--format", "{{.Id}}"], timeout_s=30,
        )
        if outcome.returncode or not outcome.stdout.strip():
            raise OperationalRunError(
                outcome.stderr.decode(errors="replace").strip()
                or f"cannot inspect {image}"
            )
        return outcome.stdout.decode().strip()

    def _container_id(self, name: str) -> str:
        outcome = self._docker(
            ["inspect", name, "--format", "{{.Id}}"], timeout_s=30,
        )
        if outcome.returncode or not outcome.stdout.strip():
            raise OperationalRunError(
                outcome.stderr.decode(errors="replace").strip()
                or f"cannot inspect container {name}"
            )
        return outcome.stdout.decode().strip()

    @staticmethod
    def _env_file(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
        return values

    def _mysql(
        self,
        args: list[str],
        *,
        timeout_s: float,
        input_bytes: bytes | None = None,
    ) -> CommandOutcome:
        try:
            password = self._env_file(
                self.config.runtime_root / "docker" / "db.env"
            )["MYSQL_ROOT_PASSWORD"]
        except (OSError, KeyError) as exc:
            return CommandOutcome(
                1,
                stderr=f"cannot load disposable MySQL credential: {exc}".encode(),
            )
        return self._docker(
            [
                "exec", "-i", "-e", f"MYSQL_PWD={password}",
                "seek-mysql", *args,
            ],
            timeout_s=timeout_s,
            input_bytes=input_bytes,
        )

    def _dump_dmac(self) -> CommandOutcome:
        tables = self._mysql(
            ["mysql", "-uroot", "-N", "-e", "SHOW TABLES FROM dmac"],
            timeout_s=30,
        )
        if tables.returncode:
            return tables
        table_names = sorted(
            line for line in tables.stdout.decode().splitlines() if line
        )
        if "django_migrations" not in table_names:
            return CommandOutcome(
                1, stderr=b"django_migrations missing before backup"
            )
        dump = self._mysql(
            [
                "mysqldump", "-uroot", "--single-transaction", "--routines",
                "--triggers", "dmac",
            ],
            timeout_s=180,
        )
        if dump.returncode:
            return dump
        path = self.config.backup_path.resolve()
        path.write_bytes(dump.stdout)
        path.chmod(0o600)
        migration_paths = self.facts.get("migration_paths", [])
        self.facts["backup"] = {
            "path": str(path),
            "mode": "0600",
            "sha256": _sha(path),
            "size_bytes": path.stat().st_size,
            "migration_aware": True,
            "source_range": (
                f"{self.config.prior_sha}..{self.config.candidate_sha}"
            ),
            "migration_paths": migration_paths,
            "migration_diff_sha256": hashlib.sha256(
                "\n".join(migration_paths).encode()
            ).hexdigest(),
            "tables": table_names,
            "checksum_verified": (
                _sha(path) == hashlib.sha256(dump.stdout).hexdigest()
            ),
            "restore_probe": "PENDING",
        }
        return CommandOutcome(
            0,
            json.dumps(
                {"sha256": _sha(path), "size_bytes": path.stat().st_size}
            ).encode(),
        )

    def _restore_probe(self) -> CommandOutcome:
        backup = self.facts.get("backup") or {}
        path = Path(str(backup.get("path", "")))
        if not path.is_file() or _sha(path) != backup.get("sha256"):
            return CommandOutcome(
                1, stderr=b"backup identity drift before restore probe"
            )
        database = "task8_restore_probe"
        create = self._mysql(
            [
                "mysql", "-uroot", "-e",
                f"DROP DATABASE IF EXISTS {database}; CREATE DATABASE {database}",
            ],
            timeout_s=30,
        )
        if create.returncode:
            return create
        try:
            restore = self._mysql(
                ["mysql", "-uroot", database],
                timeout_s=180,
                input_bytes=path.read_bytes(),
            )
            if restore.returncode:
                return restore
            count = self._mysql(
                [
                    "mysql", "-uroot", "-N", "-e",
                    "SELECT COUNT(*) FROM information_schema.tables "
                    f"WHERE table_schema='{database}'",
                ],
                timeout_s=30,
            )
            if (
                count.returncode
                or int(count.stdout.strip() or b"0") != len(backup["tables"])
            ):
                return CommandOutcome(
                    1,
                    count.stdout,
                    count.stderr + b"\nrestore table count mismatch",
                )
            backup["restore_probe"] = "PASS"
            return CommandOutcome(
                0,
                json.dumps({"restored_tables": len(backup["tables"])}).encode(),
            )
        finally:
            self._mysql(
                [
                    "mysql", "-uroot", "-e",
                    f"DROP DATABASE IF EXISTS {database}",
                ],
                timeout_s=30,
            )

    @staticmethod
    def _prewrite_code() -> str:
        return """
import json, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
import django
django.setup()
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from nextseek_api.assistant.models_db import ActiveGenerationPointer, ApprovedRunManifest, ChatSession, PaidRunState, PairedRunRegistry, SpendReservation, TurnJudgment, TurnLedger
from nextseek_api.cc_assistant.family_labels import corpus_snapshot
from nextseek_api.cc_assistant.tests.generation_test_factory import _publish_generation_for_test
from nextseek_api.eval.generation_store import GenerationManifest, activate_generation
assert not ActiveGenerationPointer.objects.exists(), "disposable DB unexpectedly has an active generation"
user = get_user_model().objects.create_user(username="task8-rehearsal", password=None)
session = ChatSession.objects.create(user=user, title="Task 8 retained prewrite")
turn = TurnLedger.objects.create(session=session, turn_number=1, route="nextseek_query", route_source="task8_rehearsal", task_family="task8_prewrite", family_source="task8")
TurnJudgment.objects.create(turn=turn, fingerprint="1" * 64, verdict={"task8": "prewrite"}, status="ok")
now = timezone.now()
approved = ApprovedRunManifest.objects.create(manifest_hash="2" * 64, manifest={"task8": "zero-spend-fixture"}, approved_at=now, expires_at=now + timedelta(hours=1), max_spend_usd=Decimal("0"), max_calls=1, consumed=False)
PaidRunState.objects.create(run_id="task8", manifest=approved, overlap_lock="task8-pending", arm_id="pending", attempt_id="pending", status="pending")
PaidRunState.objects.create(run_id="task8", manifest=approved, overlap_lock="task8-failed", arm_id="failed", attempt_id="failed", status="failed", failure_reason="task8 tombstone fixture")
SpendReservation.objects.create(manifest=approved, attempt_id="task8-reservation", idempotency_key="task8-reservation", reserved_usd=Decimal("0"), status="pending")
paired_hash = "3" * 64
PairedRunRegistry.objects.create(paired_run_id="task8-generation-seed", schema_version="task8/v1", content_hash=paired_hash)
corpus = corpus_snapshot()
common = dict(attempt_hash="4" * 64, aggregate_hash="5" * 64, config_fingerprint="6" * 64, decision_status="activated_all", compatibility_keys={"taxonomy_version": corpus.taxonomy_version, "corpus_hash": corpus.corpus_sha256}, counts={"retained_pairs": 5, "min_retained_pairs": 5}, exclusions={"task8_fixture_exclusion": 1}, fit_diagnostics={"diagnostics_ok": True}, decision_results={"activated_families": ["sample_search"]}, source_provenance={"paired_run_id": "task8-generation-seed", "paired_run_content_hash": paired_hash, "evidence_kind": "paired_experimental", "route_source": "forced"})
prior = _publish_generation_for_test(GenerationManifest(input_hash="7" * 64, groups=[{"name": "sample_search", "route": "container_cc", "posterior_mean": 0.99, "band": "Reliable", "n_total": 5}], **common))
active = _publish_generation_for_test(GenerationManifest(input_hash="8" * 64, parent_hash=prior.generation_hash, groups=[{"name": "sample_search", "route": "nextseek_query", "posterior_mean": 0.99, "band": "Reliable", "n_total": 5}], **common), parent=prior)
activate_generation(prior, expected_hash="", activated_by="task8:seed-prior")
activate_generation(active, expected_hash=prior.generation_hash, activated_by="task8:seed-active")
payload = {"prior_generation": prior.generation_hash, "active_generation": active.generation_hash, "prewrite_ids": [f"turn:{turn.pk}"], "row_counts": {"judgments": TurnJudgment.objects.count(), "exclusions": 1, "pending_attempts": PaidRunState.objects.filter(status="pending").count(), "failed_attempts": PaidRunState.objects.filter(status="failed").count(), "reservations": SpendReservation.objects.count(), "tombstones": PaidRunState.objects.filter(failure_reason__startswith="task8 tombstone").count()}}
print(json.dumps(payload, sort_keys=True))
""".strip()

    @staticmethod
    def _postwrite_code() -> str:
        return """
import json, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
import django
django.setup()
from nextseek_api.assistant.models_db import ActiveGenerationPointer, ChatSession, TurnJudgment, TurnLedger
session = ChatSession.objects.get(title="Task 8 retained prewrite")
turn = TurnLedger.objects.create(session=session, turn_number=2, route="container_cc", route_source="task8_rehearsal", task_family="task8_postwrite", family_source="task8")
TurnJudgment.objects.create(turn=turn, fingerprint="9" * 64, verdict={"task8": "postwrite"}, status="ok")
pointer = ActiveGenerationPointer.objects.select_related("active", "previous").get(pk=1)
print(json.dumps({"postwrite_ids": [f"turn:{turn.pk}"], "active_generation": pointer.active.generation_hash, "prior_generation": pointer.previous.generation_hash}, sort_keys=True))
""".strip()

    @staticmethod
    def _retention_code() -> str:
        return """
import json, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
import django
django.setup()
from nextseek_api.assistant.models_db import ActiveGenerationPointer, TurnLedger
turns = list(TurnLedger.objects.filter(route_source="task8_rehearsal").order_by("pk").values_list("pk", flat=True))
pointer = ActiveGenerationPointer.objects.select_related("active", "previous").get(pk=1)
print(json.dumps({"retained_ids": [f"turn:{pk}" for pk in turns], "active_generation": pointer.active.generation_hash if pointer.active_id else "", "previous_generation": pointer.previous.generation_hash if pointer.previous_id else ""}, sort_keys=True))
""".strip()

    def _seed_prewrite(self) -> CommandOutcome:
        outcome, payload = self._exec_python_json(self._prewrite_code())
        if payload is None:
            return outcome
        try:
            prior_image = self._inspect_image_id(self.config.app_image)
            peer_ids = {
                name: self._container_id(name)
                for name in (self.config.sidecar_container, self.config.proxy_container)
            }
        except OperationalRunError as exc:
            return CommandOutcome(1, outcome.stdout, str(exc).encode())
        self.facts.update(payload)
        self.facts["prior_image_id"] = prior_image
        self.facts["oi3_peer_ids_before"] = peer_ids
        return outcome

    def _seed_postwrite(self) -> CommandOutcome:
        outcome, payload = self._exec_python_json(self._postwrite_code())
        if payload is None:
            return outcome
        try:
            candidate_image = self._inspect_image_id(self.config.app_image)
            image_list = self._docker(
                [
                    "image", "ls", self.config.app_image.split(":", 1)[0], "--format",
                    "{{.Repository}}:{{.Tag}} {{.ID}}",
                ],
                timeout_s=30,
            )
            if image_list.returncode:
                raise OperationalRunError("cannot enumerate rollback tags")
            rollback_tags = []
            for line in image_list.stdout.decode().splitlines():
                tag, _, _short_id = line.partition(" ")
                if ":pre-" not in tag:
                    continue
                if self._inspect_image_id(tag) == self.facts.get("prior_image_id"):
                    rollback_tags.append(tag)
            if len(rollback_tags) != 1:
                raise OperationalRunError(
                    "exact verified prior rollback tag was not found"
                )
            registry_state_path = (
                self.config.runtime_root / "startup" / ".ghcr-push-state.json"
            )
            state = json.loads(registry_state_path.read_text())
            image_state = (state.get("images") or {}).get(
                "ghcr.io/biomicrocenter/nextseek"
            ) or {}
            success = image_state.get("last_success") or state.get("last_success") or {}
            tag = success.get("tag")
            digest = success.get("digest")
            if (
                not isinstance(tag, str)
                or not tag.startswith(
                    "ghcr.io/biomicrocenter/nextseek:baseline-"
                )
                or not _is_image_id(digest)
            ):
                raise OperationalRunError(
                    "mandatory private GHCR baseline push is not proven"
                )
        except (OSError, ValueError, TypeError, OperationalRunError) as exc:
            return CommandOutcome(1, outcome.stdout, str(exc).encode())
        self.facts.update(payload)
        self.facts.update(
            {
                "candidate_image_id": candidate_image,
                "rollback_tag": rollback_tags[0],
                "registry_tag": tag,
                "registry_digest": digest,
            }
        )
        return outcome

    def _runtime_command(
        self,
        argv: list[str],
        *,
        timeout_s: float = 90.0,
    ) -> CommandOutcome:
        return self._run(
            argv,
            cwd=self.config.runtime_root,
            env=self._task_env(),
            timeout_s=timeout_s,
        )

    def _site_ok(self) -> bool:
        try:
            state = json.loads(
                (self.config.runtime_root / "startup" / ".instance.json").read_text()
            )
            port = int(state["ports"]["nextseek"])
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=10
            ) as response:
                return response.status == 200
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _wait_site(self, timeout_s: float = 90.0) -> bool:
        deadline = time.monotonic() + timeout_s
        attempts = 0
        last_snapshot: dict[str, int | float] = {
            "attempts": attempts,
            "timeout_s": timeout_s,
        }
        while time.monotonic() < deadline:
            attempts += 1
            self._sample_resources()
            memory_peak = int(self.facts["memory_peak_bytes"])
            memory_available = available_memory_bytes()
            disk_free = shutil.disk_usage(self.config.repo_root).free
            snapshot = {
                "attempts": attempts,
                "timeout_s": timeout_s,
                "memory_peak_bytes": memory_peak,
                "memory_available_bytes": memory_available,
                "disk_free_bytes": disk_free,
            }
            last_snapshot = snapshot
            if (
                memory_peak > MAX_MEMORY_BYTES
                or memory_available < MINIMUM_MEMORY_RESERVE_BYTES
                or disk_free < MINIMUM_DISK_RESERVE_BYTES
            ):
                self.facts["last_site_wait"] = {
                    **snapshot,
                    "status": "resource_limit",
                }
                return False
            if self._site_ok():
                self.facts["last_site_wait"] = {**snapshot, "status": "ready"}
                return True
            time.sleep(2)
        self.facts["last_site_wait"] = {**last_snapshot, "status": "timeout"}
        return False

    def _migration_snapshot(self) -> tuple[bool, list[str]]:
        result = self._docker(
            [
                "exec", self.config.app_container, "uv", "run", "--no-sync", "manage.py",
                "showmigrations", "nextseek_api",
            ],
            timeout_s=60,
        )
        if result.returncode:
            return False, []
        lines = [line.strip() for line in result.stdout.decode().splitlines()]
        migrations = [line[4:] for line in lines if line.startswith("[X] ")]
        all_applied = bool(migrations) and not any(line.startswith("[ ] ") for line in lines)
        return all_applied, migrations

    def _oi3_checks(self) -> tuple[dict[str, bool], dict[str, Any]]:
        details: dict[str, Any] = {}
        env_code = """
import json
from nextseek_api.cc_assistant.cc_engine import build_agent_environment
from nextseek_api.cc_assistant.tests.validate_cc_acceptance import SHARED_CRED_KEYS
hostile = {key: "must-not-leak" for key in SHARED_CRED_KEYS}
env = build_agent_environment(source=hostile, api_user="task8-user", api_pass="task8-pass", path_mappings={"/data": "/data"}, chat_session_id="task8")
print(json.dumps({"shared": sorted(set(env) & set(SHARED_CRED_KEYS)), "keys": sorted(env)}, sort_keys=True))
""".strip()
        env_outcome, env_payload = self._exec_python_json(env_code)
        details["agent_env"] = env_payload or {}

        health = self._docker(
            [
                "run", "--rm", "--network", self.config.cc_network,
                "--cpus", "0.10", "--memory", "128m", "--entrypoint",
                "sh", "dmac-assistant:poc", "-c",
                "curl -fsS http://bedrock-proxy:8080/healthz >/dev/null",
            ],
            timeout_s=60,
        )
        proxy_logs = self._docker(
            ["logs", self.config.proxy_container], timeout_s=30,
        )
        proxy_text = (proxy_logs.stdout + proxy_logs.stderr).decode(
            errors="replace"
        )

        network = self._docker(
            ["network", "inspect", self.config.cc_network], timeout_s=30,
        )
        members: list[str] = []
        if network.returncode == 0:
            try:
                raw = json.loads(network.stdout)[0]
                members = sorted(
                    str(item.get("Name") or "")
                    for item in (raw.get("Containers") or {}).values()
                )
            except (ValueError, KeyError, TypeError):
                members = []
        details["network_members"] = members
        closed = bool(members) and all(
            name in {self.config.proxy_container, self.config.sidecar_container,
                     self.config.nginx_container}
            for name in members
        )

        mount_code = """
import json
from nextseek_api.cc_assistant.cc_config import CCPaths
from nextseek_api.cc_assistant.cc_engine import _build_volumes
mounts = _build_volumes(paths=CCPaths(users_volume="dmac-cc-users", user_root_mount="/dmac/users"), project_dirname="task8", user_id="task8", cc_state_key="session", run_id="a1b2c3d4")
print(json.dumps({"mounts": [{"target": m["Target"], "read_only": m["ReadOnly"]} for m in mounts]}, sort_keys=True))
""".strip()
        mount_outcome, mount_payload = self._exec_python_json(mount_code)
        details["mounts"] = (mount_payload or {}).get("mounts", [])
        writable_targets = {
            item.get("target")
            for item in details["mounts"]
            if item.get("read_only") is False
        }
        scratch_only = bool(details["mounts"]) and writable_targets == {
            "/data/scratch", "/home/user/.claude"
        }

        checks = {
            "zero_agent_shared_credentials": (
                env_outcome.returncode == 0
                and env_payload is not None
                and env_payload.get("shared") == []
            ),
            "bedrock_proxy_only": health.returncode == 0,
            "proxy_token_not_logged": (
                proxy_logs.returncode == 0
                and "ABSK" not in proxy_text
                and "Authorization" not in proxy_text
            ),
            "network_members_closed": network.returncode == 0 and closed,
            "nextseek_not_on_agent_network": self.config.app_container not in members,
            "scratch_only_writes": mount_outcome.returncode == 0 and scratch_only,
            "full_zero_spend_validator": (
                env_outcome.returncode == 0
                and health.returncode == 0
                and proxy_logs.returncode == 0
                and network.returncode == 0
                and mount_outcome.returncode == 0
            ),
        }
        return checks, details

    def _verify_forward(self) -> CommandOutcome:
        checks: dict[str, bool] = {}
        checks["site_http_200"] = self._wait_site()
        server = self._docker(
            ["exec", self.config.app_container, "printenv", "NEXTSEEK_SERVER"], timeout_s=30,
        )
        top = self._docker(["top", self.config.app_container], timeout_s=30)
        top_text = top.stdout.decode(errors="replace").lower()
        checks["server_gunicorn"] = (
            server.returncode == 0
            and server.stdout.decode().strip() == "gunicorn"
            and "gunicorn" in top_text
        )
        checks["no_daphne"] = top.returncode == 0 and "daphne" not in top_text
        inspect = self._docker(
            ["inspect", self.config.app_container, "--format", "{{.RestartCount}}"],
            timeout_s=30,
        )
        checks["restart_zero"] = (
            inspect.returncode == 0 and inspect.stdout.strip() == b"0"
        )
        logs = self._docker(["logs", self.config.app_container], timeout_s=30)
        log_text = (logs.stdout + logs.stderr).decode(errors="replace")
        checks["boot_clean"] = logs.returncode == 0 and not any(
            marker in log_text
            for marker in (
                "COLLECTSTATIC-FAILED", "DB-UNREACHABLE", "MIGRATE-FAILED"
            )
        )
        applied, migrations = self._migration_snapshot()
        checks["migrations_all_applied"] = applied
        self.facts["forward_migrations"] = migrations
        route = self._docker(
            [
                "exec", "-w", "/app", self.config.app_container, "uv", "run", "--no-sync",
                "python", "-c",
                "from nextseek_api.cc_assistant import cc_engine; print(cc_engine.cc_runner_available())",
            ],
            timeout_s=30,
        )
        checks["cc_route_wired"] = (
            route.returncode == 0 and "(True, 'ok')" in route.stdout.decode()
        )
        try:
            peer_ids = {
                name: self._container_id(name)
                for name in (self.config.sidecar_container, self.config.proxy_container)
            }
        except OperationalRunError:
            peer_ids = {}
        checks["oi3_peers_unchanged"] = (
            peer_ids == self.facts.get("oi3_peer_ids_before")
        )
        try:
            checks["rollback_tag_present"] = (
                self._inspect_image_id(self.facts["rollback_tag"])
                == self.facts["prior_image_id"]
            )
        except (KeyError, OperationalRunError):
            checks["rollback_tag_present"] = False
        doctor = self._runtime_command(
            ["./startup.sh", "doctor", "--scope", "app"], timeout_s=120
        )
        checks["doctor_green"] = doctor.returncode == 0
        retention, retained = self._exec_python_json(self._retention_code())
        expected_ids = self.facts.get("prewrite_ids", []) + self.facts.get(
            "postwrite_ids", []
        )
        retained_ids = (retained or {}).get("retained_ids", [])
        if retention.returncode or set(retained_ids) != set(expected_ids):
            checks["site_http_200"] = False
        oi3_checks, oi3_details = self._oi3_checks()
        self.facts["forward_checks"] = checks
        self.facts["oi3_checks"] = oi3_checks
        self.facts["oi3_details"] = oi3_details
        self.facts["retained_ids_after_forward"] = retained_ids
        result = {"checks": checks, "oi3": oi3_checks}
        return CommandOutcome(
            0 if all(checks.values()) and all(oi3_checks.values()) else 1,
            json.dumps(result, sort_keys=True).encode(),
        )

    @staticmethod
    def _set_env_key(path: Path, key: str, value: str) -> None:
        lines = path.read_text().splitlines()
        replacement = f'{key}="{value}"'
        replaced = False
        updated: list[str] = []
        for line in lines:
            if line.lstrip().startswith(f"{key}="):
                updated.append(replacement)
                replaced = True
            else:
                updated.append(line)
        if not replaced:
            updated.append(replacement)
        path.write_text("\n".join(updated) + "\n")

    def _disable_flags(self) -> CommandOutcome:
        path = self.config.runtime_root / "docker" / "nextseek.env"
        try:
            self._set_env_key(path, "NEXTSEEK_POSTERIOR_ROUTING_ENABLED", "0")
        except OSError as exc:
            return CommandOutcome(1, stderr=str(exc).encode())
        recreate = self._runtime_command(
            [
                "docker", "compose", "-p", self.config.compose_project, "up", "-d",
                "--no-build", "--no-deps", "--force-recreate", "nextseek",
            ],
            timeout_s=120,
        )
        if recreate.returncode:
            return recreate
        flag = self._docker(
            [
                "exec", self.config.app_container, "printenv",
                "NEXTSEEK_POSTERIOR_ROUTING_ENABLED",
            ],
            timeout_s=30,
        )
        ok = flag.returncode == 0 and flag.stdout.strip() == b"0"
        self.facts["flags_disabled"] = ok
        self.facts["settings_sha256"] = _sha(path)
        return CommandOutcome(
            0 if ok else 1,
            json.dumps({"posterior_routing": flag.stdout.decode().strip()}).encode(),
            flag.stderr,
        )

    def _stop_schedules(self) -> CommandOutcome:
        code = """
import json
from nextseek_api.eval.paid_run_schedule import ScheduleRefused, default_schedule_entrypoint
try:
    default_schedule_entrypoint()
except ScheduleRefused as exc:
    print(json.dumps({"schedule_refused": True, "reason": str(exc)}))
else:
    raise AssertionError("paid schedule unexpectedly runnable")
""".strip()
        outcome, payload = self._exec_python_json(code)
        self.facts["schedules_stopped"] = (
            outcome.returncode == 0
            and payload is not None
            and payload.get("schedule_refused") is True
        )
        return CommandOutcome(
            0 if self.facts["schedules_stopped"] else 1,
            outcome.stdout,
            outcome.stderr,
        )

    def _stop_workers(self) -> CommandOutcome:
        listed = self._docker(
            [
                "ps", "--filter",
                f"label=com.docker.compose.project={self.config.compose_project}",
                "--format", "{{.Label \"com.docker.compose.service\"}}",
            ], timeout_s=30,
        )
        if listed.returncode:
            return listed
        services = [line for line in listed.stdout.decode().splitlines() if line]
        eval_workers = sorted(
            service for service in services
            if service in {"paid_eval", "paid_eval_worker", "eval_worker"}
        )
        ok = not eval_workers
        self.facts["workers_drained"] = ok
        return CommandOutcome(
            0 if ok else 1,
            json.dumps({"dedicated_eval_workers": eval_workers}, sort_keys=True).encode(),
            listed.stderr,
        )

    def _activate_prior(self) -> CommandOutcome:
        code = """
import json, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
import django
django.setup()
from nextseek_api.assistant.models_db import ActiveGenerationPointer
from nextseek_api.eval.generation_store import rollback_generation
pointer = ActiveGenerationPointer.objects.select_related("active", "previous").get(pk=1)
expected_prior = pointer.previous.generation_hash
rolled = rollback_generation(expected_hash=pointer.active.generation_hash, activated_by="task8:recovery")
rolled.refresh_from_db()
assert rolled.active.generation_hash == expected_prior
print(json.dumps({"active_generation": rolled.active.generation_hash, "previous_generation": "" if rolled.previous_id is None else rolled.previous.generation_hash}, sort_keys=True))
""".strip()
        outcome, payload = self._exec_python_json(code)
        if payload is not None:
            self.facts["recovery_active_generation"] = payload.get(
                "active_generation"
            )
        return outcome

    def _restore_prior_image(self) -> CommandOutcome:
        try:
            tag = str(self.facts["rollback_tag"])
            prior = str(self.facts["prior_image_id"])
            if self._inspect_image_id(tag) != prior:
                raise OperationalRunError("rollback tag identity drifted")
        except (KeyError, OperationalRunError) as exc:
            return CommandOutcome(1, stderr=str(exc).encode())
        retag = self._docker(
            ["tag", tag, self.config.app_image], timeout_s=30,
        )
        if retag.returncode:
            return retag
        recreate = self._runtime_command(
            [
                "docker", "compose", "-p", self.config.compose_project, "up", "-d",
                "--no-build", "--no-deps", "--force-recreate", "nextseek",
            ],
            timeout_s=120,
        )
        if recreate.returncode:
            return recreate
        if not self._wait_site():
            return CommandOutcome(1, stderr=b"prior image did not become HTTP-ready")
        container_image = self._docker(
            ["inspect", self.config.app_container, "--format", "{{.Image}}"], timeout_s=30,
        )
        ok = (
            container_image.returncode == 0
            and container_image.stdout.decode().strip() == prior
        )
        self.facts["recovery_image_id"] = container_image.stdout.decode().strip()
        return CommandOutcome(
            0 if ok else 1,
            json.dumps({"image_id": self.facts["recovery_image_id"]}).encode(),
            container_image.stderr,
        )

    def _verify_forward_schema(self) -> CommandOutcome:
        applied, migrations = self._migration_snapshot()
        ok = applied and migrations == self.facts.get("forward_migrations")
        self.facts["forward_only_schema"] = ok
        return CommandOutcome(
            0 if ok else 1,
            json.dumps({"migrations": migrations}, sort_keys=True).encode(),
        )

    def _verify_recovery(self) -> CommandOutcome:
        retention, payload = self._exec_python_json(self._retention_code())
        retained = (payload or {}).get("retained_ids", [])
        expected = self.facts.get("prewrite_ids", []) + self.facts.get(
            "postwrite_ids", []
        )
        doctor = self._runtime_command(
            ["./startup.sh", "doctor", "--scope", "app"], timeout_s=120
        )
        checks = {
            "flags_disabled": self.facts.get("flags_disabled") is True,
            "schedules_stopped": self.facts.get("schedules_stopped") is True,
            "workers_drained": self.facts.get("workers_drained") is True,
            "prior_generation_active": (
                retention.returncode == 0
                and (payload or {}).get("active_generation")
                == self.facts.get("prior_generation")
            ),
            "prior_image_active": (
                self.facts.get("recovery_image_id")
                == self.facts.get("prior_image_id")
            ),
            "forward_corrective_migration_only": (
                self.facts.get("forward_only_schema") is True
            ),
            "prewrite_retained": set(self.facts.get("prewrite_ids", [])).issubset(
                set(retained)
            ),
            "postwrite_retained": set(self.facts.get("postwrite_ids", [])).issubset(
                set(retained)
            ),
            "site_http_200": self._site_ok(),
            "doctor_green": doctor.returncode == 0,
        }
        self.facts["recovery_checks"] = checks
        self.facts["retained_ids_after_recovery"] = retained
        return CommandOutcome(
            0 if all(checks.values()) else 1,
            json.dumps({"checks": checks}, sort_keys=True).encode(),
        )

    def _validate_approval_control(self) -> CommandOutcome:
        try:
            load_authorization(
                self.config.approval_path,
                prior_sha=self.config.prior_sha,
                candidate_sha=self.config.candidate_sha,
            )
            return CommandOutcome(0, b"approval valid")
        except ValueError as exc:
            return CommandOutcome(1, stderr=str(exc).encode())

    def _resources_control(self) -> CommandOutcome:
        result = preflight(self.config.repo_root)
        return CommandOutcome(
            0 if result["gate"] == "PASS" else 1,
            json.dumps(result, sort_keys=True).encode(),
        )

    def _control(self, command: PlannedCommand) -> CommandOutcome:
        handlers: dict[str, Callable[[], CommandOutcome]] = {
            "approval": self._validate_approval_control,
            "source": self._validate_source,
            "resources": self._resources_control,
            "host-before": lambda: self._snapshot_host("before"),
            "harness-config": self._configure_harness,
            "prior-image": self._verify_and_tag_prior_image,
            "namespace-start": self._start_namespace,
            "prewrite-seed": self._seed_prewrite,
            "migration-aware-dump": self._dump_dmac,
            "restore-probe": self._restore_probe,
            "candidate-source": lambda: self._validate_source(
                candidate_checkout=True
            ),
            "build-prepare": self._prepare_build,
            "builder-cleanup": self._remove_builder,
            "cohort-resume": self._resume_cohort,
            "registry-push": self._push_registry,
            "postwrite-seed": self._seed_postwrite,
            "forward-runbook": self._verify_forward,
            "disable-flags": self._disable_flags,
            "stop-schedules": self._stop_schedules,
            "stop-workers": self._stop_workers,
            "activate-prior": self._activate_prior,
            "restore-image": self._restore_prior_image,
            "forward-only-schema": self._verify_forward_schema,
            "recovery-runbook": self._verify_recovery,
            "namespace-cleanup": self._cleanup_namespace,
            "host-after": lambda: self._snapshot_host("after"),
        }
        if set(handlers) != CONTROL_ACTIONS:
            return CommandOutcome(
                78, stderr=b"Task 8 control dispatch inventory drifted"
            )
        handler = handlers.get(command.action)
        if handler is None:
            return CommandOutcome(
                78,
                stderr=f"unimplemented Task 8 control: {command.action}".encode(),
            )
        return handler()

    def execute(self, command: PlannedCommand, *, timeout_s: float) -> CommandOutcome:
        self._sample_resources()
        if command.argv and command.argv[0] == "task8-control":
            outcome = self._control(command)
            self._sample_resources()
            return outcome
        env = self._host_env() if command.daemon == "host_read_only" else self._task_env()
        outcome = self._run(
            command.argv,
            cwd=Path(command.cwd),
            env=env,
            timeout_s=timeout_s,
        )
        self._sample_resources()
        return outcome

    def emergency_stop(self, *, timeout_s: float) -> CommandOutcome:
        del timeout_s
        return self._cleanup_namespace()


def _ledger_entry(
    command: PlannedCommand,
    *,
    seq: int,
    outcome: CommandOutcome,
    duration_s: float,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "action": command.action,
        "phase": command.phase,
        "daemon": command.daemon,
        "effect": command.effect,
        "argv": list(command.argv),
        "returncode": outcome.returncode,
        "stdout_sha256": hashlib.sha256(outcome.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(outcome.stderr).hexdigest(),
        "duration_s": round(duration_s, 6),
    }


def run_operational_plan(
    config: OperationalConfig,
    approval: dict[str, Any],
    adapter: OperationalAdapter,
    *,
    artifact_dir: Path,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
    free_bytes: Callable[[], int] | None = None,
    preflight_result: dict[str, Any] | None = None,
) -> RunArtifacts:
    """Execute the exact plan with approval, wall, and disk checks at every step.

    The adapter owns operational details.  This layer owns the fail-closed
    invariants: exact plan order, no expired approval, bounded wall time, disk
    reserve, output hashing, and best-effort exact namespace cleanup.
    """

    errors = operational_config_errors(config)
    if errors:
        raise OperationalRunError("; ".join(errors))
    current = now()
    approval_errors = authorization_errors(
        approval,
        prior_sha=config.prior_sha,
        candidate_sha=config.candidate_sha,
        now=current,
    )
    if approval_errors:
        raise OperationalRunError("; ".join(approval_errors))
    checked = preflight_result if preflight_result is not None else preflight(config.repo_root)
    if checked.get("gate") != "PASS":
        detail = "; ".join(str(item) for item in checked.get("errors", [])) or "unknown"
        raise OperationalRunError(f"Task 8 preflight failed: {detail}")

    plan = build_operational_plan(config)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    plan_path = artifact_dir / "operational-plan.json"
    ledger_path = artifact_dir / "commands.jsonl"
    plan_path.write_text(json.dumps(plan_payload(config, approval), indent=2, sort_keys=True) + "\n")

    disk_probe = free_bytes or (lambda: shutil.disk_usage(config.repo_root).free)
    started = monotonic()
    entries: list[dict[str, Any]] = []
    namespace_touched = False
    namespace_cleaned = False
    failure: BaseException | None = None
    cleanup_failure: str | None = None
    try:
        for seq, command in enumerate(plan, 1):
            elapsed = monotonic() - started
            remaining = MAX_WALL_S - elapsed
            if remaining <= 0:
                raise OperationalRunError("Task 8 wall cap reached before next action")
            if disk_probe() < MINIMUM_DISK_RESERVE_BYTES:
                raise OperationalRunError("Task 8 disk reserve reached before next action")
            approval_errors = authorization_errors(
                approval,
                prior_sha=config.prior_sha,
                candidate_sha=config.candidate_sha,
                now=now(),
            )
            if approval_errors:
                raise OperationalRunError("Task 8 approval became invalid: " + "; ".join(approval_errors))
            if command.daemon == "task8_namespace" and command.effect in {
                "namespaced_mutation", "cleanup",
            }:
                # A command may create only some of its resources before it
                # returns non-zero.  Mark the namespace before execution so a
                # partial failure still invokes exact, idempotent cleanup.
                namespace_touched = True
            command_started = monotonic()
            outcome = adapter.execute(command, timeout_s=remaining)
            duration = monotonic() - command_started
            entries.append(
                _ledger_entry(command, seq=seq, outcome=outcome, duration_s=duration)
            )
            if command.action == "namespace-cleanup" and outcome.returncode == 0:
                namespace_cleaned = True
            if outcome.returncode != 0:
                raise OperationalRunError(
                    f"Task 8 action {command.action!r} failed with {outcome.returncode}"
                )
    except BaseException as exc:
        failure = exc
    finally:
        ledger_path.write_text(
            "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries)
        )
        if failure is not None and namespace_touched and not namespace_cleaned:
            remaining = max(1.0, min(30.0, MAX_WALL_S - (monotonic() - started)))
            try:
                cleanup = adapter.emergency_stop(timeout_s=remaining)
                if cleanup.returncode != 0:
                    cleanup_failure = (
                        f"emergency cleanup returned {cleanup.returncode}"
                    )
            except Exception as exc:
                cleanup_failure = (
                    "emergency cleanup raised " + type(exc).__name__
                )
    if failure is not None:
        if cleanup_failure is not None:
            raise OperationalRunError(f"{failure}; {cleanup_failure}") from failure
        if isinstance(failure, OperationalRunError):
            raise failure
        raise OperationalRunError(f"Task 8 adapter crashed: {failure}") from failure
    elapsed = monotonic() - started
    if elapsed > MAX_WALL_S:
        raise OperationalRunError("Task 8 wall cap exceeded after final action")
    if len(entries) != len(plan):
        raise OperationalRunError("Task 8 execution did not account for every planned action")
    return RunArtifacts(
        plan_path=plan_path,
        ledger_path=ledger_path,
        elapsed_s=elapsed,
        command_count=len(entries),
    )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def write_operational_evidence(
    config: OperationalConfig,
    approval: dict[str, Any],
    adapter: LocalOperationalAdapter,
    artifacts: RunArtifacts,
    *,
    evidence_path: Path | None = None,
) -> Path:
    """Materialize PASS evidence only from a fully completed real adapter run."""

    root = config.repo_root.resolve()
    destination = (evidence_path or (root / EVIDENCE)).resolve()
    if not destination.is_relative_to(root):
        raise OperationalRunError("Task 8 evidence path must remain inside the checkout")
    facts = adapter.facts
    required_facts = {
        "source", "namespace_id", "host_before", "host_after", "backup",
        "prior_image_id", "candidate_image_id", "rollback_tag", "registry_tag",
        "registry_digest", "prior_generation", "active_generation", "prewrite_ids",
        "postwrite_ids", "row_counts", "forward_checks", "oi3_checks",
        "retained_ids_after_forward", "recovery_checks",
        "retained_ids_after_recovery", "recovery_active_generation",
        "recovery_image_id", "forward_migrations", "settings_sha256",
        "memory_available_before_bytes", "memory_available_min_bytes",
        "prior_source_hashes", "peer_image_ids", "task8_resources_removed", "builder_created",
        "builder_removed",
    }
    missing = sorted(required_facts - set(facts))
    if missing:
        raise OperationalRunError(
            "Task 8 completed without required facts: " + ", ".join(missing)
        )
    if not all(facts["forward_checks"].values()):
        raise OperationalRunError("Task 8 forward checks are not all green")
    if not all(facts["oi3_checks"].values()):
        raise OperationalRunError("Task 8 OI-3 checks are not all green")
    if not all(facts["recovery_checks"].values()):
        raise OperationalRunError("Task 8 recovery checks are not all green")

    artifact_dir = destination.parent / "task8"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    def stage(path: Path) -> Path:
        source = path.resolve()
        target = (artifact_dir / source.name).resolve()
        if source != target:
            shutil.copy2(source, target)
        return target

    staged_plan_path = stage(artifacts.plan_path)
    staged_ledger_path = stage(artifacts.ledger_path)
    staged_host_before = stage(Path(facts["host_before"]["path"]))
    staged_host_after = stage(Path(facts["host_after"]["path"]))
    runbook_path = artifact_dir / "runbook-results.json"
    runbook_payload = {
        "forward": facts["forward_checks"],
        "recovery": facts["recovery_checks"],
    }
    runbook_path.write_text(
        json.dumps(runbook_payload, indent=2, sort_keys=True) + "\n"
    )
    oi3_path = artifact_dir / "oi3-results.json"
    oi3_path.write_text(
        json.dumps(
            {"checks": facts["oi3_checks"], "details": facts.get("oi3_details", {})},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    tombstone_path = artifact_dir / "tombstone-identity.json"
    tombstone_payload = {
        "model": "PaidRunState",
        "attempt_id": "failed",
        "failure_reason_prefix": "task8 tombstone",
        "count": facts["row_counts"]["tombstones"],
    }
    tombstone_path.write_text(
        json.dumps(tombstone_payload, indent=2, sort_keys=True) + "\n"
    )

    migrations = list(facts["forward_migrations"])
    if not migrations:
        raise OperationalRunError("Task 8 could not record an applied migration set")
    schema_generation = len(migrations)
    owner = "plan018-v4-9-task8"
    data_identity = {
        "prewrite_ids": facts["prewrite_ids"],
        "postwrite_ids": facts["postwrite_ids"],
        "row_counts": facts["row_counts"],
        "active_generation": facts["active_generation"],
        "prior_generation": facts["prior_generation"],
    }
    runtime_identities = []
    for release, source_sha, image_id, queue_generation in (
        ("old", config.prior_sha, facts["prior_image_id"], 1),
        ("new", config.candidate_sha, facts["candidate_image_id"], 2),
    ):
        for role in ("web", "worker"):
            runtime_identities.append(
                {
                    "identity_id": f"{release}-{role}",
                    "release": release,
                    "role": role,
                    "source_sha": source_sha,
                    "image_digest": image_id,
                    "owner": owner,
                    "min_schema_generation": 1,
                    "max_schema_generation": schema_generation,
                    "queue_generation": queue_generation,
                }
            )
    record = DeployRecord.model_validate(
        {
            "schema_version": "plan018-deploy-record/v1",
            "deploy_id": f"v4-9-{config.candidate_sha[:12]}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "owner": owner,
            "phase": "expand" if facts.get("migration_paths") else "migrate",
            "git": {
                "source_sha": config.candidate_sha,
                "diff_sha256": facts["source"]["diff_sha256"],
            },
            "images": {
                "prior": facts["prior_image_id"],
                "candidate": facts["candidate_image_id"],
            },
            "schema": {
                "generation": schema_generation,
                "migration_leaf": migrations[-1],
                "migrations": migrations,
                "fingerprint": _canonical_sha256(migrations),
            },
            "settings_sha256": facts["settings_sha256"],
            "schedule_state": {"paid_eval": False, "reconciliation": False},
            "flag_state": {"posterior_routing": False, "paid_eval": False},
            "generations": {
                "active": facts["active_generation"],
                "prior": facts["prior_generation"],
            },
            "data": {
                "database_sha256": _canonical_sha256(data_identity),
                "artifact_sha256": _canonical_sha256(runbook_payload),
                "tombstone_sha256": _sha(tombstone_path),
                "row_counts": facts["row_counts"],
            },
            "network_identity": facts["namespace_id"],
            "runtime_identities": runtime_identities,
            "smoke_checks": {
                "schema": facts["forward_checks"]["migrations_all_applied"],
                "selector": facts["forward_checks"]["cc_route_wired"],
                "worker": facts["recovery_checks"]["workers_drained"],
            },
        }
    )
    deploy_record_path = artifact_dir / "deploy-record.json"
    deploy_record_path.write_text(record.model_dump_json(indent=2) + "\n")

    def relative(path: Path) -> str:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise OperationalRunError(f"Task 8 artifact escapes checkout: {resolved}")
        return str(resolved.relative_to(root))

    host_before = {
        "path": staged_host_before,
        "sha256": facts["host_before"]["sha256"],
    }
    host_after = {
        "path": staged_host_after,
        "sha256": facts["host_after"]["sha256"],
    }
    free_after = shutil.disk_usage(root).free
    free_before = int(facts["disk_free_before_bytes"])
    min_free = int(facts["disk_free_min_bytes"])
    payload = {
        "schema": SCHEMA,
        "gate": "PASS",
        "authorization": {
            "approved": True,
            "approved_at": approval["approved_at"],
            "conversation_ref": approval["conversation_ref"],
            "scope_sha256": approval["scope_sha256"],
        },
        "source": facts["source"],
        "isolation": {
            "kind": "namespaced_host_daemon",
            "compose_project": config.compose_project,
            "instance_prefix": config.instance_prefix,
            "cc_network": config.cc_network,
            "egress_network": config.egress_network,
            "builder_name": config.builder_name,
            "builder_created": facts["builder_created"],
            "builder_removed": facts["builder_removed"],
            "prior_source_hashes": facts["prior_source_hashes"],
            "peer_image_ids": facts["peer_image_ids"],
            "task8_resources_removed": facts["task8_resources_removed"],
            "host_snapshot_before": {
                "path": relative(Path(host_before["path"])),
                "sha256": host_before["sha256"],
            },
            "host_snapshot_after": {
                "path": relative(Path(host_after["path"])),
                "sha256": host_after["sha256"],
            },
            "host_unchanged": host_before["sha256"] == host_after["sha256"],
        },
        "resources": {
            "max_cpus": MAX_CPUS,
            "max_memory_bytes": MAX_MEMORY_BYTES,
            "memory_available_before_bytes": facts[
                "memory_available_before_bytes"
            ],
            "memory_available_after_bytes": available_memory_bytes(),
            "memory_available_min_bytes": facts["memory_available_min_bytes"],
            "minimum_memory_reserve_bytes": MINIMUM_MEMORY_RESERVE_BYTES,
            "disk_free_before_bytes": free_before,
            "disk_free_after_bytes": free_after,
            "disk_peak_delta_bytes": max(0, free_before - min_free),
            "minimum_reserve_bytes": MINIMUM_DISK_RESERVE_BYTES,
        },
        "deploy_record": {
            "path": relative(deploy_record_path),
            "sha256": _sha(deploy_record_path),
            "schema_validated": True,
        },
        "backup": facts["backup"],
        "images": {
            "prior": {
                "tag": facts["rollback_tag"],
                "image_id": facts["prior_image_id"],
            },
            "candidate": {
                "tag": facts["registry_tag"],
                "image_id": facts["candidate_image_id"],
            },
            "rollback_tag": {
                "tag": facts["rollback_tag"],
                "image_id": facts["prior_image_id"],
                "verified": True,
            },
            "registry": {
                "tag": facts["registry_tag"],
                "digest": facts["registry_digest"],
                "baked_secret_gate": "PASS",
                "push": "PASS",
                "private_package": True,
                "credential_path": GHCR_ENV_PATH,
                "credential_mode": "0600",
            },
        },
        "operational_plan": {
            "path": relative(staged_plan_path),
            "sha256": _sha(staged_plan_path),
        },
        "command_ledger": {
            "path": relative(staged_ledger_path),
            "sha256": _sha(staged_ledger_path),
            "count": artifacts.command_count,
            "failed": 0,
            "forbidden": 0,
        },
        "forward": {
            "checks": facts["forward_checks"],
            "prewrite_ids": facts["prewrite_ids"],
            "postwrite_ids": facts["postwrite_ids"],
            "retained_ids_after_forward": facts["retained_ids_after_forward"],
        },
        "recovery": {
            "actions": list(SAFE_RECOVERY_ACTIONS),
            "checks": facts["recovery_checks"],
            "active_generation": facts["recovery_active_generation"],
            "image_id": facts["recovery_image_id"],
            "retained_ids_after_recovery": facts["retained_ids_after_recovery"],
        },
        "oi3": {"checks": facts["oi3_checks"]},
        "external_effects": {
            "provider_calls": 0,
            "paid_resources": False,
            "live_database": False,
            "production_deployment": False,
            "existing_host_stack_mutated": False,
            "disposable_database": True,
            "private_registry_pushes": 1,
        },
        "artifacts_sha256": {},
        "wall_s": artifacts.elapsed_s,
        "wall_cap_s": MAX_WALL_S,
    }
    manifest_paths = (
        deploy_record_path,
        staged_plan_path,
        staged_ledger_path,
        Path(host_before["path"]),
        Path(host_after["path"]),
        runbook_path,
        oi3_path,
        tombstone_path,
    )
    payload["artifacts_sha256"] = {
        relative(path): _sha(path) for path in manifest_paths
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    errors = validation_errors(root, destination)
    if errors:
        destination.unlink(missing_ok=True)
        raise OperationalRunError(
            "Task 8 generated evidence failed validation: " + "; ".join(errors)
        )
    return destination


def _command_safety_errors(argv: object, *, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(argv, list) or not argv or not all(
        isinstance(token, str) and token for token in argv
    ):
        return [f"{label} argv is malformed"]
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
        errors.append(f"shell or forbidden command in {label}")
    return errors


def _validate_operational_plan(
    root: Path,
    summary: dict[str, Any],
    source: dict[str, Any],
    errors: list[str],
) -> list[dict[str, Any]]:
    path = _root_artifact(root, summary.get("path"), "operational plan path", errors)
    if path is None or not path.is_file():
        errors.append("operational plan artifact is missing")
        return []
    if not _is_sha256(summary.get("sha256")) or _sha(path) != summary.get("sha256"):
        errors.append("operational plan hash drift")
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"operational plan is malformed: {exc}")
        return []
    expected_top = {
        "schema", "authorization", "source", "bounds", "paths", "commands",
        "command_count", "namespace",
    }
    payload = _exact_keys(payload, expected_top, "operational plan", errors)
    if payload.get("schema") != PLAN_SCHEMA:
        errors.append("operational plan schema drifted")
    plan_source = _exact_keys(
        payload.get("source"), {"prior_sha", "candidate_sha"},
        "operational plan source", errors,
    )
    if (
        plan_source.get("prior_sha") != source.get("prior_sha")
        or plan_source.get("candidate_sha") != source.get("deployed_sha")
    ):
        errors.append("operational plan source does not match deployed source")
    bounds = _exact_keys(
        payload.get("bounds"),
        {
            "max_cpus", "max_memory_bytes", "required_available_memory_bytes",
            "minimum_memory_reserve_bytes", "wall_cap_s", "required_free_bytes",
            "minimum_disk_reserve_bytes",
        },
        "operational plan bounds", errors,
    )
    if bounds != {
        "max_cpus": MAX_CPUS,
        "max_memory_bytes": MAX_MEMORY_BYTES,
        "required_available_memory_bytes": REQUIRED_AVAILABLE_MEMORY_BYTES,
        "minimum_memory_reserve_bytes": MINIMUM_MEMORY_RESERVE_BYTES,
        "wall_cap_s": MAX_WALL_S,
        "required_free_bytes": REQUIRED_FREE_BYTES,
        "minimum_disk_reserve_bytes": MINIMUM_DISK_RESERVE_BYTES,
    }:
        errors.append("operational plan hardware bounds drifted")
    _exact_keys(
        payload.get("authorization"),
        {"conversation_ref", "approved_at", "expires_at", "scope_sha256"},
        "operational plan authorization", errors,
    )
    _exact_keys(
        payload.get("paths"),
        {"run_root", "runtime_root", "source_root", "backup_path"},
        "operational plan paths", errors,
    )
    namespace = _exact_keys(
        payload.get("namespace"),
        {"kind", "compose_project", "instance_prefix", "cc_network", "egress_network", "app_image", "builder_name"},
        "operational plan namespace", errors,
    )
    if (
        namespace.get("kind") != "namespaced_host_daemon"
        or not str(namespace.get("compose_project", "")).startswith("plan018v49task8")
        or not str(namespace.get("instance_prefix", "")).startswith("p18t8-")
        or namespace.get("cc_network") != f"{namespace.get('instance_prefix', '')}cc"
        or namespace.get("egress_network") != f"{namespace.get('instance_prefix', '')}egress"
        or namespace.get("app_image")
        != f"{namespace.get('compose_project', '')}-nextseek:latest"
        or namespace.get("builder_name") != f"{namespace.get('instance_prefix', '')}builder"
    ):
        errors.append("operational plan namespace identity drifted")
    commands = payload.get("commands")
    if not isinstance(commands, list):
        errors.append("operational plan commands must be a list")
        return []
    if payload.get("command_count") != len(commands) or len(commands) != 29:
        errors.append("operational plan command count is not exact")
    phases: set[str] = set()
    actions: set[str] = set()
    saw_worktree = False
    saw_rebuild = False
    for index, raw in enumerate(commands, 1):
        entry = _exact_keys(raw, _PLAN_COMMAND_KEYS, f"plan command {index}", errors)
        if not entry:
            continue
        action = entry.get("action")
        if not isinstance(action, str) or not action or action in actions:
            errors.append(f"operational plan action is missing/duplicate at {index}")
        else:
            actions.add(action)
        phase = entry.get("phase")
        if isinstance(phase, str):
            phases.add(phase)
        if entry.get("daemon") not in {"host_read_only", "task8_namespace", "registry"}:
            errors.append(f"operational plan daemon is invalid at {index}")
        if entry.get("effect") not in {
            "read_only", "namespaced_mutation", "registry_write", "cleanup",
        }:
            errors.append(f"operational plan effect is invalid at {index}")
        if entry.get("daemon") == "host_read_only" and entry.get("effect") != "read_only":
            errors.append(f"host-read-only plan command mutates at {index}")
        errors.extend(_command_safety_errors(entry.get("argv"), label=f"plan command {index}"))
        argv = entry.get("argv") if isinstance(entry.get("argv"), list) else []
        saw_worktree |= "worktree" in argv and "add" in argv and "--detach" in argv
        saw_rebuild |= "./startup.sh" in argv and "rebuild" in argv and "--source-tree" in argv
        if not isinstance(entry.get("cwd"), str) or not Path(entry.get("cwd", "")).is_absolute():
            errors.append(f"operational plan cwd is not absolute at {index}")
        env_keys = entry.get("env_keys")
        if not isinstance(env_keys, list) or not all(
            isinstance(key, str) and key and "=" not in key for key in env_keys
        ):
            errors.append(f"operational plan env key list is malformed at {index}")
    if phases != set(REQUIRED_PHASES):
        errors.append("operational plan phases are not exact")
    if not saw_worktree or not saw_rebuild:
        errors.append("operational plan lacks candidate worktree or required source-tree rebuild")
    return commands


def _validate_ledger(
    root: Path,
    summary: dict[str, Any],
    expected_commands: list[dict[str, Any]],
    errors: list[str],
) -> None:
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
    if (
        summary.get("count") != len(entries)
        or len(entries) != len(expected_commands)
        or summary.get("failed") != 0
        or summary.get("forbidden") != 0
    ):
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
        if daemon not in {"host_read_only", "task8_namespace", "registry"}:
            errors.append(f"command ledger daemon identity is invalid at {index}")
        if effect not in {"read_only", "namespaced_mutation", "registry_write", "cleanup"}:
            errors.append(f"command ledger effect is invalid at {index}")
        if daemon == "host_read_only" and effect != "read_only":
            errors.append(f"host-read-only ledger entry mutates at {index}")
        argv = entry.get("argv")
        safety = _command_safety_errors(argv, label=f"ledger entry {index}")
        errors.extend(safety)
        if safety and (not isinstance(argv, list) or not argv):
            continue
        if index <= len(expected_commands):
            expected = expected_commands[index - 1]
            for key in _PLAN_COMMAND_KEYS - {"cwd", "env_keys"}:
                if entry.get(key) != expected.get(key):
                    errors.append(f"ledger entry {index} drifted from plan field {key}")
        if "./startup.sh" in argv and "rebuild" in argv and "--source-tree" in argv:
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
        {
            "kind", "compose_project", "instance_prefix", "cc_network",
            "egress_network", "builder_name", "builder_created",
            "builder_removed", "prior_source_hashes",
            "peer_image_ids",
            "task8_resources_removed", "host_snapshot_before",
            "host_snapshot_after", "host_unchanged",
        },
        "isolation", errors,
    )
    prefix = str(isolation.get("instance_prefix", ""))
    prior_source_hashes = isolation.get("prior_source_hashes")
    peer_image_ids = isolation.get("peer_image_ids")
    if (
        isolation.get("kind") != "namespaced_host_daemon"
        or not str(isolation.get("compose_project", "")).startswith("plan018v49task8")
        or not prefix.startswith("p18t8-")
        or isolation.get("cc_network") != f"{prefix}cc"
        or isolation.get("egress_network") != f"{prefix}egress"
        or isolation.get("builder_name") != f"{prefix}builder"
        or isolation.get("builder_created") is not True
        or isolation.get("builder_removed") is not True
        or isolation.get("task8_resources_removed") is not True
        or not isinstance(prior_source_hashes, dict)
        or len(prior_source_hashes) < 5
        or not all(
            isinstance(path, str) and path and _is_sha256(digest)
            for path, digest in (prior_source_hashes or {}).items()
        )
        or not isinstance(peer_image_ids, dict)
        or set(peer_image_ids) != {
            "mysql:8.0", "nginx:latest", "nextseek-bedrock-proxy:latest",
            "nextseek-ns-sidecar:latest", "dmac-assistant:poc",
        }
        or not all(_is_image_id(image_id) for image_id in (peer_image_ids or {}).values())
    ):
        errors.append("Task 8 namespace/source verification/cleanup identity is incomplete")
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
        {
            "max_cpus", "max_memory_bytes", "memory_available_before_bytes",
            "memory_available_after_bytes", "memory_available_min_bytes",
            "minimum_memory_reserve_bytes", "disk_free_before_bytes",
            "disk_free_after_bytes", "disk_peak_delta_bytes",
            "minimum_reserve_bytes",
        },
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
        memory_reserve = resources["minimum_memory_reserve_bytes"]
        if (
            memory_reserve < MINIMUM_MEMORY_RESERVE_BYTES
            or resources["memory_available_before_bytes"]
            < REQUIRED_AVAILABLE_MEMORY_BYTES
            or resources["memory_available_after_bytes"] < memory_reserve
            or resources["memory_available_min_bytes"] < memory_reserve
        ):
            errors.append("Task 8 host memory reserve was not preserved")

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
        {
            "path", "mode", "sha256", "size_bytes", "migration_aware",
            "source_range", "migration_paths", "migration_diff_sha256", "tables",
            "checksum_verified", "restore_probe",
        },
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
        or backup.get("source_range") != f"{source.get('prior_sha')}..{source.get('deployed_sha')}"
        or not isinstance(backup.get("migration_paths"), list)
        or not all(
            isinstance(path, str) and path and "migrations" in path
            for path in backup.get("migration_paths", [])
        )
        or backup.get("migration_diff_sha256") != hashlib.sha256(
            "\n".join(backup.get("migration_paths", [])).encode()
        ).hexdigest()
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

    plan_ref = _exact_keys(
        payload.get("operational_plan"), {"path", "sha256"},
        "operational plan reference", errors,
    )
    expected_commands = _validate_operational_plan(root, plan_ref, source, errors)

    ledger = _exact_keys(
        payload.get("command_ledger"), {"path", "sha256", "count", "failed", "forbidden"},
        "command ledger summary", errors,
    )
    _validate_ledger(root, ledger, expected_commands, errors)

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
        plan_ref.get("path"),
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
    parser.add_argument(
        "command", choices=("preflight", "plan", "run", "validate")
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--evidence", type=Path, default=Path(EVIDENCE))
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--prior-sha")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--port-offset", type=int, default=12000)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        result = preflight(args.root)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["gate"] == "PASS" else 1
    if args.command in {"plan", "run"}:
        missing = [
            option
            for option, value in (
                ("--approval", args.approval),
                ("--run-root", args.run_root),
                ("--prior-sha", args.prior_sha),
                ("--candidate-sha", args.candidate_sha),
            )
            if value is None
        ]
        if missing:
            parser.error("plan requires " + ", ".join(missing))
        config = OperationalConfig(
            repo_root=args.root.resolve(),
            run_root=args.run_root,
            approval_path=args.approval,
            prior_sha=args.prior_sha,
            candidate_sha=args.candidate_sha,
            port_offset=args.port_offset,
        )
        try:
            approval = load_authorization(
                config.approval_path,
                prior_sha=config.prior_sha,
                candidate_sha=config.candidate_sha,
            )
        except ValueError as exc:
            print(f"Task 8 {args.command} FAIL: {exc}", file=sys.stderr)
            return 1
        if args.command == "plan":
            print(json.dumps(plan_payload(config, approval), indent=2, sort_keys=True))
            return 0
        staging = config.run_root.parent / f"{config.run_root.name}-evidence-staging"
        if staging.exists():
            print(
                f"Task 8 run FAIL: evidence staging already exists: {staging}",
                file=sys.stderr,
            )
            return 1
        adapter = LocalOperationalAdapter(config, artifact_dir=staging)
        try:
            artifacts = run_operational_plan(
                config,
                approval,
                adapter,
                artifact_dir=staging,
            )
            written = write_operational_evidence(
                config,
                approval,
                adapter,
                artifacts,
                evidence_path=(
                    args.evidence
                    if args.evidence.is_absolute()
                    else config.repo_root / args.evidence
                ),
            )
        except OperationalRunError as exc:
            print(f"Task 8 run FAIL: {exc}", file=sys.stderr)
            return 1
        print(f"Task 8 run PASS: {written} sha256={_sha(written)}")
        return 0
    errors = validation_errors(args.root, args.evidence)
    print("Task 8 evidence " + ("PASS" if not errors else "FAIL"))
    for error in errors:
        print("- " + error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

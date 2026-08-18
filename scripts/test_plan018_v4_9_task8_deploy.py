"""Fail-closed self-tests for the Plan 018 V4-9 Task-8 evidence gate."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import plan018_v4_9_task8_deploy as gate


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _operational_config(tmp_path: Path) -> gate.OperationalConfig:
    run_root = Path("/home/taishajo/work/state/task8-runs") / tmp_path.name
    return gate.OperationalConfig(
        repo_root=gate.ROOT,
        run_root=run_root,
        approval_path=Path("/home/taishajo/work/state") / f"{tmp_path.name}-approval.json",
        prior_sha="1" * 40,
        candidate_sha="2" * 40,
    )


def _approval(config: gate.OperationalConfig) -> dict:
    return {
        "schema": gate.APPROVAL_SCHEMA,
        "approved": True,
        "approved_at": "2026-08-18T12:00:00+00:00",
        "expires_at": "2026-08-18T14:00:00+00:00",
        "conversation_ref": "maintainer-current-conversation",
        "scope_sha256": gate.AUTHORIZATION_SCOPE_SHA256,
        "prior_sha": config.prior_sha,
        "candidate_sha": config.candidate_sha,
    }


def _valid_bundle(tmp_path: Path) -> tuple[Path, Path, dict]:
    root = tmp_path / "repo"
    evidence_dir = root / "evidence" / "task8"
    evidence_dir.mkdir(parents=True)

    deploy_record = json.loads((gate.ROOT / gate.TASK7_FIXTURE).read_text())
    deploy_record_path = evidence_dir / "deploy-record.json"
    _write_json(deploy_record_path, deploy_record)

    old_sha = "1" * 40
    new_sha = "2" * 40
    config = gate.OperationalConfig(
        repo_root=gate.ROOT,
        run_root=Path("/home/taishajo/work/state/task8-runs/synthetic-bundle"),
        approval_path=Path("/home/taishajo/work/state/task8-synthetic-approval.json"),
        prior_sha=old_sha,
        candidate_sha=new_sha,
    )
    plan_path = evidence_dir / "operational-plan.json"
    _write_json(plan_path, gate.plan_payload(config, _approval(config)))

    ledger_path = evidence_dir / "commands.jsonl"
    entries = []
    for seq, command in enumerate(gate.build_operational_plan(config), 1):
        entries.append(
            {
                "seq": seq,
                "action": command.action,
                "phase": command.phase,
                "daemon": command.daemon,
                "effect": command.effect,
                "argv": list(command.argv),
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
            "kind": "namespaced_host_daemon",
            "compose_project": config.compose_project,
            "instance_prefix": config.instance_prefix,
            "cc_network": config.cc_network,
            "egress_network": config.egress_network,
            "builder_name": config.builder_name,
            "builder_created": True,
            "builder_removed": True,
            "prior_source_hashes": {
                f"verified/path-{index}.py": str(index) * 64
                for index in range(1, 6)
            },
            "peer_image_ids": {
                image: "sha256:" + str(index) * 64
                for index, image in enumerate(
                    (
                        "mysql:8.0", "nginx:latest",
                        "nextseek-bedrock-proxy:latest",
                        "nextseek-ns-sidecar:latest", "dmac-assistant:poc",
                    ),
                    1,
                )
            },
            "task8_resources_removed": True,
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
            "max_memory_bytes": gate.MAX_MEMORY_BYTES,
            "memory_available_before_bytes": gate.REQUIRED_AVAILABLE_MEMORY_BYTES,
            "memory_available_after_bytes": gate.MINIMUM_MEMORY_RESERVE_BYTES,
            "memory_available_min_bytes": gate.MINIMUM_MEMORY_RESERVE_BYTES,
            "minimum_memory_reserve_bytes": gate.MINIMUM_MEMORY_RESERVE_BYTES,
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
            "source_range": f"{old_sha}..{new_sha}",
            "migration_paths": [],
            "migration_diff_sha256": gate.EMPTY_SHA256,
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
        "operational_plan": {
            "path": str(plan_path.relative_to(root)),
            "sha256": _sha(plan_path),
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
            str(plan_path.relative_to(root)): _sha(plan_path),
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
        (lambda p: p["isolation"].__setitem__("task8_resources_removed", False), "namespace/source verification/cleanup"),
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
    entries[2]["effect"] = "namespaced_mutation"
    entries[2]["argv"] = ["sh", "-c", "docker system prune"]
    entries[-2]["phase"] = "snapshot"
    ledger.write_text("".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries))
    payload["command_ledger"]["sha256"] = _sha(ledger)
    payload["artifacts_sha256"][payload["command_ledger"]["path"]] = _sha(ledger)
    _write_json(evidence_path, payload)

    errors = gate.validation_errors(root, evidence_path)
    assert any("host-read-only ledger entry mutates" in error for error in errors)
    assert any("shell or forbidden command" in error for error in errors)
    assert any("command phases" in error for error in errors)


def test_non_namespaced_project_is_rejected(tmp_path: Path) -> None:
    root, evidence_path, payload = _valid_bundle(tmp_path)
    payload["isolation"]["compose_project"] = "plan018-v4-9-task8"
    _write_json(evidence_path, payload)

    assert any("namespace/source verification/cleanup" in error for error in gate.validation_errors(root, evidence_path))


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


def test_preflight_estimate_preserves_candidate_cohort_and_host_reserve() -> None:
    tools = {name: True for name in ("docker", "git", "uv", "taskset")}
    images = {image: True for image in gate.REQUIRED_LOCAL_IMAGES}

    passing = gate.evaluate_preflight(
        free_bytes=gate.REQUIRED_FREE_BYTES,
        available_memory=gate.REQUIRED_AVAILABLE_MEMORY_BYTES,
        tools=tools,
        local_images=images,
        credential_mode=0o600,
    )
    failing = gate.evaluate_preflight(
        free_bytes=gate.REQUIRED_FREE_BYTES - 1,
        available_memory=gate.REQUIRED_AVAILABLE_MEMORY_BYTES,
        tools=tools,
        local_images=images,
        credential_mode=0o600,
    )

    assert passing["gate"] == "PASS"
    assert passing["minimum_reserve_bytes"] == 4 * 1024**3
    assert failing["gate"] == "FAIL"
    assert any("insufficient disk" in error for error in failing["errors"])


def test_task8_compose_is_a_bounded_app_cohort_not_a_duplicate_full_stack() -> None:
    payload = yaml.safe_load((gate.ROOT / "docker-compose.task8.yml").read_text())
    services = payload["services"]

    assert set(services) == {
        "db", "nextseek", "nextseek_nginx", "bedrock-proxy", "nextseek-sidecar"
    }
    assert not ({"seek", "neo4j", "solr"} & set(services))
    assert all(service.get("pull_policy") == "never" for service in services.values())
    cpu_total = sum(
        Decimal(str(service["deploy"]["resources"]["limits"]["cpus"]))
        for service in services.values()
    )
    assert cpu_total <= Decimal(gate.MAX_CPUS)
    memory_units = {"M": 1024**2, "G": 1024**3}
    memory_total = sum(
        int(str(service["deploy"]["resources"]["limits"]["memory"])[0:-1])
        * memory_units[str(service["deploy"]["resources"]["limits"]["memory"])[-1]]
        for service in services.values()
    )
    assert memory_total <= gate.MAX_MEMORY_BYTES
    assert services["nextseek"]["build"] == "."
    assert all("build" not in services[name] for name in services if name != "nextseek")
    db_mounts = services["db"]["volumes"]
    assert any("docker-entrypoint-initdb.d" in mount for mount in db_mounts)
    assert payload["networks"]["task8-cc"]["internal"] is True
    serialized = json.dumps(payload, sort_keys=True)
    assert "${INSTANCE_PREFIX}" in serialized
    assert "${TASK8_CC_NETWORK}" in serialized


def test_preflight_refuses_missing_tool_or_private_credential_mode() -> None:
    tools = {name: True for name in ("docker", "git", "uv", "taskset")}
    tools["taskset"] = False
    images = {image: True for image in gate.REQUIRED_LOCAL_IMAGES}
    images["nginx:latest"] = False

    result = gate.evaluate_preflight(
        free_bytes=gate.REQUIRED_FREE_BYTES,
        available_memory=gate.REQUIRED_AVAILABLE_MEMORY_BYTES,
        tools=tools,
        local_images=images,
        credential_mode=0o644,
    )

    assert result["gate"] == "FAIL"
    assert any("toolchain" in error for error in result["errors"])
    assert any("local-image cohort" in error for error in result["errors"])
    assert any("credential" in error for error in result["errors"])


def test_preflight_refuses_memory_below_stack_ceiling_plus_host_reserve() -> None:
    tools = {
        name: True
        for name in ("docker", "git", "uv", "taskset")
    }
    images = {image: True for image in gate.REQUIRED_LOCAL_IMAGES}
    result = gate.evaluate_preflight(
        free_bytes=gate.REQUIRED_FREE_BYTES,
        available_memory=gate.REQUIRED_AVAILABLE_MEMORY_BYTES - 1,
        tools=tools,
        local_images=images,
        credential_mode=0o600,
    )

    assert result["gate"] == "FAIL"
    assert any("insufficient available memory" in error for error in result["errors"])


def test_exact_expiring_authorization_accepts_only_bound_scope(tmp_path: Path) -> None:
    config = _operational_config(tmp_path)
    payload = _approval(config)
    now = datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc)

    assert gate.authorization_errors(
        payload,
        prior_sha=config.prior_sha,
        candidate_sha=config.candidate_sha,
        now=now,
    ) == []

    mutations = (
        ({**payload, "scope_sha256": "0" * 64}, "scope hash"),
        ({**payload, "candidate_sha": "3" * 40}, "source identities"),
        ({**payload, "expires_at": "2026-08-18T12:59:59+00:00"}, "expired"),
        ({**payload, "unexpected": True}, "keys are not exact"),
    )
    for mutated, expected in mutations:
        errors = gate.authorization_errors(
            mutated,
            prior_sha=config.prior_sha,
            candidate_sha=config.candidate_sha,
            now=now,
        )
        assert any(expected in error for error in errors)


def test_operational_layout_refuses_broad_or_overlapping_targets(tmp_path: Path) -> None:
    config = _operational_config(tmp_path)
    assert gate.operational_config_errors(config) == []

    broad = gate.OperationalConfig(
        repo_root=config.repo_root,
        run_root=Path("/home/taishajo/work/state/task8-runs"),
        approval_path=config.approval_path,
        prior_sha=config.prior_sha,
        candidate_sha=config.candidate_sha,
    )
    overlapping = gate.OperationalConfig(
        repo_root=config.repo_root,
        run_root=config.repo_root / "task8-run",
        approval_path=config.approval_path,
        prior_sha=config.prior_sha,
        candidate_sha=config.candidate_sha,
    )
    same_source = gate.OperationalConfig(
        repo_root=config.repo_root,
        run_root=config.run_root,
        approval_path=config.approval_path,
        prior_sha=config.prior_sha,
        candidate_sha=config.prior_sha,
    )

    assert any("named child" in error for error in gate.operational_config_errors(broad))
    assert any("must not contain" in error for error in gate.operational_config_errors(overlapping))
    assert any("must differ" in error for error in gate.operational_config_errors(same_source))


def test_operational_plan_is_exact_bounded_and_secret_free(tmp_path: Path) -> None:
    config = _operational_config(tmp_path)
    commands = gate.build_operational_plan(config)
    payload = gate.plan_payload(config, _approval(config))

    assert len(commands) == 29
    assert len({entry.action for entry in commands}) == 29
    assert {entry.phase for entry in commands} == set(gate.REQUIRED_PHASES)
    assert config.runtime_root.name == "NExtSEEK"
    assert config.source_root != config.runtime_root
    assert not any("install" in entry.argv for entry in commands)
    worktrees = [entry for entry in commands if "worktree" in entry.argv and "add" in entry.argv]
    assert {entry.action for entry in worktrees} == {"candidate-worktree", "source-worktree"}
    rebuild = next(entry for entry in commands if entry.action == "forward-rebuild")
    assert rebuild.cwd == str(config.runtime_root.resolve())
    assert rebuild.argv[-1] == str(config.source_root.resolve())
    assert rebuild.argv[rebuild.argv.index("--builder") + 1] == config.builder_name
    assert "--no-restart" in rebuild.argv
    assert "--no-registry-push" in rebuild.argv
    assert any(
        "./startup.sh" in entry.argv and "rebuild" in entry.argv
        and "--source-tree" in entry.argv
        for entry in commands
    )
    assert payload["bounds"] == {
        "max_cpus": gate.MAX_CPUS,
        "max_memory_bytes": gate.MAX_MEMORY_BYTES,
        "required_available_memory_bytes": gate.REQUIRED_AVAILABLE_MEMORY_BYTES,
        "minimum_memory_reserve_bytes": gate.MINIMUM_MEMORY_RESERVE_BYTES,
        "wall_cap_s": gate.MAX_WALL_S,
        "required_free_bytes": gate.REQUIRED_FREE_BYTES,
        "minimum_disk_reserve_bytes": gate.MINIMUM_DISK_RESERVE_BYTES,
    }
    serialized = json.dumps(payload, sort_keys=True).lower()
    assert "ghcr_token=" not in serialized
    assert "password=" not in serialized
    assert "secret_key=" not in serialized
    assert "docker system prune" not in serialized
    assert "dockerd-rootless" not in serialized
    assert "neo4j" not in serialized
    assert "solr" not in serialized
    assert " down -v" not in serialized
    assert "$(" not in serialized
    assert "`" not in serialized


def test_named_builder_is_resource_bounded_and_exactly_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _operational_config(tmp_path)
    adapter = gate.LocalOperationalAdapter(config, artifact_dir=tmp_path)
    docker_calls: list[list[str]] = []
    runtime_calls: list[list[str]] = []
    builder_image = "sha256:" + "a" * 64

    def fake_runtime(argv, *, timeout_s):
        runtime_calls.append(argv)
        return gate.CommandOutcome(0)

    def fake_docker(argv, *, timeout_s, input_bytes=None, input_path=None):
        docker_calls.append(argv)
        if argv[:2] == ["ps", "-q"]:
            return gate.CommandOutcome(0, b"builder-container\n")
        if argv[:2] == ["inspect", "builder-container"]:
            return gate.CommandOutcome(0, f"{builder_image}\n".encode())
        return gate.CommandOutcome(0)

    monkeypatch.setattr(adapter, "_runtime_command", fake_runtime)
    monkeypatch.setattr(adapter, "_docker", fake_docker)
    adapter.facts["preexisting_image_ids"] = set()

    assert adapter._prepare_build().returncode == 0
    assert runtime_calls[0][:3] == ["docker", "compose", "stop"]
    create = next(call for call in docker_calls if call[:2] == ["buildx", "create"])
    assert "cpuset-cpus=" + gate.TASKSET_CPU_LIST in create
    assert "cpu-quota=150000" in create
    assert "memory=4g" in create

    assert adapter._remove_builder().returncode == 0
    assert ["buildx", "rm", config.builder_name] in docker_calls
    assert ["image", "rm", builder_image] in docker_calls
    assert adapter.facts["builder_created"] is True
    assert adapter.facts["builder_removed"] is True


def test_command_input_path_streams_without_loading_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = gate.LocalOperationalAdapter(
        _operational_config(tmp_path), artifact_dir=tmp_path
    )
    payload = tmp_path / "large-enough-to-stream.bin"
    payload.write_bytes(b"streamed-task8-input" * 1024)
    monkeypatch.setattr(adapter, "_sample_resources", lambda _pids=None: None)

    outcome = adapter._run(
        [
            sys.executable,
            "-c",
            "import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())",
        ],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout_s=10,
        input_path=payload,
    )

    assert outcome.returncode == 0
    assert outcome.stdout.strip().decode() == hashlib.sha256(payload.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="bytes or a path"):
        adapter._run(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout_s=10,
            input_bytes=b"not-allowed-together",
            input_path=payload,
        )


def test_restore_probe_streams_backup_with_bounded_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = gate.LocalOperationalAdapter(
        _operational_config(tmp_path), artifact_dir=tmp_path
    )
    backup_path = tmp_path / "dmac.sql"
    backup_path.write_bytes(b"-- task8 fixture\n")
    adapter.facts["backup"] = {
        "path": str(backup_path),
        "sha256": hashlib.sha256(backup_path.read_bytes()).hexdigest(),
        "tables": ["django_migrations"],
        "restore_probe": "PENDING",
    }
    calls: list[tuple[list[str], float, bytes | None, Path | None]] = []

    def fake_mysql(
        args, *, timeout_s, input_bytes=None, input_path=None,
    ):
        calls.append((args, timeout_s, input_bytes, input_path))
        if "SELECT COUNT(*)" in " ".join(args):
            return gate.CommandOutcome(0, b"1\n")
        return gate.CommandOutcome(0)

    monkeypatch.setattr(adapter, "_mysql", fake_mysql)

    assert adapter._restore_probe().returncode == 0
    restore = next(call for call in calls if call[0] == ["mysql", "-uroot", "task8_restore_probe"])
    assert restore[1:] == (300, None, backup_path)
    assert adapter.facts["backup"]["restore_probe"] == "PASS"


def _bounded_wait_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> gate.LocalOperationalAdapter:
    adapter = gate.LocalOperationalAdapter(
        _operational_config(tmp_path), artifact_dir=tmp_path
    )
    adapter.facts["memory_peak_bytes"] = 0
    monkeypatch.setattr(adapter, "_sample_resources", lambda: None)
    monkeypatch.setattr(
        gate, "available_memory_bytes",
        lambda: gate.MINIMUM_MEMORY_RESERVE_BYTES + 1,
    )
    monkeypatch.setattr(
        gate.shutil, "disk_usage",
        lambda _path: SimpleNamespace(free=gate.MINIMUM_DISK_RESERVE_BYTES + 1),
    )
    return adapter


def test_wait_site_records_ready_resource_and_timeout_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _bounded_wait_adapter(tmp_path, monkeypatch)
    monkeypatch.setattr(adapter, "_site_ok", lambda: True)
    assert adapter._wait_site(timeout_s=17) is True
    assert adapter.facts["last_site_wait"]["status"] == "ready"
    assert adapter.facts["last_site_wait"]["attempts"] == 1

    adapter.facts["memory_peak_bytes"] = gate.MAX_MEMORY_BYTES + 1
    assert adapter._wait_site(timeout_s=17) is False
    assert adapter.facts["last_site_wait"]["status"] == "resource_limit"
    assert (
        adapter.facts["last_site_wait"]["memory_peak_bytes"]
        == gate.MAX_MEMORY_BYTES + 1
    )

    adapter.facts["memory_peak_bytes"] = 0
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(gate.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(gate.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(adapter, "_site_ok", lambda: False)
    assert adapter._wait_site(timeout_s=1) is False
    assert adapter.facts["last_site_wait"]["status"] == "timeout"
    assert adapter.facts["last_site_wait"]["attempts"] == 1
    assert (
        adapter.facts["last_site_wait"]["memory_available_bytes"]
        == gate.MINIMUM_MEMORY_RESERVE_BYTES + 1
    )
    assert (
        adapter.facts["last_site_wait"]["disk_free_bytes"]
        == gate.MINIMUM_DISK_RESERVE_BYTES + 1
    )


def test_start_and_resume_use_bounded_measured_readiness_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = gate.OperationalConfig(
        repo_root=gate.ROOT,
        run_root=tmp_path / "run",
        approval_path=tmp_path / "approval.json",
        prior_sha="1" * 40,
        candidate_sha="2" * 40,
    )
    adapter = gate.LocalOperationalAdapter(
        config, artifact_dir=tmp_path
    )
    runtime = adapter.config.runtime_root
    for relative in (
        "docker/db.env",
        "docker/nextseek.env",
        "docker/bedrock-proxy/proxy-secret.env",
        "dmac/local_settings.py",
    ):
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")
    monkeypatch.setattr(
        adapter, "_runtime_command", lambda *args, **kwargs: gate.CommandOutcome(0)
    )
    monkeypatch.setattr(
        adapter, "_docker", lambda *args, **kwargs: gate.CommandOutcome(0, b"healthy\n")
    )
    readiness_windows: list[float] = []
    monkeypatch.setattr(
        adapter, "_wait_site",
        lambda timeout_s: readiness_windows.append(timeout_s) or True,
    )

    assert adapter._start_namespace().returncode == 0
    assert adapter._resume_cohort().returncode == 0
    assert readiness_windows == [240, 180]


class _FakeAdapter:
    def __init__(
        self,
        *,
        fail_action: str | None = None,
        cleanup_returncode: int = 0,
        cleanup_raises: bool = False,
    ) -> None:
        self.fail_action = fail_action
        self.cleanup_returncode = cleanup_returncode
        self.cleanup_raises = cleanup_raises
        self.actions: list[str] = []
        self.emergency_stops = 0

    def execute(self, command: gate.PlannedCommand, *, timeout_s: float) -> gate.CommandOutcome:
        assert 0 < timeout_s <= gate.MAX_WALL_S
        self.actions.append(command.action)
        return gate.CommandOutcome(
            returncode=17 if command.action == self.fail_action else 0,
            stdout=f"ok:{command.action}".encode(),
        )

    def emergency_stop(self, *, timeout_s: float) -> gate.CommandOutcome:
        assert 0 < timeout_s <= 30
        self.emergency_stops += 1
        if self.cleanup_raises:
            raise RuntimeError("synthetic cleanup failure")
        return gate.CommandOutcome(returncode=self.cleanup_returncode)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.01
        return self.value


def _passing_preflight() -> dict:
    return {
        "gate": "PASS",
        "errors": [],
    }


def test_bounded_runner_executes_exact_plan_and_hashes_outputs(tmp_path: Path) -> None:
    config = _operational_config(tmp_path)
    adapter = _FakeAdapter()
    clock = _Clock()

    artifacts = gate.run_operational_plan(
        config,
        _approval(config),
        adapter,
        artifact_dir=tmp_path / "artifacts",
        now=lambda: datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc),
        monotonic=clock,
        free_bytes=lambda: gate.REQUIRED_FREE_BYTES,
        preflight_result=_passing_preflight(),
    )

    planned = gate.build_operational_plan(config)
    assert adapter.actions == [command.action for command in planned]
    assert adapter.emergency_stops == 0
    assert artifacts.command_count == 29
    assert artifacts.plan_path.is_file()
    entries = [json.loads(line) for line in artifacts.ledger_path.read_text().splitlines()]
    assert len(entries) == 29
    for entry, command in zip(entries, planned, strict=True):
        assert entry["action"] == command.action
        assert entry["argv"] == list(command.argv)
        assert entry["returncode"] == 0
        assert entry["stdout_sha256"] == hashlib.sha256(
            f"ok:{command.action}".encode()
        ).hexdigest()
        assert entry["stderr_sha256"] == gate.EMPTY_SHA256


def test_bounded_runner_stops_after_failure_and_emergency_cleans_namespace(tmp_path: Path) -> None:
    config = _operational_config(tmp_path)
    adapter = _FakeAdapter(fail_action="prewrite-seed")

    with pytest.raises(gate.OperationalRunError, match="prewrite-seed.*17"):
        gate.run_operational_plan(
            config,
            _approval(config),
            adapter,
            artifact_dir=tmp_path / "artifacts",
            now=lambda: datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc),
            monotonic=_Clock(),
            free_bytes=lambda: gate.REQUIRED_FREE_BYTES,
            preflight_result=_passing_preflight(),
        )

    assert adapter.actions[-1] == "prewrite-seed"
    assert "migration-aware-dump" not in adapter.actions
    assert adapter.emergency_stops == 1


def test_bounded_runner_cleans_partially_started_namespace(tmp_path: Path) -> None:
    config = _operational_config(tmp_path)
    adapter = _FakeAdapter(fail_action="namespace-start")

    with pytest.raises(gate.OperationalRunError, match="namespace-start.*17"):
        gate.run_operational_plan(
            config,
            _approval(config),
            adapter,
            artifact_dir=tmp_path / "artifacts",
            now=lambda: datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc),
            monotonic=_Clock(),
            free_bytes=lambda: gate.REQUIRED_FREE_BYTES,
            preflight_result=_passing_preflight(),
        )

    assert adapter.actions[-1] == "namespace-start"
    assert adapter.emergency_stops == 1


@pytest.mark.parametrize(
    ("adapter", "expected"),
    (
        (_FakeAdapter(fail_action="namespace-start", cleanup_returncode=23),
         "emergency cleanup returned 23"),
        (_FakeAdapter(fail_action="namespace-start", cleanup_raises=True),
         "emergency cleanup raised RuntimeError"),
    ),
)
def test_bounded_runner_surfaces_emergency_cleanup_failure(
    tmp_path: Path, adapter: _FakeAdapter, expected: str,
) -> None:
    config = _operational_config(tmp_path)

    with pytest.raises(gate.OperationalRunError, match=expected):
        gate.run_operational_plan(
            config,
            _approval(config),
            adapter,
            artifact_dir=tmp_path / "artifacts",
            now=lambda: datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc),
            monotonic=_Clock(),
            free_bytes=lambda: gate.REQUIRED_FREE_BYTES,
            preflight_result=_passing_preflight(),
        )

    assert adapter.emergency_stops == 1


def test_bounded_runner_refuses_disk_reserve_before_any_action(tmp_path: Path) -> None:
    config = _operational_config(tmp_path)
    adapter = _FakeAdapter()

    with pytest.raises(gate.OperationalRunError, match="disk reserve"):
        gate.run_operational_plan(
            config,
            _approval(config),
            adapter,
            artifact_dir=tmp_path / "artifacts",
            now=lambda: datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc),
            monotonic=_Clock(),
            free_bytes=lambda: gate.MINIMUM_DISK_RESERVE_BYTES - 1,
            preflight_result=_passing_preflight(),
        )

    assert adapter.actions == []
    assert adapter.emergency_stops == 0


def test_real_evidence_writer_uses_only_completed_run_facts(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    artifact_dir = root / "evidence" / "task8"
    artifact_dir.mkdir(parents=True)
    config = gate.OperationalConfig(
        repo_root=root,
        run_root=Path("/home/taishajo/work/state/task8-runs/evidence-writer-test"),
        approval_path=Path("/home/taishajo/work/state/task8-writer-approval.json"),
        prior_sha="1" * 40,
        candidate_sha="2" * 40,
    )
    approval = _approval(config)
    plan_path = artifact_dir / "operational-plan.json"
    _write_json(plan_path, gate.plan_payload(config, approval))
    ledger_path = artifact_dir / "commands.jsonl"
    entries = []
    for seq, command in enumerate(gate.build_operational_plan(config), 1):
        entries.append(
            gate._ledger_entry(
                command,
                seq=seq,
                outcome=gate.CommandOutcome(0),
                duration_s=0.01,
            )
        )
    ledger_path.write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries)
    )
    host_before = artifact_dir / "host-before.json"
    host_after = artifact_dir / "host-after.json"
    _write_json(host_before, {"host": "unchanged"})
    _write_json(host_after, {"host": "unchanged"})
    backup = tmp_path / "private" / "dmac.sql"
    backup.parent.mkdir()
    backup.write_bytes(b"-- verified disposable backup\n")
    backup.chmod(0o600)
    free = gate.shutil.disk_usage(root).free
    prior_image = "sha256:" + "a" * 64
    candidate_image = "sha256:" + "b" * 64
    prior_generation = "6" * 64
    active_generation = "7" * 64
    fake = _FakeAdapter()
    fake.facts = {
        "source": {
            "branch": "origin/dev",
            "remote_sha": config.candidate_sha,
            "deployed_sha": config.candidate_sha,
            "prior_sha": config.prior_sha,
            "clean": True,
            "ahead": 0,
            "behind": 0,
            "diff_sha256": gate.EMPTY_SHA256,
        },
        "namespace_id": config.compose_project,
        "prior_source_hashes": {
            f"verified/path-{index}.py": str(index) * 64
            for index in range(1, 6)
        },
        "peer_image_ids": {
            image: "sha256:" + str(index) * 64
            for index, image in enumerate(
                (
                    "mysql:8.0", "nginx:latest",
                    "nextseek-bedrock-proxy:latest",
                    "nextseek-ns-sidecar:latest", "dmac-assistant:poc",
                ),
                1,
            )
        },
        "builder_created": True,
        "builder_removed": True,
        "task8_resources_removed": True,
        "host_before": {"path": host_before, "sha256": _sha(host_before)},
        "host_after": {"path": host_after, "sha256": _sha(host_after)},
        "backup": {
            "path": str(backup),
            "mode": "0600",
            "sha256": _sha(backup),
            "size_bytes": backup.stat().st_size,
            "migration_aware": True,
            "source_range": f"{config.prior_sha}..{config.candidate_sha}",
            "migration_paths": [],
            "migration_diff_sha256": gate.EMPTY_SHA256,
            "tables": ["django_migrations"],
            "checksum_verified": True,
            "restore_probe": "PASS",
        },
        "prior_image_id": prior_image,
        "candidate_image_id": candidate_image,
        "rollback_tag": "nextseek-nextseek:pre-task8",
        "registry_tag": "ghcr.io/biomicrocenter/nextseek:baseline-20260818-22222222",
        "registry_digest": "sha256:" + "c" * 64,
        "prior_generation": prior_generation,
        "active_generation": active_generation,
        "prewrite_ids": ["turn:1"],
        "postwrite_ids": ["turn:2"],
        "row_counts": {
            "judgments": 1,
            "exclusions": 1,
            "pending_attempts": 1,
            "failed_attempts": 1,
            "reservations": 1,
            "tombstones": 1,
        },
        "forward_checks": {name: True for name in gate.REQUIRED_FORWARD_CHECKS},
        "oi3_checks": {name: True for name in gate.REQUIRED_OI3_CHECKS},
        "oi3_details": {"source": "direct probes"},
        "retained_ids_after_forward": ["turn:1", "turn:2"],
        "recovery_checks": {name: True for name in gate.REQUIRED_RECOVERY_CHECKS},
        "retained_ids_after_recovery": ["turn:1", "turn:2"],
        "recovery_active_generation": prior_generation,
        "recovery_image_id": prior_image,
        "forward_migrations": ["0019_merge_attribute_async_turn_ledger"],
        "migration_paths": [],
        "settings_sha256": "5" * 64,
        "disk_free_before_bytes": free,
        "disk_free_min_bytes": free,
        "memory_peak_bytes": 1024,
        "memory_available_before_bytes": gate.REQUIRED_AVAILABLE_MEMORY_BYTES,
        "memory_available_min_bytes": gate.MINIMUM_MEMORY_RESERVE_BYTES,
    }
    artifacts = gate.RunArtifacts(
        plan_path=plan_path,
        ledger_path=ledger_path,
        elapsed_s=12.0,
        command_count=len(entries),
    )

    written = gate.write_operational_evidence(
        config,
        approval,
        fake,
        artifacts,
    )

    assert written == root / gate.EVIDENCE
    assert gate.validation_errors(root, written) == []


def test_real_evidence_writer_refuses_missing_or_red_facts(tmp_path: Path) -> None:
    config = _operational_config(tmp_path)
    fake = _FakeAdapter()
    fake.facts = {"forward_checks": {"site_http_200": False}}
    artifacts = gate.RunArtifacts(
        plan_path=tmp_path / "missing-plan.json",
        ledger_path=tmp_path / "missing-ledger.jsonl",
        elapsed_s=1.0,
        command_count=0,
    )

    with pytest.raises(gate.OperationalRunError, match="required facts"):
        gate.write_operational_evidence(
            config,
            _approval(config),
            fake,
            artifacts,
        )


def test_real_adapter_dispatch_covers_every_planned_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _operational_config(tmp_path)
    adapter = gate.LocalOperationalAdapter(config, artifact_dir=tmp_path)
    green = lambda *args, **kwargs: gate.CommandOutcome(0)
    for name in (
        "_validate_approval_control", "_resources_control", "_validate_source",
        "_configure_harness", "_verify_and_tag_prior_image", "_start_namespace",
        "_snapshot_host", "_seed_prewrite", "_dump_dmac", "_restore_probe",
        "_prepare_build", "_remove_builder", "_resume_cohort", "_push_registry",
        "_seed_postwrite", "_verify_forward", "_disable_flags",
        "_stop_schedules", "_stop_workers", "_activate_prior",
        "_restore_prior_image", "_verify_forward_schema", "_verify_recovery",
        "_cleanup_namespace",
    ):
        monkeypatch.setattr(adapter, name, green)

    controls = [
        command
        for command in gate.build_operational_plan(config)
        if command.argv[0] == "task8-control"
    ]
    assert {command.action for command in controls} == gate.CONTROL_ACTIONS
    assert all(adapter._control(command).returncode == 0 for command in controls)
    unknown = gate.PlannedCommand(
        action="not-planned",
        phase="preflight",
        daemon="host_read_only",
        effect="read_only",
        argv=("task8-control", "not-planned"),
        cwd=str(gate.ROOT),
    )
    assert adapter._control(unknown).returncode == 78


def test_embedded_disposable_db_controls_compile_and_never_name_provider_calls(
    tmp_path: Path,
) -> None:
    adapter = gate.LocalOperationalAdapter(_operational_config(tmp_path), artifact_dir=tmp_path)
    snippets = (
        adapter._prewrite_code(),
        adapter._postwrite_code(),
        adapter._retention_code(),
    )
    for index, source in enumerate(snippets):
        compile(source, f"<task8-control-{index}>", "exec")
        lowered = source.lower()
        assert "bedrock" not in lowered
        assert "anthropic" not in lowered
        assert "provider" not in lowered
        assert "requests." not in lowered

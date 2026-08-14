"""Unit tests for build_tools.plan005_validate_plugins (Plan 005 Task 7)."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from build_tools.plan005_validate_plugins.validate import (
    IMMUTABLE_VALIDATOR_IMAGE,
    PluginValidationError,
    hash_plugin_tree,
    validate_installed_plugins,
)
from nextseek_api.cc_assistant.op_registry.install_oracle import discover_install

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATE_MODULE = "build_tools.plan005_validate_plugins"
PYTHONPATH = f"{REPO_ROOT}:{REPO_ROOT / 'dmac_assistant' / 'src'}"


def _write_manifest(plugin_dir: Path, *, payload: dict[str, object] | None = None) -> None:
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    body = payload or {
        "name": plugin_dir.name,
        "version": "0.1.0",
        "description": "Claude Code plugin for NExtSEEK research workflows.",
        "author": {"name": "BMC"},
    }
    if "name" not in body:
        body = {**body, "name": plugin_dir.name}
    (manifest_dir / "plugin.json").write_text(json.dumps(body), encoding="utf-8")


def _write_shim(plugin_dir: Path, shim_name: str) -> None:
    shim_path = plugin_dir / "bin" / shim_name
    shim_path.parent.mkdir(parents=True, exist_ok=True)
    shim_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shim_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def _write_dockerfile(path: Path, *, copy_plugins: tuple[str, ...]) -> None:
    lines = ["FROM scratch"]
    for plugin in copy_plugins:
        lines.append(
            f"COPY build_context/plugins/{plugin}/ /app/plugins/{plugin}/"
        )
        lines.append(f'ENV PATH="/app/plugins/{plugin}/bin:${{PATH}}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_plugin_tree(
    plugins_root: Path,
    plugin_name: str,
    *,
    manifest_payload: dict[str, object] | None = None,
) -> Path:
    plugin_dir = plugins_root / plugin_name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(plugin_dir, payload=manifest_payload)
    _write_shim(plugin_dir, "nextseek-alpha-op")
    return plugin_dir


def _fixture_repo(tmp_path: Path, *, plugins: tuple[str, ...]) -> Path:
    repo = tmp_path / "repo"
    plugins_root = repo / "docker/cc-runtime/build_context/plugins"
    dockerfile = repo / "docker/cc-runtime/Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    for plugin in plugins:
        _seed_plugin_tree(plugins_root, plugin)
    _write_dockerfile(dockerfile, copy_plugins=plugins)
    return repo


def test_hash_plugin_tree_changes_when_tree_changes(tmp_path: Path):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "a.txt").write_text("one", encoding="utf-8")
    before = hash_plugin_tree(plugin_dir)
    (plugin_dir / "b.txt").write_text("two", encoding="utf-8")
    after = hash_plugin_tree(plugin_dir)
    assert before != after


def test_validate_rejects_mutable_validator_image(tmp_path: Path):
    repo = _fixture_repo(tmp_path, plugins=("alpha-plugin",))
    with pytest.raises(PluginValidationError, match="immutable"):
        validate_installed_plugins(
            repo_root=repo,
            validator_image="claude:latest",
            skip_docker=True,
        )


def test_validate_skips_installed_plugin_is_red(tmp_path: Path):
    repo = _fixture_repo(tmp_path, plugins=("alpha-plugin", "beta-plugin"))
    with mock.patch(
        "build_tools.plan005_validate_plugins.validate.discover_install",
        wraps=discover_install,
    ) as wrapped:
        real = wrapped(
            plugins_root=repo / "docker/cc-runtime/build_context/plugins",
            dockerfile_path=repo / "docker/cc-runtime/Dockerfile",
        )
        trimmed = type(real)(
            plugins=real.plugins,
            copy_destinations=real.copy_destinations,
            path_entries=real.path_entries,
            manifests=tuple(m for m in real.manifests if m.plugin_dir == "alpha-plugin"),
            skills=real.skills,
            commands=real.commands,
            shims=real.shims,
        )
        wrapped.return_value = trimmed
        with pytest.raises(PluginValidationError, match="skipped"):
            validate_installed_plugins(
                repo_root=repo,
                skip_docker=True,
            )


def test_validate_invalid_manifest_is_red(tmp_path: Path):
    repo = _fixture_repo(tmp_path, plugins=("alpha-plugin",))
    manifest = (
        repo
        / "docker/cc-runtime/build_context/plugins/alpha-plugin/.claude-plugin/plugin.json"
    )
    manifest.write_text(
        json.dumps(
            {
                "name": "alpha-plugin",
                "version": "0.1.0",
                "description": "Claude Code plugin for NExtSEEK research workflows.",
                "author": {"name": "BMC"},
                "operations": ["nextseek-query"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PluginValidationError, match="inventory-like|local identity"):
        validate_installed_plugins(repo_root=repo, skip_docker=True)


def test_validate_second_plugin_without_code_change(tmp_path: Path):
    repo = _fixture_repo(tmp_path, plugins=("alpha-plugin", "beta-plugin"))
    with mock.patch(
        "build_tools.plan005_validate_plugins.validate.run_claude_plugin_validate",
        return_value=mock.Mock(returncode=0, stdout=b"", stderr=b""),
    ) as docker:
        outcome = validate_installed_plugins(repo_root=repo, skip_docker=False)
        assert outcome.plugins == ("alpha-plugin", "beta-plugin")
        assert docker.call_count == 2


def test_validate_duplicate_plugin_validation_is_red(tmp_path: Path):
    repo = _fixture_repo(tmp_path, plugins=("alpha-plugin",))
    discovery = discover_install(
        plugins_root=repo / "docker/cc-runtime/build_context/plugins",
        dockerfile_path=repo / "docker/cc-runtime/Dockerfile",
    )
    dup_manifests = discovery.manifests + discovery.manifests
    dup_discovery = type(discovery)(
        plugins=discovery.plugins,
        copy_destinations=discovery.copy_destinations,
        path_entries=discovery.path_entries,
        manifests=dup_manifests,
        skills=discovery.skills,
        commands=discovery.commands,
        shims=discovery.shims,
    )
    with mock.patch(
        "build_tools.plan005_validate_plugins.validate.discover_install",
        return_value=dup_discovery,
    ):
        with pytest.raises(PluginValidationError, match="duplicate"):
            validate_installed_plugins(repo_root=repo, skip_docker=True)


def test_validate_plugin_tree_change_after_docker_is_red(tmp_path: Path):
    repo = _fixture_repo(tmp_path, plugins=("alpha-plugin",))
    plugin_dir = repo / "docker/cc-runtime/build_context/plugins/alpha-plugin"

    def _mutate_after_validate(**kwargs):
        (plugin_dir / "mutated.txt").write_text("changed", encoding="utf-8")
        return mock.Mock(returncode=0, stdout=b"", stderr=b"")

    with mock.patch(
        "build_tools.plan005_validate_plugins.validate.run_claude_plugin_validate",
        side_effect=_mutate_after_validate,
    ):
        with pytest.raises(PluginValidationError, match="tree changed"):
            validate_installed_plugins(repo_root=repo, skip_docker=False)


def test_outcome_plugins_and_hashes_match_oracle(tmp_path: Path):
    repo = _fixture_repo(tmp_path, plugins=("alpha-plugin", "beta-plugin"))
    discovery = discover_install(
        plugins_root=repo / "docker/cc-runtime/build_context/plugins",
        dockerfile_path=repo / "docker/cc-runtime/Dockerfile",
    )
    with mock.patch(
        "build_tools.plan005_validate_plugins.validate.run_claude_plugin_validate",
        return_value=mock.Mock(returncode=0, stdout=b"", stderr=b""),
    ):
        outcome = validate_installed_plugins(repo_root=repo, skip_docker=False)
    assert outcome.plugins == discovery.plugins
    for plugin in discovery.plugins:
        plugin_dir = repo / "docker/cc-runtime/build_context/plugins" / plugin
        assert outcome.tree_hashes[plugin] == hash_plugin_tree(plugin_dir)


def test_docker_timeout_is_mapped_to_nonzero_result():
    from build_tools.plan005_validate_plugins.docker_runner import (
        run_claude_plugin_validate,
    )

    with mock.patch(
        "build_tools.plan005_validate_plugins.docker_runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=1),
    ):
        result = run_claude_plugin_validate(
            plugin_dir=REPO_ROOT
            / "docker/cc-runtime/build_context/plugins/nextseek",
            validator_image=IMMUTABLE_VALIDATOR_IMAGE,
            timeout_seconds=1,
        )
    assert result.returncode == 124
    assert b"timed out" in result.stderr


def test_cli_runs_on_stdlib_host_python_with_repo_only_pythonpath(tmp_path: Path):
    repo = _fixture_repo(tmp_path, plugins=("alpha-plugin",))
    host_python = "/usr/bin/python3"
    pydantic_probe = subprocess.run(
        [host_python, "-c", "import pydantic"],
        capture_output=True,
        check=False,
    )
    assert pydantic_probe.returncode != 0, "host python must not provide pydantic"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [
            host_python,
            "-m",
            VALIDATE_MODULE,
            "--repo-root",
            str(repo),
            "--validator-image",
            IMMUTABLE_VALIDATOR_IMAGE,
            "--per-plugin-timeout",
            "60",
            "--skip-docker",
        ],
        cwd="/tmp",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_cli_is_cwd_independent(tmp_path: Path):
    repo = _fixture_repo(tmp_path, plugins=("alpha-plugin",))
    env = {**dict(os.environ), "PYTHONPATH": PYTHONPATH}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            VALIDATE_MODULE,
            "--repo-root",
            str(repo),
            "--validator-image",
            IMMUTABLE_VALIDATOR_IMAGE,
            "--per-plugin-timeout",
            "60",
            "--skip-docker",
        ],
        cwd="/tmp",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(
    not Path("/var/run/docker.sock").exists(),
    reason="docker daemon unavailable",
)
def test_real_claude_validator_passes_current_tree():
    outcome = validate_installed_plugins(
        repo_root=REPO_ROOT,
        validator_image=IMMUTABLE_VALIDATOR_IMAGE,
        per_plugin_timeout=60,
        skip_docker=False,
    )
    assert outcome.plugins == ("nextseek",)

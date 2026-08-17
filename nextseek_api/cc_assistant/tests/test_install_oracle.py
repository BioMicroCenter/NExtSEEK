"""Focused tests for the CC image install oracle (Plan 005 Task 1)."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from nextseek_api.cc_assistant.op_registry import (
    InstallDiscovery,
    InstallOracleError,
    discover_install,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PLUGINS_ROOT = REPO_ROOT / "docker" / "cc-runtime" / "build_context" / "plugins"
DEFAULT_DOCKERFILE = REPO_ROOT / "docker" / "cc-runtime" / "Dockerfile"

SHIM_PREFIX = "nextseek-"


def _write_manifest(plugin_dir: Path, *, name: str | None = None) -> None:
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name if name is not None else plugin_dir.name,
        "version": "0.0.1",
        "description": "synthetic plugin for install-oracle tests",
    }
    (manifest_dir / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_skill(plugin_dir: Path, skill_name: str) -> Path:
    skill_path = plugin_dir / "skills" / skill_name / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(
        f"---\nname: {skill_name}\ndescription: test\n---\n",
        encoding="utf-8",
    )
    return skill_path


def _write_command(plugin_dir: Path, command_name: str) -> Path:
    command_path = plugin_dir / "commands" / f"{command_name}.md"
    command_path.parent.mkdir(parents=True, exist_ok=True)
    command_path.write_text(
        f"---\ndescription: {command_name}\n---\n",
        encoding="utf-8",
    )
    return command_path


def _write_shim(
    plugin_dir: Path,
    shim_name: str,
    *,
    executable: bool = True,
) -> Path:
    shim_path = plugin_dir / "bin" / shim_name
    shim_path.parent.mkdir(parents=True, exist_ok=True)
    shim_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    mode = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    if not executable:
        mode = stat.S_IRUSR | stat.S_IWUSR
    shim_path.chmod(mode)
    return shim_path


def _write_plugin_tree(
    plugins_root: Path,
    plugin_name: str,
    *,
    manifest_name: str | None = None,
    skill_names: tuple[str, ...] = ("alpha-skill",),
    command_names: tuple[str, ...] = ("alpha-cmd",),
    shim_names: tuple[str, ...] = (f"{SHIM_PREFIX}alpha-op",),
) -> Path:
    plugin_dir = plugins_root / plugin_name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(plugin_dir, name=manifest_name)
    for skill_name in skill_names:
        _write_skill(plugin_dir, skill_name)
    for command_name in command_names:
        _write_command(plugin_dir, command_name)
    for shim_name in shim_names:
        _write_shim(plugin_dir, shim_name)
    return plugin_dir


def _write_dockerfile(
    path: Path,
    *,
    copy_plugins: tuple[str, ...],
    path_plugins: tuple[str, ...] | None = None,
    extra_lines: tuple[str, ...] = (),
) -> None:
    if path_plugins is None:
        path_plugins = copy_plugins
    lines = ["FROM scratch"]
    for plugin in copy_plugins:
        lines.append(
            f"COPY build_context/plugins/{plugin}/ /app/plugins/{plugin}/"
        )
    for plugin in path_plugins:
        lines.append(f'ENV PATH="/app/plugins/{plugin}/bin:${{PATH}}"')
    lines.extend(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _discover(
    tmp_path: Path,
    *,
    copy_plugins: tuple[str, ...],
    path_plugins: tuple[str, ...] | None = None,
    extra_dockerfile_lines: tuple[str, ...] = (),
) -> InstallDiscovery:
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    for plugin in copy_plugins:
        _write_plugin_tree(plugins_root, plugin)
    _write_dockerfile(
        dockerfile,
        copy_plugins=copy_plugins,
        path_plugins=path_plugins,
        extra_lines=extra_dockerfile_lines,
    )
    return discover_install(plugins_root=plugins_root, dockerfile_path=dockerfile)


def test_add_plugin_is_discovered_without_oracle_code_change(tmp_path: Path):
    discovery = _discover(tmp_path, copy_plugins=("alpha-plugin", "beta-plugin"))
    assert set(discovery.plugins) == {"alpha-plugin", "beta-plugin"}
    assert {m.plugin_dir for m in discovery.manifests} == {
        "alpha-plugin",
        "beta-plugin",
    }


def test_remove_plugin_disappears_from_discovery(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    _write_plugin_tree(plugins_root, "keep-plugin")
    _write_plugin_tree(plugins_root, "drop-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("keep-plugin",))
    discovery = discover_install(plugins_root=plugins_root, dockerfile_path=dockerfile)
    assert discovery.plugins == ("keep-plugin",)
    assert {m.plugin_dir for m in discovery.manifests} == {"keep-plugin"}


def test_rename_plugin_changes_discovery(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    _write_plugin_tree(plugins_root, "renamed-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("renamed-plugin",))
    discovery = discover_install(plugins_root=plugins_root, dockerfile_path=dockerfile)
    assert discovery.plugins == ("renamed-plugin",)
    assert all(skill.plugin_dir == "renamed-plugin" for skill in discovery.skills)
    assert all(cmd.plugin_dir == "renamed-plugin" for cmd in discovery.commands)
    assert all(shim.plugin_dir == "renamed-plugin" for shim in discovery.shims)


def test_missing_manifest_fails(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    plugin_dir = plugins_root / "no-manifest"
    plugin_dir.mkdir(parents=True)
    _write_skill(plugin_dir, "lonely-skill")
    _write_dockerfile(dockerfile, copy_plugins=("no-manifest",))
    with pytest.raises(InstallOracleError, match="manifest"):
        discover_install(plugins_root=plugins_root, dockerfile_path=dockerfile)


def test_duplicate_copy_destination_fails(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    _write_plugin_tree(plugins_root, "dup-plugin")
    _write_dockerfile(
        dockerfile,
        copy_plugins=("dup-plugin",),
        extra_lines=(
            "COPY build_context/plugins/dup-plugin/ /app/plugins/dup-plugin/",
        ),
    )
    with pytest.raises(InstallOracleError, match="duplicate"):
        discover_install(plugins_root=plugins_root, dockerfile_path=dockerfile)


def test_traversal_in_plugin_name_fails(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    _write_plugin_tree(plugins_root, "evil")
    _write_dockerfile(
        dockerfile,
        copy_plugins=("evil",),
        extra_lines=(
            "COPY build_context/plugins/../plugins/evil/ /app/plugins/evil/",
        ),
    )
    with pytest.raises(InstallOracleError, match="traversal|unsafe"):
        discover_install(plugins_root=plugins_root, dockerfile_path=dockerfile)


def test_copy_path_disagreement_fails(tmp_path: Path):
    with pytest.raises(InstallOracleError, match="COPY.*PATH|PATH.*COPY"):
        _discover(
            tmp_path,
            copy_plugins=("only-copy",),
            path_plugins=("only-path",),
        )


def test_copied_plugin_absent_from_path_fails(tmp_path: Path):
    with pytest.raises(InstallOracleError, match="COPY.*PATH|PATH.*COPY"):
        _discover(
            tmp_path,
            copy_plugins=("copied-plugin",),
            path_plugins=(),
        )


def test_duplicate_path_entries_fail(tmp_path: Path):
    with pytest.raises(InstallOracleError, match="duplicate"):
        _discover(
            tmp_path,
            copy_plugins=("dup-path-plugin",),
            extra_dockerfile_lines=(
                'ENV PATH="/app/plugins/dup-path-plugin/bin:${PATH}"',
            ),
        )


def test_broad_plugins_directory_copy_fails(tmp_path: Path):
    with pytest.raises(InstallOracleError, match="broad"):
        _discover(
            tmp_path,
            copy_plugins=("ok-plugin",),
            extra_dockerfile_lines=(
                "COPY build_context/plugins/ /app/plugins/",
            ),
        )


def test_missing_plugin_bin_directory_fails(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    plugin_dir = plugins_root / "no-bin-plugin"
    plugin_dir.mkdir(parents=True)
    _write_manifest(plugin_dir)
    _write_dockerfile(dockerfile, copy_plugins=("no-bin-plugin",))
    with pytest.raises(InstallOracleError, match="bin"):
        discover_install(plugins_root=plugins_root, dockerfile_path=dockerfile)


def test_plugin_path_without_literal_braced_path_fails(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    _write_plugin_tree(plugins_root, "plain-path-plugin")
    dockerfile.write_text(
        "\n".join(
            [
                "FROM scratch",
                "COPY build_context/plugins/plain-path-plugin/ /app/plugins/plain-path-plugin/",
                'ENV PATH="/app/plugins/plain-path-plugin/bin:/usr/bin"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(InstallOracleError, match=r"\$\{PATH\}"):
        discover_install(plugins_root=plugins_root, dockerfile_path=dockerfile)


def test_non_executable_shim_fails(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    plugin_dir = _write_plugin_tree(
        plugins_root,
        "bad-shim-plugin",
        shim_names=(f"{SHIM_PREFIX}bad-op",),
    )
    _write_shim(plugin_dir, f"{SHIM_PREFIX}bad-op", executable=False)
    _write_dockerfile(dockerfile, copy_plugins=("bad-shim-plugin",))
    with pytest.raises(InstallOracleError, match="executable"):
        discover_install(plugins_root=plugins_root, dockerfile_path=dockerfile)


def test_manifest_name_directory_mismatch_fails(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    _write_plugin_tree(
        plugins_root,
        "dir-name",
        manifest_name="other-name",
    )
    _write_dockerfile(dockerfile, copy_plugins=("dir-name",))
    with pytest.raises(InstallOracleError, match="manifest.*directory|directory.*manifest"):
        discover_install(plugins_root=plugins_root, dockerfile_path=dockerfile)


def test_discovery_reports_skills_commands_and_shims(tmp_path: Path):
    discovery = _discover(
        tmp_path,
        copy_plugins=("report-plugin",),
        path_plugins=("report-plugin",),
    )
    assert {skill.skill_name for skill in discovery.skills} == {"alpha-skill"}
    assert {cmd.command_name for cmd in discovery.commands} == {"alpha-cmd"}
    assert {shim.shim_name for shim in discovery.shims} == {f"{SHIM_PREFIX}alpha-op"}


def test_current_tree_discovery_succeeds_without_fixed_counts():
    discovery = discover_install(
        plugins_root=DEFAULT_PLUGINS_ROOT,
        dockerfile_path=DEFAULT_DOCKERFILE,
    )
    assert discovery.plugins, "expected at least one installed plugin in the current tree"
    assert discovery.manifests
    assert discovery.skills
    assert discovery.commands
    assert discovery.shims
    assert all(
        shim.shim_name.startswith(SHIM_PREFIX) for shim in discovery.shims
    )
    assert all(os.access(shim.shim_path, os.X_OK) for shim in discovery.shims)
    copy_plugins = {dest.plugin_name for dest in discovery.copy_destinations}
    path_plugins = {entry.plugin_name for entry in discovery.path_entries}
    assert copy_plugins == path_plugins == set(discovery.plugins)

"""Discover CC image plugin installs from Dockerfile COPY/PATH and plugin trees."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

PLUGIN_COPY_RE = re.compile(
    r"^COPY\s+build_context/plugins/(?P<plugin>[^/\s]+)/\s+/app/plugins/(?P<dest>[^/\s]+)/\s*$"
)
PLUGIN_PATH_RE = re.compile(r"/app/plugins/(?P<plugin>[^/]+)/bin")
ENV_PATH_RE = re.compile(r"^ENV\s+PATH=(.+)$")
BROAD_PLUGIN_COPY_RE = re.compile(
    r"^COPY\s+(?:--\S+\s+)*build_context/plugins(?:/?|\./)\s+/app/plugins/?\s*$"
)
LITERAL_PATH_REF = "${PATH}"
SHIM_PREFIX = "nextseek-"
MANIFEST_RELATIVE = Path(".claude-plugin") / "plugin.json"


class InstallOracleError(ValueError):
    """Raised when plugin install discovery or reconciliation fails."""


@dataclass(frozen=True)
class CopyDestination:
    plugin_name: str
    source: str
    destination: str


@dataclass(frozen=True)
class PathEntry:
    plugin_name: str
    path_fragment: str


@dataclass(frozen=True)
class PluginManifest:
    plugin_dir: str
    manifest_path: Path
    name: str


@dataclass(frozen=True)
class SkillDiscovery:
    plugin_dir: str
    skill_name: str
    skill_path: Path


@dataclass(frozen=True)
class CommandDiscovery:
    plugin_dir: str
    command_name: str
    command_path: Path


@dataclass(frozen=True)
class ShimDiscovery:
    plugin_dir: str
    shim_name: str
    shim_path: Path


@dataclass(frozen=True)
class InstallDiscovery:
    plugins: tuple[str, ...]
    copy_destinations: tuple[CopyDestination, ...]
    path_entries: tuple[PathEntry, ...]
    manifests: tuple[PluginManifest, ...]
    skills: tuple[SkillDiscovery, ...]
    commands: tuple[CommandDiscovery, ...]
    shims: tuple[ShimDiscovery, ...]


def discover_install(
    *,
    plugins_root: Path,
    dockerfile_path: Path,
) -> InstallDiscovery:
    """Reconcile Dockerfile installs with manifest-bearing plugin source trees."""
    copy_destinations = _parse_copy_destinations(dockerfile_path.read_text(encoding="utf-8"))
    path_entries = _parse_path_entries(dockerfile_path.read_text(encoding="utf-8"))
    manifests = _scan_manifests(plugins_root)
    skills = _scan_skills(plugins_root)
    commands = _scan_commands(plugins_root)
    shims = _scan_shims(plugins_root)

    copy_plugins = {dest.plugin_name for dest in copy_destinations}
    path_plugins = {entry.plugin_name for entry in path_entries}
    manifest_plugins = {manifest.plugin_dir for manifest in manifests}

    _ensure_no_traversal(copy_destinations)
    _ensure_manifests_for_copy_plugins(copy_plugins, manifest_plugins, plugins_root)
    _ensure_copy_path_agreement(copy_plugins, path_plugins)
    _ensure_plugin_bins(copy_plugins, plugins_root)

    plugins = tuple(sorted(copy_plugins))
    return InstallDiscovery(
        plugins=plugins,
        copy_destinations=copy_destinations,
        path_entries=path_entries,
        manifests=tuple(
            manifest for manifest in manifests if manifest.plugin_dir in copy_plugins
        ),
        skills=tuple(skill for skill in skills if skill.plugin_dir in copy_plugins),
        commands=tuple(cmd for cmd in commands if cmd.plugin_dir in copy_plugins),
        shims=tuple(shim for shim in shims if shim.plugin_dir in copy_plugins),
    )


def _parse_copy_destinations(dockerfile_text: str) -> tuple[CopyDestination, ...]:
    destinations: list[CopyDestination] = []
    seen: set[str] = set()
    for line in dockerfile_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ".." in stripped:
            raise InstallOracleError(f"unsafe traversal in Dockerfile line: {stripped}")
        if BROAD_PLUGIN_COPY_RE.match(stripped):
            raise InstallOracleError(
                "broad COPY of the plugins directory is forbidden: "
                f"{stripped}"
            )
        match = PLUGIN_COPY_RE.match(stripped)
        if not match:
            continue
        plugin_name = match.group("plugin")
        dest_name = match.group("dest")
        if plugin_name != dest_name:
            raise InstallOracleError(
                "plugin COPY source and destination names must match: "
                f"{plugin_name!r} != {dest_name!r}"
            )
        if plugin_name in seen:
            raise InstallOracleError(
                f"duplicate COPY destination for installed plugin {plugin_name!r}"
            )
        seen.add(plugin_name)
        destinations.append(
            CopyDestination(
                plugin_name=plugin_name,
                source=f"build_context/plugins/{plugin_name}/",
                destination=f"/app/plugins/{plugin_name}/",
            )
        )
    return tuple(destinations)


def _parse_path_entries(dockerfile_text: str) -> tuple[PathEntry, ...]:
    entries: list[PathEntry] = []
    seen: set[str] = set()
    for line in dockerfile_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        env_match = ENV_PATH_RE.match(stripped)
        if not env_match:
            continue
        path_value = env_match.group(1)
        plugin_names = PLUGIN_PATH_RE.findall(path_value)
        if plugin_names and LITERAL_PATH_REF not in path_value:
            raise InstallOracleError(
                "plugin PATH must preserve literal ${PATH}: "
                f"{stripped}"
            )
        for plugin_name in plugin_names:
            if plugin_name in seen:
                raise InstallOracleError(
                    f"duplicate PATH entry for installed plugin {plugin_name!r}"
                )
            seen.add(plugin_name)
            entries.append(
                PathEntry(
                    plugin_name=plugin_name,
                    path_fragment=f"/app/plugins/{plugin_name}/bin",
                )
            )
    return tuple(sorted(entries, key=lambda entry: entry.plugin_name))


def _scan_manifests(plugins_root: Path) -> tuple[PluginManifest, ...]:
    manifests: list[PluginManifest] = []
    if not plugins_root.is_dir():
        return tuple()
    for plugin_dir in sorted(p for p in plugins_root.iterdir() if p.is_dir()):
        manifest_path = plugin_dir / MANIFEST_RELATIVE
        if not manifest_path.is_file():
            continue
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InstallOracleError(
                f"manifest at {manifest_path} must contain a non-empty string name"
            )
        if name != plugin_dir.name:
            raise InstallOracleError(
                "manifest name must match plugin directory name: "
                f"{name!r} != {plugin_dir.name!r}"
            )
        manifests.append(
            PluginManifest(
                plugin_dir=plugin_dir.name,
                manifest_path=manifest_path,
                name=name,
            )
        )
    return tuple(manifests)


def _scan_skills(plugins_root: Path) -> tuple[SkillDiscovery, ...]:
    skills: list[SkillDiscovery] = []
    if not plugins_root.is_dir():
        return tuple()
    for plugin_dir in sorted(p for p in plugins_root.iterdir() if p.is_dir()):
        skills_dir = plugin_dir / "skills"
        if not skills_dir.is_dir():
            continue
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            skill_path = skill_dir / "SKILL.md"
            if skill_path.is_file():
                skills.append(
                    SkillDiscovery(
                        plugin_dir=plugin_dir.name,
                        skill_name=skill_dir.name,
                        skill_path=skill_path,
                    )
                )
    return tuple(skills)


def _scan_commands(plugins_root: Path) -> tuple[CommandDiscovery, ...]:
    commands: list[CommandDiscovery] = []
    if not plugins_root.is_dir():
        return tuple()
    for plugin_dir in sorted(p for p in plugins_root.iterdir() if p.is_dir()):
        commands_dir = plugin_dir / "commands"
        if not commands_dir.is_dir():
            continue
        for command_path in sorted(commands_dir.glob("*.md")):
            commands.append(
                CommandDiscovery(
                    plugin_dir=plugin_dir.name,
                    command_name=command_path.stem,
                    command_path=command_path,
                )
            )
    return tuple(commands)


def _scan_shims(plugins_root: Path) -> tuple[ShimDiscovery, ...]:
    shims: list[ShimDiscovery] = []
    if not plugins_root.is_dir():
        return tuple()
    for plugin_dir in sorted(p for p in plugins_root.iterdir() if p.is_dir()):
        bin_dir = plugin_dir / "bin"
        if not bin_dir.is_dir():
            continue
        for shim_path in sorted(bin_dir.iterdir()):
            if not shim_path.is_file():
                continue
            if not shim_path.name.startswith(SHIM_PREFIX):
                continue
            if not os.access(shim_path, os.X_OK):
                raise InstallOracleError(
                    f"shim must be executable: {shim_path}"
                )
            shims.append(
                ShimDiscovery(
                    plugin_dir=plugin_dir.name,
                    shim_name=shim_path.name,
                    shim_path=shim_path,
                )
            )
    return tuple(shims)


def _ensure_no_traversal(copy_destinations: tuple[CopyDestination, ...]) -> None:
    for dest in copy_destinations:
        for value in (dest.plugin_name, dest.source, dest.destination):
            if ".." in value or value.startswith("/app/plugins//"):
                raise InstallOracleError(f"unsafe traversal in install path: {value!r}")


def _ensure_manifests_for_copy_plugins(
    copy_plugins: set[str],
    manifest_plugins: set[str],
    plugins_root: Path,
) -> None:
    missing = sorted(copy_plugins - manifest_plugins)
    if missing:
        raise InstallOracleError(
            "installed plugin missing manifest-bearing tree: "
            + ", ".join(missing)
        )
    for plugin_name in copy_plugins:
        plugin_dir = plugins_root / plugin_name
        manifest_path = plugin_dir / MANIFEST_RELATIVE
        if not manifest_path.is_file():
            raise InstallOracleError(
                f"installed plugin {plugin_name!r} missing manifest at {MANIFEST_RELATIVE}"
            )


def manifest_plugin_dirs(plugins_root: Path) -> tuple[str, ...]:
    """Return directory names of manifest-bearing plugin trees, sorted."""
    return tuple(manifest.plugin_dir for manifest in _scan_manifests(plugins_root))


def _ensure_plugin_bins(copy_plugins: set[str], plugins_root: Path) -> None:
    missing = [
        name
        for name in sorted(copy_plugins)
        if not (plugins_root / name / "bin").is_dir()
    ]
    if missing:
        raise InstallOracleError(
            "installed plugin missing bin directory: " + ", ".join(missing)
        )


def _ensure_copy_path_agreement(copy_plugins: set[str], path_plugins: set[str]) -> None:
    missing_path = sorted(copy_plugins - path_plugins)
    missing_copy = sorted(path_plugins - copy_plugins)
    if missing_path or missing_copy:
        raise InstallOracleError(
            "COPY/PATH disagreement: "
            f"copy_only={missing_path or []}, path_only={missing_copy or []}"
        )


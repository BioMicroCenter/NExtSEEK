"""Orchestrate local and Claude validator checks for installed plugins."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from build_tools.plan005_validate_plugins.docker_runner import run_claude_plugin_validate
from nextseek_api.cc_assistant.op_registry.install_oracle import (
    InstallOracleError,
    discover_install,
)
from nextseek_api.cc_assistant.op_registry.plugin_identity import (
    PluginIdentityError,
    load_and_validate_manifest,
)

DEFAULT_PLUGINS_ROOT_REL = Path("docker/cc-runtime/build_context/plugins")
DEFAULT_DOCKERFILE_REL = Path("docker/cc-runtime/Dockerfile")
IMMUTABLE_VALIDATOR_IMAGE = (
    "sha256:6f4f309cfe24f24047590251ba0ad34ff0c0ed7868b58b080f97b44ed800654c"
)


class PluginValidationError(RuntimeError):
    """Raised when plugin validation fails for any installed plugin."""


@dataclass(frozen=True)
class PluginValidationResult:
    plugin_dir: str
    tree_hash: str
    manifest_path: Path
    docker_returncode: int


@dataclass(frozen=True)
class ValidationOutcome:
    plugins: tuple[str, ...]
    results: tuple[PluginValidationResult, ...]
    tree_hashes: dict[str, str]

    def to_json(self) -> str:
        payload = {
            "plugins": list(self.plugins),
            "tree_hashes": dict(sorted(self.tree_hashes.items())),
            "results": [
                {
                    "plugin_dir": item.plugin_dir,
                    "tree_hash": item.tree_hash,
                    "manifest_path": item.manifest_path.as_posix(),
                    "docker_returncode": item.docker_returncode,
                }
                for item in self.results
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def hash_plugin_tree(plugin_dir: Path) -> str:
    """Return a stable SHA-256 digest over every file in a plugin tree."""
    digest = hashlib.sha256()
    for path in sorted(p for p in plugin_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(plugin_dir).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _assert_immutable_validator_image(validator_image: str) -> None:
    if validator_image != IMMUTABLE_VALIDATOR_IMAGE:
        raise PluginValidationError(
            "validator image must be the immutable locally verified digest: "
            f"{IMMUTABLE_VALIDATOR_IMAGE}"
        )


def validate_installed_plugins(
    *,
    repo_root: Path,
    validator_image: str = IMMUTABLE_VALIDATOR_IMAGE,
    per_plugin_timeout: int = 60,
    skip_docker: bool = False,
) -> ValidationOutcome:
    """Discover installed plugins and validate identity manifests locally and via Claude."""
    repo_root = repo_root.resolve()
    plugins_root = repo_root / DEFAULT_PLUGINS_ROOT_REL
    dockerfile_path = repo_root / DEFAULT_DOCKERFILE_REL
    _assert_immutable_validator_image(validator_image)

    try:
        discovery = discover_install(
            plugins_root=plugins_root,
            dockerfile_path=dockerfile_path,
        )
    except InstallOracleError as exc:
        raise PluginValidationError(str(exc)) from exc

    expected_plugins = tuple(sorted(discovery.plugins))
    results: list[PluginValidationResult] = []
    tree_hashes: dict[str, str] = {}
    seen_plugins: set[str] = set()

    for manifest in discovery.manifests:
        if manifest.plugin_dir in seen_plugins:
            raise PluginValidationError(
                f"duplicate validation requested for plugin {manifest.plugin_dir!r}"
            )
        seen_plugins.add(manifest.plugin_dir)

        plugin_path = plugins_root / manifest.plugin_dir
        tree_hash = hash_plugin_tree(plugin_path)
        tree_hashes[manifest.plugin_dir] = tree_hash

        try:
            load_and_validate_manifest(manifest.manifest_path)
        except (PluginIdentityError, json.JSONDecodeError, OSError) as exc:
            raise PluginValidationError(
                f"local identity validation failed for {manifest.plugin_dir}: {exc}"
            ) from exc

        docker_returncode = 0
        if not skip_docker:
            docker = run_claude_plugin_validate(
                plugin_dir=plugin_path,
                validator_image=validator_image,
                timeout_seconds=per_plugin_timeout,
            )
            docker_returncode = docker.returncode
            if docker_returncode != 0:
                stderr = docker.stderr.decode("utf-8", errors="replace").strip()
                stdout = docker.stdout.decode("utf-8", errors="replace").strip()
                detail = stderr or stdout or f"exit {docker_returncode}"
                raise PluginValidationError(
                    f"Claude validator failed for {manifest.plugin_dir}: {detail}"
                )

            post_hash = hash_plugin_tree(plugin_path)
            if post_hash != tree_hash:
                raise PluginValidationError(
                    f"plugin tree changed during validation for {manifest.plugin_dir}"
                )

        results.append(
            PluginValidationResult(
                plugin_dir=manifest.plugin_dir,
                tree_hash=tree_hash,
                manifest_path=manifest.manifest_path,
                docker_returncode=docker_returncode,
            )
        )

    if seen_plugins != set(expected_plugins):
        missing = sorted(set(expected_plugins) - seen_plugins)
        raise PluginValidationError(
            "validation skipped installed plugins: " + ", ".join(missing)
        )

    return ValidationOutcome(
        plugins=expected_plugins,
        results=tuple(sorted(results, key=lambda item: item.plugin_dir)),
        tree_hashes=dict(sorted(tree_hashes.items())),
    )

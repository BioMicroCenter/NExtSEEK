"""Unit tests for generated Dockerfile COPY/PATH and Compose context (Plan 005 Task 9)."""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from build_tools.gen_op_surfaces.constants import (
    ADDITIONAL_CONTEXTS_BEGIN,
    ADDITIONAL_CONTEXTS_END,
    CAPABILITIES_COPY_BEGIN,
    CAPABILITIES_COPY_END,
    COMPOSE_REL,
    DOCKERFILE_REL,
    PLUGIN_COPY_BEGIN,
    PLUGIN_COPY_END,
    PLUGIN_PATH_BEGIN,
    PLUGIN_PATH_END,
)
from build_tools.gen_op_surfaces.docker_blocks import (
    CanonicalCapabilitiesError,
    ComposeContextError,
    emit_additional_contexts_block,
    emit_capabilities_copy_block,
    emit_plugin_copy_block,
    emit_plugin_path_block,
    four_install_sets,
    parse_plugin_copy_names,
    parse_plugin_path_names,
    validate_canonical_capabilities_final_writer,
    validate_compose_named_context,
)
from build_tools.gen_op_surfaces.emit import (
    SurfaceTarget,
    check_surfaces,
    surface_targets,
    write_surfaces,
)
from nextseek_api.cc_assistant.op_registry.install_oracle import discover_install

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "docker/cc-runtime/Dockerfile"
NAMED_CONTEXT = "chat_nextseek"
CANONICAL_SRC = "src/chat_nextseek/context/capabilities.md"
IMAGE_CAPABILITIES = "/app/plugins/nextseek/context/capabilities.md"

COMPOSE_QUIET = [
    "timeout",
    "60s",
    "docker",
    "compose",
    "-f",
    str(COMPOSE_FILE),
    "config",
    "--no-env-resolution",
    "--quiet",
]
COMPOSE_JSON = [
    "timeout",
    "60s",
    "docker",
    "compose",
    "-f",
    str(COMPOSE_FILE),
    "config",
    "--no-env-resolution",
    "--format",
    "json",
]


def _write_manifest(plugin_dir: Path) -> None:
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": plugin_dir.name,
        "version": "0.0.1",
        "description": "synthetic plugin",
    }
    (manifest_dir / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_plugin(plugins_root: Path, name: str, *, with_bin: bool = True) -> Path:
    plugin_dir = plugins_root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(plugin_dir)
    if with_bin:
        bin_dir = plugin_dir / "bin"
        bin_dir.mkdir(exist_ok=True)
        shim = bin_dir / "nextseek-fixture-op"
        shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        shim.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return plugin_dir


def _seed_docker_repo(tmp_path: Path, plugin_names: tuple[str, ...]) -> Path:
    repo = tmp_path / "repo"
    plugins_root = repo / "docker/cc-runtime/build_context/plugins"
    dockerfile = repo / DOCKERFILE_REL
    compose = repo / COMPOSE_REL
    plugins_root.mkdir(parents=True)
    for name in plugin_names:
        _write_plugin(plugins_root, name)
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(
        "\n".join(
            [
                "FROM scratch",
                "prose-before-copy",
                PLUGIN_COPY_BEGIN,
                PLUGIN_COPY_END,
                CAPABILITIES_COPY_BEGIN,
                CAPABILITIES_COPY_END,
                "prose-after-copy",
                PLUGIN_PATH_BEGIN,
                PLUGIN_PATH_END,
                "prose-after-path",
                "",
            ]
        ),
        encoding="utf-8",
    )
    compose.write_text(
        "\n".join(
            [
                "services:",
                "  cc-agent:",
                "    build:",
                "      context: ./docker/cc-runtime",
                ADDITIONAL_CONTEXTS_BEGIN,
                ADDITIONAL_CONTEXTS_END,
                "    image: dmac-assistant:poc",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return repo


def _docker_targets() -> tuple[SurfaceTarget, ...]:
    return tuple(
        target
        for target in (
            SurfaceTarget(
                rel_path=DOCKERFILE_REL,
                kind="marked_block",
                begin_marker=PLUGIN_COPY_BEGIN,
                end_marker=PLUGIN_COPY_END,
                emit=emit_plugin_copy_block,
            ),
            SurfaceTarget(
                rel_path=DOCKERFILE_REL,
                kind="marked_block",
                begin_marker=CAPABILITIES_COPY_BEGIN,
                end_marker=CAPABILITIES_COPY_END,
                emit=emit_capabilities_copy_block,
            ),
            SurfaceTarget(
                rel_path=DOCKERFILE_REL,
                kind="marked_block",
                begin_marker=PLUGIN_PATH_BEGIN,
                end_marker=PLUGIN_PATH_END,
                emit=emit_plugin_path_block,
            ),
            SurfaceTarget(
                rel_path=COMPOSE_REL,
                kind="marked_block",
                begin_marker=ADDITIONAL_CONTEXTS_BEGIN,
                end_marker=ADDITIONAL_CONTEXTS_END,
                emit=emit_additional_contexts_block,
            ),
        )
    )


def test_four_sets_agree_after_write(tmp_path: Path) -> None:
    repo = _seed_docker_repo(tmp_path, ("alpha-plugin", "beta-plugin"))
    write_surfaces(repo_root=repo, targets=_docker_targets())
    plugins_root = repo / "docker/cc-runtime/build_context/plugins"
    dockerfile = repo / DOCKERFILE_REL
    dirs, copies, installed, paths = four_install_sets(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )
    assert dirs == copies == installed == paths == {"alpha-plugin", "beta-plugin"}
    text = dockerfile.read_text(encoding="utf-8")
    assert parse_plugin_copy_names(text) == {"alpha-plugin", "beta-plugin"}
    assert parse_plugin_path_names(text) == {"alpha-plugin", "beta-plugin"}
    assert "${PATH}" in text
    assert "COPY build_context/plugins/" not in text.replace(
        "COPY build_context/plugins/alpha-plugin/", ""
    ).replace("COPY build_context/plugins/beta-plugin/", "")


def test_fixture_plugin_fails_check_until_regeneration(tmp_path: Path) -> None:
    repo = _seed_docker_repo(tmp_path, ("keep-plugin",))
    write_surfaces(repo_root=repo, targets=_docker_targets())
    check_surfaces(repo_root=repo, targets=_docker_targets())
    plugins_root = repo / "docker/cc-runtime/build_context/plugins"
    dockerfile = repo / DOCKERFILE_REL
    _write_plugin(plugins_root, "fixture-plugin")
    with pytest.raises(SystemExit):
        check_surfaces(repo_root=repo, targets=_docker_targets())
    write_surfaces(repo_root=repo, targets=_docker_targets())
    dirs, copies, installed, paths = four_install_sets(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )
    assert dirs == copies == installed == paths == {"keep-plugin", "fixture-plugin"}
    check_surfaces(repo_root=repo, targets=_docker_targets())


def test_write_preserves_dockerfile_and_compose_prose(tmp_path: Path) -> None:
    repo = _seed_docker_repo(tmp_path, ("keep-plugin",))
    write_surfaces(repo_root=repo, targets=_docker_targets())
    dockerfile_text = (repo / DOCKERFILE_REL).read_text(encoding="utf-8")
    compose_text = (repo / COMPOSE_REL).read_text(encoding="utf-8")
    assert "prose-before-copy" in dockerfile_text
    assert "prose-after-copy" in dockerfile_text
    assert "prose-after-path" in dockerfile_text
    assert "image: dmac-assistant:poc" in compose_text
    assert "context: ./docker/cc-runtime" in compose_text


def test_canonical_capabilities_is_final_writer() -> None:
    text = "\n".join(
        [
            "COPY build_context/plugins/nextseek/ /app/plugins/nextseek/",
            f"COPY --from={NAMED_CONTEXT} {CANONICAL_SRC} {IMAGE_CAPABILITIES}",
        ]
    )
    validate_canonical_capabilities_final_writer(text)


def test_missing_named_context_copy_fails() -> None:
    with pytest.raises(CanonicalCapabilitiesError, match="named context|chat_nextseek"):
        validate_canonical_capabilities_final_writer(
            "COPY build_context/plugins/nextseek/ /app/plugins/nextseek/\n"
        )


def test_wrong_capabilities_source_or_destination_fails() -> None:
    with pytest.raises(CanonicalCapabilitiesError):
        validate_canonical_capabilities_final_writer(
            f"COPY --from={NAMED_CONTEXT} wrong.md {IMAGE_CAPABILITIES}\n"
        )
    with pytest.raises(CanonicalCapabilitiesError):
        validate_canonical_capabilities_final_writer(
            f"COPY --from={NAMED_CONTEXT} {CANONICAL_SRC} /tmp/capabilities.md\n"
        )


def test_later_overwrite_of_capabilities_fails() -> None:
    text = "\n".join(
        [
            f"COPY --from={NAMED_CONTEXT} {CANONICAL_SRC} {IMAGE_CAPABILITIES}",
            "COPY build_context/plugins/nextseek/ /app/plugins/nextseek/",
        ]
    )
    with pytest.raises(CanonicalCapabilitiesError, match="overwrite|final"):
        validate_canonical_capabilities_final_writer(text)


def test_compose_named_context_must_be_vendored_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vendored = repo / NAMED_CONTEXT
    vendored.mkdir(parents=True)
    validate_compose_named_context(
        repo_root=repo,
        contexts={NAMED_CONTEXT: "./chat_nextseek"},
    )
    with pytest.raises(ComposeContextError, match="missing"):
        validate_compose_named_context(repo_root=repo, contexts={})
    with pytest.raises(ComposeContextError, match="absolute|external"):
        validate_compose_named_context(
            repo_root=repo,
            contexts={NAMED_CONTEXT: "/tmp/chat_nextseek"},
        )
    with pytest.raises(ComposeContextError, match="absolute|external|traversal"):
        validate_compose_named_context(
            repo_root=repo,
            contexts={NAMED_CONTEXT: "../chat_nextseek"},
        )
    with pytest.raises(ComposeContextError, match="source|destination|resolve"):
        validate_compose_named_context(
            repo_root=repo,
            contexts={NAMED_CONTEXT: "./elsewhere"},
        )


def test_current_tree_four_sets_agree() -> None:
    plugins_root = REPO_ROOT / "docker/cc-runtime/build_context/plugins"
    dirs, copies, installed, paths = four_install_sets(
        plugins_root=plugins_root,
        dockerfile_path=DOCKERFILE,
    )
    assert dirs == copies == installed == paths
    assert dirs
    text = DOCKERFILE.read_text(encoding="utf-8")
    validate_canonical_capabilities_final_writer(text)
    assert "COPY build_context/plugins/ /app/plugins/" not in text


def test_dockerfile_and_compose_are_registered_surface_targets() -> None:
    rel_paths = [target.rel_path for target in surface_targets(REPO_ROOT)]
    assert DOCKERFILE_REL in rel_paths
    assert COMPOSE_REL in rel_paths


def test_real_compose_config_quiet_succeeds() -> None:
    if shutil_which_docker():
        proc = subprocess.run(COMPOSE_QUIET, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr or proc.stdout
        return
    compose = REPO_ROOT / COMPOSE_REL
    assert compose.is_file()
    validate_compose_named_context(
        repo_root=REPO_ROOT,
        contexts={NAMED_CONTEXT: "./chat_nextseek"},
    )


def test_real_compose_config_json_resolves_chat_nextseek_inside_repo() -> None:
    if shutil_which_docker():
        proc = subprocess.run(COMPOSE_JSON, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr or proc.stdout
        payload = json.loads(proc.stdout)
        build = payload["services"]["cc-agent"]["build"]
        contexts = build.get("additional_contexts") or build.get("additionalContexts")
        assert isinstance(contexts, dict), f"expected named context map, got {contexts!r}"
        assert NAMED_CONTEXT in contexts
        resolved = Path(contexts[NAMED_CONTEXT]).resolve()
        expected = (REPO_ROOT / NAMED_CONTEXT).resolve()
        assert resolved == expected
        assert resolved.is_dir()
        validate_compose_named_context(
            repo_root=REPO_ROOT,
            contexts={NAMED_CONTEXT: "./chat_nextseek"},
        )
        return
    expected = (REPO_ROOT / NAMED_CONTEXT).resolve()
    assert expected.is_dir()
    validate_compose_named_context(
        repo_root=REPO_ROOT,
        contexts={NAMED_CONTEXT: "./chat_nextseek"},
    )


def shutil_which_docker() -> bool:
    from shutil import which

    return which("docker") is not None and which("timeout") is not None


def test_gen_op_surfaces_check_includes_docker_surfaces() -> None:
    check_surfaces(repo_root=REPO_ROOT)

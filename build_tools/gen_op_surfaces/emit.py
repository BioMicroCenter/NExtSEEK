"""Surface target registry and generation/check orchestration."""
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from build_tools.gen_op_surfaces.blocks import render_marked_file
from build_tools.gen_op_surfaces.claude_md import (
    emit_claude_ops_block,
    emit_claude_plugins_block,
    emit_claude_skills_block,
    guard_claude_md_render,
)
from build_tools.gen_op_surfaces.commands import (
    discover_command_surface_paths,
    emit_command_ops_block,
)
from build_tools.gen_op_surfaces.constants import (
    ADDITIONAL_CONTEXTS_BEGIN,
    ADDITIONAL_CONTEXTS_END,
    BAKED_CAPABILITIES_REL,
    CANONICAL_CAPABILITIES_REL,
    CAPABILITIES_COPY_BEGIN,
    CAPABILITIES_COPY_END,
    CLAUDE_MD_REL,
    ROUTE_CAPABILITIES_REL,
    CLAUDE_OPS_BEGIN,
    CLAUDE_OPS_END,
    CLAUDE_PLUGINS_BEGIN,
    CLAUDE_PLUGINS_END,
    CLAUDE_SKILLS_BEGIN,
    CLAUDE_SKILLS_END,
    COMMAND_OPS_BEGIN,
    COMMAND_OPS_END,
    COMPOSE_REL,
    DOCKERFILE_REL,
    EXIT_CHANGES_WRITTEN,
    EXIT_NO_CHANGE,
    NEXTSEEK_DOCS_BEGIN,
    PLUGIN_COPY_BEGIN,
    PLUGIN_COPY_END,
    PLUGIN_PATH_BEGIN,
    PLUGIN_PATH_END,
    SKILL_OPS_BEGIN,
    SKILL_OPS_END,
)
from build_tools.gen_op_surfaces.docker_blocks import (
    emit_additional_contexts_block,
    emit_capabilities_copy_block,
    emit_plugin_copy_block,
    emit_plugin_path_block,
)
from build_tools.gen_op_surfaces.paths import resolve_under_root
from build_tools.gen_op_surfaces.route_capabilities import emit_route_capabilities
from build_tools.gen_op_surfaces.skills import (
    discover_skill_surface_paths,
    emit_skill_ops_block,
)


@dataclass(frozen=True, order=True)
class SurfaceTarget:
    rel_path: str
    kind: Literal["whole_file", "marked_block"]
    emit: Callable[[Path], bytes | str]
    begin_marker: str | None = None
    end_marker: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "marked_block":
            if not self.begin_marker or not self.end_marker:
                raise ValueError(
                    f"marked_block target {self.rel_path} requires begin/end markers"
                )


def capabilities_bytes(repo_root: Path) -> bytes:
    """Return canonical capabilities.md bytes without parsing or rewriting."""
    canonical = resolve_under_root(repo_root, CANONICAL_CAPABILITIES_REL)
    if not canonical.is_file():
        raise SystemExit(
            f"gen_op_surfaces failed: missing canonical capabilities at {canonical}"
        )
    return canonical.read_bytes()


def _command_surface_targets(repo_root: Path) -> tuple[SurfaceTarget, ...]:
    command_targets: list[SurfaceTarget] = []
    for plugin_dir, rel_path in discover_command_surface_paths(repo_root):
        command_targets.append(
            SurfaceTarget(
                rel_path=rel_path,
                kind="marked_block",
                begin_marker=COMMAND_OPS_BEGIN,
                end_marker=COMMAND_OPS_END,
                emit=lambda root, pd=plugin_dir: emit_command_ops_block(
                    root,
                    plugin_dir=pd,
                ),
            )
        )
    return tuple(command_targets)


def _skill_surface_targets(repo_root: Path) -> tuple[SurfaceTarget, ...]:
    skill_targets: list[SurfaceTarget] = []
    for skill_name, rel_path in discover_skill_surface_paths(repo_root):
        skill_targets.append(
            SurfaceTarget(
                rel_path=rel_path,
                kind="marked_block",
                begin_marker=SKILL_OPS_BEGIN,
                end_marker=SKILL_OPS_END,
                emit=lambda root, name=skill_name: emit_skill_ops_block(
                    root,
                    skill_name=name,
                ),
            )
        )
    return tuple(skill_targets)


def _docker_surface_targets(repo_root: Path) -> tuple[SurfaceTarget, ...]:
    targets: list[SurfaceTarget] = []
    dockerfile = repo_root / DOCKERFILE_REL
    if dockerfile.is_file():
        text = dockerfile.read_text(encoding="utf-8")
        if PLUGIN_COPY_BEGIN in text and PLUGIN_COPY_END in text:
            targets.append(
                SurfaceTarget(
                    rel_path=DOCKERFILE_REL,
                    kind="marked_block",
                    begin_marker=PLUGIN_COPY_BEGIN,
                    end_marker=PLUGIN_COPY_END,
                    emit=emit_plugin_copy_block,
                )
            )
        if CAPABILITIES_COPY_BEGIN in text and CAPABILITIES_COPY_END in text:
            targets.append(
                SurfaceTarget(
                    rel_path=DOCKERFILE_REL,
                    kind="marked_block",
                    begin_marker=CAPABILITIES_COPY_BEGIN,
                    end_marker=CAPABILITIES_COPY_END,
                    emit=emit_capabilities_copy_block,
                )
            )
        if PLUGIN_PATH_BEGIN in text and PLUGIN_PATH_END in text:
            targets.append(
                SurfaceTarget(
                    rel_path=DOCKERFILE_REL,
                    kind="marked_block",
                    begin_marker=PLUGIN_PATH_BEGIN,
                    end_marker=PLUGIN_PATH_END,
                    emit=emit_plugin_path_block,
                )
            )
    compose = repo_root / COMPOSE_REL
    if compose.is_file():
        text = compose.read_text(encoding="utf-8")
        if ADDITIONAL_CONTEXTS_BEGIN in text and ADDITIONAL_CONTEXTS_END in text:
            targets.append(
                SurfaceTarget(
                    rel_path=COMPOSE_REL,
                    kind="marked_block",
                    begin_marker=ADDITIONAL_CONTEXTS_BEGIN,
                    end_marker=ADDITIONAL_CONTEXTS_END,
                    emit=emit_additional_contexts_block,
                )
            )
    return tuple(targets)


def _claude_md_surface_targets(repo_root: Path) -> tuple[SurfaceTarget, ...]:
    path = repo_root / CLAUDE_MD_REL
    if not path.is_file():
        return tuple()
    text = path.read_text(encoding="utf-8")
    targets: list[SurfaceTarget] = []
    if CLAUDE_PLUGINS_BEGIN in text and CLAUDE_PLUGINS_END in text:
        targets.append(
            SurfaceTarget(
                rel_path=CLAUDE_MD_REL,
                kind="marked_block",
                begin_marker=CLAUDE_PLUGINS_BEGIN,
                end_marker=CLAUDE_PLUGINS_END,
                emit=emit_claude_plugins_block,
            )
        )
    if CLAUDE_SKILLS_BEGIN in text and CLAUDE_SKILLS_END in text:
        targets.append(
            SurfaceTarget(
                rel_path=CLAUDE_MD_REL,
                kind="marked_block",
                begin_marker=CLAUDE_SKILLS_BEGIN,
                end_marker=CLAUDE_SKILLS_END,
                emit=emit_claude_skills_block,
            )
        )
    if CLAUDE_OPS_BEGIN in text and CLAUDE_OPS_END in text:
        targets.append(
            SurfaceTarget(
                rel_path=CLAUDE_MD_REL,
                kind="marked_block",
                begin_marker=CLAUDE_OPS_BEGIN,
                end_marker=CLAUDE_OPS_END,
                emit=emit_claude_ops_block,
            )
        )
    return tuple(targets)


def surface_targets(repo_root: Path) -> tuple[SurfaceTarget, ...]:
    """Return declared generated targets in stable sorted order."""
    targets = (
        SurfaceTarget(
            rel_path=BAKED_CAPABILITIES_REL,
            kind="whole_file",
            emit=capabilities_bytes,
        ),
        SurfaceTarget(
            rel_path=ROUTE_CAPABILITIES_REL,
            kind="whole_file",
            emit=emit_route_capabilities,
        ),
        *_command_surface_targets(repo_root),
        *_skill_surface_targets(repo_root),
        *_docker_surface_targets(repo_root),
        *_claude_md_surface_targets(repo_root),
    )
    return tuple(sorted(targets, key=lambda item: item.rel_path))


def _render_target_bytes(target: SurfaceTarget, repo_root: Path) -> bytes:
    emitted = target.emit(repo_root)
    if target.kind == "whole_file":
        if isinstance(emitted, str):
            return emitted.encode("utf-8")
        return emitted

    path = resolve_under_root(repo_root, target.rel_path)
    if not path.is_file():
        raise SystemExit(f"gen_op_surfaces failed: missing marked target {path}")
    original = path.read_text(encoding="utf-8")
    block = emitted if isinstance(emitted, str) else emitted.decode("utf-8")
    updated = render_marked_file(
        original,
        target.begin_marker or "",
        target.end_marker or "",
        block,
    )
    if target.rel_path == CLAUDE_MD_REL and NEXTSEEK_DOCS_BEGIN in original:
        guard_claude_md_render(original=original, updated=updated)
    return updated.encode("utf-8")


def check_surfaces(
    *,
    repo_root: Path,
    targets: Sequence[SurfaceTarget] | None = None,
    tmp_dir: Path | None = None,
) -> None:
    """Render every target under a temp directory and byte-compare committed files."""
    ordered = tuple(targets) if targets is not None else surface_targets(repo_root)
    prefix = "gen-op-surfaces-check-"
    if tmp_dir is None:
        temp_parent = os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp"
        cm = tempfile.TemporaryDirectory(prefix=prefix, dir=temp_parent)
    else:
        cm = tempfile.TemporaryDirectory(prefix=prefix, dir=str(tmp_dir))

    with cm as tmp:
        tmp_root = Path(tmp)
        for target in ordered:
            expected = _render_target_bytes(target, repo_root)
            temp_path = tmp_root / target.rel_path
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_bytes(expected)
            if temp_path.read_bytes() != expected:
                raise SystemExit(
                    f"gen_op_surfaces check failed: temp render mismatch at {temp_path}"
                )

            committed = resolve_under_root(repo_root, target.rel_path)
            if not committed.is_file():
                raise SystemExit(f"gen_op_surfaces check failed: missing {committed}")
            actual = committed.read_bytes()
            if actual != expected:
                raise SystemExit(
                    f"gen_op_surfaces check failed: stale bytes at {committed}"
                )


def write_surfaces(
    *,
    repo_root: Path,
    targets: Sequence[SurfaceTarget] | None = None,
) -> int:
    """Write generated targets; return exit code semantics for CLI."""
    ordered = tuple(targets) if targets is not None else surface_targets(repo_root)
    changed = False
    for target in ordered:
        expected = _render_target_bytes(target, repo_root)
        path = resolve_under_root(repo_root, target.rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.read_bytes() if path.is_file() else b""
        if current != expected:
            path.write_bytes(expected)
            changed = True
    return EXIT_CHANGES_WRITTEN if changed else EXIT_NO_CHANGE

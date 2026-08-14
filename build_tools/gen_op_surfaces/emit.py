"""Surface target registry and generation/check orchestration."""
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from build_tools.gen_op_surfaces.blocks import render_marked_file
from build_tools.gen_op_surfaces.constants import (
    BAKED_CAPABILITIES_REL,
    CANONICAL_CAPABILITIES_REL,
    EXIT_CHANGES_WRITTEN,
    EXIT_NO_CHANGE,
)
from build_tools.gen_op_surfaces.paths import resolve_under_root


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


def surface_targets(repo_root: Path) -> tuple[SurfaceTarget, ...]:
    """Return declared generated targets in stable sorted order."""
    del repo_root
    targets = (
        SurfaceTarget(
            rel_path=BAKED_CAPABILITIES_REL,
            kind="whole_file",
            emit=capabilities_bytes,
        ),
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

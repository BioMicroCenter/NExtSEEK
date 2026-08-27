"""Generated container CLAUDE.md plugin, skill, and operation inventories."""
from __future__ import annotations

from pathlib import Path

from build_tools.gen_op_surfaces.constants import (
    CLAUDE_OPS_BEGIN,
    CLAUDE_OPS_END,
    CLAUDE_PLUGINS_BEGIN,
    CLAUDE_PLUGINS_END,
    CLAUDE_SKILLS_BEGIN,
    CLAUDE_SKILLS_END,
    NEXTSEEK_DOCS_BEGIN,
    NEXTSEEK_DOCS_END,
)
from nextseek_api.cc_assistant.op_registry.install_oracle import (
    InstallDiscovery,
    discover_install,
)
from nextseek_api.cc_assistant.op_registry.models import OpSpec
from nextseek_api.cc_assistant.op_registry.ops import OPS

_PLUGINS_ROOT_REL = Path("docker/cc-runtime/build_context/plugins")
_DOCKERFILE_REL = Path("docker/cc-runtime/Dockerfile")


class ClaudeMdDocsError(ValueError):
    """Raised when Plan005 markers collide with the unique NEXTSEEK-DOCS block."""


def extract_nextseek_docs_block(text: str) -> str:
    """Return the unique NEXTSEEK-DOCS block, including both markers."""
    begin_count = text.count(NEXTSEEK_DOCS_BEGIN)
    end_count = text.count(NEXTSEEK_DOCS_END)
    if begin_count != 1 or end_count != 1:
        raise ClaudeMdDocsError(
            "NEXTSEEK-DOCS markers must occur exactly once: "
            f"BEGIN count={begin_count}, END count={end_count}"
        )
    begin_idx = text.index(NEXTSEEK_DOCS_BEGIN)
    end_idx = text.index(NEXTSEEK_DOCS_END)
    if begin_idx >= end_idx:
        raise ClaudeMdDocsError("inverted NEXTSEEK-DOCS markers")
    return text[begin_idx : end_idx + len(NEXTSEEK_DOCS_END)]


def validate_plan005_markers_outside_docs(text: str) -> None:
    """Fail if any PLAN005-GEN marker sits inside the unique NEXTSEEK-DOCS block."""
    block = extract_nextseek_docs_block(text)
    if "PLAN005-GEN" in block:
        raise ClaudeMdDocsError(
            "Plan005 inventory markers must stay outside NEXTSEEK-DOCS"
        )


def guard_claude_md_render(*, original: str, updated: str) -> None:
    """Keep inventory markers outside docs and leave the docs block untouched."""
    validate_plan005_markers_outside_docs(original)
    validate_plan005_markers_outside_docs(updated)
    if extract_nextseek_docs_block(original) != extract_nextseek_docs_block(updated):
        raise ClaudeMdDocsError(
            "NEXTSEEK-DOCS block must remain byte-identical during inventory generation"
        )


def installed_claude_plugins(discovery: InstallDiscovery) -> frozenset[str]:
    return frozenset(discovery.plugins)


def installed_claude_skills(
    discovery: InstallDiscovery,
) -> frozenset[tuple[str, str]]:
    return frozenset(
        (skill.plugin_dir, skill.skill_name) for skill in discovery.skills
    )


def installed_claude_ops(
    discovery: InstallDiscovery,
    ops: list[OpSpec],
) -> frozenset[tuple[str, str, str]]:
    shims = {shim.shim_name for shim in discovery.shims}
    rows = [
        (
            op.bin_name,
            op.op_id,
            op.skill_row.purpose.strip() if op.skill_row is not None else "",
        )
        for op in ops
        if op.available and op.bin_name in shims
    ]
    if len(rows) != len(set(rows)):
        raise ValueError("duplicate Claude.md operation inventory rows")
    return frozenset(rows)


def _parse_tsv_block(text: str, begin: str, end: str, width: int) -> frozenset[tuple[str, ...]]:
    begin_idx = text.find(begin)
    end_idx = text.find(end)
    if begin_idx == -1 or end_idx == -1 or end_idx <= begin_idx:
        return frozenset()
    block = text[begin_idx + len(begin) : end_idx]
    rows: set[tuple[str, ...]] = set()
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = tuple(part.strip() for part in stripped.split("\t"))
        if len(parts) != width:
            continue
        rows.add(parts)
    return frozenset(rows)


def parse_claude_plugins_block(text: str) -> frozenset[str]:
    rows = _parse_tsv_block(text, CLAUDE_PLUGINS_BEGIN, CLAUDE_PLUGINS_END, 1)
    return frozenset(row[0] for row in rows)


def parse_claude_skills_block(text: str) -> frozenset[tuple[str, str]]:
    rows = _parse_tsv_block(text, CLAUDE_SKILLS_BEGIN, CLAUDE_SKILLS_END, 2)
    return frozenset((row[0], row[1]) for row in rows)


def parse_claude_ops_block(text: str) -> frozenset[tuple[str, str, str]]:
    rows = _parse_tsv_block(text, CLAUDE_OPS_BEGIN, CLAUDE_OPS_END, 3)
    return frozenset((row[0], row[1], row[2]) for row in rows)


def _discover(
    repo_root: Path,
    *,
    plugins_root: Path | None,
    dockerfile_path: Path | None,
) -> InstallDiscovery:
    plugins_root = plugins_root or (repo_root / _PLUGINS_ROOT_REL)
    dockerfile_path = dockerfile_path or (repo_root / _DOCKERFILE_REL)
    return discover_install(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile_path,
    )


def emit_claude_plugins_block(
    repo_root: Path,
    *,
    plugins_root: Path | None = None,
    dockerfile_path: Path | None = None,
) -> str:
    discovery = _discover(
        repo_root, plugins_root=plugins_root, dockerfile_path=dockerfile_path
    )
    names = sorted(installed_claude_plugins(discovery))
    if not names:
        return ""
    return "\n".join(names) + "\n"


def emit_claude_skills_block(
    repo_root: Path,
    *,
    plugins_root: Path | None = None,
    dockerfile_path: Path | None = None,
) -> str:
    discovery = _discover(
        repo_root, plugins_root=plugins_root, dockerfile_path=dockerfile_path
    )
    rows = sorted(installed_claude_skills(discovery))
    if not rows:
        return ""
    return "\n".join(f"{plugin}\t{skill}" for plugin, skill in rows) + "\n"


def emit_claude_ops_block(
    repo_root: Path,
    *,
    ops: list[OpSpec] | None = None,
    plugins_root: Path | None = None,
    dockerfile_path: Path | None = None,
) -> str:
    discovery = _discover(
        repo_root, plugins_root=plugins_root, dockerfile_path=dockerfile_path
    )
    source_ops = OPS if ops is None else ops
    rows = sorted(installed_claude_ops(discovery, source_ops), key=lambda row: row[0])
    if not rows:
        return ""
    return "\n".join(f"{bin_name}\t{op_id}\t{purpose}" for bin_name, op_id, purpose in rows) + "\n"

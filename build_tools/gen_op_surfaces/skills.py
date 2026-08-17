"""Generated SKILL.md capability matrices from OpSpec and install oracle."""
from __future__ import annotations

from pathlib import Path

from build_tools.gen_op_surfaces.constants import (
    SKILL_OPS_BEGIN,
    SKILL_OPS_END,
    SKILL_OPS_FIELDS,
)
from nextseek_api.cc_assistant.op_registry.install_oracle import discover_install
from nextseek_api.cc_assistant.op_registry.models import GateClass, OpSpec, Transport
from nextseek_api.cc_assistant.op_registry.ops import OPS

_PLUGINS_ROOT_REL = Path("docker/cc-runtime/build_context/plugins")
_DOCKERFILE_REL = Path("docker/cc-runtime/Dockerfile")
_TRUE = "true"
_FALSE = "false"


class SkillOpsParseError(ValueError):
    """Raised when a generated skill-ops block is not a strict field matrix."""


def _parse_bool(value: str) -> bool:
    if value == _TRUE:
        return True
    if value == _FALSE:
        return False
    raise SkillOpsParseError(f"invalid boolean {value!r}; expected true or false")


def _normalize_row(parts: list[str]) -> tuple[str, str, str, str, str, bool, bool]:
    if len(parts) != len(SKILL_OPS_FIELDS):
        raise SkillOpsParseError(
            f"skill-ops row must have {len(SKILL_OPS_FIELDS)} fields, got {len(parts)}"
        )
    op_id, bin_name, purpose, transport, gate_class, availability, per_op_gate = (
        part.strip() for part in parts
    )
    try:
        transport_value = Transport(transport).value
        gate_value = GateClass(gate_class).value
    except ValueError as exc:
        raise SkillOpsParseError(f"invalid enum in skill-ops row: {exc}") from exc
    return (
        op_id,
        bin_name,
        purpose,
        transport_value,
        gate_value,
        _parse_bool(availability),
        _parse_bool(per_op_gate),
    )


def _row_from_op(op: OpSpec) -> tuple[str, str, str, str, str, bool, bool]:
    purpose = op.skill_row.purpose.strip() if op.skill_row is not None else ""
    return (
        op.op_id.strip(),
        op.bin_name.strip(),
        purpose,
        op.transport.value,
        op.gate_class.value,
        op.available,
        op.per_op_gate_enabled,
    )


def installed_skill_ops_rows(
    ops: list[OpSpec],
    *,
    skill_name: str,
) -> frozenset[tuple[str, str, str, str, str, bool, bool]]:
    """Independently project OpSpec rows that belong to one discovered skill."""
    rows = [_row_from_op(op) for op in ops if op.skill_name == skill_name]
    if len(rows) != len(set(rows)):
        raise SkillOpsParseError(f"duplicate OpSpec rows for skill {skill_name!r}")
    return frozenset(rows)


def parse_skill_ops_block(
    text: str,
) -> frozenset[tuple[str, str, str, str, str, bool, bool]]:
    """Parse generated skill-ops rows after schema-declared whitespace strip."""
    begin = text.find(SKILL_OPS_BEGIN)
    end = text.find(SKILL_OPS_END)
    if begin == -1 or end == -1 or end <= begin:
        raise SkillOpsParseError("missing or inverted skill-ops markers")
    block = text[begin + len(SKILL_OPS_BEGIN) : end]
    rows: list[tuple[str, str, str, str, str, bool, bool]] = []
    seen: set[tuple[str, str, str, str, str, bool, bool]] = set()
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        row = _normalize_row(stripped.split("\t"))
        if row in seen:
            raise SkillOpsParseError(f"duplicate skill-ops row: {row[0]}")
        seen.add(row)
        rows.append(row)
    return frozenset(rows)


def emit_skill_ops_block(
    repo_root: Path,
    *,
    skill_name: str,
    ops: list[OpSpec] | None = None,
    plugins_root: Path | None = None,
    dockerfile_path: Path | None = None,
) -> str:
    """Render tab-separated capability rows for one installed skill."""
    plugins_root = plugins_root or (repo_root / _PLUGINS_ROOT_REL)
    dockerfile_path = dockerfile_path or (repo_root / _DOCKERFILE_REL)
    source_ops = OPS if ops is None else ops
    discovery = discover_install(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile_path,
    )
    discovered = {skill.skill_name for skill in discovery.skills}
    if skill_name not in discovered:
        return ""
    lines: list[str] = []
    for op in sorted(source_ops, key=lambda item: item.op_id):
        if op.skill_name != skill_name:
            continue
        row = _row_from_op(op)
        lines.append(
            "\t".join(
                [
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    _TRUE if row[5] else _FALSE,
                    _TRUE if row[6] else _FALSE,
                ]
            )
        )
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def discover_skill_surface_paths(
    repo_root: Path,
    *,
    plugins_root: Path | None = None,
    dockerfile_path: Path | None = None,
) -> tuple[tuple[str, str], ...]:
    """Return (skill_name, rel_path) for every install-oracle SKILL.md."""
    plugins_root = plugins_root or (repo_root / _PLUGINS_ROOT_REL)
    dockerfile_path = dockerfile_path or (repo_root / _DOCKERFILE_REL)
    if not plugins_root.is_dir() or not dockerfile_path.is_file():
        return tuple()
    discovery = discover_install(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile_path,
    )
    pairs: list[tuple[str, str]] = []
    repo_resolved = repo_root.resolve()
    for skill in discovery.skills:
        rel = skill.skill_path.resolve().relative_to(repo_resolved).as_posix()
        pairs.append((skill.skill_name, rel))
    return tuple(sorted(pairs, key=lambda item: item[1]))

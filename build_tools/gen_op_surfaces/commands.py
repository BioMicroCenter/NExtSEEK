"""Generated command-doc surfaces from OPS and install oracle membership."""
from __future__ import annotations

from pathlib import Path

from build_tools.gen_op_surfaces.constants import COMMAND_OPS_BEGIN, COMMAND_OPS_END
from nextseek_api.cc_assistant.op_registry.install_oracle import discover_install
from nextseek_api.cc_assistant.op_registry.models import OpSpec
from nextseek_api.cc_assistant.op_registry.ops import OPS

_PLUGINS_ROOT_REL = Path("docker/cc-runtime/build_context/plugins")
_DOCKERFILE_REL = Path("docker/cc-runtime/Dockerfile")


def parse_command_ops_block(text: str) -> frozenset[tuple[str, str, str]]:
    """Parse generated command-op rows as (bin_name, op_id, transport)."""
    begin = text.find(COMMAND_OPS_BEGIN)
    end = text.find(COMMAND_OPS_END)
    if begin == -1 or end == -1 or end <= begin:
        return frozenset()
    block = text[begin + len(COMMAND_OPS_BEGIN) : end]
    rows: set[tuple[str, str, str]] = set()
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if len(parts) != 3:
            continue
        rows.add((parts[0], parts[1], parts[2]))
    return frozenset(rows)


def emit_command_ops_block(
    repo_root: Path,
    *,
    plugin_dir: str,
    ops: list[OpSpec] | None = None,
    plugins_root: Path | None = None,
    dockerfile_path: Path | None = None,
) -> str:
    """Render tab-separated op inventory rows for one installed plugin."""
    plugins_root = plugins_root or (repo_root / _PLUGINS_ROOT_REL)
    dockerfile_path = dockerfile_path or (repo_root / _DOCKERFILE_REL)
    source_ops = OPS if ops is None else ops
    discovery = discover_install(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile_path,
    )
    shim_names = {
        shim.shim_name
        for shim in discovery.shims
        if shim.plugin_dir == plugin_dir
    }
    lines: list[str] = []
    for op in sorted(source_ops, key=lambda item: item.bin_name):
        if not op.available:
            continue
        if op.bin_name not in shim_names:
            continue
        lines.append(f"{op.bin_name}\t{op.op_id}\t{op.transport.value}")
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def discover_command_surface_paths(
    repo_root: Path,
    *,
    plugins_root: Path | None = None,
    dockerfile_path: Path | None = None,
) -> tuple[tuple[str, str], ...]:
    """Return (plugin_dir, rel_path) pairs whose command docs contain Plan005 markers."""
    del dockerfile_path
    plugins_root = plugins_root or (repo_root / _PLUGINS_ROOT_REL)
    if not plugins_root.is_dir():
        return tuple()
    pairs: list[tuple[str, str]] = []
    for plugin_dir in sorted(p for p in plugins_root.iterdir() if p.is_dir()):
        commands_dir = plugin_dir / "commands"
        if not commands_dir.is_dir():
            continue
        for command_path in sorted(commands_dir.glob("*.md")):
            text = command_path.read_text(encoding="utf-8")
            if COMMAND_OPS_BEGIN in text and COMMAND_OPS_END in text:
                rel = command_path.relative_to(repo_root).as_posix()
                pairs.append((plugin_dir.name, rel))
    return tuple(sorted(pairs, key=lambda item: item[1]))

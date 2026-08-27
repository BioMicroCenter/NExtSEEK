"""Unit tests for generated command docs (Plan 005 Task 7)."""
from __future__ import annotations

import stat
from pathlib import Path

import pytest

from build_tools.gen_op_surfaces.commands import (
    emit_command_ops_block,
    parse_command_ops_block,
)
from build_tools.gen_op_surfaces.constants import COMMAND_OPS_BEGIN, COMMAND_OPS_END
from build_tools.gen_op_surfaces.emit import (
    SurfaceTarget,
    check_surfaces,
    surface_targets,
    write_surfaces,
)
from nextseek_api.cc_assistant.op_registry.models import GateClass, OpSpec, Transport
from nextseek_api.cc_assistant.op_registry.ops import OPS

REPO_ROOT = Path(__file__).resolve().parents[3]


def _fixture_op(op_id: str, bin_name: str) -> OpSpec:
    return OpSpec(
        op_id=op_id,
        bin_name=bin_name,
        runner_key=op_id,
        runner="bin/_nextseek_runner.py",
        transport=Transport.sidecar,
        assistant_endpoint=f"/nextseek_api/assistant/{op_id}/",
        gate_class=GateClass.read,
    )


def test_parse_command_ops_block_returns_exact_set():
    text = (
        "prefix\n"
        f"{COMMAND_OPS_BEGIN}\n"
        "nextseek-alpha\talpha\tsidecar\n"
        "nextseek-beta\tbeta\tviewset\n"
        f"{COMMAND_OPS_END}\n"
        "suffix\n"
    )
    assert parse_command_ops_block(text) == {
        ("nextseek-alpha", "alpha", "sidecar"),
        ("nextseek-beta", "beta", "viewset"),
    }


def test_emit_command_ops_block_changes_exactly_on_add_remove(tmp_path: Path):
    repo = tmp_path / "repo"
    plugins_root = repo / "docker/cc-runtime/build_context/plugins"
    dockerfile = repo / "docker/cc-runtime/Dockerfile"
    plugin_dir = plugins_root / "alpha-plugin"
    plugin_dir.mkdir(parents=True)
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    manifest_dir.joinpath("plugin.json").write_text(
        '{"name":"alpha-plugin","version":"0.1.0","description":"Claude Code plugin for NExtSEEK research workflows.","author":{"name":"BMC"}}',
        encoding="utf-8",
    )
    shim_dir = plugin_dir / "bin"
    shim_dir.mkdir()
    for name in ("nextseek-alpha-op", "nextseek-beta-op", "nextseek-gamma-op"):
        path = shim_dir / name
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(
        "\n".join(
            [
                "FROM scratch",
                "COPY build_context/plugins/alpha-plugin/ /app/plugins/alpha-plugin/",
                'ENV PATH="/app/plugins/alpha-plugin/bin:${PATH}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    base_ops = [
        _fixture_op("alpha", "nextseek-alpha-op"),
        _fixture_op("beta", "nextseek-beta-op"),
    ]
    base_block = emit_command_ops_block(
        repo,
        plugin_dir="alpha-plugin",
        ops=base_ops,
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )
    base_set = parse_command_ops_block(
        f"{COMMAND_OPS_BEGIN}\n{base_block}{COMMAND_OPS_END}\n"
    )

    added_ops = [
        *base_ops,
        _fixture_op("gamma", "nextseek-gamma-op"),
    ]
    added_block = emit_command_ops_block(
        repo,
        plugin_dir="alpha-plugin",
        ops=added_ops,
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )
    added_set = parse_command_ops_block(
        f"{COMMAND_OPS_BEGIN}\n{added_block}{COMMAND_OPS_END}\n"
    )
    assert added_set - base_set == {("nextseek-gamma-op", "gamma", "sidecar")}

    removed_ops = [base_ops[0]]
    removed_block = emit_command_ops_block(
        repo,
        plugin_dir="alpha-plugin",
        ops=removed_ops,
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )
    removed_set = parse_command_ops_block(
        f"{COMMAND_OPS_BEGIN}\n{removed_block}{COMMAND_OPS_END}\n"
    )
    assert base_set - removed_set == {("nextseek-beta-op", "beta", "sidecar")}


def test_command_surface_is_registered_for_nextseek_command():
    rel_paths = [target.rel_path for target in surface_targets(REPO_ROOT)]
    assert (
        "docker/cc-runtime/build_context/plugins/nextseek/commands/nextseek.md"
        in rel_paths
    )


def test_gen_op_surfaces_check_passes_with_generated_command_block():
    check_surfaces(repo_root=REPO_ROOT)


def test_ops_and_export_do_not_load_plugin_json_for_discovery():
    repo_root = REPO_ROOT
    ops_source = (repo_root / "nextseek_api/cc_assistant/op_registry/ops.py").read_text(
        encoding="utf-8"
    )
    export_source = (
        repo_root / "nextseek_api/cc_assistant/op_registry/export.py"
    ).read_text(encoding="utf-8")
    assert "plugin.json" not in ops_source
    assert "plugin.json" not in export_source


def test_write_surfaces_updates_command_block_once(tmp_path: Path):
    repo = tmp_path / "repo"
    rel = "docker/cc-runtime/build_context/plugins/alpha-plugin/commands/nextseek.md"
    command_path = repo / rel
    command_path.parent.mkdir(parents=True)
    command_path.write_text(
        "\n".join(
            [
                "---",
                "description: test",
                "---",
                "",
                f"{COMMAND_OPS_BEGIN}",
                "stale-row",
                f"{COMMAND_OPS_END}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    plugins_root = repo / "docker/cc-runtime/build_context/plugins"
    plugin_dir = plugins_root / "alpha-plugin"
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    manifest_dir.joinpath("plugin.json").write_text(
        '{"name":"alpha-plugin","version":"0.1.0","description":"Claude Code plugin for NExtSEEK research workflows.","author":{"name":"BMC"}}',
        encoding="utf-8",
    )
    shim = plugin_dir / "bin/nextseek-alpha-op"
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\n", encoding="utf-8")
    shim.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    dockerfile = repo / "docker/cc-runtime/Dockerfile"
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(
        "\n".join(
            [
                "FROM scratch",
                "COPY build_context/plugins/alpha-plugin/ /app/plugins/alpha-plugin/",
                'ENV PATH="/app/plugins/alpha-plugin/bin:${PATH}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    targets = [
        SurfaceTarget(
            rel_path=rel,
            kind="marked_block",
            begin_marker=COMMAND_OPS_BEGIN,
            end_marker=COMMAND_OPS_END,
            emit=lambda root: emit_command_ops_block(
                root,
                plugin_dir="alpha-plugin",
                ops=[_fixture_op("alpha", "nextseek-alpha-op")],
                plugins_root=plugins_root,
                dockerfile_path=dockerfile,
            ),
        )
    ]
    write_surfaces(repo_root=repo, targets=tuple(targets))
    updated = command_path.read_text(encoding="utf-8")
    parsed = parse_command_ops_block(updated)
    assert parsed == {("nextseek-alpha-op", "alpha", "sidecar")}
    assert "stale-row" not in updated

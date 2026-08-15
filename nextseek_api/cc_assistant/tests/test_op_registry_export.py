"""Independent export oracle for Plan 005 Task 3 (canonical ops.json)."""
from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from nextseek_api.cc_assistant.op_registry import OPS, OpList, OpSpec, discover_install
from nextseek_api.cc_assistant.op_registry.export import (
    BAKED_OPS_RELATIVE,
    CANONICAL_OPS_PATH,
    check_export,
    export_target_paths,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PLUGINS_ROOT = REPO_ROOT / "docker" / "cc-runtime" / "build_context" / "plugins"
DEFAULT_DOCKERFILE = REPO_ROOT / "docker" / "cc-runtime" / "Dockerfile"
EXPORT_MODULE = "nextseek_api.cc_assistant.op_registry.export"

SHIM_PREFIX = "nextseek-"


def _independent_canonical_bytes(ops: list[OpSpec]) -> bytes:
    """Test-side serializer: strict OpList dumps + sorted JSON, no export imports."""
    payload = OpList.dump_python(ops, mode="json")
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_manifest(plugin_dir: Path, *, name: str | None = None) -> None:
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name if name is not None else plugin_dir.name,
        "version": "0.0.1",
        "description": "synthetic plugin for export tests",
    }
    (manifest_dir / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_shim(plugin_dir: Path, shim_name: str) -> Path:
    shim_path = plugin_dir / "bin" / shim_name
    shim_path.parent.mkdir(parents=True, exist_ok=True)
    shim_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shim_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return shim_path


def _write_plugin_tree(plugins_root: Path, plugin_name: str) -> Path:
    plugin_dir = plugins_root / plugin_name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(plugin_dir)
    _write_shim(plugin_dir, f"{SHIM_PREFIX}alpha-op")
    return plugin_dir


def _write_dockerfile(path: Path, *, copy_plugins: tuple[str, ...]) -> None:
    lines = ["FROM scratch"]
    for plugin in copy_plugins:
        lines.append(
            f"COPY build_context/plugins/{plugin}/ /app/plugins/{plugin}/"
        )
        lines.append(f'ENV PATH="/app/plugins/{plugin}/bin:${{PATH}}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_independent_serializer_sha_matches_canonical_and_baked_copies():
    expected = _independent_canonical_bytes(OPS)
    digest = _sha256(expected)
    assert CANONICAL_OPS_PATH.is_file(), "canonical ops.json must be committed"
    assert _sha256(CANONICAL_OPS_PATH.read_bytes()) == digest
    for baked_path in export_target_paths(
        plugins_root=DEFAULT_PLUGINS_ROOT,
        dockerfile_path=DEFAULT_DOCKERFILE,
    ):
        if baked_path == CANONICAL_OPS_PATH:
            continue
        assert baked_path.is_file(), f"missing baked copy: {baked_path}"
        assert _sha256(baked_path.read_bytes()) == digest


def test_export_check_cli_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", EXPORT_MODULE, "--check"],
        cwd=REPO_ROOT,
        env={
            **dict(__import__("os").environ),
            "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'dmac_assistant' / 'src'}",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_export_contains_runner_key_and_per_op_gate_enabled_not_locked_query():
    payload = json.loads(_independent_canonical_bytes(OPS).decode("utf-8"))
    assert isinstance(payload, list)
    assert payload, "OPS export must not be empty"
    for row in payload:
        assert "runner_key" in row
        assert isinstance(row["runner_key"], str) and row["runner_key"]
        assert "per_op_gate_enabled" in row
        assert isinstance(row["per_op_gate_enabled"], bool)
    raw = _independent_canonical_bytes(OPS)
    assert b"LockedQuery" not in raw


def test_missing_baked_copy_fails_check(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    _write_plugin_tree(plugins_root, "export-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("export-plugin",))
    canonical = tmp_path / "canonical" / "ops.json"
    expected = _independent_canonical_bytes(OPS)
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(expected)
    with pytest.raises(SystemExit):
        check_export(
            canonical_path=canonical,
            plugins_root=plugins_root,
            dockerfile_path=dockerfile,
        )


def test_stale_canonical_bytes_fail_check(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    plugin_dir = _write_plugin_tree(plugins_root, "stale-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("stale-plugin",))
    baked = plugin_dir / BAKED_OPS_RELATIVE
    expected = _independent_canonical_bytes(OPS)
    baked.parent.mkdir(parents=True, exist_ok=True)
    baked.write_bytes(expected)
    canonical = tmp_path / "canonical" / "ops.json"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b'[{"op_id":"stale"}]\n')
    with pytest.raises(SystemExit):
        check_export(
            canonical_path=canonical,
            plugins_root=plugins_root,
            dockerfile_path=dockerfile,
        )


def test_duplicate_row_in_canonical_fails_check(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    plugin_dir = _write_plugin_tree(plugins_root, "dup-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("dup-plugin",))
    canonical = tmp_path / "canonical" / "ops.json"
    expected = _independent_canonical_bytes(OPS)
    canonical.parent.mkdir(parents=True)
    rows = json.loads(expected.decode("utf-8"))
    duplicated = [*rows, rows[0]]
    canonical.write_bytes(
        (json.dumps(duplicated, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
    )
    baked = plugin_dir / BAKED_OPS_RELATIVE
    baked.parent.mkdir(parents=True, exist_ok=True)
    baked.write_bytes(expected)
    with pytest.raises(SystemExit):
        check_export(
            canonical_path=canonical,
            plugins_root=plugins_root,
            dockerfile_path=dockerfile,
        )


def test_extra_field_in_baked_copy_fails_check(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    plugin_dir = _write_plugin_tree(plugins_root, "extra-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("extra-plugin",))
    canonical = tmp_path / "canonical" / "ops.json"
    expected = _independent_canonical_bytes(OPS)
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(expected)
    baked = plugin_dir / BAKED_OPS_RELATIVE
    baked.parent.mkdir(parents=True, exist_ok=True)
    rows = json.loads(expected.decode("utf-8"))
    rows[0]["bogus_field"] = 1
    baked.write_bytes(
        (json.dumps(rows, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    )
    with pytest.raises(SystemExit):
        check_export(
            canonical_path=canonical,
            plugins_root=plugins_root,
            dockerfile_path=dockerfile,
        )


def test_nondeterministic_reorder_in_file_fails_check(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    plugin_dir = _write_plugin_tree(plugins_root, "reorder-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("reorder-plugin",))
    canonical = tmp_path / "canonical" / "ops.json"
    expected = _independent_canonical_bytes(OPS)
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(expected)
    baked = plugin_dir / BAKED_OPS_RELATIVE
    baked.parent.mkdir(parents=True, exist_ok=True)
    rows = json.loads(expected.decode("utf-8"))
    reordered = list(reversed(rows))
    baked.write_bytes(
        (json.dumps(reordered, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
    )
    with pytest.raises(SystemExit):
        check_export(
            canonical_path=canonical,
            plugins_root=plugins_root,
            dockerfile_path=dockerfile,
        )


def test_synthetic_installed_plugin_requires_baked_copy_target(tmp_path: Path):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    _write_plugin_tree(plugins_root, "alpha-plugin")
    _write_plugin_tree(plugins_root, "beta-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("alpha-plugin", "beta-plugin"))
    targets = export_target_paths(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )
    baked = {
        path
        for path in targets
        if path.name == "ops.json" and "context" in path.parts
    }
    assert baked == {
        plugins_root / "alpha-plugin" / BAKED_OPS_RELATIVE,
        plugins_root / "beta-plugin" / BAKED_OPS_RELATIVE,
    }


def test_mutation_add_op_fails_until_regeneration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    plugin_dir = _write_plugin_tree(plugins_root, "mut-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("mut-plugin",))
    canonical = tmp_path / "canonical" / "ops.json"
    expected = _independent_canonical_bytes(OPS)
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(expected)
    baked = plugin_dir / BAKED_OPS_RELATIVE
    baked.parent.mkdir(parents=True, exist_ok=True)
    baked.write_bytes(expected)

    synthetic = OPS[0].model_copy(update={"op_id": "synthetic-mut", "bin_name": "nextseek-synthetic-mut"})
    mutated_ops = [*OPS, synthetic]
    monkeypatch.setattr(
        "nextseek_api.cc_assistant.op_registry.export.OPS",
        mutated_ops,
    )
    with pytest.raises(SystemExit):
        check_export(
            canonical_path=canonical,
            plugins_root=plugins_root,
            dockerfile_path=dockerfile,
        )

    regenerated = _independent_canonical_bytes(mutated_ops)
    assert _sha256(regenerated) != _sha256(expected)
    canonical.write_bytes(regenerated)
    baked.write_bytes(regenerated)
    check_export(
        canonical_path=canonical,
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )


def test_mutation_delete_op_fails_until_regeneration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    plugin_dir = _write_plugin_tree(plugins_root, "del-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("del-plugin",))
    canonical = tmp_path / "canonical" / "ops.json"
    expected = _independent_canonical_bytes(OPS)
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(expected)
    baked = plugin_dir / BAKED_OPS_RELATIVE
    baked.parent.mkdir(parents=True, exist_ok=True)
    baked.write_bytes(expected)

    mutated_ops = OPS[1:]
    monkeypatch.setattr(
        "nextseek_api.cc_assistant.op_registry.export.OPS",
        mutated_ops,
    )
    with pytest.raises(SystemExit):
        check_export(
            canonical_path=canonical,
            plugins_root=plugins_root,
            dockerfile_path=dockerfile,
        )

    regenerated = _independent_canonical_bytes(mutated_ops)
    assert _sha256(regenerated) != _sha256(expected)
    canonical.write_bytes(regenerated)
    baked.write_bytes(regenerated)
    check_export(
        canonical_path=canonical,
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )


def test_mutation_change_op_field_fails_until_regeneration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    plugins_root = tmp_path / "build_context" / "plugins"
    dockerfile = tmp_path / "Dockerfile"
    plugin_dir = _write_plugin_tree(plugins_root, "chg-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("chg-plugin",))
    canonical = tmp_path / "canonical" / "ops.json"
    expected = _independent_canonical_bytes(OPS)
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(expected)
    baked = plugin_dir / BAKED_OPS_RELATIVE
    baked.parent.mkdir(parents=True, exist_ok=True)
    baked.write_bytes(expected)

    mutated_ops = [
        OPS[0].model_copy(update={"per_op_gate_enabled": not OPS[0].per_op_gate_enabled}),
        *OPS[1:],
    ]
    monkeypatch.setattr(
        "nextseek_api.cc_assistant.op_registry.export.OPS",
        mutated_ops,
    )
    with pytest.raises(SystemExit):
        check_export(
            canonical_path=canonical,
            plugins_root=plugins_root,
            dockerfile_path=dockerfile,
        )

    regenerated = _independent_canonical_bytes(mutated_ops)
    assert _sha256(regenerated) != _sha256(expected)
    canonical.write_bytes(regenerated)
    baked.write_bytes(regenerated)
    check_export(
        canonical_path=canonical,
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )


def test_export_check_does_not_write_restore_or_create_repo_temps():
    before = {
        path: (path.stat().st_mtime_ns, path.stat().st_size)
        for path in [
            REPO_ROOT / "nextseek_api" / "cc_assistant" / "op_registry" / "ops.json",
            *sorted(
                (
                    REPO_ROOT
                    / "docker"
                    / "cc-runtime"
                    / "build_context"
                    / "plugins"
                ).rglob("ops.json")
            ),
        ]
        if path.is_file()
    }
    from nextseek_api.cc_assistant.op_registry.export import main as export_main

    assert export_main(["--check", "--root", str(REPO_ROOT)]) == 0
    after = {
        path: (path.stat().st_mtime_ns, path.stat().st_size) for path in before
    }
    assert after == before
    stray = list(REPO_ROOT.glob("op-registry-export-check-*"))
    assert stray == []
    pyc = list(
        (REPO_ROOT / "nextseek_api" / "cc_assistant" / "op_registry").glob("__pycache__/*")
    )
    # check mode must not rewrite targets even if a cache already exists
    for target, stamp in before.items():
        assert (target.stat().st_mtime_ns, target.stat().st_size) == stamp


def test_export_root_flag_rejects_stale_canonical(tmp_path: Path):
    from nextseek_api.cc_assistant.op_registry.export import main as export_main

    root = tmp_path / "repo"
    canonical = root / "nextseek_api" / "cc_assistant" / "op_registry" / "ops.json"
    plugins = root / "docker" / "cc-runtime" / "build_context" / "plugins"
    dockerfile = root / "docker" / "cc-runtime" / "Dockerfile"
    plugin_dir = _write_plugin_tree(plugins, "alpha-plugin")
    _write_dockerfile(dockerfile, copy_plugins=("alpha-plugin",))
    canonical.parent.mkdir(parents=True)
    canonical.write_text("{}\n", encoding="utf-8")
    baked = plugin_dir / BAKED_OPS_RELATIVE
    baked.parent.mkdir(parents=True, exist_ok=True)
    baked.write_text("{}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        export_main(["--check", "--root", str(root)])


def test_current_tree_export_targets_include_nextseek_baked_copy():
    discovery = discover_install(
        plugins_root=DEFAULT_PLUGINS_ROOT,
        dockerfile_path=DEFAULT_DOCKERFILE,
    )
    targets = export_target_paths(
        plugins_root=DEFAULT_PLUGINS_ROOT,
        dockerfile_path=DEFAULT_DOCKERFILE,
    )
    for plugin in discovery.plugins:
        assert DEFAULT_PLUGINS_ROOT / plugin / BAKED_OPS_RELATIVE in targets

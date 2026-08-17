"""Unit tests for plugin identity validation (Plan 005 Task 7)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nextseek_api.cc_assistant.op_registry.plugin_identity import (
    PluginIdentityError,
    load_and_validate_manifest,
    validate_plugin_identity,
)

VALID = {
    "name": "alpha-plugin",
    "version": "0.1.0",
    "description": "Claude Code plugin for NExtSEEK research workflows.",
    "author": {"name": "BMC"},
    "keywords": ["nextseek"],
}


def test_validate_accepts_supported_identity_fields():
    identity = validate_plugin_identity(VALID)
    assert identity.name == "alpha-plugin"
    assert identity.keywords == ("nextseek",)


@pytest.mark.parametrize(
    "field",
    ["operations", "skills", "commands", "families", "routes", "tools", "ops"],
)
def test_validate_rejects_inventory_like_fields(field: str):
    payload = {**VALID, field: ["nextseek-query"]}
    with pytest.raises(PluginIdentityError, match="inventory-like"):
        validate_plugin_identity(payload)


def test_validate_rejects_unknown_fields():
    with pytest.raises(PluginIdentityError, match="unknown manifest fields"):
        validate_plugin_identity({**VALID, "homepage": "https://example.test"})


def test_validate_rejects_enumerating_description():
    payload = {
        **VALID,
        "description": "Runs nextseek-query and nextseek-parse for search workflows.",
    }
    with pytest.raises(PluginIdentityError, match="enumerate"):
        validate_plugin_identity(payload)


def test_load_and_validate_manifest_requires_directory_name_match(tmp_path: Path):
    plugin_dir = tmp_path / "dir-name"
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    payload = {**VALID, "name": "other-name"}
    manifest = manifest_dir / "plugin.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PluginIdentityError, match="directory"):
        load_and_validate_manifest(manifest)


def test_current_plugin_manifest_passes_local_validation():
    repo_root = Path(__file__).resolve().parents[3]
    manifest = (
        repo_root
        / "docker/cc-runtime/build_context/plugins/nextseek/.claude-plugin/plugin.json"
    )
    identity = load_and_validate_manifest(manifest)
    assert identity.name == "nextseek"
    assert "nextseek-query" not in identity.description.lower()

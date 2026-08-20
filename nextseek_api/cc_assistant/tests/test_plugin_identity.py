"""Mutant-killer tests for plugin identity validation (cc_assistant coverage lane)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nextseek_api.cc_assistant.op_registry import (
    PluginIdentityError,
    load_and_validate_manifest,
    validate_plugin_identity,
)

VALID = {
    "name": "alpha-plugin",
    "version": "0.1.0",
    "description": "Claude Code plugin for NExtSEEK research workflows.",
    "author": {"name": "BMC"},
}


def test_validate_rejects_non_object_and_empty_identity_fields():
    with pytest.raises(PluginIdentityError, match="JSON object"):
        validate_plugin_identity(["nope"])
    for field in ("name", "version", "description"):
        payload = {**VALID, field: "  "}
        with pytest.raises(PluginIdentityError):
            validate_plugin_identity(payload)


def test_validate_rejects_author_and_keyword_faults():
    with pytest.raises(PluginIdentityError, match="author must be an object"):
        validate_plugin_identity({**VALID, "author": "BMC"})
    with pytest.raises(PluginIdentityError, match="unknown author"):
        validate_plugin_identity({**VALID, "author": {"name": "BMC", "twitter": "x"}})
    with pytest.raises(PluginIdentityError, match="author.name"):
        validate_plugin_identity({**VALID, "author": {"name": ""}})
    with pytest.raises(PluginIdentityError, match="keywords must be an array"):
        validate_plugin_identity({**VALID, "keywords": "nextseek"})
    with pytest.raises(PluginIdentityError, match="keywords entries"):
        validate_plugin_identity({**VALID, "keywords": ["ok", ""]})
    identity = validate_plugin_identity({**VALID, "keywords": None})
    assert identity.keywords == ()


def test_load_and_validate_manifest_accepts_matching_directory(tmp_path: Path):
    plugin_dir = tmp_path / "alpha-plugin"
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "plugin.json"
    manifest.write_text(json.dumps(VALID), encoding="utf-8")
    identity = load_and_validate_manifest(manifest)
    assert identity.name == "alpha-plugin"
    payload = {**VALID, "name": "other"}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PluginIdentityError, match="directory"):
        load_and_validate_manifest(manifest)

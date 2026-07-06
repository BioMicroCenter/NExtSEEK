"""Doc-contract tests: the entity-extract UserPromptSubmit hook + context manifest.

Pin the two mechanisms added 2026-07-06 so the port can't silently drop them:
  1. A plugin UserPromptSubmit hook that ALWAYS runs nextseek-entity-extract
     before the agent acts (resolve vocabulary / expand abbreviations like GBM),
     fail-open and isolation-preserving.
  2. A context/MANIFEST.md that tells the agent which context files exist and
     when to consult each, referenced from SKILL.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_PLUGIN = (
    Path(__file__).resolve().parents[3]
    / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek"
)
_HOOKS_JSON = _PLUGIN / "hooks" / "hooks.json"
_HOOK_SH = _PLUGIN / "hooks" / "entity_preamble.sh"
_MANIFEST = _PLUGIN / "context" / "MANIFEST.md"
_SKILL = _PLUGIN / "skills" / "nextseek" / "SKILL.md"


def test_hooks_json_registers_userpromptsubmit_entity_hook():
    obj = json.loads(_HOOKS_JSON.read_text(encoding="utf-8"))
    ups = obj["hooks"]["UserPromptSubmit"]
    cmds = [h["command"] for group in ups for h in group["hooks"] if h.get("type") == "command"]
    assert any("entity_preamble.sh" in c for c in cmds), cmds


def test_hook_script_exists_is_executable_and_fail_open():
    assert _HOOK_SH.is_file()
    assert os.access(_HOOK_SH, os.X_OK), "entity_preamble.sh must be executable"
    body = _HOOK_SH.read_text(encoding="utf-8")
    # Runs entity-extract; bounded; degrades without blocking the turn.
    assert "nextseek-entity-extract" in body
    assert "timeout" in body
    assert "UserPromptSubmit" in body
    assert "exit 0" in body  # fail-open path


def test_hook_preserves_isolation_no_new_creds_or_network():
    body = _HOOK_SH.read_text(encoding="utf-8")
    # Must not introduce credentials or new network endpoints — reuse the bin only.
    for banned in ("NEO4J_", "MYSQL_", "AWS_", "curl ", "wget ", "http://", "https://"):
        assert banned not in body, f"hook must not reference {banned!r}"


def test_manifest_lists_context_files_and_when_to_consult():
    md = _MANIFEST.read_text(encoding="utf-8")
    for f in ("capabilities.md", "min_sampletypes_db.json", "neo4j_schema.json",
              "min_graph_schema.json", "projects_db.json", "min_api_endpoints"):
        assert f in md, f"manifest missing pointer to {f}"
    assert "consult" in md.lower()
    # Every non-manifest context file must be pointed to by the manifest.
    ctx_dir = _PLUGIN / "context"
    for p in ctx_dir.iterdir():
        if p.name == "MANIFEST.md" or not p.is_file():
            continue
        assert p.name in md, f"context file {p.name} not listed in MANIFEST.md"


def test_skill_points_to_manifest_and_auto_entity_extract():
    skill = _SKILL.read_text(encoding="utf-8")
    assert "MANIFEST.md" in skill
    assert "entity-extract" in skill
    assert "automatic" in skill.lower() or "UserPromptSubmit" in skill

"""container/CLAUDE.md plugin-section content contract.

Ported from dmac-assistant tests/unit/test_container_claude_md_plugin_section.py.
The ingest-pipeline regression test (test_ingest_pipeline_produces_non_empty_block)
is NOT ported — its build_tools.ingest_nextseek_docs orchestrator was not ported
to NExtSEEK. The remaining assertions (no legacy nextseek-api references, canonical
plugin paths, auto-gen sentinel integrity) are content-only. No chat_nextseek import.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLAUDE_MD = (
    REPO_ROOT / "docker" / "cc-runtime" / "container" / "CLAUDE.md"
).resolve()


def test_no_nextseek_api_references_in_container_claude_md():
    """D12 + D25: the image ships only the new `nextseek` plugin. The legacy
    `nextseek-api` name MUST NOT appear anywhere in container/CLAUDE.md."""
    text = CLAUDE_MD.read_text()
    occurrences = text.count("nextseek-api")
    assert occurrences == 0, (
        f"container/CLAUDE.md contains {occurrences} reference(s) to the legacy "
        f"`nextseek-api` plugin name; expected zero."
    )


def test_plugins_section_uses_canonical_paths():
    """The 'Plugins available' section names the canonical new-plugin artifacts
    at the correct paths."""
    text = CLAUDE_MD.read_text()
    expected_strings = [
        "**`nextseek`**",
        "/app/plugins/nextseek/skills/nextseek/SKILL.md",
        "/app/plugins/nextseek/commands/nextseek.md",
        "/app/plugins/nextseek/bin/",
        "/app/plugins/nextseek/context/",
        "read the SKILL.md first",
        "translated to `API_USER` / `API_PASS` by the container entrypoint",
    ]
    missing = [s for s in expected_strings if s not in text]
    assert not missing, (
        f"container/CLAUDE.md is missing expected `nextseek` plugin-section "
        f"strings: {missing}"
    )


def test_auto_gen_sentinel_block_intact():
    """The auto-gen sentinel block must remain present + structurally sound.
    BEGIN must precede END; content between them may be empty or non-empty."""
    text = CLAUDE_MD.read_text()
    begin_idx = text.find("<!-- BEGIN NEXTSEEK-DOCS")
    end_idx = text.find("<!-- END NEXTSEEK-DOCS")
    assert begin_idx >= 0, "container/CLAUDE.md is missing the BEGIN NEXTSEEK-DOCS sentinel"
    assert end_idx >= 0, "container/CLAUDE.md is missing the END NEXTSEEK-DOCS sentinel"
    assert begin_idx < end_idx, (
        f"BEGIN sentinel (idx {begin_idx}) must precede END sentinel (idx {end_idx})."
    )

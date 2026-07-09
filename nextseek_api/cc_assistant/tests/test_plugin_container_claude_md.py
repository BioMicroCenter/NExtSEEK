"""container/CLAUDE.md plugin-section content contract.

Ported from dmac-assistant tests/unit/test_container_claude_md_plugin_section.py.
The ingest-pipeline regression test (test_ingest_pipeline_produces_non_empty_block)
is NOT ported — its build_tools.ingest_nextseek_docs orchestrator was not ported
to NExtSEEK. The remaining assertions (no legacy nextseek-api references, canonical
plugin paths, auto-gen sentinel integrity) are content-only. No chat_nextseek import.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.host_only

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


@pytest.mark.skipif(
    not (REPO_ROOT / ".git").exists(),
    reason="source-tree check: runs against the working checkout, not inside "
    "a built image (the image strips .git/.gitignore).",
)
def test_container_claude_md_is_git_tracked_not_ignored():
    """Step 7d regression lock: docker/cc-runtime/container/CLAUDE.md is a
    required `COPY` input of the cc-agent image, so a clean clone must
    contain it. It was silently ignored by the bare `CLAUDE.md` rule in the
    root .gitignore, which made the image unbuildable from tracked files
    only. It must be tracked, and `git check-ignore` must not match it."""
    rel = "docker/cc-runtime/container/CLAUDE.md"
    check_ignore = subprocess.run(
        ["git", "check-ignore", "-q", rel],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert check_ignore.returncode != 0, (
        f"{rel} is matched by a .gitignore rule; the cc-agent image cannot "
        "be built from a clean clone. Keep the "
        f"`!{rel}` negation after the bare `CLAUDE.md` rule."
    )
    ls_files = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert ls_files.returncode == 0, (
        f"{rel} is not tracked by git; the cc-agent image cannot be built "
        "from a clean clone."
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

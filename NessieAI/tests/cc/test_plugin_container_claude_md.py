"""container/CLAUDE.md plugin-section content contract.

Ported from dmac-assistant tests/unit/test_container_claude_md_plugin_section.py.
The ingest-pipeline regression test (test_ingest_pipeline_produces_non_empty_block)
is NOT ported — its NessieAI.build_tools.ingest_nextseek_docs orchestrator was not ported
to NExtSEEK. The remaining assertions (no legacy nextseek-api references, canonical
plugin paths, auto-gen sentinel integrity) are content-only. No chat_nextseek import.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from NessieAI import paths

pytestmark = pytest.mark.host_only

REPO_ROOT = Path(__file__).resolve().parents[3]
CLAUDE_MD = (paths.CC_RUNTIME_DIR / "container" / "CLAUDE.md").resolve()


def test_no_nextseek_api_references_in_container_claude_md():
    """D12 + D25: the image ships only the new `nextseek` plugin. The legacy
    `nextseek-api` name MUST NOT appear anywhere in container/CLAUDE.md."""
    text = CLAUDE_MD.read_text()
    # Current bins nextseek-api-read / nextseek-api-write contain the substring
    # "nextseek-api"; only the legacy plugin token itself is forbidden.
    occurrences = re.findall(r"(?<![\w-])nextseek-api(?!-\w)", text)
    assert not occurrences, (
        f"container/CLAUDE.md contains {len(occurrences)} reference(s) to the legacy "
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
    """Step 7d regression lock: NessieAI/docker/cc-runtime/container/CLAUDE.md is a
    required `COPY` input of the cc-agent image, so a clean clone must
    contain it. It was silently ignored by the bare `CLAUDE.md` rule in the
    root .gitignore, which made the image unbuildable from tracked files
    only. It must be tracked, and `git check-ignore` must not match it."""
    rel = "NessieAI/docker/cc-runtime/container/CLAUDE.md"
    check_ignore = subprocess.run(
        ["git", "check-ignore", "-q", rel],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert check_ignore.returncode != 0, (
        f"{rel} is matched by a .gitignore rule; the cc-agent image cannot "
        "be built from a clean clone. Keep the "
        "`!NessieAI/**/CLAUDE.md` negation after the bare `CLAUDE.md` rule."
    )
    ls_files = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert ls_files.returncode == 0, (
        f"{rel} is not tracked by git; the cc-agent image cannot be built "
        "from a clean clone."
    )


def test_container_claude_md_documents_query_and_recall_ops():
    text = CLAUDE_MD.read_text()
    assert "nextseek-query" in text
    assert "nextseek-recall" in text
    assert "live chat session" in text.lower()
    assert "--turn" in text


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


# --------------------------------------------------------------------------
# Runtime claims: what the file tells the agent about its own runtime must
# match what the CC engine actually does. Checked against cc_engine itself,
# imported lazily so the content-only checks above still run where docker-py
# is absent.
# --------------------------------------------------------------------------

_MOUNT_LINE = re.compile(r"^\s*- `(/[^`]+)` \((read-only|read-write)\)", re.MULTILINE)


def _engine():
    return pytest.importorskip("NessieAI.cc.cc_engine")


def _every_agent_mount():
    """Every mount a turn can get, optional ones included."""
    cc_engine = _engine()
    from NessieAI.cc.cc_config import CCPaths

    return cc_engine._build_volumes(
        paths=CCPaths(users_volume="dmac-cc-users", user_root_mount="/dmac/users"),
        project_dirname="42-px", user_id="alice", cc_state_key="S1", run_id="R1",
        transcripts_subpath="42-px/alice/_memory/S1/transcripts",
        previous_turns=True,
    )


def test_container_claude_md_lists_exactly_the_agent_mounts_with_their_modes():
    """The mount list names every mount _build_volumes gives the agent, each
    with its real mode, and no other."""
    text = CLAUDE_MD.read_text()
    documented = {path: mode for path, mode in _MOUNT_LINE.findall(text)}
    actual = {
        m["Target"]: ("read-only" if m["ReadOnly"] else "read-write")
        for m in _every_agent_mount()
    }
    assert documented == actual


def test_container_claude_md_names_no_data_path_that_is_not_mounted():
    """A /data path the agent is told about must exist in its container."""
    text = CLAUDE_MD.read_text()
    targets = {m["Target"] for m in _every_agent_mount()}
    named = {p.rstrip("/.") for p in re.findall(r"/data/[\w.-]+", text)}
    assert named - targets == set()


def test_container_claude_md_names_no_dmac_switch_the_agent_never_gets():
    """Every DMAC_* variable the file names is one build_agent_environment
    sets. The agent never gets DMAC_ROUTER_ENABLED or DMAC_RUNTIME_MODE."""
    env = _engine().build_agent_environment(
        source={}, api_user="u", api_pass="p", path_mappings={}, chat_session_id="c",
    )
    named = set(re.findall(r"\bDMAC_[A-Z0-9_]+\b", CLAUDE_MD.read_text()))
    assert named - set(env) == set()


def test_container_claude_md_describes_one_container_per_turn():
    """A turn is a fresh container fed one message on stdin and removed after,
    never a docker exec into an idle, long-lived container."""
    cc_engine = _engine()
    kwargs = cc_engine._run_kwargs(
        image="img", command=["claude"], environment={}, mounts=None,
        run_id="0123abcd", user_id="alice",
    )
    assert kwargs["stdin_open"] is True
    assert kwargs["auto_remove"] is True
    text = CLAUDE_MD.read_text()
    for stale in ("docker exec", "idle mode", "long-lived"):
        assert stale not in text, f"container/CLAUDE.md still says {stale!r}"


def test_container_claude_md_turn_limit_matches_the_engine_default():
    """The documented wall-clock limit is the engine's default ceiling. Read
    from source: the running process may carry a deployment override."""
    src = Path(_engine().__file__).read_text(encoding="utf-8")
    default = re.search(r'"NEXTSEEK_CC_TIMEOUT_HARD_MAX", "(\d+)"', src)
    assert default, "cc_engine no longer defaults NEXTSEEK_CC_TIMEOUT_HARD_MAX"
    documented = re.search(r"stopped after (\d+) seconds", CLAUDE_MD.read_text())
    assert documented, "container/CLAUDE.md does not state the turn's time limit"
    assert documented.group(1) == default.group(1)

"""SKILL.md content contract for the per-op nextseek workflow.

Ported from dmac-assistant tests/unit/test_skill_md.py and adapted to the
per-op design (2026-07-05 amendment: nextseek-query disabled; the agent
orchestrates the 8 decomposed ops directly). LOAD-BEARING write-safety and
isolation invariants (L1 auto-mode classifier, L3 plain-text confirmation,
DMAC_PATH_MAPPINGS, error taxonomy, frontmatter) are preserved verbatim from
the original; the single-shot-nextseek-query assertions were rewritten for the
per-op design. Markdown-only; no chat_nextseek import.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_PATH = (
    REPO_ROOT
    / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek"
    / "skills" / "nextseek" / "SKILL.md"
)

# Per-op amendment: the 8 decomposed ops. nextseek-query is DISABLED and must
# NOT appear (see test_nextseek_query_is_absent).
CAPABILITY_MATRIX_TOOLS = (
    "nextseek-api-write",
    "nextseek-generate-submission",
    "nextseek-report",
    "nextseek-entity-extract",
    "nextseek-parse",
    "nextseek-plan",
    "nextseek-api-read",
    "nextseek-graph",
)

# Full ERROR_EXIT taxonomy the runner/sidecar can surface (docker/cc-runtime/
# build_context/plugins/nextseek/bin/_ws_contract.py ERROR_EXIT).
RUNNER_EXIT_CODES = (
    "CONFIG_MISSING",
    "IMPORT_FAILED",
    "VALIDATION",
    "AGENT_FAILED",
    "WRITE_BLOCKED",
    "CONFIG_ERROR",
    "TRANSPORT_ERROR",
    "AUTH_FAILED",
    "STAGING_ERROR",
)


def _read_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_skill_md_exists():
    assert SKILL_PATH.exists(), f"SKILL.md missing at {SKILL_PATH}"


def test_yaml_frontmatter_shape():
    text = _read_skill()
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert m, "SKILL.md must start with a YAML frontmatter block"
    fm = m.group(1)
    assert re.search(r"^name:\s*nextseek\s*$", fm, flags=re.MULTILINE), (
        "frontmatter must declare `name: nextseek`"
    )
    assert re.search(r"^disable-model-invocation:\s*false\s*$", fm, flags=re.MULTILINE), (
        "frontmatter must declare `disable-model-invocation: false`"
    )


def test_nextseek_query_is_absent():
    """Per-op amendment: nextseek-query is disabled and removed from the plugin.
    The SKILL.md must NOT reference it (otherwise the agent would try to call a
    bin that no longer exists)."""
    text = _read_skill()
    assert "nextseek-query" not in text, (
        "SKILL.md must not reference the disabled `nextseek-query` op"
    )


def test_per_op_orchestration_recipes_present():
    """Replacement for the retired single-shot default-path assertion. The
    per-op SKILL.md must document the search recipe (parse -> api-read) and the
    op-selection section, since the agent now orchestrates the pieces itself."""
    text = _read_skill()
    assert "## Choosing the op for a task" in text, (
        "SKILL.md must have a `## Choosing the op for a task` section"
    )
    assert "nextseek-parse" in text and "nextseek-api-read" in text, (
        "SKILL.md must document the search recipe (nextseek-parse -> nextseek-api-read)"
    )


def test_d19_dmac_path_mappings_referenced():
    """D19 / NEW-3: SKILL.md reads DMAC_PATH_MAPPINGS, never hard-codes the
    /persistent/output/{user_id} literal."""
    text = _read_skill()
    assert "DMAC_PATH_MAPPINGS" in text, (
        "Reply hygiene subsection must reference DMAC_PATH_MAPPINGS"
    )
    assert "/persistent/output/{user_id}" not in text, (
        "FORBIDDEN: SKILL.md must not hard-code /persistent/output/{user_id}"
    )


def test_l3_forbids_askuserquestion_and_uses_plain_text_prompt():
    """L3 (write-safety behavioral layer): SKILL.md MUST forbid AskUserQuestion
    and provide the plain-text confirmation template. Load-bearing user-facing
    invariant for any write operation."""
    text = _read_skill()
    assert "About to execute a WRITE-classified operation" in text
    pattern = re.compile(r"\*?\*?NEVER\*?\*?[^\n]{0,64}AskUserQuestion", re.MULTILINE)
    assert pattern.search(text), (
        "SKILL.md must explicitly forbid AskUserQuestion at the L3 boundary"
    )
    askuser_lines = [line for line in text.splitlines() if "AskUserQuestion" in line]
    assert askuser_lines, "AskUserQuestion must be referenced at the L3 boundary"
    negative_pattern = re.compile(
        r"(NEVER|never|forbid|MUST be plain text|does not render|doesn't render|"
        r"can't render|don't|do not)",
        re.IGNORECASE,
    )
    for line in askuser_lines:
        assert negative_pattern.search(line), (
            f"every AskUserQuestion mention must carry a negative qualifier; "
            f"offending line: {line!r}"
        )


def test_layer_1_describes_auto_mode_classifier_screening():
    """Layer 1 (Claude Code permission gating) under auto mode (OI-5): the
    container_cc route runs `--permission-mode auto`; every tool call is
    screened by the auto-mode classifier, a behavioral gate (defense-in-depth),
    with L2/L3 load-bearing. Pins the contract against silent elevation."""
    text = _read_skill()
    assert "--permission-mode auto" in text, (
        "Layer 1 must reference --permission-mode auto"
    )
    assert "classifier" in text, (
        "Layer 1 must describe the auto-mode classifier screening tool calls"
    )
    assert "defense-in-depth" in text or "defence-in-depth" in text, (
        "Layer 1 must be described as defense-in-depth (not a guarantee)"
    )
    assert "L2 and L3" in text or "L3 and L2" in text, (
        "Write-safety section must name L2 and L3 as the load-bearing layers"
    )


def test_isolation_no_shared_creds_or_chat_nextseek_source():
    """Per-op amendment / OI-3: the SKILL.md must not instruct the agent to
    reach shared backend credentials or chat_nextseek source. It must state ops
    run server-side and forbid spelunking the bin/chat_nextseek internals."""
    text = _read_skill()
    assert "server-side" in text, (
        "SKILL.md must state that ops run server-side (isolation)"
    )
    # The hard-prohibition on spelunking bin internals must survive.
    assert "/app/plugins/nextseek/bin/" in text and "MUST NOT" in text, (
        "SKILL.md must keep the hard prohibition on reading bin internals"
    )


def test_tool_capability_matrix_lists_the_eight_ops():
    text = _read_skill()
    assert "## Tool capability matrix" in text, (
        "SKILL.md must have a `## Tool capability matrix` section"
    )
    for tool in CAPABILITY_MATRIX_TOOLS:
        assert tool in text, f"Tool capability matrix must reference: {tool}"


def test_errors_section_lists_the_runner_codes():
    """Errors block must enumerate the exit-code mnemonics the runner/sidecar
    surface (_ws_contract.ERROR_EXIT), so the in-image agent can interpret them."""
    text = _read_skill()
    assert "## Errors" in text
    for code in RUNNER_EXIT_CODES:
        assert code in text, f"Errors section must document exit code {code}"

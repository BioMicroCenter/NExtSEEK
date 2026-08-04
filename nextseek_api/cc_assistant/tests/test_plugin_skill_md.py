"""SKILL.md content contract for the per-op nextseek workflow.

Ported from dmac-assistant tests/unit/test_skill_md.py and adapted to the
per-op design with inventory-derived query/recall ops (spec-001 T11). LOAD-BEARING
write-safety and isolation invariants (L1 auto-mode classifier, L3 plain-text
confirmation, DMAC_PATH_MAPPINGS, error taxonomy, frontmatter) are preserved
verbatim from the original. Markdown-only; no chat_nextseek import.
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

CAPABILITY_MATRIX_TOOLS = (
    "nextseek-api-write",
    "nextseek-generate-submission",
    "nextseek-report",
    "nextseek-entity-extract",
    "nextseek-parse",
    "nextseek-plan",
    "nextseek-api-read",
    "nextseek-graph",
    "nextseek-query",
    "nextseek-recall",
)

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


def test_nextseek_query_and_recall_documented_with_roles():
    text = _read_skill()
    assert "nextseek-query" in text
    assert "nextseek-recall" in text
    assert "live chat session" in text.lower() or "live chat session" in text
    assert "--turn" in text


def test_per_op_orchestration_recipes_present():
    text = _read_skill()
    assert "## Choosing the op for a task" in text, (
        "SKILL.md must have a `## Choosing the op for a task` section"
    )
    assert "nextseek-parse" in text and "nextseek-api-read" in text, (
        "SKILL.md must document the search recipe (nextseek-parse -> nextseek-api-read)"
    )


def test_d19_dmac_path_mappings_referenced():
    text = _read_skill()
    assert "DMAC_PATH_MAPPINGS" in text, (
        "Reply hygiene subsection must reference DMAC_PATH_MAPPINGS"
    )
    assert "/persistent/output/{user_id}" not in text, (
        "FORBIDDEN: SKILL.md must not hard-code /persistent/output/{user_id}"
    )


def test_l3_forbids_askuserquestion_and_uses_plain_text_prompt():
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
    text = _read_skill()
    assert "server-side" in text, (
        "SKILL.md must state that ops run server-side (isolation)"
    )
    assert "/app/plugins/nextseek/bin/" in text and "MUST NOT" in text, (
        "SKILL.md must keep the hard prohibition on reading bin internals"
    )


def test_tool_capability_matrix_lists_the_query_ops():
    text = _read_skill()
    assert "## Tool capability matrix" in text, (
        "SKILL.md must have a `## Tool capability matrix` section"
    )
    for tool in CAPABILITY_MATRIX_TOOLS:
        assert tool in text, f"Tool capability matrix must reference: {tool}"


def test_errors_section_lists_the_runner_codes():
    text = _read_skill()
    assert "## Errors" in text
    for code in RUNNER_EXIT_CODES:
        assert code in text, f"Errors section must document exit code {code}"


def test_reingest_recipe_names_every_required_a_star_attribute():
    """The reingest row recipe must teach the WHOLE required set.

    Every ``A.*`` sample type requires File_PrimaryData, Link_PrimaryData, Scientist,
    Parent and Checksum_PrimaryData (``sampletypes_db.json``). The recipe previously
    named only a subset, so the agent composed structurally invalid rows that the
    server rejected on upload. QA now HARD_REJECTs a blank required field, which makes
    this prose load-bearing: if it drifts back, the reingest path silently stops
    producing workbooks.
    """
    text = SKILL_PATH.read_text(encoding="utf-8")
    recipe = text.split("Reingest pipeline outputs", 1)[1].split("**Multi-step", 1)[0]
    for attr in ("File_PrimaryData", "Link_PrimaryData", "Scientist",
                 "Parent", "Checksum_PrimaryData"):
        assert attr in recipe, f"reingest recipe never mentions {attr}"
    # The basename/path split is the part most easily got wrong.
    assert "basename" in recipe and "absolute Luria path" in recipe
    # Placeholder convention must stay, and stay distinct from "blank".
    assert "*** PLACEHOLDER" in recipe
    assert "never leave a required field blank" in recipe.lower()


def test_reingest_recipe_keeps_the_do_not_upload_clause():
    """The human-commits-via-UI gate is policy, not friction — it must not be edited out."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    recipe = text.split("Reingest pipeline outputs", 1)[1].split("**Multi-step", 1)[0]
    assert "you do not" in recipe.lower() and "upload" in recipe.lower()


def test_reingest_recipe_requires_reporting_incomplete_runs():
    """A HARD_REJECT drops a whole sample type; presenting the rest as the finished
    job is the failure mode `complete`/`rejected_types` exists to prevent."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    recipe = text.split("Reingest pipeline outputs", 1)[1].split("**Multi-step", 1)[0]
    assert "complete" in recipe and "rejected_types" in recipe

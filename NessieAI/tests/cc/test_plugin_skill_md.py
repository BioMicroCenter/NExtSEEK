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

from NessieAI import paths

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_PATH = paths.CC_PLUGIN_DIR / "skills" / "nextseek" / "SKILL.md"

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


# --------------------------------------------------------------------------
# 2026-09-22: the mapping was right and the instruction asked for a key that does not
# exist. A Container-CC reply said, verbatim: "Host path: I couldn't translate it,
# DMAC_PATH_MAPPINGS was present but I couldn't parse a mapping from it, so I'm giving
# the container path." The value was well-formed and non-empty; G7-10 had deliberately
# replaced `host_root` with `logical_root` (cc_engine.py's own comment says so), while
# this document still asked for the "host-side path" and documented no schema at all --
# no key names, no prefix rule. Two archived plan reviews predicted exactly this
# ("post-cutover DMAC_PATH_MAPPINGS schema undefined"), the code locked the schema, and
# the agent-facing document was never updated. Nothing compared the two, so these tests
# read the key names out of the engine and require the document to name them.
# --------------------------------------------------------------------------

def test_the_skill_documents_the_path_mapping_schema_the_engine_writes():
    from NessieAI.cc.cc_engine import path_mappings_for

    text = _read_skill()
    mapping = path_mappings_for(output_mnt="/dmac/users/p/u/output",
                                run_scratch_mnt="/dmac/users/p/u/scratch/run-1")

    for name, entry in mapping.items():
        assert name in text, f"SKILL.md never names the {name!r} mapping"
        for key in entry:
            assert key in text, f"SKILL.md never names the {key!r} key the engine writes"


def test_the_skill_states_the_substitution_rather_than_a_host_path():
    text = _read_skill()
    lowered = text.lower()

    assert "prefix" in lowered, "the operation is a prefix replacement; say so"
    assert "host-side path" not in lowered, "there is no host path in the mapping any more"


def test_the_engine_maps_both_roots_and_survives_a_run_with_no_scratch():
    from NessieAI.cc.cc_engine import path_mappings_for

    full = path_mappings_for(output_mnt="/dmac/users/p/u/output",
                            run_scratch_mnt="/dmac/users/p/u/scratch/run-1")
    assert full == {
        "output": {"container_root": "/data/output", "logical_root": "/dmac/users/p/u/output"},
        "scratch": {"container_root": "/data/scratch", "logical_root": "/dmac/users/p/u/scratch/run-1"},
    }

    # A turn with no run id has no per-run scratch root, and an entry whose logical_root
    # is None is worse than no entry: it is exactly what the agent cannot translate.
    assert path_mappings_for(output_mnt="/dmac/users/p/u/output", run_scratch_mnt=None) == {
        "output": {"container_root": "/data/output", "logical_root": "/dmac/users/p/u/output"},
    }

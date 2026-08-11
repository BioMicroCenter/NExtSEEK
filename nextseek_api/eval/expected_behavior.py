"""T0.2 — Deterministic `task_family → ExpectedBehavior` rule function.

Locked-spec anchors:
- DD-30: pattern-based Python rule function (deterministic, no LLM)
- DL-021 (2026-05-15): user-approved 22-row mapping (source of truth)
- DL-013 / R15: `Unsupported` family resolves to `StateUnsupportedBoundary` as
  the coarse default. The 5 corpus queries in this family split across ≥4
  distinct semantic behaviors (GenerateArtifact / AnswerDirectly /
  StateUnsupportedBoundary / ClarifyIfAmbiguous), but the `(task_family,)`
  signature has no axis to disambiguate. Known accuracy loss is documented
  in the Risk Register row R15. Revisiting this requires a new DD with
  explicit user direction.

Build-plan anchors:
- DL-027: executor-forcing pinning of the 22-row mapping source-of-truth.
  The 22 pairs are encoded as a flat dict literal in EXPECTED_BEHAVIOR_BY_FAMILY
  below; any divergence from the locked-design table cell-for-cell is a defect.
- Path B / DL-029 (2026-05-16): the enum distribution
  `{AnswerDirectly: 13, GenerateArtifact: 4, UsePriorContext: 2,
   StateUnsupportedBoundary: 1, RefuseUnsafeOnly: 2}` (sum = 22) is the
  operative result of applying this rule across FAMILIES_22. This diverges from
  locked-design line 318's `AnswerDirectly (14 families)` summary because that
  line is an arithmetic-error summary; the row enumeration at lines 295-316 is
  canonical (and contains exactly 13 AnswerDirectly entries).

Project memory:
- `feedback_categorize_with_full_corpus_context.md` 2026-05-15: Write-Create
  and Write-Update both resolve to `RefuseUnsafeOnly` (NOT `ClarifyIfAmbiguous`).
  Rationale anchor: the `Write-safety on NExtSEEK` paragraph of `container/CLAUDE.md` destructive-NExtSEEK-op
  confirmation policy.
"""
from __future__ import annotations

from nextseek_api.eval.enums import ExpectedBehavior

__all__ = [
    "EXPECTED_BEHAVIOR_BY_FAMILY",
    "FAMILIES_22",
    "expected_behavior_rule",
]


# Canonical 22-row mapping verbatim from locked-design lines 295-316 (DL-021).
# Order preserved for audit-trail readability; lookup is by string key.
EXPECTED_BEHAVIOR_BY_FAMILY: dict[str, ExpectedBehavior] = {
    "Edge": ExpectedBehavior.AnswerDirectly,
    "Graph-Assay": ExpectedBehavior.AnswerDirectly,
    "Graph-Count": ExpectedBehavior.AnswerDirectly,
    "Graph-Lineage": ExpectedBehavior.AnswerDirectly,
    "Graph-Study": ExpectedBehavior.AnswerDirectly,
    "Memory": ExpectedBehavior.UsePriorContext,
    "Report-GEO": ExpectedBehavior.GenerateArtifact,
    "Report-NFCORE": ExpectedBehavior.GenerateArtifact,
    "Report-PRIDE": ExpectedBehavior.GenerateArtifact,
    "Report-SRA": ExpectedBehavior.GenerateArtifact,
    "Reporter-Summary": ExpectedBehavior.AnswerDirectly,
    "Retrieve": ExpectedBehavior.AnswerDirectly,
    "SampleTree": ExpectedBehavior.AnswerDirectly,
    "Search-Attribute": ExpectedBehavior.AnswerDirectly,
    "Search-Basic": ExpectedBehavior.AnswerDirectly,
    "Search-MultiAssay": ExpectedBehavior.AnswerDirectly,
    "Search-Refine": ExpectedBehavior.UsePriorContext,
    "System-Capabilities": ExpectedBehavior.AnswerDirectly,
    "System-Entity": ExpectedBehavior.AnswerDirectly,
    "Unsupported": ExpectedBehavior.StateUnsupportedBoundary,
    "Write-Create": ExpectedBehavior.RefuseUnsafeOnly,
    "Write-Update": ExpectedBehavior.RefuseUnsafeOnly,
}

# The rule function's input domain — exactly these 22 strings.
FAMILIES_22: tuple[str, ...] = tuple(EXPECTED_BEHAVIOR_BY_FAMILY.keys())


def expected_behavior_rule(task_family: str) -> ExpectedBehavior:
    """Map task_family → ExpectedBehavior per locked DD-30 + DL-021.

    Coarse-default behavior for the `Unsupported` family per DL-013 / R15:
    `Unsupported` resolves to `StateUnsupportedBoundary` (known accuracy loss
    because the family is semantically heterogeneous; the `(task_family,)`
    signature has no axis to disambiguate).

    Write-* families (Write-Create, Write-Update) resolve to `RefuseUnsafeOnly`
    per project memory `feedback_categorize_with_full_corpus_context.md` —
    rationale anchored to the `Write-safety on NExtSEEK` paragraph of `container/CLAUDE.md` destructive-NExtSEEK-op
    confirmation policy.

    Raises:
        KeyError: if `task_family` is not one of the 22 canonical strings
                  in FAMILIES_22. Callers MUST handle unknown families
                  explicitly rather than relying on a silent default.
    """
    return EXPECTED_BEHAVIOR_BY_FAMILY[task_family]

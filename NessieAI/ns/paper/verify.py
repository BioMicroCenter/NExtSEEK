"""Stage 4 (spec §4 and §4.2): check the agent's curation, then make what still fails safe.

The launcher bounces a curation with errors back to the agent once (needs_bounce). What still fails after that is
made safe here, never written as it is: a failing value is blanked, a bad attribute dropped, a bad edge or a broken
constraint holds its rows, an unplaced deposit row is excluded, and each becomes a gap. A structural problem (a row
or parent naming no node, a loop) cannot be made safe: CurationUnusable fails the run at stage verify.
"""
from __future__ import annotations

from NessieAI.ns.paper import curation_check
from NessieAI.ns.paper.curation_models import (Blank, ContextArtifact, CurationArtifact, Excluded, Finding, HeldRow,
                                               Note, VerifiedArtifact)
from NessieAI.ns.paper.models import EvidenceArtifact
from NessieAI.ns.paper.rulebook import Rulebook
from NessieAI.ns.paper.uid_plan import plan_uids

BLANK_CODES = frozenset({"value_not_in_sources", "evidence_unresolved", "decision_unknown", "rule_unknown",
                         "rule_not_a_value_rule"})
#: Constraint checks whose cell-level findings drop the cell rather than blank it.
DROP_CHECKS = frozenset({"attributes_exist", "doi_on_own_rows"})


class CurationUnusable(ValueError):
    def __init__(self, findings: list[Finding]):
        super().__init__("; ".join(f.detail for f in findings[:5]))
        self.findings = findings


def check(curation: CurationArtifact, evidence: EvidenceArtifact, context: ContextArtifact,
          rulebook: Rulebook) -> list[Finding]:
    chosen = context.project.chosen
    raw = curation_check.check_all(curation.model_dump(mode="json"), evidence.model_dump(mode="json"),
                                   context.model_dump(mode="json"), rulebook.model_dump(mode="json"),
                                   project_id=chosen.project_id if chosen else None)
    return [Finding.model_validate(f) for f in raw]


def needs_bounce(findings: list[Finding]) -> bool:
    return any(f.severity == "error" for f in findings)


def verify(curation: CurationArtifact, evidence: EvidenceArtifact, context: ContextArtifact, rulebook: Rulebook,
           *, bounced: bool) -> VerifiedArtifact:
    findings = check(curation, evidence, context, rulebook)
    structural = [f for f in findings if f.code in curation_check.STRUCTURAL]
    if structural:
        raise CurationUnusable(structural)
    checks = {r.id: r.check for r in rulebook.rules}
    cur = curation.model_copy(deep=True)
    rows = {r.row_key: r for r in cur.rows}
    held: dict[str, str] = {}
    gaps = list(cur.gaps)
    dropped: set[str] = set()

    def hold_node(node_id: str, reason: str) -> None:
        for row in cur.rows:
            if row.node_id == node_id:
                held.setdefault(row.row_key, reason)

    for f in findings:
        row = rows.get(f.row_key) if f.row_key else None
        cell_level = row is not None and f.attribute is not None and f.attribute in row.values
        if f.code in BLANK_CODES and cell_level:
            row.values[f.attribute] = Blank(blank="not_in_sources")
        elif f.code in ("attribute_unknown", "doi_misplaced") and cell_level:
            row.values.pop(f.attribute)
            if f.code == "attribute_unknown":
                gaps.append(Note(topic="attribute", detail=f.detail, refs=[f.row_key]))
        elif f.code == "constraint" and cell_level:
            if checks.get(f.rule) in DROP_CHECKS:
                row.values.pop(f.attribute)
            else:
                row.values[f.attribute] = Blank(blank="not_in_sources")
        elif f.code in ("edge_not_allowed", "assay_unknown") and f.node_id:
            hold_node(f.node_id, f.detail)
        elif f.code == "constraint" and f.row_key:
            held.setdefault(f.row_key, f.detail)
        elif f.code == "constraint" and f.node_id:
            hold_node(f.node_id, f.detail)
        elif f.code == "constraint":
            gaps.append(Note(topic="rule", detail=f.detail))
        elif f.code == "skeleton_uncovered":
            cur.excluded.append(Excluded(row_key=f.row_key, reason="not placed by the curation"))
            gaps.append(Note(topic="deposit row", detail=f"{f.row_key} was not placed by the curation",
                             refs=[f.row_key]))
        elif f.code == "row_key_unknown":
            dropped.add(f.row_key)
            gaps.append(Note(topic="row", detail=f"{f.row_key} is not a deposit row and was dropped"))
        elif f.code == "reuse_without_evidence" and row is not None:
            row.reuse_uid = None
            gaps.append(Note(topic="reuse", detail=f"{f.row_key}: no evidence of the match, so it is minted new"))
    cur.rows = [r for r in cur.rows if r.row_key not in dropped]
    plan, decisions, uid_held = plan_uids(cur, evidence, context)
    known = {d.id for d in cur.decisions}
    cur.decisions += [d for d in decisions if d.id not in known]
    for h in uid_held:
        held.setdefault(h.row_key, h.reason)
    return VerifiedArtifact(curation=cur, held=[HeldRow(row_key=k, reason=v) for k, v in sorted(held.items())],
                            gaps=gaps, findings=findings, uid_plan=[p for p in plan if p.row_key not in held],
                            bounced=bounced)

#!/usr/bin/env python3
"""Remap corpus.json onto the 28 code-derived task families in FAMILIES.json.

Mechanical. Does not add, remove or edit a single variant's turns or criteria.
What it changes:

  * `families` blocks are rekeyed to the 28 family names, so block == family
    everywhere and the "16 declared, 14 blocks" divergence stops existing
  * every variant's `family` field is set to its new family
  * `family_defaults` gets one entry per family, with `hibayes_subtype` drawn
    ONLY from the canonical 22 in dmac-assistant/tools/hibayes/expected_behavior.py
    (`FAMILIES_22`), or null where no upstream label honestly applies
  * `family_floor.floors` is carried across to the new names
  * the 7 `route_gate` variants gain `no_floor`, because they assert `route` only
    and their old family had no floor; without it the remap would hand them an
    outcome floor they cannot satisfy
  * 4 variants carrying subtypes outside the canonical 22 are corrected

Run with --dry-run to see the diff summary without writing.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
NT = HERE.parent
CORPUS = NT / "corpus.json"
FAMILIES = NT / "FAMILIES.json"

# The closed upstream vocabulary. dmac-assistant/tools/hibayes/expected_behavior.py:45-71.
# `expected_behavior_rule` raises KeyError on anything else, so a value outside
# this set is a hard failure at grading time, not a soft one.
CANONICAL_22 = (
    "Edge", "Graph-Assay", "Graph-Count", "Graph-Lineage", "Graph-Study", "Memory",
    "Report-GEO", "Report-NFCORE", "Report-PRIDE", "Report-SRA", "Reporter-Summary",
    "Retrieve", "SampleTree", "Search-Attribute", "Search-Basic", "Search-MultiAssay",
    "Search-Refine", "System-Capabilities", "System-Entity", "Unsupported",
    "Write-Create", "Write-Update",
)

# family -> (hibayes_subtype | None, expected_behavior, artifact_expected, artifact_kind)
# None subtype is deliberate: those families test surface upstream never had a
# label for, so inventing one would put our rows in a stratum of size ours-only.
# Design decision D5 lets the posterior pool on `task_family` instead.
FAMILY_DEFAULTS = {
    "sample_search":              ("Search-Basic",        "AnswerDirectly",           False, "NONE_EXPECTED"),
    "sample_retrieve":            ("Retrieve",            "AnswerDirectly",           False, "NONE_EXPECTED"),
    "catalog_browse":             ("System-Entity",       "AnswerDirectly",           False, "NONE_EXPECTED"),
    "graph_traversal":            ("Graph-Count",         "AnswerDirectly",           False, "NONE_EXPECTED"),
    "lineage_tree":               ("Graph-Lineage",       "AnswerDirectly",           False, "NONE_EXPECTED"),
    "vocabulary_resolution":      ("System-Entity",       "AnswerDirectly",           False, "NONE_EXPECTED"),
    "system_capability_question": ("System-Capabilities", "AnswerDirectly",           False, "NONE_EXPECTED"),
    "followup_over_results":      ("Memory",              "UsePriorContext",          False, "NONE_EXPECTED"),
    "search_refinement":          ("Search-Refine",       "UsePriorContext",          False, "NONE_EXPECTED"),
    "retrieval_path_selection":   (None,                  "AnswerDirectly",           False, "NONE_EXPECTED"),
    "project_summary_report":     ("Reporter-Summary",    "AnswerDirectly",           False, "NONE_EXPECTED"),
    "submission_package":         ("Report-GEO",          "GenerateArtifact",         True,  "NONE_EXPECTED"),
    "artifact_delivery":          (None,                  "GenerateArtifact",         True,  "NONE_EXPECTED"),
    "pipeline_launch":            ("Report-NFCORE",       "GenerateArtifact",         True,  "NFCORE_RNASEQ_CSV"),
    "pipeline_output_reingest":   (None,                  "GenerateArtifact",         True,  "NONE_EXPECTED"),
    "batch_upload_preparation":   ("Write-Update",        "GenerateArtifact",         True,  "NONE_EXPECTED"),
    "harmonization":              ("Search-Attribute",    "AnswerDirectly",           False, "NONE_EXPECTED"),
    "entity_write":               ("Write-Create",        "RefuseUnsafeOnly",         False, "NONE_EXPECTED"),
    "writes_unsupported":         ("Write-Create",        "RefuseUnsafeOnly",         False, "NONE_EXPECTED"),
    "engine_routing":             (None,                  "AnswerDirectly",           False, "NONE_EXPECTED"),
    "route_overrides":            (None,                  "AnswerDirectly",           False, "NONE_EXPECTED"),
    "cross_session_memory":       ("Memory",              "UsePriorContext",          False, "NONE_EXPECTED"),
    "session_lifecycle":          (None,                  "AnswerDirectly",           False, "NONE_EXPECTED"),
    "turn_limits_and_failure":    ("Edge",                "AnswerDirectly",           False, "NONE_EXPECTED"),
    "turn_delivery_and_trace":    (None,                  "AnswerDirectly",           False, "NONE_EXPECTED"),
    "cc_sandbox_contract":        ("Edge",                "RefuseUnsafeOnly",         False, "NONE_EXPECTED"),
    "unsupported":                ("Unsupported",         "StateUnsupportedBoundary", False, "NONE_EXPECTED"),
    "turn_evaluation_and_retry":  (None,                  "AnswerDirectly",           False, "NONE_EXPECTED"),
}

# Old family name -> new. Carried verbatim; the floor block keys on family.
FLOOR_CARRY = {
    "search_advanced": "sample_search",
    "search_retrieve": "sample_retrieve",
    "search_tree": "lineage_tree",
    "search_parents_by_child": "lineage_tree",
    "graph_query": "graph_traversal",
    "reporting": "project_summary_report",
}
# reporting's floor also applies to the submission half it split into.
FLOOR_EXTRA = {"submission_package": "reporting"}

ORIGINAL_BLOCKS: dict = {}

SUBTYPE_FIX = {"Write-Export": "Unsupported", "Write-Delete": "Write-Update"}

EXPLICIT = {
    "green.global_count": "sample_search",
    "green.mus_ndma": "sample_search",
    "green.refine_recall": "search_refinement",
    "repro.parent_attr_aggregate": "followup_over_results",
    "repro.thin_bundle_recall": "followup_over_results",
    "repro.eof_truncation_reporter": "pipeline_launch",
    "repro.cypher_uid_dot": "lineage_tree",
    "route.ns_pipeline_launch": "pipeline_launch",
    "route.cc_reingest": "pipeline_output_reingest",
    "route.ns_advanced": "sample_search",
    "route.unrelated": "unsupported",
    "route.cc_write_investigation": "entity_write",
    "route.cc_open_ended_analysis": "unsupported",
    "route.ns_plain_study_membership": "graph_traversal",
}

ASSAY = re.compile(
    r"flow cytomet|sequenc|mass spec|luminex|elisa|\bpcr\b|imaging|crystallograph|comet|"
    r"titer|proteomic|assay|underwent|processed via|single cell|library prep|"
    r"tissue collection|cell sort", re.I)


def _fields(v):
    return [(c.get("field"), c.get("value")) for t in v["turns"] for c in t.get("pass_criteria", [])]


def _qtext(v):
    return " ".join(t.get("query", "") for t in v["turns"]).lower()


def destination(v, block):
    if v["id"] in EXPLICIT:
        return EXPLICIT[v["id"]]
    fam = v.get("family") or block
    modes = {val for f, val in _fields(v) if f == "parser_plan.mode"}
    if fam == "routing_lab":
        return "sample_search"
    if fam == "routing_graph":
        return "graph_traversal"
    if fam == "search_retrieve":
        return "sample_retrieve"
    if fam in ("search_tree", "search_parents_by_child"):
        return "lineage_tree"
    if fam == "graph_query":
        return "graph_traversal"
    if fam == "pipeline_nfcore":
        return "pipeline_launch"
    if fam == "unsupported":
        return "unsupported"
    if fam == "writes_unsupported":
        return "writes_unsupported"
    if fam == "search_advanced":
        return "graph_traversal" if ("graph_query" in modes or ASSAY.search(_qtext(v))) else "sample_search"
    if fam == "system_question":
        return ("catalog_browse"
                if re.search(r"\blist all\b|show me all (project|assay|people|protocol|investigation|sop)", _qtext(v))
                else "system_capability_question")
    if fam == "refine_and_recall":
        return "search_refinement" if "refine_last_search" in modes else "followup_over_results"
    if fam == "reporting":
        has_type = any(f == "reporter_plan.report_type" for f, _ in _fields(v))
        has_art = any((f or "").startswith("api_artifact") for f, _ in _fields(v))
        if has_type or has_art:
            return ("pipeline_launch"
                    if re.search(r"nf.?core|rnaseq|scrnaseq|samplesheet", _qtext(v))
                    else "submission_package")
        return "project_summary_report"
    raise SystemExit(f"unmapped family {fam!r} on {v['id']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    payload = json.loads(CORPUS.read_text())
    global ORIGINAL_BLOCKS
    ORIGINAL_BLOCKS = json.loads(CORPUS.read_text())["families"]
    fam_meta = {f["family"]: f for f in json.loads(FAMILIES.read_text())["families"]}
    order = list(fam_meta)

    moved = collections.Counter()
    tagged, fixed = [], []
    buckets = {name: [] for name in order}

    for block, body in payload["families"].items():
        for v in body["variants"]:
            dest = destination(v, block)
            if dest not in buckets:
                raise SystemExit(f"{v['id']} -> unknown family {dest!r}")
            if (v.get("family") or block) != dest:
                moved[dest] += 1
            v["family"] = dest

            tags = list(v.get("tags") or [])
            if "route_gate" in tags and "no_floor" not in tags:
                tags.append("no_floor")
                tagged.append(v["id"])
            v["tags"] = tags

            st = v.get("hibayes_subtype")
            if st in SUBTYPE_FIX:
                fixed.append((v["id"], st, SUBTYPE_FIX[st]))
                v["hibayes_subtype"] = SUBTYPE_FIX[st]
            elif st is not None and st not in CANONICAL_22:
                raise SystemExit(f"{v['id']}: subtype {st!r} outside the canonical 22")

            buckets[dest].append(v)

    payload["families"] = {
        name: {"description": fam_meta[name]["description"], "variants": buckets[name]}
        for name in order
    }

    defaults = {
        "_note": payload["family_defaults"].get("_note", ""),
        "_canonical_22": (
            "hibayes_subtype is NOT ours to invent. It is a foreign key into "
            "dmac-assistant/tools/hibayes/expected_behavior.py FAMILIES_22, kept so these rows "
            "concatenate with upstream runs (design decision D5). expected_behavior_rule raises "
            "KeyError outside those 22. A null subtype is deliberate, not missing: that family "
            "tests surface upstream has no label for, and the posterior pools it on task_family."),
    }
    for name in order:
        st, eb, ae, ak = FAMILY_DEFAULTS[name]
        defaults[name] = {"hibayes_subtype": st, "expected_behavior": eb,
                          "artifact_expected": ae, "artifact_kind": ak}
    payload["family_defaults"] = defaults

    # ── route_policy ─────────────────────────────────────────────────────────
    # Keyed on family like the floor block, but it cannot be carried name-to-name:
    # lineage_tree inherits BOTH search_tree (an NS|CC alternation, because the
    # operator answered EITHER) and search_parents_by_child (eq nextseek_query).
    # Picking one silently rewrites the other. So compute each variant's EFFECTIVE
    # rule under the old policy, give each new family the modal rule among its
    # members, and pin every member that differs with a per-id override. Overrides
    # already win over the family rule, so the result is exactly the old rule for
    # every variant, and provably so.
    rp = payload["route_policy"]
    old_rules, old_overrides = rp.get("families", {}), dict(rp.get("overrides", {}))
    effective = {}
    for block, body in ORIGINAL_BLOCKS.items():
        for v in body["variants"]:
            fam = v.get("family") or block
            if v["id"] in old_overrides:
                continue  # already pinned by id, survives untouched
            if fam in old_rules:
                effective[v["id"]] = old_rules[fam]

    new_rules, pinned = {}, 0
    for name in order:
        members = [v["id"] for v in buckets[name] if v["id"] in effective]
        if not members:
            continue
        tally = collections.Counter(json.dumps(effective[i], sort_keys=True) for i in members)
        modal = json.loads(tally.most_common(1)[0][0])
        new_rules[name] = modal
        for i in members:
            if effective[i] != modal:
                old_overrides[i] = effective[i]
                pinned += 1
    rp["families"] = new_rules
    rp["overrides"] = old_overrides
    rp["_2026_08_04_remap"] = (
        "Carried across the 28-family remap by effective rule, not by family name: "
        "lineage_tree inherits search_tree's NS|CC alternation and "
        "search_parents_by_child's eq nextseek_query, which cannot both be the "
        f"family default. {pinned} variant(s) whose rule differs from their new "
        "family's modal rule are pinned by id, so every variant keeps the exact "
        "assertion it had before.")

    old_floors = payload["family_floor"]["floors"]
    new_floors = {}
    for old, new in FLOOR_CARRY.items():
        if old in old_floors:
            new_floors.setdefault(new, old_floors[old])
    for new, src in FLOOR_EXTRA.items():
        if src in old_floors:
            new_floors[new] = old_floors[src]
    payload["family_floor"]["floors"] = {n: new_floors[n] for n in order if n in new_floors}

    counts = collections.Counter(v["family"] for b in payload["families"].values() for v in b["variants"])
    active = collections.Counter(v["family"] for b in payload["families"].values()
                                 for v in b["variants"] if v.get("status") == "active")

    print(f"{'family':30} {'all':>4} {'active':>7} {'floor':>6} {'subtype':>20}")
    for n in order:
        st = FAMILY_DEFAULTS[n][0] or "-"
        print(f"{n:30} {counts.get(n,0):4} {active.get(n,0):7} "
              f"{'yes' if n in payload['family_floor']['floors'] else '-':>6} {st:>20}")
    print(f"\ntotal {sum(counts.values())} variants, {sum(active.values())} active")
    print(f"reassigned: {sum(moved.values())}   no_floor added: {len(tagged)}   subtypes corrected: {len(fixed)}")
    for i, o, n in fixed:
        print(f"   {i}: {o} -> {n}")

    if a.dry_run:
        print("\nDRY RUN, nothing written")
        return
    CORPUS.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"\nwrote {CORPUS}")


if __name__ == "__main__":
    sys.exit(main())

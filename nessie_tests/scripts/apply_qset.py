"""Apply the 2026-08-06 question-set rework to nessie_tests/corpus.json.

Reads a declarative spec (qset.json) and edits corpus.json in place:

  select   -> is_bayesian: true
  deselect -> is_bayesian: false + `_deselected_2026_08_06_qset` reason
  retire   -> status: "retired" + retirement record (definition kept)
  edit     -> replace turns and/or criteria, id preserved, `_edited_2026_08_06_qset`
  add      -> a brand-new variant appended to its declared family

Idempotent: running twice produces the same file.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

CORPUS = Path(sys.argv[1] if len(sys.argv) > 1 else "nessie_tests/corpus.json")
SPEC = Path(sys.argv[2] if len(sys.argv) > 2 else
            "/tmp/claude-1000/-home-cdemu-code-dmac-docker/"
            "7c6b89bb-13b7-48d6-8ccd-7b0eda6e02a0/scratchpad/qset.json")

TEMPLATE = {
    "artifact_expected": None, "artifact_kind": None, "expected_behavior": None,
    "hibayes_subtype": None, "origin": "overlay", "requires_env": [],
    "retirement": None, "status": "active", "is_bayesian": True,
    "tags": ["nessie", "full"],
}


def main() -> int:
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    index = {v["id"]: (fam, v)
             for fam, block in payload["families"].items()
             for v in block["variants"]}

    def need(vid):
        if vid not in index:
            raise SystemExit(f"unknown variant id: {vid}")
        return index[vid][1]

    for vid in spec.get("select", []):
        v = need(vid)
        v["is_bayesian"] = True
        v.pop("_deselected_2026_08_06_qset", None)

    for vid, reason in spec.get("deselect", {}).items():
        v = need(vid)
        v["is_bayesian"] = False
        v["_deselected_2026_08_06_qset"] = reason

    for vid, reason in spec.get("retire", {}).items():
        v = need(vid)
        v["status"] = "retired"
        v["is_bayesian"] = False
        v["retirement"] = {
            "decided_by": "2026-08-06 question-set design review",
            "family": v["family"], "reason": reason,
            "retired_on": "2026-08-06", "source": v.get("origin", "base"),
        }

    for vid, patch in spec.get("edit", {}).items():
        v = need(vid)
        if "turns" in patch:
            v["turns"] = patch["turns"]
        if "name" in patch:
            v["name"] = patch["name"]
        v["is_bayesian"] = patch.get("is_bayesian", True)
        v["_edited_2026_08_06_qset"] = patch["why"]

    for new in spec.get("add", []):
        fam = new["family"]
        if fam not in payload["families"]:
            raise SystemExit(f"no such family block: {fam}")
        if new["id"] in index:
            # idempotent re-apply: replace the previously added body
            _, existing = index[new["id"]]
            existing.clear()
            existing.update({**TEMPLATE, **new})
            continue
        payload["families"][fam]["variants"].append({**TEMPLATE, **new})

    # `ClarifyIfAmbiguous` is not decoration: a variant whose reply criterion
    # DEMANDS a clarification but whose behaviour says GenerateArtifact scores
    # correct behaviour as failure on both arms. `test_the_clarify_behaviour_is_
    # actually_used` enforces the pairing, so the label is derived from the
    # criteria rather than remembered by hand.
    for vid in spec.get("clarify", []):
        need(vid)["expected_behavior"] = "ClarifyIfAmbiguous"

    # Promotion out of the atlas set is an ORIGIN flip, and it has to happen the
    # moment a generated variant is read, ground-truthed and put into the paid
    # selection. `origin: "atlas"` becomes the `atlas` tag at load time, and
    # `test_the_atlas_set_is_additive_and_inert` asserts of that set that nothing
    # in it is `is_bayesian` and that every member is single-turn -- both of which
    # a reviewed, repaired, selected variant breaks. Leaving the origin alone would
    # not be a smaller change; it would be a false claim that nobody has read it.
    for vid in set(spec.get("select", [])) | set(spec.get("edit", {})):
        v = need(vid)
        if v.get("origin") == "atlas" or "atlas" in (v.get("tags") or []):
            v["origin"] = "overlay"
            # The TAG has to go with the origin. `_to_variants` unions the two, so
            # leaving `"atlas"` in `tags` keeps the variant inside the atlas set at
            # load time no matter what `origin` says -- which is how the first
            # attempt at this flip silently did nothing. `_atlas` (the machine
            # provenance block) is deliberately NOT removed: it is a true record of
            # where the question came from.
            v["tags"] = [t for t in (v.get("tags") or []) if t != "atlas"]
            v["_promoted_2026_08_06_qset"] = (
                "read, ground-truthed and selected for the 2026-08-06 paired study; "
                "origin flipped atlas -> overlay, which is what promotion IS")

    CORPUS.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    sel = [v["id"] for b in payload["families"].values() for v in b["variants"]
           if v.get("is_bayesian") and v.get("status") == "active"]
    print(f"selection: {len(sel)} variants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

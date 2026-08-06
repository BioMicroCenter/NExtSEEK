"""Collision / gradability checks over the SELECTED set of nessie_tests/corpus.json.

Enforces the three design rules of the 2026-08-06 question set:

  1. no two selected variants share a normalised query string (anti-priming)
  2. every selected variant asserts something SUBSTANTIVE on `last_reply`
     (the only field that survives forcing on a container_cc arm)
  3. no two selected variants assert the same numeric ground-truth value
     (a repeated number is a repeated question wearing a different hat)
"""
from __future__ import annotations
import json, re, sys, collections
from pathlib import Path

CORPUS = Path(sys.argv[1] if len(sys.argv) > 1 else "nessie_tests/corpus.json")
payload = json.loads(CORPUS.read_text(encoding="utf-8"))
sel = [v for b in payload["families"].values() for v in b["variants"]
       if v.get("is_bayesian") and v.get("status") == "active"]


def norm(q):
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", q.lower())).strip()


print(f"selected: {len(sel)}")
print("by family:")
for fam, n in sorted(collections.Counter(v["family"] for v in sel).items()):
    print(f"  {fam:<28} {n}")

# 1. duplicate queries
seen = collections.defaultdict(list)
for v in sel:
    for t in v["turns"]:
        seen[norm(t["query"])].append(v["id"] + "/" + t["label"])
dupes = {k: w for k, w in seen.items() if len(w) > 1}
print(f"\n[1] duplicate normalised queries among selected: {len(dupes)}")
for k, w in dupes.items():
    print(f"    {w}  <- {k[:70]}")

# 2. substantive last_reply criterion
TRIVIAL_OPS = {"nonempty"}
weak = []
for v in sel:
    crits = [c for t in v["turns"] for c in t["pass_criteria"]]
    good = [c for c in crits
            if c["field"] == "last_reply" and c["op"] not in TRIVIAL_OPS]
    art = [c for c in crits if str(c["field"]).startswith("api_artifact.")]
    if not good and not art:
        weak.append(v["id"])
print(f"\n[2] selected variants with NO substantive reply/artifact assertion: {len(weak)}")
for vid in weak:
    print("    " + vid)

# 3. repeated asserted numbers
nums = collections.defaultdict(list)
for v in sel:
    for t in v["turns"]:
        for c in t["pass_criteria"]:
            if c["field"] != "last_reply" or c["op"] != "matches_re":
                continue
            for m in re.findall(r"\d[\d,?]{2,}", str(c["value"])):
                nums[m.replace(",?", "").replace(",", "")].append(v["id"])
rep = {k: sorted(set(w)) for k, w in nums.items() if len(set(w)) > 1}
print(f"\n[3] ground-truth numbers asserted by more than one variant: {len(rep)}")
for k, w in sorted(rep.items()):
    print(f"    {k}: {w}")

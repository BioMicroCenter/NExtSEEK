"""Turn the hand-authored question set (qset_data.py) into qset.json.

Modes
-----
keep    the question text is UNCHANGED from corpus.json. The builder asserts
        that byte-for-byte and refuses otherwise, so a silent reword can never
        pass itself off as a preserved grade. Existing non-`last_reply`
        criteria are kept; existing `last_reply` criteria are replaced.
reword  the id is preserved but the text changed -> the human grade for that id
        is a comparison baseline, not a valid pre-fill.
new     a brand-new id.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from qset_data import QUESTIONS, DESELECT, RETIRE, CLARIFY  # noqa: E402

CORPUS = Path("nessie_tests/corpus.json")
OUT = Path(__file__).parent / "qset.json"

payload = json.loads(CORPUS.read_text(encoding="utf-8"))
index = {v["id"]: v for b in payload["families"].values() for v in b["variants"]}

spec = {"select": [], "deselect": DESELECT, "retire": RETIRE, "edit": {}, "add": [],
        "clarify": CLARIFY, "doc": {}}
errors = []
seen_ids = set()

for q in QUESTIONS:
    vid, mode = q["id"], q["mode"]
    if vid in seen_ids:
        errors.append(f"{vid}: listed twice")
    seen_ids.add(vid)
    spec["doc"][vid] = {k: q[k] for k in ("family", "mode", "gt", "how", "tests")}

    if mode == "new":
        if vid in index:
            errors.append(f"{vid}: mode=new but the id already exists")
            continue
        spec["add"].append({
            "id": vid, "family": q["family"], "name": q["name"],
            "turns": q["turns"], "_why": q["gt"] + " || VERIFIED: " + q["how"]
                     + " || TESTS: " + q["tests"],
            "_added_2026_08_06_qset": "authored for the 2026-08-06 question set",
        })
        continue

    if vid not in index:
        errors.append(f"{vid}: mode={mode} but no such id in corpus.json")
        continue
    cur = index[vid]

    if mode == "select":
        # Text AND criteria untouched. Used only where the existing assertion is
        # already stronger than anything this pass would write -- the two
        # `*_must_confirm_first` guards and the investigation-create guard, which
        # tests/test_write_refusal_coverage.py exercises against 39 hand-written
        # replies apiece. Rewriting those would throw away that coverage.
        if [t["query"] for t in cur["turns"]] != [t["query"] for t in q["turns"]]:
            errors.append(f"{vid}: select, but the recorded text does not match corpus.json")
            continue
        spec["select"].append(vid)
        continue

    if mode == "keep":
        if len(cur["turns"]) != len(q["turns"]):
            errors.append(f"{vid}: keep, but turn count {len(cur['turns'])} != {len(q['turns'])}")
            continue
        turns = []
        for old, new in zip(cur["turns"], q["turns"]):
            if old["query"] != new["query"]:
                errors.append(f"{vid}: keep, but text changed\n  was: {old['query']!r}\n  now: {new['query']!r}")
                break
            kept = [c for c in old["pass_criteria"] if c["field"] != "last_reply"]
            turns.append({"label": old["label"], "query": old["query"],
                          "pass_criteria": kept + new["pass_criteria"]})
        else:
            spec["edit"][vid] = {
                "turns": turns, "is_bayesian": True,
                "why": ("CRITERIA ONLY -- question text unchanged, so the existing "
                        "human grade stays a valid reference for this id. "
                        + q["gt"] + " || VERIFIED: " + q["how"]),
            }
        continue

    if mode == "reword":
        spec["edit"][vid] = {
            "turns": q["turns"], "name": q["name"], "is_bayesian": True,
            "why": ("TEXT CHANGED -- the prior human grade for this id is a "
                    "comparison baseline, not a valid pre-fill. "
                    + q["gt"] + " || VERIFIED: " + q["how"] + " || TESTS: " + q["tests"]),
        }
        continue

    errors.append(f"{vid}: unknown mode {mode!r}")

if errors:
    print("REFUSING TO BUILD:\n" + "\n".join("  " + e for e in errors))
    raise SystemExit(1)

OUT.write_text(json.dumps(spec, indent=1), encoding="utf-8")
print(f"questions: {len(QUESTIONS)}  (new {len(spec['add'])}, "
      f"edit {len(spec['edit'])})  deselect {len(DESELECT)}  retire {len(RETIRE)}")

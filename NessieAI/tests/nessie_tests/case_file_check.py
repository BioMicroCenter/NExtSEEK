"""Check a `--cases` file before a paid run pays for its mistakes.

A cases file (a probe) is written by hand, often by an agent, right before a paid run. The
harness's own loader (`corpus.load_case_file`) validates the criteria's shape and nothing else,
and its models ignore unknown keys, so a misspelled `pass_criteria` loads as a turn with no
criteria and the case is then counted a real failure for asserting nothing. The checks that used
to be done by eye before a run (unique ids, families that exist, `include_ids` that resolve, a
`_measure` block that matches its criteria, no writing family on production, no repeated
question) are here, in one pass that names every problem:

    python -m NessieAI.tests.nessie_tests.case_file_check <file> [--instance dev|prod|local] [--diverse]

Exit 0 when there is no error (warnings are printed), 2 when there is at least one.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from NessieAI.tests.nessie_tests import corpus as C
from NessieAI.tests.nessie_tests.evaluate import is_unobservable
from NessieAI.tests.nessie_tests.scripts.pin_probe_truths import number_pattern

# Families and ids that write or launch: never on production (the launch skill's rule).
PROD_FORBIDDEN_FAMILIES = ("entity_write", "pipeline_launch", "pipeline_output_reingest",
                           "batch_upload_preparation")
PROD_FORBIDDEN_ID_PREFIXES = ("write.",)

TOP_KEYS = {"families", "include_ids", "version"}
FAMILY_KEYS = {"description", "variants"}
VARIANT_KEYS = {"family", "id", "name", "tags", "requires_env", "turns"}
TURN_KEYS = {"label", "query", "pass_criteria"}
CRITERION_KEYS = {"field", "op", "value"}
# `eq` with a null value is a real assertion ("no suggestions were offered"), so it is not here.
VALUE_OPS = {"contains", "gte", "lte", "mentions", "matches_re"}


@dataclass
class Result:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", (q or "").lower())).strip()


def _unknown(keys, allowed, where: str) -> list[str]:
    bad = sorted(k for k in keys if k not in allowed and not str(k).startswith("_"))
    return [f"{where}: unknown key(s) {bad} (a typo here is dropped silently by the loader)"] if bad else []


def _shape(spec: dict, r: Result) -> None:
    r.errors += _unknown(spec, TOP_KEYS, "top level")
    for fam, block in (spec.get("families") or {}).items():
        if not isinstance(block, dict):
            r.errors.append(f"families.{fam}: not an object")
            continue
        r.errors += _unknown(block, FAMILY_KEYS, f"families.{fam}")
        for v in block.get("variants") or []:
            vid = v.get("id", "?")
            r.errors += _unknown(v, VARIANT_KEYS, vid)
            for t in v.get("turns") or []:
                r.errors += _unknown(t, TURN_KEYS, f"{vid}/{t.get('label', '?')}")
                for c in t.get("pass_criteria") or []:
                    if isinstance(c, dict):
                        r.errors += _unknown(c, CRITERION_KEYS, f"{vid}/{t.get('label', '?')} criterion")


def _criteria(v, r: Result, prod: bool) -> None:
    observable = 0
    for t in v.turns:
        if not t.pass_criteria:
            r.warnings.append(f"{v.id}/{t.label}: no criteria on this turn")
        for c in t.pass_criteria:
            where = f"{v.id}/{t.label} {c.field} {c.op}"
            if c.op in VALUE_OPS and c.value is None:
                r.errors.append(f"{where}: this op needs a value")
            if c.op in ("gte", "lte") and not isinstance(c.value, (int, float)):
                r.errors.append(f"{where}: gte and lte compare numbers, got {c.value!r}")
            if c.op == "matches_re":
                try:
                    re.compile(str(c.value))
                except re.error as e:
                    r.errors.append(f"{where}: the regex does not compile ({e})")
            if not is_unobservable(c.field, c.op):
                observable += 1
    if observable == 0:
        r.errors.append(f"{v.id}: no criterion it carries can be observed over HTTP; the harness would "
                        "count the case a real failure for asserting nothing")


def _measure(spec: dict, by_id: dict, r: Result) -> None:
    for cid, entry in (spec.get("_measure") or {}).items():
        if cid not in by_id:
            r.errors.append(f"_measure.{cid}: not a case in this file")
            continue
        if not isinstance(entry, dict) or "locals" not in entry or "cypher" not in entry:
            r.errors.append(f"_measure.{cid}: needs locals and cypher")
            continue
        cy = entry["cypher"] if isinstance(entry["cypher"], list) else [entry["cypher"]]
        if any(re.search(r"(^|\n)\s*--", str(s)) for s in cy):
            r.errors.append(f"_measure.{cid}: a `--` comment line: cypher-shell rejects it (use //)")
        locs = entry["locals"] if isinstance(entry["locals"], list) else [entry["locals"]]
        try:
            groups = [[int(x)] for x in locs] if entry.get("mode") == "each" else [[int(x) for x in locs]]
        except (TypeError, ValueError):
            r.errors.append(f"_measure.{cid}: locals must be whole numbers, got {locs!r}")
            continue
        patterns = {c.value for t in by_id[cid].turns for c in t.pass_criteria if c.op == "matches_re"}
        for g in groups:
            if number_pattern(g) not in patterns:
                r.errors.append(f"_measure.{cid}: no criterion carries the pattern for {g} (the pin script "
                                "would refuse it; a case that states several numbers needs \"mode\": \"each\")")


def check(path, *, instance: str | None = None, diverse: bool = False) -> Result:
    r = Result()
    p = Path(path)
    try:
        spec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        r.errors.append(f"{p}: {e}")
        return r
    if not isinstance(spec, dict):
        r.errors.append("a cases file is a JSON object")
        return r
    _shape(spec, r)
    try:
        include, inline = C.load_case_file(p)
    except Exception as e:  # the harness's own validation, verbatim
        r.errors.append(f"the harness loader refused it: {e}")
        return r
    corpus_variants = C.merged()
    corpus_ids = {v.id for v in corpus_variants}
    corpus_families = {v.family for v in corpus_variants}
    try:
        selected = C.select_cases(corpus_variants, include, inline)
    except ValueError as e:
        r.errors.append(str(e))
        selected = list(inline)

    ids = [v.id for v in inline]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        r.errors.append(f"an id appears twice: {dup}")
    for i in sorted(set(ids) & set(include)):
        r.errors.append(f"{i} is both in include_ids and defined inline: it would run twice")
    for i in sorted(set(ids) & corpus_ids):
        r.warnings.append(f"{i} redefines a corpus case: the inline definition wins for this run")

    prod = instance == "prod"
    for v in inline:
        if v.family not in corpus_families:
            (r.errors if diverse else r.warnings).append(
                f"{v.id}: family {v.family!r} is not a corpus family")
        bad_tags = {"known_fail", "retired"} & set(v.tags)
        if bad_tags:
            r.warnings.append(f"{v.id}: tags {sorted(bad_tags)} (copied from the corpus? nothing on this path "
                              "reads them, and the case runs and bills)")
        _criteria(v, r, prod)
    for v in selected:
        if prod and (v.family in PROD_FORBIDDEN_FAMILIES or v.id.startswith(PROD_FORBIDDEN_ID_PREFIXES)):
            r.errors.append(f"{v.id} ({v.family}) writes or launches: never on prod")

    seen: dict[str, str] = {}
    for v in inline:
        for t in v.turns:
            key = _norm(t.query)
            if key in seen and seen[key] != v.id:
                (r.errors if diverse else r.warnings).append(
                    f"{v.id}/{t.label} repeats the question of {seen[key]}: {t.query[:70]!r}")
            seen.setdefault(key, v.id)
    _measure(spec, {v.id: v for v in inline}, r)
    if spec.get("_measure") and instance in ("dev", "prod"):
        r.warnings.append(f"a _measure block: re-pin it on {instance} before the run")

    r.summary = {"cases": len(selected), "inline": len(inline), "include_ids": len(include),
                 "turns": sum(len(v.turns) for v in selected),
                 "families": dict(sorted(Counter(v.family for v in selected).items())),
                 "measured": len(spec.get("_measure") or {})}
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--instance", choices=["dev", "prod", "local"])
    ap.add_argument("--diverse", action="store_true",
                    help="a repeated question and a family outside the corpus are errors")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)
    r = check(a.path, instance=a.instance, diverse=a.diverse)
    if a.json:
        print(json.dumps({"errors": r.errors, "warnings": r.warnings, "summary": r.summary}, indent=2))
    else:
        for w in r.warnings:
            print(f"WARN: {w}")
        for e in r.errors:
            print(f"ERROR: {e}")
        s = r.summary
        if s:
            print(f"{s['cases']} cases ({s['inline']} inline, {s['include_ids']} by id), {s['turns']} turns, "
                  f"{len(s['families'])} families, {s['measured']} measured")
        print("OK" if not r.errors else f"{len(r.errors)} error(s)")
    return 2 if r.errors else 0


if __name__ == "__main__":
    sys.exit(main())

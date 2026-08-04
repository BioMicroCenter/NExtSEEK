#!/usr/bin/env python3
"""Render the split grading report for a paired (`--bayesian`) run.

Inputs
------
  <run>/<MANIFEST_NAME>       the paired record   (nessie_tests.bayes_manifest)
  <run>/artifacts/<id>/<arm>/ the collected evidence      (nessie_tests.collect)
  <run>/stage_c.json          the LLM verdicts, OPTIONAL  (dmac-assistant Stage C)
  nessie_tests/corpus.json    the question each variant asked

The paired manifest is named through `bayes_manifest.MANIFEST_NAME` and never
spelled out. A normal run's `manifest.json` is a different schema that validates
as an EMPTY `BayesManifest` rather than raising, and two reproduced data-loss
defects on this branch came from exactly that collision; a filename literal here
would survive a rename of the constant and quietly reintroduce it.

Output is ONE self-contained HTML file that a human grades every answer in,
BLIND, and then downloads as `grades.json`.

BLIND IS THE FEATURE, NOT THE STYLING. The operator chose to grade every answer
twice -- once by Stage C, once by hand -- so the two can be compared and the LLM
grader validated on this corpus. If the page showed Stage C's verdict beside the
transcript while the human graded, agreement would be inflated by anchoring and
the disagreement set, which is the entire reason both graders exist, would stop
meaning anything. So the verdict is embedded in the page (`const LLM`) and NEVER
rendered for a row until that row carries a human grade. `test_bayes_report.py`
pins it by asserting that no verdict string appears outside a `<script>` block;
anything this file emits into the static markup would break that, which is why
every one of these literals is rendered client-side.

grades.json is a CONTRACT, not a convenience. `merge_grades.py` reads

    {"<variant_id>::<arm>": {"grade": "pass"|"fail", "note": str, "ts": iso}}

with the same `<id>::<arm>` key form Stage C uses -- `export.stage_b_query_id`,
not a separator restated here. `GRADE_CONTRACT` in the page is that agreement
written down, and the page's own key builder reads it, so the two cannot drift.

EXCLUDED ARMS ARE SHOWN AND NOT GRADED. `export._exclusion` keeps provider
outages, never-executed and deadline-aborted arms out of the runtime CSVs, so
Stage C never grades them and `merge_grades` never asks for a human grade for
one. Grading such an arm is wasted effort out of a 300-grade budget -- but
DROPPING it makes a run that failed to observe 40 arms look like a complete
pass. So the arm is rendered, banded with its cause, its grade controls removed,
and counted in its own tile outside the progress denominator. The predicate is
imported rather than restated: a row the model never scores must not be a row the
human grades, and the only way to guarantee that is one implementation.

Usage
-----
    python build_bayes_report.py --run ./nessie_bayes_out --out ./report_bayes.html
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

# This script ships INSIDE nessie_tests (…/nessie_tests/output-skill-bayesian/
# scripts/), so the repo root is four levels up. Put it on the path BEFORE the
# nessie_tests imports: a subprocess launched as `python build_bayes_report.py`
# gets the script's own directory as sys.path[0], never the repo root.
ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nessie_tests import bayes_manifest, export  # noqa: E402

TPL_DEFAULT = pathlib.Path(__file__).resolve().parent.parent / "templates" / "report_bayes.html.tpl"
CORPUS_DEFAULT = pathlib.Path(__file__).resolve().parents[2] / "corpus.json"

ARMS = export.ARMS

# Enough of a turn to judge the answer against, not a full event log. A CC turn
# emits one `search_started` per tool call and a long one runs to hundreds; the
# whole page holds 254 arms, and a grader scrolling past 300 Read/Bash lines is
# not reading the trace at all.
MAX_EVENTS_PER_TURN = 60
MAX_DETAIL_CHARS = 220
MAX_ARTIFACT_PATHS = 40

STANDALONE = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
              '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
              "{head}\n</head>\n<body>\n{body}\n</body>\n</html>\n")


def js(obj) -> str:
    """A JSON literal safe to paste inside a `<script>` block.

    `<`, `>` and `&` are escaped so no payload can close the script element --
    a CC reply quoting HTML is not hypothetical.

    `;` is escaped for a subtler reason: every reader of this page (the tests
    here, and any rehydrator written later) recovers a literal with a non-greedy
    `const NAME = (\\[.*?\\]|\\{.*?\\});` match, which stops at the first `];` or
    `};`. A reply containing `];` -- one line of JavaScript in an answer -- would
    truncate the whole literal into invalid JSON. `;` never occurs in JSON
    structure, only inside strings, so escaping it globally makes the terminator
    unambiguous and `json.loads` restores the character unchanged.
    """
    return (json.dumps(obj, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace(";", "\\u003b"))


def lj(path):
    return json.loads(pathlib.Path(path).read_bytes().decode("utf-8", errors="replace"))


def _flatten_corpus(payload) -> dict:
    out = {}
    for fam in (payload.get("families") or {}).values():
        for v in fam.get("variants") or []:
            out[v["id"]] = v
    return out


def _turn_defs(variant, group) -> list[dict]:
    """The declared turns of a variant: what was actually asked, in order.

    A consistency group keeps its queries in `queries` rather than in `turns`;
    both shapes reach the page as `{label, query}` so the template has one.
    """
    if variant and variant.get("turns"):
        return [{"label": t.get("label") or f"turn {i + 1}", "query": t.get("query") or ""}
                for i, t in enumerate(variant["turns"])]
    if group:
        return [{"label": f"q{i + 1}", "query": q}
                for i, q in enumerate(group.get("queries") or [])]
    return []


def _clip(text, n=MAX_DETAIL_CHARS) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= n else text[:n] + "…"


def _event_line(ev) -> dict | None:
    """One progress event as `{e, s, d}`, or None when it carries nothing to read.

    `query_complete` is dropped here and handled as the arm's reply: rendering it
    twice is how the one thing a grader must read gets buried.
    """
    name = ev.get("event") or ""
    if name in ("query_complete", "search_complete"):
        return None
    data = ev.get("data") or {}
    if not isinstance(data, dict):
        return {"e": name, "s": "", "d": _clip(data)}
    src = data.get("source") or data.get("route") or ""
    detail = (data.get("description") or data.get("query") or data.get("message")
              or data.get("reasoning") or data.get("tool") or data.get("status") or "")
    return {"e": name, "s": str(src), "d": _clip(detail)}


def _reply_of(row) -> str:
    """The answer this turn produced.

    `result.reply` first because that is the endpoint's own final field; the
    `query_complete` event is the fallback for a row whose result never landed.
    """
    result = row.get("result")
    if isinstance(result, dict) and result.get("reply"):
        return str(result["reply"])
    for ev in reversed(row.get("progress") or []):
        data = ev.get("data") or {}
        if isinstance(data, dict) and data.get("reply"):
            return str(data["reply"])
    return ""


def _trace(rows, turn_defs) -> list[dict]:
    """One record per collected turn, paired positionally with the declared turns.

    Positional, not by text: `collect` writes an arm's rows in turn order and the
    corpus declares them in the same order, so the index IS the join. Matching on
    query text instead would mis-assign the 25 `refine_and_recall` variants whose
    follow-ups repeat across cases.
    """
    out = []
    for i, row in enumerate(rows):
        declared = turn_defs[i] if i < len(turn_defs) else {}
        events = [e for e in (_event_line(ev) for ev in (row.get("progress") or [])) if e]
        out.append({
            "label": declared.get("label") or f"turn {i + 1}",
            "query": declared.get("query") or "",
            "status": row.get("status") or "",
            "events": events[:MAX_EVENTS_PER_TURN],
            "dropped": max(0, len(events) - MAX_EVENTS_PER_TURN),
            "reply": _reply_of(row),
        })
    return out


def _artifacts(run, pair_id, arm) -> dict:
    """`{count, observed, reason, paths}` -- the count from `export`, the names
    from disk.

    The COUNT is `export.artifact_evidence`, the same number the HiBayes row
    carries, so the page and the CSV can never disagree about how much an arm
    produced. The listing is only so a grader can see WHAT it produced.
    """
    art_root = run / "artifacts"
    evidence = export.artifact_evidence(art_root, pair_id, arm)
    names: list[str] = []
    if arm == "cc":
        listing = art_root / pair_id / arm / "artifacts.json"
        if listing.is_file():
            published = lj(listing).get("artifacts") or []
            names = [a if isinstance(a, str) else json.dumps(a) for a in published]
    else:
        for base in evidence["paths"]:
            if base.is_dir():
                names += [str(p.relative_to(base)) for p in sorted(base.rglob("*"))
                          if p.is_file()]
    return {"count": evidence["count"], "observed": evidence["observed"],
            "reason": evidence["reason"], "paths": names[:MAX_ARTIFACT_PATHS],
            "more": max(0, len(names) - MAX_ARTIFACT_PATHS)}


def _observations(entry) -> list[dict]:
    """The criteria that SURVIVED evaluation.

    A `skipped` observation records a criterion the harness could not evaluate at
    all; it is stored with `passed=True` (an unevaluable criterion is not evidence
    either way), so showing it beside a real pass would read as corroboration for
    an answer nothing checked. The count of them is carried instead.
    """
    out = []
    for obs in entry.observations:
        if obs.skipped:
            continue
        out.append({"turn": obs.turn, "field": obs.field, "op": obs.op,
                    "expected": _clip(obs.expected), "observed": _clip(obs.observed),
                    "passed": bool(obs.passed), "reason": _clip(obs.reason)})
    return out


def _arm_payload(entry, *, run, pair_id, arm, turn_defs) -> dict:
    rows = export._turn_rows(run / "artifacts", pair_id, arm)
    verdict = export._exclusion(entry, rows)
    trace = _trace(rows, turn_defs)
    # The LAST turn's reply. A follow-up's answer is the one under grade; turn 1
    # of a refine_and_recall case answered a different question.
    reply = next((t["reply"] for t in reversed(trace) if t["reply"]), "")
    return {
        "status": entry.status,
        "route": entry.route,
        "engine": entry.engine,
        "elapsed": entry.elapsed_s,
        "cost": entry.cost,
        "outage": entry.outage,
        "reason": _clip(entry.reason, 400),
        "failed": list(entry.failed_criteria),
        "turns": len(entry.task_ids) or len(rows),
        "reply": reply,
        "trace": trace,
        "artifacts": _artifacts(run, pair_id, arm),
        "observations": _observations(entry),
        "skipped_criteria": sum(1 for o in entry.observations if o.skipped),
        "excluded": None if verdict is None else {"cause": verdict[0], "reason": verdict[1]},
    }


def build(run: pathlib.Path, corpus_path: pathlib.Path, template: pathlib.Path,
          *, standalone: bool = True) -> tuple[str, dict]:
    manifest = bayes_manifest.read_bayes_manifest(run)
    if manifest is None:
        extra = (f"  ({run / 'manifest.json'} exists -- that is a NORMAL run's manifest "
                 f"and a DIFFERENT schema: it validates as an EMPTY paired manifest "
                 f"rather than raising, so reading it here would report a run of "
                 f"nothing.)\n" if (run / "manifest.json").is_file() else "")
        sys.exit(f"no {bayes_manifest.MANIFEST_NAME} in {run}\n{extra}"
                 f"Run the suite with --bayesian, then nessie_tests/collect.py.")
    if not manifest.pairs:
        sys.exit(f"{run / bayes_manifest.MANIFEST_NAME} records no pairs")

    corpus_raw = lj(corpus_path) if pathlib.Path(corpus_path).is_file() else {}
    variants = _flatten_corpus(corpus_raw)
    groups = {g["id"]: g for g in corpus_raw.get("consistency_groups") or []}
    if not variants:
        print(f"WARNING: no corpus at {corpus_path}; the page will carry ids without "
              f"the questions they asked", file=sys.stderr)

    llm_path = run / "stage_c.json"
    llm = lj(llm_path) if llm_path.is_file() else {}

    pairs, arms_seen, gradable = [], 0, 0
    by_cause: dict[str, int] = {}
    for pair in manifest.pairs:
        turn_defs = _turn_defs(variants.get(pair.id), groups.get(pair.id))
        record = {"id": pair.id, "family": pair.family,
                  "subtype": pair.hibayes_subtype, "turns": turn_defs}
        for arm in ARMS:
            entry = getattr(pair, arm)
            if entry is None:
                # A half-written pair: arms are persisted as they complete so a
                # paid run can resume. An arm that never ran is not a failed one
                # and is not a gradable one either.
                record[arm] = None
                continue
            arms_seen += 1
            payload = _arm_payload(entry, run=run, pair_id=pair.id, arm=arm,
                                   turn_defs=turn_defs)
            if payload["excluded"] is None:
                gradable += 1
            else:
                cause = payload["excluded"]["cause"]
                by_cause[cause] = by_cause.get(cause, 0) + 1
            record[arm] = payload
        pairs.append(record)

    excluded = arms_seen - gradable
    run_meta = manifest.run_meta or {}
    run_id = str(run_meta.get("run_id") or run_meta.get("started_at") or run.name)
    meta = {
        "run_id": run_id,
        "title": f"nessie paired run: blind grading ({run_id})",
        "runline": [f"pairs {len(pairs)}", f"arms {arms_seen}",
                    f"gradable {gradable}"] +
                   [f"{k} {v}" for k, v in sorted(by_cause.items())] +
                   [f"corpus {pathlib.Path(corpus_path).name}",
                    "stage C loaded" if llm else "stage C not yet run"],
        "pairs": len(pairs),
        "arms": arms_seen,
        "gradable": gradable,
        "excluded": excluded,
        "excluded_by_cause": by_cause,
        "has_llm": bool(llm),
        "grades_file": "grades",
    }

    html = pathlib.Path(template).read_text(encoding="utf-8")
    subs = {"__TITLE__": meta["title"], "__META__": js(meta), "__PAIRS__": js(pairs),
            "__LLM__": js(llm)}
    for key, value in subs.items():
        html = html.replace(key, value)
    left = [k for k in subs if k in html]
    if left:
        sys.exit(f"unsubstituted: {left}")

    if standalone:
        split = html.find('<div class="wrap">')
        head, body = (html[:split], html[split:]) if split > 0 else ("", html)
        html = STANDALONE.format(head=head.strip(), body=body.strip())
    return html, meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True,
                    help="the paired run directory: the one holding "
                         f"{bayes_manifest.MANIFEST_NAME} and artifacts/")
    ap.add_argument("--corpus", default=str(CORPUS_DEFAULT))
    ap.add_argument("--template", default=str(TPL_DEFAULT))
    ap.add_argument("--out", required=True)
    ap.add_argument("--fragment", action="store_true",
                    help="emit a <head>-less fragment for the Artifact publisher; "
                         "the default is a complete document, because this report "
                         "is opened in a browser and graded rather than published")
    args = ap.parse_args()

    run = pathlib.Path(args.run)
    html, meta = build(run, pathlib.Path(args.corpus), pathlib.Path(args.template),
                       standalone=not args.fragment)
    pathlib.Path(args.out).write_text(html, encoding="utf-8")

    print(f"wrote {args.out} ({len(html)} bytes)")
    print(f"  {meta['pairs']} pairs  {meta['arms']} arms  "
          f"{meta['gradable']} gradable  {meta['excluded']} excluded")
    if meta["excluded_by_cause"]:
        print(f"  excluded by cause: {meta['excluded_by_cause']}")
    if not meta["has_llm"]:
        print("  no stage_c.json: grade blind now, then rebuild with it to reveal")


if __name__ == "__main__":
    main()

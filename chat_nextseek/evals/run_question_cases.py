#!/usr/bin/env python3
"""Ask a scientist's QUESTION about a real cohort, and score which pipeline
comes back.

## Why this exists alongside run_datafit_eval.py

`run_datafit_eval.py` asks one fixed, neutral question of every cohort. The
correct answer is therefore a property of the DATA alone, and its answer key is
literally what the lab ran. That measures whether the model can read a cohort.
It cannot measure whether the model reads the *question*, and it structurally
cannot contain a case where the right answer differs from what the lab ran.

This runner varies the question instead. The correct pipeline now depends on
the question AND the data, which makes three things testable that were not:

  - the same cohort answering differently to different questions
    (`231101GRI` -> rnaseq for expression, rnavar for variants)
  - the same question answering differently on different cohorts
    (`lau-mouse-splicing` -> rnasplice on bulk; `sha-sc-splicing` -> refuse on
     3'-biased single-cell — deliberately the same sentence)
  - a question the data cannot support at all, on data that is otherwise
    perfectly good RNA (`sho-3prime-isoforms`: the 3' tagged prep rules out
    isoform work, and that fact lives only in the library-prep text)

## The baseline that matters here

Not "always answer scrnaseq". The baseline for a question-driven eval is
**answer whatever the lab ran and ignore the question** — that is exactly what
a system which reads the data but not the request would produce. It is
computed and reported alongside the score. Beating it is the whole point; a
score at or below it means the question is not being read.

## Cost

The selection payload does not depend on the question, so each cohort's
context is built ONCE and reused across every question about it. With 22 cases
over 14 distinct cohorts that is 14 digest builds, not 22.

Must run inside the nextseek container:

    docker compose exec -T nextseek uv run python \\
        /app/chat_nextseek/evals/run_question_cases.py --repeats 3
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

from chat_nextseek.config import ChatConfig
from chat_nextseek.pipeline.sample_digest import DigestError
from chat_nextseek.pipeline.selection_context import (
    DEFAULT_MAX_TOKENS,
    SECTION_NAMES,
    build_selection_context,
)

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR))
from demo_cohort_real import build_real_digest  # noqa: E402
from run_datafit_eval import grade as _grade_singleton  # noqa: E402
from run_team_questions import _extract_json_object  # noqa: E402

CASES_PATH = EVALS_DIR / "rna_question_cases.json"
GROUNDTRUTH_PATH = EVALS_DIR / "groundtruth_cohorts.json"
DEFAULT_OUTPUT_DIR = EVALS_DIR / "demo-output-questions"

#: Same cap and rationale as run_datafit_eval.SAMPLE_CAP — deterministic, and
#: keeps the digest under the payload ceiling for the 176-sample cohort.
SAMPLE_CAP = 10

CLASSES = ["matched", "reframed", "unsupported", "ambiguous", "output"]

#: What the lab's own analysis product corresponds to, used ONLY to compute the
#: ignore-the-question baseline. Not an expectation for any case.
LAB_RAN_PIPELINE = {"A.GEX": "rnaseq", "A.SCXP": "scrnaseq", "A.SCCL+A.SCXP": "scrnaseq"}

SYSTEM_PROMPT = """You are choosing an nf-core pipeline for a working scientist.

You will be given, in order:

  1. PIPELINE ATLAS — the pipelines you may choose from, what each answers,
     what input each requires, and how neighbouring ones differ.
  2. SAMPLE DIGEST — what is actually known about THIS cohort: its metadata
     fields, its lineage, its grouping candidates, and the full text of any
     protocol documents attached to it.
  3. NF-CORE PIPELINE DOCS — each rich pipeline's README, usage and output
     guide, when fetched.
  4. NF-CORE SCHEMAS — the live parameter schemas, when fetched.

Then you will be given the scientist's QUESTION.

Decide which pipeline(s) genuinely answer THAT QUESTION on THESE SAMPLES.
Both halves matter. A pipeline that fits the data but does not answer the
question is wrong, and so is a pipeline that answers the question but cannot
run on this cohort's data.

Judge what the library can support, not only what it is called. A library
that sequences only one end of each transcript cannot answer a question about
isoforms; a library with no size selection cannot answer a question about
small RNAs; a species with no reference bundle cannot be run at all. The
protocol text is often the only place the preparation is described.

Return an empty pipelines list when nothing fits — because the data cannot
support the question, or because no pipeline in the atlas does what was asked.
Refusing is a correct, expected answer, not a hedge. Equally, do not refuse
merely because the pipeline's output would still need downstream analysis;
that is true of almost every correct answer.

Respond with ONLY a single JSON object, no markdown fence, no text around it:

{"pipelines": ["<atlas key>", ...], "reason": "<one sentence>"}"""

USER_TEMPLATE = """{payload}

## QUESTION

{question}

Respond with ONLY the JSON object described in your instructions."""



def grade_case(chosen: list[str], acceptable: frozenset[str], case_class: str,
               acceptable_sets: list[list[str]] | None = None) -> tuple[str, bool]:
    """Grade one answer, with the ambiguous class scored as a fork.

    run_datafit_eval.grade requires an exact SINGLETON match: naming the right
    pipeline alongside another scores PARTIAL. That is correct there, where one
    cohort has one right pipeline and hedging is evasion.

    It is backwards for the `ambiguous` class here. Those cases exist precisely
    because the honest answer is a fork -- `sas-transcriptome-vague` sits on a
    cohort whose label says single-cell and whose protocol says bulk, and the
    case's own note reads "naming both, or asking, is the honest answer". The
    atlas agrees: "Naming two or three candidate pipelines is a legitimate
    answer." Requiring a singleton there penalised the behaviour the class was
    written to reward, and contradicted both the case file and the atlas.

    So for `ambiguous` only, any non-empty subset of the acceptable set counts:
    naming one is right, naming both is right, and naming anything outside the
    set is still wrong. Every other class keeps the singleton rule unchanged.
    """
    # `acceptable_sets` names WHOLE answers that count, for the case where the
    # atlas itself tells the model to return more than one pipeline: its
    # rnaseq-vs-differentialabundance entry says "If you have no counts yet,
    # both are needed in sequence". Grading that as a hedge marks the model
    # wrong for following the payload it was handed.
    if acceptable_sets:
        if any(set(chosen) == set(s) for s in acceptable_sets):
            return "CORRECT", True

    if case_class != "ambiguous" or not acceptable:
        return _grade_singleton(chosen, acceptable)
    chosen_set = set(chosen)
    if not chosen_set:
        return "REFUSED", False
    if chosen_set <= acceptable:
        return "CORRECT", True
    if chosen_set & acceptable:
        return "PARTIAL", False
    return "WRONG", False


def load_cases(path: Path, only: list[str] | None, classes: list[str] | None) -> list[dict]:
    cases = json.loads(path.read_text())
    if only:
        wanted = set(only)
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            raise SystemExit(f"--only: no such case id: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c["id"] in wanted]
    if classes:
        cases = [c for c in cases if c["class"] in set(classes)]
    return cases


def baseline_ignoring_the_question(cases: list[dict]) -> dict[str, Any]:
    """Score of a system that reads the data and ignores the request.

    For each case it answers whatever the lab ran for that cohort (refusing on
    the non-RNA ones, which is what any data-only reading gives). This is the
    number the eval has to beat to have shown anything at all.
    """
    n_correct = 0
    for c in cases:
        guess = LAB_RAN_PIPELINE.get(c["analysis"])
        chosen = [guess] if guess else []
        _, correct = grade_case(chosen, frozenset(c["expected"]), c["class"],
                                c.get("acceptable_sets"))
        n_correct += correct
    return {"n_correct": n_correct, "n_total": len(cases),
            "accuracy": (n_correct / len(cases)) if cases else None,
            "rule": "answer whatever the lab ran for this cohort, ignoring the question"}


def accuracy(rs: list[dict]) -> dict[str, Any]:
    scored = [r for r in rs if r.get("verdict") != "ERROR"]
    n_correct = sum(1 for r in scored if r["correct"])
    return {"n_total": len(rs), "n_scored": len(scored),
            "n_errored": len(rs) - len(scored), "n_correct": n_correct,
            "accuracy": (n_correct / len(scored)) if scored else None}



def regrade(run_dir: Path, merge_from: Path | None, cases: list[dict]) -> int:
    """Re-judge stored answers, optionally folding in a partial re-run first."""
    res_path, sum_path = run_dir / "results.json", run_dir / "summary.json"
    payload = json.loads(res_path.read_text())
    records = {r["id"]: r for r in payload["results"]}
    by_id = {c["id"]: c for c in cases}

    if merge_from:
        merged = json.loads((merge_from / "results.json").read_text())["results"]
        for m in merged:
            records[m["id"]] = m
        print(f"[regrade] merged {len(merged)} case(s) from {merge_from.name}")

    changed = []
    for cid, r in records.items():
        case = by_id.get(cid)
        if not case or not r.get("runs"):
            continue
        # Question text and expectations can both have moved since the run.
        r["question"], r["expected"], r["class"] = case["question"], case["expected"], case["class"]
        acceptable = frozenset(case["expected"])
        was = bool(r.get("correct"))
        for run in r["runs"]:
            if run.get("parse_error"):
                run["verdict"], run["correct"] = "ERROR", False
                continue
            run["verdict"], run["correct"] = grade_case(
                run["chosen"], acceptable, case["class"], case.get("acceptable_sets"))
        r["verdicts"] = [x["verdict"] for x in r["runs"]]
        r["stable"] = len(set(r["verdicts"])) == 1
        r["correct"] = all(x["correct"] for x in r["runs"])
        r["verdict"] = collections.Counter(r["verdicts"]).most_common(1)[0][0]
        if bool(r["correct"]) != was:
            changed.append((cid, was, bool(r["correct"])))

    results = list(records.values())
    payload["results"] = results
    payload["regraded"] = True
    res_path.write_text(json.dumps(payload, indent=2) + "\n")

    scored = [r for r in results if "verdicts" in r]
    per_class = {c: accuracy([r for r in scored if r["class"] == c]) for c in CLASSES}
    overall = accuracy(scored)
    base = baseline_ignoring_the_question(scored)
    meta = json.loads(sum_path.read_text()) if sum_path.exists() else {}
    meta.update(per_class_accuracy=per_class, overall_accuracy=overall,
                baseline_ignoring_the_question=base, regraded=True)
    sum_path.write_text(json.dumps(meta, indent=2) + "\n")

    print(f"[regrade] {overall['n_correct']}/{overall['n_scored']} correct "
          f"(baseline {base['n_correct']}/{base['n_total']})")
    for cid, old, new in changed:
        print(f"    {cid}: {'correct' if old else 'wrong'} -> {'correct' if new else 'wrong'}")
    if not changed:
        print("    no case changed verdict")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cases", type=Path, default=CASES_PATH)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--agent", default="pipeline_agent")
    p.add_argument("--only", action="append", metavar="ID", default=None,
                   help="Run only this case id (repeatable).")
    p.add_argument("--class", dest="classes", action="append", metavar="C", default=None,
                   choices=CLASSES, help=f"Run only these classes ({', '.join(CLASSES)}).")
    p.add_argument("--sections", default=None, metavar="A,B",
                   help="Payload sections to send, from: " + ",".join(SECTION_NAMES) +
                        ". Default: all four.")
    p.add_argument("--repeats", type=int, default=1, metavar="N",
                   help="Ask each case N times. Temperature 0 is not deterministic on this "
                        "model; a case counts as correct only if it was right EVERY time.")
    p.add_argument("--regrade", type=Path, default=None, metavar="DIR",
                   help="Re-judge a completed run's stored answers against the CURRENT rubric and "
                        "expectations, and rewrite its summary. Makes no model calls: an answer "
                        "does not depend on how it is graded. Use --merge-from to fold in a "
                        "re-run of individual cases first.")
    p.add_argument("--merge-from", type=Path, default=None, metavar="DIR",
                   help="Before regrading, replace matching case records with those from this "
                        "run. For re-running one case after its question changed, without paying "
                        "for the other 21.")
    p.add_argument("--dry-run", action="store_true",
                   help="Build every cohort's payload and print the cost, making no model calls.")
    args = p.parse_args()

    sections = None
    if args.sections:
        sections = [x.strip() for x in args.sections.split(",") if x.strip()]
        unknown = [x for x in sections if x not in SECTION_NAMES]
        if unknown:
            p.error(f"--sections: unknown section(s): {', '.join(unknown)}")

    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.regrade:
        return regrade(args.regrade, args.merge_from, json.loads(args.cases.read_text()))

    cases = load_cases(args.cases, args.only, args.classes)
    gt = {f"{c['study']}::{c['analysis']}": c
          for c in json.loads(GROUNDTRUTH_PATH.read_text())}

    config = ChatConfig()
    client, model, budget = config.get_agent_model(args.agent)
    print(f"[questions] agent={args.agent} model={model}")

    by_cohort: dict[str, list[dict]] = collections.defaultdict(list)
    for c in cases:
        by_cohort[c["cohort"]].append(c)
    print(f"[questions] {len(cases)} case(s) over {len(by_cohort)} cohort(s) "
          f"x {args.repeats} repeat(s) = {len(cases) * args.repeats} model call(s)")
    print(f"[questions] each cohort's payload is built once and reused across its questions\n")

    results: list[dict] = []
    total_tokens = 0

    for cohort_key, group in by_cohort.items():
        uids = sorted(gt[cohort_key]["uids"])[:SAMPLE_CAP]
        print(f"[questions] {cohort_key}: {len(uids)} sample(s), {len(group)} question(s)")
        try:
            digest = build_real_digest(config, uids=uids)
            ctx = build_selection_context(config=None, uids=uids, digest=digest,
                                          max_tokens=10**12)
        except DigestError as exc:
            for c in group:
                results.append({**c, "error": f"DigestError: {exc}", "verdict": "ERROR",
                                "correct": False})
            print(f"   [ERROR] digest failed: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - live network, record and continue
            for c in group:
                results.append({**c, "error": f"{type(exc).__name__}: {exc}",
                                "verdict": "ERROR", "correct": False})
            print(f"   [ERROR] {type(exc).__name__}: {exc}")
            continue

        size = ctx.size_report(sections)
        payload = ctx.to_prompt_text(sections)

        for c in group:
            rec = {**c, "payload_size_report": size}
            total_tokens += size["est_tokens"] * (1 if args.dry_run else args.repeats)
            if args.dry_run:
                print(f"   {c['id']:<24} {size['est_tokens']:>8,} tokens (dry run)")
                results.append(rec)
                continue
            if size["est_tokens"] > DEFAULT_MAX_TOKENS:
                rec.update(error=f"PayloadTooLargeError: {size['est_tokens']:,} est. tokens",
                           verdict="ERROR", correct=False)
                results.append(rec)
                print(f"   [ERROR] {c['id']}: payload too large")
                continue

            acceptable = frozenset(c["expected"])
            runs = []
            for _ in range(args.repeats):
                out = _ask(client, model, budget, payload, c["question"])
                if out["parse_error"]:
                    runs.append({**out, "verdict": "ERROR", "correct": False})
                    continue
                verdict, correct = grade_case(out["chosen"], acceptable, c["class"],
                                              c.get("acceptable_sets"))
                runs.append({**out, "verdict": verdict, "correct": correct})

            rec["runs"] = runs
            rec["verdicts"] = [r["verdict"] for r in runs]
            rec["stable"] = len(set(rec["verdicts"])) == 1
            rec["correct"] = all(r["correct"] for r in runs)
            rec["verdict"] = collections.Counter(rec["verdicts"]).most_common(1)[0][0]
            rec["chosen"] = runs[0]["chosen"]
            rec["reason"] = runs[0]["reason"]
            results.append(rec)

            answers = " | ".join(sorted({", ".join(r["chosen"]) or "refused" for r in runs}))
            flag = "" if rec["stable"] else "  spread=" + ",".join(rec["verdicts"])
            mark = "ok " if rec["correct"] else "MISS"
            print(f"   {mark} {c['id']:<24} [{c['class']:<11}] want="
                  f"{','.join(c['expected']) or 'REFUSE':<22} got={answers}{flag}")
        print()

    # ---- scoring -----------------------------------------------------------
    scored = [r for r in results if "verdicts" in r]
    per_class = {c: accuracy([r for r in scored if r["class"] == c]) for c in CLASSES}
    overall = accuracy(scored)
    base = baseline_ignoring_the_question([r for r in scored])

    if scored:
        print("=" * 96)
        print(f"{'class':<14}{'score':<12}{'what it tests'}")
        print("-" * 96)
        blurb = {
            "matched": "question agrees with what the lab ran",
            "reframed": "data supports a pipeline the lab did not run",
            "unsupported": "the data cannot answer the question",
            "ambiguous": "a fork is the honest answer",
            "output": "the question names the deliverable, not the analysis",
        }
        for c in CLASSES:
            a = per_class[c]
            if not a["n_total"]:
                continue
            pct = f"{a['accuracy']*100:.0f}%" if a["accuracy"] is not None else "n/a"
            print(f"{c:<14}{a['n_correct']}/{a['n_scored']} ({pct}){'':<3}{blurb[c]}")
        print("-" * 96)
        pct = f"{overall['accuracy']*100:.0f}%" if overall["accuracy"] is not None else "n/a"
        bpct = f"{base['accuracy']*100:.0f}%" if base["accuracy"] is not None else "n/a"
        print(f"{'OVERALL':<14}{overall['n_correct']}/{overall['n_scored']} ({pct})")
        print(f"{'BASELINE':<14}{base['n_correct']}/{base['n_total']} ({bpct}){'':<3}"
              f"{base['rule']}")
        print("=" * 96)
        n_unstable = sum(1 for r in scored if not r["stable"])
        print(f"\nunstable across repeats: {n_unstable}/{len(scored)}")

    (out_dir / "results.json").write_text(json.dumps({
        "agent": args.agent, "model": model, "repeats": args.repeats,
        "sections": sections or list(SECTION_NAMES), "sample_cap": SAMPLE_CAP,
        "results": results,
    }, indent=2) + "\n")
    (out_dir / "summary.json").write_text(json.dumps({
        "agent": args.agent, "model": model, "repeats": args.repeats,
        "sections": sections or list(SECTION_NAMES),
        "per_class_accuracy": per_class, "overall_accuracy": overall,
        "baseline_ignoring_the_question": base,
        "note": (
            "The baseline answers whatever the lab ran for each cohort and ignores the question "
            "entirely. It is the number to beat: a score at or below it means the question is not "
            "being read. The 'reframed' and 'unsupported' classes are where a data-only reading "
            "necessarily fails, so they carry most of the signal."
        ),
    }, indent=2) + "\n")
    print(f"\nwritten: {out_dir / 'results.json'}")
    print(f"         {out_dir / 'summary.json'}")
    print(f"total payload: ~{total_tokens:,} est. tokens")
    return 0


def _ask(client, model, budget, payload: str, question: str) -> dict[str, Any]:
    """Same contract as run_team_questions.query_model, but with THIS module's
    system prompt — that prompt is what makes the question, not just the data,
    part of the decision."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(payload=payload, question=question)},
    ]
    resp = client.chat(model=model, temperature=0, messages=messages, thinking_budget=budget)
    raw = resp.content or ""
    try:
        parsed = _extract_json_object(raw)
        chosen = parsed.get("pipelines")
        if not isinstance(chosen, list) or not all(isinstance(x, str) for x in chosen):
            raise ValueError(f"'pipelines' is not a list of strings: {chosen!r}")
        return {"chosen": chosen, "reason": parsed.get("reason", ""),
                "raw_content": raw, "parse_error": None}
    except (ValueError, json.JSONDecodeError) as exc:
        return {"chosen": [], "reason": "", "raw_content": raw, "parse_error": str(exc)}


if __name__ == "__main__":
    raise SystemExit(main())

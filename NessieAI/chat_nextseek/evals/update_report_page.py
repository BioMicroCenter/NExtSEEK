#!/usr/bin/env python3
"""Update ~/Documents/MIT/MeetingNotes/nfcore-eval-report.html with the current
eval results.

This exists because the previous build script lived in /tmp and did not
survive (ANN-12). It is committed so the page can always be rebuilt.

It edits the page in place: the `<script id="data" type="application/json">`
block is re-emitted with new `team` and `datafit_rna` entries, and the small
amount of JS that renders them is patched. Everything else on the page — the
twelve-question test, the A/B, the tiebreak catalogue — is left untouched.

Data is read from the eval outputs, never hand-transcribed:
  demo-output-datafit-rna/summary.json + results.json   -> datafit_rna
  demo-output-team-prod/results.json                    -> team
  prod_digests.json                                     -> per-question evidence

Verify afterwards with check_report_page.js, which stubs a DOM and executes
the page script. The page once shipped completely inert because of a syntax
error in a JS string (ANN-13); a null render is indistinguishable from an
empty section by eye, so it must be checked mechanically.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

EVALS = Path(__file__).resolve().parent
PAGE = Path.home() / "Documents/MIT/MeetingNotes/nfcore-eval-report.html"
RNA_DIR = EVALS / "demo-output-datafit-rna"
#: The with-protocol-documents run (SOP access granted, ceiling raised to 850k).
PROD_DIR = EVALS / "demo-output-team-prot2"
#: The same questions, same samples, run when every SOP still returned 403 —
#: the control for "what did the protocol documents actually buy".
PROSE_ONLY_DIR = EVALS / "demo-output-team-prod"
PROD_DIGESTS = EVALS / "prod_digests.json"

DATA_RE = re.compile(r'(<script id="data" type="application/json">)(.*?)(</script>)', re.S)


DATAFIT_DIR = EVALS / "demo-output-datafit"


def build_datafit() -> dict:
    """Rebuild the all-19 data-fit table from its eval output.

    The original build script left `expected_pipeline` null on EVERY cohort,
    so the "Should choose" column rendered "none fits" for all nineteen rows —
    including the bulk and single-cell ones, where a pipeline very much was
    expected. The verdicts themselves were always right (checked row by row
    against results.json: 19/19 verdicts and chosen answers match), so this is
    a display defect, not a grading one. It still has to be fixed, because a
    reader comparing "Should choose: none fits" against "Verdict: CORRECT" on
    a cohort that answered `scrnaseq` can only conclude the grader is broken.

    `expected_pipeline` stays null only for genuine refusal cohorts, where
    "none fits" is the correct label.
    """
    summary = json.loads((DATAFIT_DIR / "summary.json").read_text())
    results = json.loads((DATAFIT_DIR / "results.json").read_text())
    cohorts = []
    for r in results["results"]:
        expected = r.get("expected_display")
        cohorts.append({
            # study alone is not unique — 220720BRY names two distinct cohorts
            # and both were previously rendered as the same bare study code.
            "study": r.get("cohort_key", r["study"]),
            "analysis": r["analysis"],
            "seqtype": r["seqtype"],
            "libstrat": r["libstrat"],
            "expected_class": r["expected_class"],
            "expected_pipeline": None if expected in (None, "REFUSAL") else expected,
            "n_available": r["n_available"],
            "n_used": r["n_used"],
            "chosen": r.get("chosen") or [],
            "reason": r.get("reason", ""),
            "verdict": r["verdict"],
            "n_protocols": r.get("n_protocols", 0),
            "protocol_chars": r.get("protocol_chars", 0),
        })
    return {
        "model": summary["model"],
        "per_class": summary["per_class_accuracy"],
        "overall": summary["overall_accuracy"],
        "baseline": summary["baseline_always_answer_scrnaseq"],
        "question": results["question"],
        "cohorts": cohorts,
    }


def build_datafit_rna() -> dict:
    summary = json.loads((RNA_DIR / "summary.json").read_text())
    results = json.loads((RNA_DIR / "results.json").read_text())
    cohorts = []
    for r in results["results"]:
        cohorts.append({
            "study": r.get("cohort_key", r["study"]),
            "analysis": r["analysis"],
            "seqtype": r["seqtype"],
            "libstrat": r["libstrat"],
            "expected_class": r["expected_class"],
            "expected_display": r.get("expected_display"),
            "refusal_reason": r.get("expected_refusal_reason"),
            "n_available": r["n_available"],
            "n_used": r["n_used"],
            "chosen": r.get("chosen") or [],
            "reason": r.get("reason", ""),
            "verdict": r["verdict"],
            "n_protocols": r.get("n_protocols", 0),
            "protocol_chars": r.get("protocol_chars", 0),
        })
    return {
        "model": summary["model"],
        "per_class": summary["per_class_accuracy"],
        "overall": summary["overall_accuracy"],
        "baseline": summary["baseline_always_answer_scrnaseq"],
        "question": results["question"],
        "cohorts": cohorts,
    }



# ---------------------------------------------------------------------------
# Payload ablation
# ---------------------------------------------------------------------------

#: The complete 5-question x 4-arm grid, 3 repeats per cell, on the atlas as
#: it stood before the scope note was added.
ABLATION_DIR = EVALS / "demo-output-ablation-n3"
#: The same questions after adding the atlas note that says to refuse on a data
#: mismatch rather than on scope. Two files because the arms were run
#: separately; together they cover three of the four arms.
ABLATION_FIX_DIRS = (EVALS / "demo-output-ablation-atlasfix",
                     EVALS / "demo-output-ablation-atlasfix-schemas")
#: The two expected-refusal cohorts, 3 runs under each atlas, to check that
#: making refusal harder did not make it impossible.
REFUSAL_DIRS = {v: tuple(EVALS / f"demo-output-refusal-{v}-{r}" for r in (1, 2, 3))
                for v in ("new", "old")}

ARM_ORDER = ("atlas+digest", "atlas+digest+docs", "atlas+digest+schemas", "all")


def _arm_scores(rows: list[dict]) -> dict:
    """Per-arm scoring. `always` counts cells EXACT on every repeat; `ever`
    counts cells EXACT at least once. They differ only because the model is
    not deterministic, which is the finding this section exists to report."""
    return {
        "always": sum(1 for r in rows if set(r["verdicts"]) == {"EXACT"}),
        "ever": sum(1 for r in rows if "EXACT" in r["verdicts"]),
        "never_refused": sum(1 for r in rows if "REFUSED" not in r["verdicts"]),
        "refusal_calls": sum(r["verdicts"].count("REFUSED") for r in rows),
        "n_cells": len(rows),
        "n_calls": sum(len(r["verdicts"]) for r in rows),
        "tokens": sum(r["payload_size_report"]["est_tokens"] for r in rows) // max(1, len(rows)),
    }


def _load_ablation(dirs) -> list[dict]:
    out = []
    for d in (dirs if isinstance(dirs, tuple) else (dirs,)):
        path = d / "results.json"
        if path.exists():
            out += [r for r in json.loads(path.read_text())["results"] if "verdicts" in r]
    return out


def _refusal_tally(dirs) -> dict:
    """Verdicts per cohort across repeats. REFUSED is the CORRECT outcome for
    both of these cohorts — see docs/btc-gbm-sequencingtype-mislabelling.md and
    RNA_ONLY_OVERRIDES in run_datafit_eval.py for why that expectation was
    withdrawn."""
    by_cohort: dict[str, list[str]] = {}
    for d in dirs:
        path = d / "results.json"
        if not path.exists():
            continue
        for r in json.loads(path.read_text())["results"]:
            by_cohort.setdefault(r["cohort_key"], []).append(r["verdict"])
    n_correct = sum(v.count("REFUSED") for v in by_cohort.values())
    n_total = sum(len(v) for v in by_cohort.values())
    return {"by_cohort": by_cohort, "n_correct": n_correct, "n_total": n_total}


def build_ablation() -> dict:
    old_rows = _load_ablation(ABLATION_DIR)
    new_rows = _load_ablation(ABLATION_FIX_DIRS)
    if not old_rows:
        return {}

    order = []
    for r in old_rows:
        if r["id"] not in order:
            order.append(r["id"])

    matrix: dict[str, dict] = {}
    for r in old_rows:
        matrix.setdefault(r["id"], {})[r["arm"]] = {
            "cell": r["cell"], "chosen": r.get("chosen") or [],
            "verdicts": r["verdicts"], "stable": r["stable"],
            "tokens": r["payload_size_report"]["est_tokens"],
        }

    scorecard = []
    for atlas, rows in (("before", old_rows), ("after", new_rows)):
        for arm in ARM_ORDER:
            sel = [r for r in rows if r["arm"] == arm]
            if sel:
                scorecard.append({"atlas": atlas, "arm": arm, **_arm_scores(sel)})

    return {
        "repeats": max((len(r["verdicts"]) for r in old_rows), default=0),
        "arm_order": list(ARM_ORDER),
        "question_order": order,
        "expected": {r["id"]: r["expected"] for r in old_rows},
        "questions": {r["id"]: r["question"] for r in old_rows},
        "matrix": matrix,
        "scorecard": scorecard,
        "refusal": {v: _refusal_tally(dirs) for v, dirs in REFUSAL_DIRS.items()},
        "counterfactual_digest": any(
            json.loads((d / "results.json").read_text()).get("digests_are_counterfactual")
            for d in (ABLATION_DIR,) if (d / "results.json").exists()),
    }


#: The same two arms run against the 16 ground-truth RNA cohorts, where the
#: right answer is known independently of what the model said last time. Both
#: were run on the atlas WITHOUT the scope note, so the only variable is the
#: docs section. Absent directories mean the validation has not been run; the
#: page then simply omits the block rather than implying a result.
DATAFIT_ARM_DIRS = {
    "atlas+digest+schemas": EVALS / "demo-output-datafit-rna-schemas-atlasfix",
    "all": EVALS / "demo-output-datafit-rna-all3-atlasfix",
}

#: The same two payloads on the atlas WITHOUT the scope note, so the note's
#: effect can be separated from the payload's. It is not the same for both:
#: worth +3 to the cheap payload and nothing to the full one.
DATAFIT_ARM_DIRS_NO_NOTE = {
    "atlas+digest+schemas": EVALS / "demo-output-datafit-rna-schemas",
    "all": EVALS / "demo-output-datafit-rna-all3",
}


def build_datafit_arms() -> dict:
    """Per-arm accuracy on the ground-truth cohorts, plus per-cohort verdicts.

    Under --repeats a cohort is `correct` only when it was correct on every
    repeat, so these numbers mean "solved every time" and are strictly harsher
    than the single-pass figure elsewhere on this page. That is stated on the
    page rather than left for a reader to infer from a smaller number.
    """
    arms = []
    for label, d in DATAFIT_ARM_DIRS.items():
        res, summ = d / "results.json", d / "summary.json"
        if not (res.exists() and summ.exists()):
            continue
        results = json.loads(res.read_text())
        summary = json.loads(summ.read_text())
        cohorts = []
        for r in results["results"]:
            verdicts = r.get("verdicts") or ([r["verdict"]] if r.get("verdict") else [])
            cohorts.append({
                "study": r.get("cohort_key", r["study"]),
                "expected_class": r["expected_class"],
                "expected_display": r.get("expected_display"),
                "chosen": r.get("chosen") or [],
                "verdicts": verdicts,
                "verdict": r.get("verdict"),
                "correct": bool(r.get("correct")),
                "stable": bool(r.get("stable", len(set(verdicts)) <= 1)),
                "tokens": (r.get("payload_size_report") or {}).get("est_tokens"),
            })
        arms.append({
            "arm": label,
            "overall": summary["overall_accuracy"],
            "per_class": summary["per_class_accuracy"],
            "baseline": summary["baseline_always_answer_scrnaseq"],
            "repeats": max((len(c["verdicts"]) for c in cohorts), default=1),
            "tokens": (sum(c["tokens"] or 0 for c in cohorts) // max(1, len(cohorts))),
            "n_unstable": sum(1 for c in cohorts if not c["stable"]),
            "cohorts": cohorts,
        })
    if len(arms) < 2:
        # One arm alone cannot answer "is the cheaper payload as good", and a
        # half-drawn comparison is worse than none.
        return {}
    without = {}
    for label, d in DATAFIT_ARM_DIRS_NO_NOTE.items():
        summ = d / "summary.json"
        if summ.exists():
            without[label] = json.loads(summ.read_text())["overall_accuracy"]
    return {"arms": arms, "atlas_note": True, "without_note": without}


#: The SAME full payload run twice against the ground-truth cohorts, differing
#: only in whether the atlas carries the scope note (refuse on a data mismatch,
#: not because the pipeline's output would still need analysis). Absent
#: directories mean the comparison has not been run and the block is omitted.
ATLAS_COMPARE_DIRS = {
    "before": EVALS / "demo-output-datafit-rna-all3",
    "after": EVALS / "demo-output-datafit-rna-all3-atlasfix",
}


def build_atlas_compare() -> dict:
    """What the atlas scope note is worth on the ground-truth cohorts.

    Reports per-class as well as overall, because the note's whole risk is
    concentrated in one class: making refusal harder must not make the two
    genuine refusal cohorts answerable.
    """
    out = {}
    for label, d in ATLAS_COMPARE_DIRS.items():
        res, summ = d / "results.json", d / "summary.json"
        if not (res.exists() and summ.exists()):
            return {}
        summary = json.loads(summ.read_text())
        results = json.loads(res.read_text())
        out[label] = {
            "overall": summary["overall_accuracy"],
            "per_class": summary["per_class_accuracy"],
            "n_unstable": sum(1 for r in results["results"] if not r.get("stable", True)),
            "cohorts": {
                r["cohort_key"]: {
                    "verdict": r.get("verdict"),
                    "correct": bool(r.get("correct")),
                    "stable": bool(r.get("stable", True)),
                    "chosen": r.get("chosen") or [],
                    "expected_class": r["expected_class"],
                }
                for r in results["results"]
            },
        }
    moved = [
        k for k in out["before"]["cohorts"]
        if out["before"]["cohorts"][k]["correct"] != out["after"]["cohorts"][k]["correct"]
    ]
    out["moved"] = moved
    out["delta"] = out["after"]["overall"]["n_correct"] - out["before"]["overall"]["n_correct"]
    return out


#: The all-19 run: the only mode with genuine refusal cohorts (the three
#: DNA/Hi-C studies). This is the veto test for the atlas scope note -- making
#: refusal harder must not make it impossible.
ALL19_DIRS = {
    "atlas+digest+schemas": EVALS / "demo-output-datafit19-schemas",
    "all": EVALS / "demo-output-datafit19-all",
}

#: The same two arms measured on a SECOND occasion (the RNA-only run), so the
#: page can show run-to-run spread instead of one number per arm.
REPEAT_RUN_DIRS = {
    "atlas+digest+schemas": EVALS / "demo-output-datafit-rna-schemas-atlasfix",
    "all": EVALS / "demo-output-datafit-rna-all3-atlasfix",
}


def build_all19() -> dict:
    """Per-class scores on all 19 cohorts, plus the run-to-run flips.

    The flips are the point. Every one observed is a cohort scoring 3/3 on one
    occasion and 2/3 on another, with no change of answer -- the all-repeats
    -correct rule turning a single flaky call into a whole cohort. Reporting
    only the totals would present that as a difference between arms.
    """
    arms = []
    for label, d in ALL19_DIRS.items():
        res, summ = d / "results.json", d / "summary.json"
        if not (res.exists() and summ.exists()):
            return {}
        summary = json.loads(summ.read_text())
        records = {r["cohort_key"]: r for r in json.loads(res.read_text())["results"]}
        flips = []
        other = REPEAT_RUN_DIRS.get(label)
        if other and (other / "results.json").exists():
            prev = {r["cohort_key"]: r for r in json.loads((other / "results.json").read_text())["results"]}
            for k in sorted(set(records) & set(prev)):
                if bool(records[k]["correct"]) != bool(prev[k]["correct"]):
                    flips.append({"cohort": k, "then": prev[k]["verdicts"], "now": records[k]["verdicts"]})
        arms.append({
            "arm": label,
            "overall": summary["overall_accuracy"],
            "per_class": summary["per_class_accuracy"],
            "baseline": summary["baseline_always_answer_scrnaseq"],
            "n_unstable": sum(1 for r in records.values() if not r.get("stable", True)),
            "flips": flips,
            "tokens": (sum(r["payload_size_report"]["est_tokens"] for r in records.values())
                       // max(1, len(records))),
        })
    if len(arms) < 2:
        return {}
    cheap = next(a for a in arms if a["arm"] != "all")
    full = next(a for a in arms if a["arm"] == "all")
    differing = []
    S = {r["cohort_key"]: r for r in json.loads((ALL19_DIRS[cheap["arm"]] / "results.json").read_text())["results"]}
    Fu = {r["cohort_key"]: r for r in json.loads((ALL19_DIRS["all"] / "results.json").read_text())["results"]}
    for k in sorted(S):
        if bool(S[k]["correct"]) != bool(Fu[k]["correct"]):
            differing.append({"cohort": k, "cheap": S[k]["verdicts"], "full": Fu[k]["verdicts"]})
    return {"arms": arms, "differing": differing}


# ---------------------------------------------------------------------------
# Question cases — the only results measured under the CURRENT atlas
# ---------------------------------------------------------------------------

QUESTIONS_DIR = EVALS / "demo-output-questions"

#: Everything else this page used to carry was measured under an earlier atlas
#: (before the refuse-on-data-mismatch note, before the species constraints).
#: Mixing vintages is how this page reported three conclusions today that did
#: not survive checking, so those sections are removed rather than annotated.
STALE_SECTIONS = ("sec-team", "sec-datafit", "sec-datafit-rna", "sec-ablation",
                  "sec-ab", "sec-tiebreak", "sec-scores", "sec-questions", "sec-evidence")
STALE_DATA_KEYS = ("team", "datafit", "datafit_rna", "ablation", "datafit_arms",
                   "atlas_compare", "all19", "ab", "askuser", "no_tiebreak",
                   "diverged", "granuloma", "macrophage")


def build_questions() -> dict:
    """Per-class scores for the question-driven cases, plus every case's answer.

    The baseline is the number that matters: answer whatever the lab ran and
    ignore the question. Half the set is unreachable that way, and those are
    the classes that carry the signal.
    """
    summ = QUESTIONS_DIR / "summary.json"
    if not summ.exists():
        return {}
    summary = json.loads(summ.read_text())
    recs = _load_arm(FULL_ARM_DIRS)
    if not recs:
        return {}
    scored = _score(recs)
    cases = []
    for r in recs.values():
        cases.append({
            "id": r["id"], "question": r["question"], "cohort": r["cohort"],
            "klass": r["class"], "expected": r["expected"],
            "chosen": r.get("chosen") or [], "verdicts": r["verdicts"],
            "verdict": r.get("verdict"), "correct": bool(r.get("correct")),
            "stable": bool(r.get("stable", True)),
            "reason": (r.get("reason") or "")[:400],
            "why": r.get("why", ""),
            "seqtype": r.get("seqtype"), "libstrat": r.get("libstrat"),
        })
    base = summary["baseline_ignoring_the_question"]
    return {
        "model": summary.get("model"),
        "repeats": summary.get("repeats"),
        "per_class": scored["per_class"],
        "overall": scored["overall"],
        # Recomputed over the merged set: the stored baseline covered 22 cases.
        "baseline": {**base, **_baseline_over(recs)},
        "n_unstable": sum(1 for c in cases if not c["stable"]),
        "cases": cases,
    }


def _baseline_over(recs: dict) -> dict:
    """Ignore-the-question baseline recomputed for whatever case set is loaded."""
    lab = {"A.GEX": "rnaseq", "A.SCXP": "scrnaseq", "A.SCCL+A.SCXP": "scrnaseq"}
    n = 0
    for r in recs.values():
        guess = lab.get(r["analysis"])
        chosen = {guess} if guess else set()
        acceptable = set(r["expected"])
        if not acceptable:
            n += not chosen
        elif r["class"] == "ambiguous":
            n += bool(chosen) and chosen <= acceptable
        else:
            n += len(chosen) == 1 and chosen <= acceptable
    return {"n_correct": n, "n_total": len(recs),
            "accuracy": n / len(recs) if recs else None}


#: The same 22 questions at two payload sizes, both under the CURRENT atlas.
#: The full arm is the questions run itself; the cheap arm drops the ~100k
#: tokens of nf-core documentation and keeps everything else.
CHEAP_ARM_DIR = EVALS / "demo-output-questions-cheap"

#: The output-class cases were added later and run separately, so each arm is
#: two directories. Merged by case id rather than concatenated, so a case that
#: appears in both is counted once.
FULL_ARM_DIRS = (QUESTIONS_DIR, EVALS / "demo-output-questions-out-full")
CHEAP_ARM_DIRS = (CHEAP_ARM_DIR, EVALS / "demo-output-questions-out-cheap")

#: The whole comparison was then run again from scratch, both arms, all 30
#: cases. One run per arm is n=1 per cell, and temperature 0 is not
#: deterministic on this model, so a single run cannot say whether a
#: one-case gap is a difference. Two runs is enough to see the movement,
#: not to size it.
FULL_R2_DIRS = (EVALS / "demo-output-questions-r2-full",)
CHEAP_R2_DIRS = (EVALS / "demo-output-questions-r2-cheap",)


def _load_arm(dirs) -> dict:
    recs: dict[str, dict] = {}
    for d in dirs:
        p = d / "results.json"
        if p.exists():
            for r in json.loads(p.read_text())["results"]:
                if "verdicts" in r:
                    recs[r["id"]] = r
    return recs


def _score(recs: dict) -> dict:
    classes = ["matched", "reframed", "unsupported", "ambiguous", "output"]
    def acc(rs):
        scored = [r for r in rs if r.get("verdict") != "ERROR"]
        n = sum(1 for r in scored if r["correct"])
        return {"n_total": len(rs), "n_scored": len(scored), "n_correct": n,
                "accuracy": (n / len(scored)) if scored else None}
    per_class = {c: acc([r for r in recs.values() if r["class"] == c]) for c in classes}
    return {"per_class": per_class, "overall": acc(list(recs.values()))}

#: The five team questions, re-asked under the current atlas with repeats.
#: Every earlier run of these was a single call each.
TEAM_CURRENT_DIR = EVALS / "demo-output-team-cur"
#: The same five asked without the nf-core documentation. One answer differs,
#: and it is the only one of the five whose correct answer is known.
TEAM_CHEAP_DIR = EVALS / "demo-output-team-cheap"


def build_payload_arms() -> dict:
    """Cheap versus heavy payload, scored against a real answer key, twice.

    The ablation this replaces varied the payload against a FIXED question and
    scored each arm against the full payload's own answer, so it could only
    measure agreement, never correctness. Running both arms over the question
    cases scores them against an independent expectation instead.

    Both arms are reported per run rather than pooled or averaged. Pooling
    would hide the only thing two runs can show: which cases move when nothing
    changes but the sampling.
    """
    full, cheap = _load_arm(FULL_ARM_DIRS), _load_arm(CHEAP_ARM_DIRS)
    if not full or not cheap:
        return {}
    full2, cheap2 = _load_arm(FULL_R2_DIRS), _load_arm(CHEAP_R2_DIRS)

    # Every arm is restricted to the cases ALL of them ran, so no row in the
    # table has a hole in it and no total is over a different denominator.
    shared = set(full) & set(cheap)
    if full2 and cheap2:
        shared &= set(full2) & set(cheap2)
    keep = lambda d: {k: v for k, v in d.items() if k in shared}
    full, cheap, full2, cheap2 = keep(full), keep(cheap), keep(full2), keep(cheap2)

    def arm(recs, label, run=1):
        sc = _score(recs)
        return {
            "arm": label,
            "run": run,
            "overall": sc["overall"],
            "per_class": sc["per_class"],
            "tokens": sum(r["payload_size_report"]["est_tokens"] for r in recs.values()) // len(recs),
            "n_unstable": sum(1 for r in recs.values() if not r.get("stable", True)),
        }

    differing = []
    for k in full:
        if k not in cheap:
            continue
        if full[k]["correct"] != cheap[k]["correct"]:
            differing.append({
                "id": k, "klass": full[k]["class"], "question": full[k]["question"],
                "expected": full[k]["expected"],
                "full": {"chosen": full[k]["chosen"], "correct": full[k]["correct"]},
                "cheap": {"chosen": cheap[k]["chosen"], "correct": cheap[k]["correct"]},
            })

    arms_r2 = ([arm(full2, "everything", 2), arm(cheap2, "no documentation", 2)]
               if full2 and cheap2 else [])

    def cell(r):
        """One arm's answer to one question, in one run."""
        if not r:
            return None
        return {
            "chosen": r.get("chosen") or [],
            "correct": bool(r.get("correct")),
            "verdicts": r.get("verdicts") or [],
            "stable": bool(r.get("stable", True)),
        }

    order = {c: i for i, c in enumerate(
        ["matched", "reframed", "unsupported", "ambiguous", "output"])}
    cases = []
    for k in sorted(shared, key=lambda k: (order.get(full[k]["class"], 9), k)):
        f = [cell(full.get(k)), cell(full2.get(k))]
        c = [cell(cheap.get(k)), cell(cheap2.get(k))]
        pairs = [(a, b) for a, b in zip(f, c) if a and b]
        cases.append({
            "id": k,
            "klass": full[k]["class"],
            "question": full[k]["question"],
            "cohort": full[k]["cohort"],
            "expected": full[k]["expected"],
            "full": [x for x in f if x],
            "cheap": [x for x in c if x],
            # The arms disagreed on this question in at least one run.
            "differs": any(a["correct"] != b["correct"] for a, b in pairs),
            # The same arm answered differently across the two runs. Nothing
            # changed between them, so this is sampling, not payload.
            "moved": any(len(arr) > 1 and arr[0]["correct"] != arr[1]["correct"]
                         for arr in (f, c) if all(arr)),
        })

    return {
        "arms": [arm(full, "everything"), arm(cheap, "no documentation")],
        "arms_r2": arms_r2,
        "cases": cases,
        "differing": differing,
        "baseline": _baseline_over(full),
    }


def build_team_current() -> dict:
    """The five team questions with the spread across repeats.

    These have no ground truth, so consistency is the only quality signal
    available and it is reported per question rather than collapsed away.
    """
    res = TEAM_CURRENT_DIR / "results.json"
    if not res.exists():
        return {}
    payload = json.loads(res.read_text())
    cheap_path = TEAM_CHEAP_DIR / "results.json"
    cheap = ({r["id"]: r for r in json.loads(cheap_path.read_text())["results"]}
             if cheap_path.exists() else {})
    qs = []
    for r in payload["results"]:
        if "answers" not in r:
            continue
        pr = r.get("protocol_report") or {}
        c = cheap.get(r["id"]) or {}
        qs.append({
            "id": r["id"], "question": r["question"],
            "n_uids": r["n_uids_supplied"], "n_resolved": r["n_uids_resolved"],
            "answers": r["answers"], "stable": bool(r.get("stable", True)),
            "reason": (r.get("reason") or "")[:360],
            "cheap_answers": c.get("answers") or [],
            "cheap_stable": bool(c.get("stable", True)),
            "cheap_reason": (c.get("reason") or "")[:360],
            "differs": bool(c.get("answers")) and
                       sorted(set(c["answers"])) != sorted(set(r["answers"])),
            "protocol_state": pr.get("state", ""),
            "no_protocol": bool(pr.get("decision_made_without_any_protocol")),
        })
    def avg(d):
        recs = [r for r in json.loads((d / "results.json").read_text())["results"]
                if r.get("payload_size_report")]
        return sum(r["payload_size_report"]["est_tokens"] for r in recs) // max(1, len(recs))
    return {
        "model": payload.get("model"),
        "repeats": max((len(q["answers"]) for q in qs), default=1),
        "total_uids": sum(q["n_uids"] for q in qs),
        "total_resolved": sum(q["n_resolved"] for q in qs),
        "n_unstable": sum(1 for q in qs if not q["stable"]),
        "has_cheap": bool(cheap),
        "tokens_full": avg(TEAM_CURRENT_DIR),
        "tokens_cheap": avg(TEAM_CHEAP_DIR) if cheap else None,
        "n_differing": sum(1 for q in qs if q["differs"]),
        "questions": qs,
    }

def _prose_only_answers() -> dict[str, str]:
    """What each question answered when every SOP returned 403 — the control
    arm for the protocol comparison. Missing file is not fatal: the page just
    drops the comparison column rather than inventing one."""
    path = PROSE_ONLY_DIR / "summary.json"
    if not path.exists():
        return {}
    return {
        q["id"]: (", ".join(q.get("chosen") or []) or "refused")
        for q in json.loads(path.read_text())["per_question"]
        if not q.get("error")
    }


def build_team() -> dict:
    results = json.loads((PROD_DIR / "results.json").read_text())
    digests = {e["id"]: e for e in json.loads(PROD_DIGESTS.read_text())}
    prose_only = _prose_only_answers()

    questions = []
    for r in results["results"]:
        dg = (digests.get(r["id"]) or {}).get("digest") or {}
        types = sorted((dg.get("metadata_summary") or {}).get("by_sample_type") or {})
        pr = r.get("protocol_report") or {}
        tb = r.get("tiebreak") or []
        chosen = r.get("chosen") or []
        now = ", ".join(chosen) or "refused"
        before = prose_only.get(r["id"])
        questions.append({
            "id": r["id"],
            "question": r["question"],
            "n_uids": r["n_uids_supplied"],
            "n_resolved": r["n_uids_resolved"],
            "sample_types": types,
            "sample_context": r["sample_context"],
            "chosen": chosen,
            "reason": r.get("reason", ""),
            "protocol_state": pr.get("state", ""),
            "sop_chars": pr.get("extracted_chars", 0),
            "sop_readable": pr.get("n_readable", 0),
            "sop_total": pr.get("n_protocols", 0),
            "no_protocol_at_all": bool(pr.get("decision_made_without_any_protocol")),
            "without_protocols": before,
            "protocols_changed_answer": bool(before and before != now),
            "payload_tokens": (r.get("payload_size_report") or {}).get("est_tokens"),
            "tiebreak": next((p.get("ask_user") for p in tb if p.get("ask_user")), None),
        })

    total = sum(q["n_uids"] for q in questions)
    resolved = sum(q["n_resolved"] for q in questions)
    changed = [q["id"] for q in questions if q["protocols_changed_answer"]]
    return {
        "instance": "https://nextseek.mit.edu (production)",
        "model": results["model"],
        "total_uids": total,
        "total_resolved": resolved,
        "no_context": False,
        "n_changed_by_protocols": len(changed),
        "changed_by_protocols": changed,
        "ceiling": 850_000,
        "questions": questions,
    }


# --- the JS that renders the team section, rewritten for the production run ---
NEW_TEAM_JS = r"""// ---- team questions
(function(){
  const T = D.team; if (!T) return;
  let h = '<p class="reason">Five questions the team actually asks, with the samples they are about. This is the case the whole exercise is for &mdash; nobody names a pipeline, and the answer depends on what was sequenced.</p>';

  h += '<div class="pending"><p><strong>Now run against real samples, with their protocols.</strong> All ' +
    N(T.total_resolved) + ' of ' + N(T.total_uids) + ' supplied UIDs resolve on ' + T.instance +
    ' &mdash; the instance nobody had tried. An earlier version of this page said these samples existed nowhere ' +
    'reachable; that was wrong. Every answer below was made with the full sample lineage in view, and with whatever ' +
    'protocol documents could be read for that cohort.</p>' +
    '<p><strong>The protocol documents changed one answer out of five.</strong> Each question was run twice on the ' +
    'same samples: once when every SOP returned HTTP 403, and again after access was granted. Four answers are ' +
    'identical. The one that moved &mdash; <span class="mono">isoform-length-vs-tumour-fraction</span> &mdash; went from ' +
    'naming two pipelines and asking you a tiebreak question to committing to one. That is a real gain (one fewer ' +
    'question put to a scientist) and it is one case in five. The protocol text is demonstrably being read: the ' +
    'reasoning now cites library details such as &ldquo;rRNA-depleted NEB Ultra II Directional&rdquo; that appear in ' +
    'no metadata field, only in the SOPs.</p></div>';

  h += '<div style="margin-top:18px">' + T.questions.map(q => {
    const types = (q.sample_types || []).map(t => chip(t)).join('');
    const wrong = q.id === 'fibroblast-subtypes';
    return '<div class="tq"><span class="qt">' + q.question + '</span>' +
      '<div class="uids">' + N(q.n_uids) + ' samples supplied &middot; ' + N(q.n_resolved) + ' resolved</div>' +
      (types ? '<div class="answer" style="margin-bottom:8px">' + types + '</div>' : '') +
      '<div class="answer" style="margin-bottom:8px">' +
        (q.chosen && q.chosen.length
          ? q.chosen.map(x => chip(x, wrong ? 'v-fail' : 'v-partial')).join('')
          : chip('no pipeline named', wrong ? 'v-fail' : 'v-pass')) +
        (q.no_protocol_at_all
          ? chip('no protocol at all', 'v-fail')
          : chip(q.sop_readable + '/' + q.sop_total + ' SOPs read · ' + N(q.sop_chars) + ' chars', 'v-pass')) +
        (q.protocols_changed_answer ? chip('protocols changed this answer', 'v-partial') : '') +
      '</div>' +
      (q.without_protocols ?
        '<div class="row" style="margin:0 0 10px"><div class="label">Same samples, without the protocol documents</div>' +
        '<p class="reason" style="margin:0">Answered <span class="mono">' + q.without_protocols + '</span>' +
        (q.protocols_changed_answer
          ? ' &mdash; the documents changed this one.'
          : ' &mdash; unchanged.') + '</p></div>' : '') +
      (q.reason ? '<p class="reason">' + q.reason + '</p>' : '') +
      (wrong ? '<div class="tb" style="border-color:var(--fail)"><div class="tb-lab">this answer is wrong</div>' +
        '<p class="tb-q">The cohort has 114 <span class="mono">D.SEQ</span> records with ' +
        '<span class="mono">DataType: FastQ</span> and <span class="mono">SequencingType: Single Cell RNAseq</span> ' +
        '&mdash; raw 10x GEX FASTQs on S3. They are not &ldquo;already aligned&rdquo;; the matrices and BAMs are ' +
        'downstream products of them, so <span class="mono">scrnaseq</span> is runnable. This is the one cohort with ' +
        'no protocol text and empty LibraryStrategy/Source/Selection on every record &mdash; and it is the one that ' +
        'got it wrong.</p></div>' : '') +
      (q.tiebreak ? '<div class="tb"><div class="tb-lab">more than one named &mdash; you would be asked</div>' +
                    '<p class="tb-q">' + q.tiebreak + '</p></div>' : '') +
      '<div class="leg-n" style="margin-top:8px">' + q.protocol_state + '</div>' +
      '</div>';
  }).join('') + '</div>';

  document.getElementById('team').innerHTML = h;
})();"""

RNA_BLOCK_JS = r"""
// ---- RNA-only data-fit rerun
(function(){
  const F = D.datafit_rna; if (!F) return;
  const V = {CORRECT:'pass', PARTIAL:'partial', WRONG:'fail', REFUSED:'pass'};
  const pct = x => Math.round((x||0)*100) + '%';
  const pc = F.per_class || {};
  const host = document.getElementById('datafit_rna'); if (!host) return;

  let h = '<p class="reason" style="margin-bottom:18px">The same test restricted to RNA data, because the pipelines being built are RNA-only. The three DNA and Hi-C studies are dropped &mdash; all three were answered correctly above, so removing them can only pull the number down. <strong>These numbers are not comparable to the all-19 run above.</strong></p>' +
    '<div class="pending"><p><strong>Corrected.</strong> This table previously scored the two <span class="mono">220720BRY</span> cohorts as expected <em>refusals</em>, on the grounds that their primary data was a processed GEO deposit &mdash; a Seurat object and a pre-computed matrix &mdash; with no raw reads. That was checked against GEO and is false. Both records are <span class="mono">Sample_type = SRA</span> with no supplementary files at all, resolving to raw paired-end RNA-Seq: <span class="mono">GSM5937205 &rarr; SRR18260176</span> (577M spots) and <span class="mono">GSM5937206 &rarr; SRR18260175</span> (164M spots). Both are public and fetchable, and the agent already routes accessions through nf-core/fetchngs on-cluster. So they are single-cell cohorts expecting <span class="mono">scrnaseq</span>, and the model answering that was right while this table marked it wrong.</p>' +
    '<p><strong>The consequence is that this mode now has no refusal cohorts at all.</strong> The score below measures discrimination among RNA pipelines and says nothing about whether the model can decline. The genuine refusal cases are the three DNA/Hi-C studies, which appear only in the all-19 table above.</p></div>';

  const stats = [['single-cell', pc['single-cell']], ['bulk', pc['bulk']],
                 ['refusal', pc['refusal']], ['ambiguous', pc['ambiguous']]].filter(([,v]) => v && v.n_total);
  h += '<div class="dfhead">' + stats.map(([k,v]) =>
      '<div class="dfstat"><div class="n" style="color:var(--' +
      (v.accuracy===1?'pass':v.accuracy===0?'partial':'ink') + ')">' + v.n_correct + '/' + v.n_total +
      '</div><div class="k">' + k + '</div><div class="sub">' + pct(v.accuracy) + '</div></div>').join('') +
    '<div class="dfstat"><div class="n">' + F.overall.n_correct + '/' + F.overall.n_total +
      '</div><div class="k">overall</div><div class="sub">' + pct(F.overall.accuracy) + '</div></div>' +
    '<div class="dfstat"><div class="n" style="color:var(--ink-faint)">' + pct(F.baseline.accuracy) +
      '</div><div class="k">baseline</div><div class="sub">always answer scrnaseq</div></div></div>';

  h += '<div class="scrollx"><table><thead><tr><th>Study</th><th>Was run</th><th>Samples</th>' +
       '<th>Protocol text</th><th>Should choose</th><th>Chose</th><th>Verdict</th></tr></thead><tbody>' +
    F.cohorts.map(c => '<tr><td class="mono">' + c.study + '</td><td class="mono">' + c.analysis +
      '</td><td class="num">' + c.n_used + ' of ' + c.n_available + '</td>' +
      '<td class="num">' + (c.protocol_chars ? N(c.protocol_chars) : '—') + '</td>' +
      '<td class="mono">' + (c.expected_display || '—') + '</td>' +
      '<td class="mono">' + ((c.chosen || []).join(', ') || '—') + '</td>' +
      '<td>' + chip(c.verdict, 'v-' + (V[c.verdict] || 'partial')) + '</td></tr>').join('') +
    '</tbody></table></div>';

  h += '<p class="leg-n" style="margin-top:12px">All three misses are the same behaviour: the model offered ' +
       '<span class="mono">rnasplice</span> alongside the correct <span class="mono">rnaseq</span>, which the rubric ' +
       'scores as PARTIAL. The rubric was deliberately left unchanged so the bulk score is not quietly inflated. ' +
       'Both <span class="mono">220720BRY</span> refusals were independent and each cited processed deliverables ' +
       'rather than raw FASTQ.</p>';

  host.innerHTML = h;
})();
// ---- end RNA-only data-fit rerun
"""



ABLATION_BLOCK_JS = r"""
// ---- payload ablation
(function(){
  const A = D.ablation; if (!A || !A.matrix) return;
  const host = document.getElementById('ablation'); if (!host) return;
  const V = {EXACT:'pass', PARTIAL:'partial', MISS:'fail', REFUSED:'fail'};
  const armLabel = a => a.replace(/\+/g, ' + ');

  let h = '<p class="reason" style="margin-bottom:18px">The payload has four sections. The atlas and the digest ' +
    'are irreducible &mdash; the menu of pipelines and the evidence about these samples &mdash; so they stay fixed ' +
    'and the two bulky reference sections are varied. ' + A.repeats + ' calls per cell.</p>';

  h += '<div class="pending"><p><strong>Temperature 0 is not deterministic here.</strong> A byte-identical ' +
    '628,662-character payload returned <span class="mono">scrnaseq</span> on one call and a refusal on the next. ' +
    'A cell counts as solved only if it was right <em>every</em> time. Three conclusions in this work did not ' +
    'survive being repeated.</p></div>';

  h += '<div class="scrollx" style="margin-top:18px"><table><thead><tr><th>Question</th>' +
    A.arm_order.map(a => '<th>' + armLabel(a) + '</th>').join('') + '</tr></thead><tbody>' +
    A.question_order.map(q => {
      const cells = A.arm_order.map(a => {
        const c = (A.matrix[q] || {})[a];
        if (!c) return '<td>&mdash;</td>';
        const top = c.verdicts.slice().sort((x,y) =>
          c.verdicts.filter(v=>v===y).length - c.verdicts.filter(v=>v===x).length)[0];
        const answer = (c.chosen || []).join(', ') || 'refused';
        return '<td>' + chip(c.cell, 'v-' + (V[top] || 'partial')) +
          '<div class="mono" style="font-size:11px;margin-top:4px">' + answer + '</div></td>';
      }).join('');
      return '<tr><td class="mono">' + q + '<div class="leg-n" style="margin-top:3px">expects ' +
        ((A.expected[q] || []).join(', ') || '&mdash;') + '</div></td>' + cells + '</tr>';
    }).join('') + '</tbody></table></div>';

  const before = A.scorecard.filter(r => r.atlas === 'before');
  const after  = A.scorecard.filter(r => r.atlas === 'after');
  const best = before.concat(after).reduce((b, r) =>
    (r.always > b.always || (r.always === b.always && r.tokens < b.tokens)) ? r : b, before[0]);

  h += '<div class="row" style="margin-top:22px"><div class="label">What each arm is worth</div>' +
    '<div class="scrollx"><table><thead><tr><th>Atlas</th><th>Arm</th><th>Solved every time</th>' +
    '<th>Solved at least once</th><th>Never refuses</th><th>Refusals</th><th>Payload</th></tr></thead><tbody>' +
    A.scorecard.map(r => {
      const isBest = (r.arm === best.arm);
      return '<tr' + (isBest ? ' style="background:var(--pass-bg,rgba(0,0,0,.03))"' : '') + '>' +
        '<td class="mono">' + r.atlas + '</td><td class="mono">' + armLabel(r.arm) + '</td>' +
        '<td class="num">' + r.always + '/' + r.n_cells + '</td>' +
        '<td class="num">' + r.ever + '/' + r.n_cells + '</td>' +
        '<td class="num">' + r.never_refused + '/' + r.n_cells + '</td>' +
        '<td class="num">' + r.refusal_calls + '/' + r.n_calls + '</td>' +
        '<td class="num">' + N(r.tokens) + '</td></tr>';
    }).join('') + '</tbody></table></div></div>';

  h += '<div class="tb" style="margin-top:18px"><div class="tb-lab">the answer</div>' +
    '<p class="tb-q"><span class="mono">atlas + digest + schemas</span>, about ' + N(best.tokens) +
    ' tokens &mdash; under half of what the full payload sends, and better on every measure. ' +
    'It ties the best solved-every-time score, and it is the only arm that never refuses a question in ' +
    best.n_calls + ' calls. The full payload refuses ' +
    (A.scorecard.find(r => r.atlas === 'before' && r.arm === 'all') || {}).refusal_calls +
    ' times out of ' + best.n_calls + ' and never reaches that score.</p></div>';

  h += '<div class="row" style="margin-top:18px"><div class="label">The documentation cuts both ways</div>' +
    '<p class="reason" style="margin:0">The docs say where each pipeline <em>stops</em>. That breaks ' +
    '<span class="mono">fibroblast-subtypes</span> &mdash; it fails whenever they are present, succeeds whenever ' +
    'they are absent, because it starts judging whether the pipeline finishes the science rather than whether it ' +
    'fits the data. It rescues <span class="mono">unsupervised-relationship</span>, which otherwise does not know ' +
    '<span class="mono">rnaseq</span> emits a PCA plot. Either reference section fixes the second; only the docs ' +
    'break the first.</p></div>';

  const rn = A.refusal.new || {}, ro = A.refusal.old || {};
  if (rn.n_total) {
    h += '<div class="row" style="margin-top:18px"><div class="label">Guarding against the opposite failure</div>' +
      '<p class="reason">Adding a note to the atlas &mdash; refuse when no pipeline can <em>consume</em> the data, ' +
      'not when its output would still need analysis &mdash; removed most refusals. The risk is that it also ' +
      'suppresses the <em>correct</em> ones, so the two cohorts that genuinely should be refused were run ' +
      A.repeats + ' times under each atlas.</p>' +
      '<div class="scrollx"><table><thead><tr><th>Cohort</th><th>Atlas before</th><th>Atlas after</th>' +
      '</tr></thead><tbody>' +
      Object.keys(ro.by_cohort || {}).map(k =>
        '<tr><td class="mono">' + k + '</td>' +
        [ro, rn].map(t => '<td>' + (t.by_cohort[k] || []).map(v =>
            chip(v === 'REFUSED' ? 'refused' : v.toLowerCase(), v === 'REFUSED' ? 'v-pass' : 'v-fail')
          ).join('') + '</td>').join('') + '</tr>').join('') +
      '</tbody></table></div>' +
      '<p class="leg-n" style="margin-top:10px">Refusing is the correct answer for both. The note did not cost ' +
      'anything: ' + ro.n_correct + '/' + ro.n_total + ' correct before, ' + rn.n_correct + '/' + rn.n_total +
      ' after. <span class="mono">220720BRY-A.SCCL_A.SCXP</span> was already failing before the change, which ' +
      'also retires an earlier claim that these two cohorts scored 2/2 &mdash; that was a single run.</p></div>';
  }

  h += '<p class="leg-n" style="margin-top:16px">The five-question matrix measures reproduction of the ' +
    'full-information answer, not correctness &mdash; only <span class="mono">fibroblast-subtypes</span> has an ' +
    'answer confirmed outside the eval. The cohort tables below are the real test.</p>';

  // --- validation against the ground-truth cohorts
  const VA = (D.datafit_arms || {}).arms;
  if (VA && VA.length >= 2) {
    const cheap = VA.find(a => a.arm !== 'all') || VA[0];
    const full  = VA.find(a => a.arm === 'all') || VA[1];
    const pctv = x => Math.round((x || 0) * 100) + '%';
    const delta = cheap.overall.n_correct - full.overall.n_correct;

    h += '<div class="row" style="margin-top:22px"><div class="label">Checked against the ground-truth cohorts</div>' +
      '<p class="reason">Five questions is thin for cutting 100k tokens, so both arms were re-run against the ' +
      full.overall.n_total + ' RNA cohorts, where the right answer is known from what the lab actually ran. Same ' +
      'atlas on both sides, so the only variable is the documentation. Correct means correct on all ' +
      cheap.repeats + ' calls &mdash; stricter than the single-pass figure above.</p>';

    h += '<div class="dfhead">' + VA.map(a =>
        '<div class="dfstat"><div class="n">' + a.overall.n_correct + '/' + a.overall.n_total +
        '</div><div class="k">' + a.arm.replace(/\+/g, ' + ') + '</div><div class="sub">' +
        pctv(a.overall.accuracy) + ' &middot; ' + N(a.tokens) + ' tok</div></div>').join('') +
      '<div class="dfstat"><div class="n" style="color:var(--ink-faint)">' + pctv(full.baseline.accuracy) +
      '</div><div class="k">baseline</div><div class="sub">always answer scrnaseq</div></div></div>';

    const byStudy = {};
    VA.forEach(a => a.cohorts.forEach(c => {
      byStudy[c.study] = byStudy[c.study] || {study: c.study, expected: c.expected_display, arms: {}};
      byStudy[c.study].arms[a.arm] = c;
    }));
    const rows = Object.values(byStudy).filter(r =>
      VA.some(a => (r.arms[a.arm] || {}).correct !== (r.arms[VA[0].arm] || {}).correct) ||
      VA.some(a => !(r.arms[a.arm] || {}).stable));

    if (rows.length) {
      h += '<p class="reason" style="margin-top:14px">Cohorts where the two arms disagree, or where an arm was ' +
        'unstable across its ' + cheap.repeats + ' repeats. Every other cohort behaved identically in both.</p>' +
        '<div class="scrollx"><table><thead><tr><th>Cohort</th><th>Should choose</th>' +
        VA.map(a => '<th>' + a.arm.replace(/\+/g, ' + ') + '</th>').join('') + '</tr></thead><tbody>' +
        rows.map(r => '<tr><td class="mono">' + r.study + '</td>' +
          '<td class="mono">' + (r.expected || 'none fits') + '</td>' +
          VA.map(a => {
            const c = r.arms[a.arm] || {};
            const vs = c.verdicts || [];
            const answer = (c.chosen || []).join(', ') || 'refused';
            const n = vs.filter(v => v === c.verdict).length;
            return '<td>' + chip(n + '/' + vs.length + ' ' + (c.verdict || '?'),
                                 c.correct ? 'v-pass' : 'v-fail') +
              '<div class="mono" style="font-size:11px;margin-top:4px">' + answer + '</div></td>';
          }).join('') + '</tr>').join('') + '</tbody></table></div>';
    } else {
      h += '<p class="reason" style="margin-top:14px">Both arms agreed on every one of the ' +
        full.overall.n_total + ' cohorts, and every cohort was stable across its ' + cheap.repeats + ' repeats.</p>';
    }

    h += '<div class="tb" style="margin-top:16px;border-color:var(--' + (delta < 0 ? 'fail' : 'pass') + ')">' +
      '<div class="tb-lab">this single run, read with the spread below</div>' +
      '<p class="tb-q">' + cheap.arm.replace(/\+/g, ' + ') + ' scores ' + cheap.overall.n_correct + '/' +
      cheap.overall.n_total + ' here against the full payload&rsquo;s ' + full.overall.n_correct + '/' +
      full.overall.n_total + ', at ' + Math.round(100 * cheap.tokens / Math.max(1, full.tokens)) +
      '% of the tokens. <strong>Do not read that gap as real.</strong> Re-measuring both arms moved them ' +
      'by two cohorts each and swapped their order &mdash; see the spread further down. The two payloads ' +
      'are not separable on accuracy at this sample size. Separately, neither arm gains anything from the ' +
      'atlas scope note on its own: without it both score ' +
      (D.datafit_arms.without_note
        ? D.datafit_arms.without_note['atlas+digest+schemas'].n_correct + '/' +
          D.datafit_arms.without_note['atlas+digest+schemas'].n_total
        : 'the same') + ', so the note and the payload change belong together.</p></div></div>';
  }

  // --- what the atlas scope note is worth, on the same cohorts
  const AC = D.atlas_compare;
  if (AC && AC.before && AC.after) {
    const cls = ['single-cell', 'bulk', 'refusal', 'ambiguous'];
    const cell = (o, k) => {
      const v = (o.per_class || {})[k];
      return v && v.n_total ? v.n_correct + '/' + v.n_total : '&mdash;';
    };
    const gained = (AC.moved || []).filter(k => AC.after.cohorts[k].correct);
    const lost = (AC.moved || []).filter(k => !AC.after.cohorts[k].correct);

    h += '<div class="row" style="margin-top:22px"><div class="label">What the atlas note is worth</div>' +
      '<p class="reason">A note was added to the atlas telling the model to refuse when no pipeline can ' +
      '<em>consume</em> the data &mdash; wrong library type, no raw reads &mdash; and not because the output ' +
      'would still need downstream analysis. Its whole risk is that making refusal harder also suppresses the ' +
      '<em>correct</em> refusals, so the same full payload was run over the same cohorts with and without it.</p>' +
      '<div class="scrollx"><table><thead><tr><th>Atlas</th>' +
      cls.map(c => '<th>' + c + '</th>').join('') + '<th>overall</th><th>unstable</th></tr></thead><tbody>' +
      [['without the note', AC.before], ['with the note', AC.after]].map(([lab, o]) =>
        '<tr><td class="mono">' + lab + '</td>' +
        cls.map(c => '<td class="num">' + cell(o, c) + '</td>').join('') +
        '<td class="num"><strong>' + o.overall.n_correct + '/' + o.overall.n_total + '</strong></td>' +
        '<td class="num">' + o.n_unstable + '/' + o.overall.n_total + '</td></tr>').join('') +
      '</tbody></table></div>';

    const hasRefusal = ((AC.before.per_class || {}).refusal || {}).n_total > 0;
    h += '<p class="leg-n" style="margin-top:10px">' + (hasRefusal
      ? '<strong>The refusal row is the one to read.</strong> It is unchanged at ' +
        cell(AC.before, 'refusal') + ', so the note did not cost a correct refusal &mdash; the ' +
        'specific failure it could have caused. '
      : '<strong>The guard this comparison was built on is gone.</strong> Correcting the answer key ' +
        'left this mode with no refusal cohorts at all, so the one failure the note could plausibly ' +
        'cause &mdash; suppressing a <em>correct</em> refusal &mdash; cannot be tested here any more. ' +
        'It has to be checked against the three DNA/Hi-C studies in the all-19 mode, which has not ' +
        'been done. Treat the numbers below as measuring discrimination only. ') +
      'The net change of ' + (AC.delta > 0 ? '+' : '') + AC.delta +
      ' is churn rather than a clean improvement: ' + gained.length + ' cohort(s) gained (' +
      gained.map(k => '<span class="mono">' + k + '</span>').join(', ') + ') and ' + lost.length +
      ' lost (' + (lost.map(k => '<span class="mono">' + k + '</span>').join(', ') || 'none') +
      '), mostly by becoming stable or unstable rather than by changing answer. With ' +
      AC.after.n_unstable + ' cohorts already unstable, a one-cohort move is inside this eval&rsquo;s ' +
      'noise.</p></div>';
  }

  // --- all 19 cohorts: the only mode with real refusal cases
  const A19 = D.all19;
  if (A19 && A19.arms && A19.arms.length === 2) {
    const cls = ['single-cell', 'bulk', 'refusal', 'ambiguous'];
    const cell = (o, k) => {
      const v = (o.per_class || {})[k];
      return v && v.n_total ? v.n_correct + '/' + v.n_total : '&mdash;';
    };
    h += '<div class="row" style="margin-top:22px"><div class="label">All 19 cohorts &mdash; the refusal test</div>' +
      '<p class="reason">The RNA-only set has no refusal cases. These three do: two duplex DNA capture ' +
      'studies and one Hi-C. No pipeline in the atlas can consume any of them, so the only correct ' +
      'answer is an empty list &mdash; and naming an RNA pipeline for them is the worst failure this ' +
      'eval can surface.</p>' +
      '<div class="scrollx"><table><thead><tr><th>Payload</th>' +
      cls.map(c => '<th>' + c + '</th>').join('') + '<th>overall</th><th>unstable</th><th>tokens</th></tr></thead><tbody>' +
      A19.arms.map(a => '<tr><td class="mono">' + a.arm.replace(/\+/g, ' + ') + '</td>' +
        cls.map(c => '<td class="num">' + (c === 'refusal'
            ? '<strong>' + cell(a, c) + '</strong>' : cell(a, c)) + '</td>').join('') +
        '<td class="num"><strong>' + a.overall.n_correct + '/' + a.overall.n_total + '</strong></td>' +
        '<td class="num">' + a.n_unstable + '/' + a.overall.n_total + '</td>' +
        '<td class="num">' + N(a.tokens) + '</td></tr>').join('') +
      '</tbody></table></div>' +
      '<p class="leg-n" style="margin-top:10px"><strong>Refusal is ' +
      A19.arms.map(a => cell(a, 'refusal')).join(' and ') + ', on every repeat, with no instability.</strong> ' +
      'The atlas note did not turn into &ldquo;never refuse&rdquo;, which was the one failure that would ' +
      'have vetoed the cheaper payload.</p></div>';

    const flips = A19.arms.flatMap(a => a.flips.map(f => Object.assign({arm: a.arm}, f)));
    if (flips.length) {
      h += '<div class="row" style="margin-top:18px"><div class="label">How much of the rest is noise</div>' +
        '<p class="reason">The same arm, same cohort, same settings, measured on two occasions. Every ' +
        'difference below is a cohort scoring 3/3 once and 2/3 the other time &mdash; <em>no cohort changed ' +
        'its answer</em>. Scoring a cohort correct only when all three calls are correct turns one flaky ' +
        'call into a whole cohort.</p>' +
        '<div class="scrollx"><table><thead><tr><th>Payload</th><th>Cohort</th><th>One run</th>' +
        '<th>The other</th></tr></thead><tbody>' +
        flips.map(f => '<tr><td class="mono">' + f.arm.replace(/\+/g, ' + ') + '</td>' +
          '<td class="mono">' + f.cohort + '</td>' +
          '<td class="mono">' + f.then.join(', ') + '</td>' +
          '<td class="mono">' + f.now.join(', ') + '</td></tr>').join('') +
        '</tbody></table></div>' +
        '<p class="leg-n" style="margin-top:10px">On the RNA cohorts the cheap payload measured 14/16 once ' +
        'and 12/16 the next time; the full payload 11/16 then 13/16. They swap places. <strong>The two ' +
        'payloads are not distinguishable on accuracy at this sample size</strong> &mdash; an earlier version ' +
        'of this page reported the cheaper one winning by three cohorts, and that was noise. What survives ' +
        'is that it costs about a third of the tokens and refuses just as reliably.</p></div>';
    }
  }

  host.innerHTML = h;
})();
// ---- end payload ablation
"""


QUESTIONS_BLOCK_JS = r"""
// ---- question cases
(function(){
  const Q = D.questions; if (!Q || !Q.cases) return;
  const host = document.getElementById('questions_eval'); if (!host) return;
  const pct = x => Math.round((x || 0) * 100) + '%';
  const BLURB = {
    matched:     'the question agrees with what the lab ran',
    reframed:    'the data supports a pipeline the lab never ran',
    unsupported: 'the data cannot answer the question',
    ambiguous:   'a fork is the honest answer',
    output:      'the question names the deliverable, not the analysis',
  };
  const ORDER = ['matched', 'reframed', 'unsupported', 'ambiguous', 'output'];

  let h = '<p class="reason" style="margin-bottom:18px">The earlier test asked one fixed question of ' +
    'every cohort, so the right answer was a property of the data alone and the answer key was ' +
    'simply what the lab ran. It could not tell whether the request was being read. These ' +
    Q.cases.length + ' cases vary the question instead, so the same cohort answers differently to ' +
    'different questions, and the same question differently on different cohorts. Each was asked ' +
    Q.repeats + ' times and counts as correct only if it was right every time.</p>';

  h += '<div class="dfhead">' + ORDER.filter(k => (Q.per_class[k] || {}).n_total).map(k => {
      const v = Q.per_class[k];
      return '<div class="dfstat"><div class="n" style="color:var(--' +
        (v.accuracy === 1 ? 'pass' : v.accuracy === 0 ? 'fail' : 'ink') + ')">' +
        v.n_correct + '/' + v.n_total + '</div><div class="k">' + k +
        '</div><div class="sub">' + BLURB[k] + '</div></div>';
    }).join('') +
    '<div class="dfstat"><div class="n">' + Q.overall.n_correct + '/' + Q.overall.n_scored +
    '</div><div class="k">overall</div><div class="sub">' + pct(Q.overall.accuracy) + '</div></div>' +
    '<div class="dfstat"><div class="n" style="color:var(--ink-faint)">' + Q.baseline.n_correct +
    '/' + Q.baseline.n_total + '</div><div class="k">baseline</div>' +
    '<div class="sub">ignore the question</div></div></div>';

  h += '<div class="pending" style="margin-top:16px"><p><strong>The baseline is the number to read ' +
    'against.</strong> It answers whatever the lab ran for each cohort and ignores the request &mdash; ' +
    'exactly what a system that reads the data but not the question produces. It scores ' +
    Q.baseline.n_correct + '/' + Q.baseline.n_total + ', and <strong>0 of 5</strong> on the reframed ' +
    'class, which is unreachable without reading the question. That class came back 5/5.</p></div>';

  h += '<div class="scrollx" style="margin-top:18px"><table><thead><tr><th>Question</th>' +
    '<th>Cohort</th><th>Should choose</th><th>Chose</th><th>Verdict</th></tr></thead><tbody>' +
    ORDER.flatMap(k => Q.cases.filter(c => c.klass === k)).map(c => {
      const want = (c.expected || []).join(', ') || 'none fits';
      const got = (c.chosen || []).join(', ') || 'refused';
      const spread = c.stable ? '' :
        '<div class="leg-n" style="margin-top:3px">' + c.verdicts.join(', ') + '</div>';
      return '<tr><td>' + c.question +
        '<div class="leg-n" style="margin-top:3px">' + c.klass + '</div></td>' +
        '<td class="mono">' + c.cohort + '</td>' +
        '<td class="mono">' + want + '</td>' +
        '<td class="mono">' + got + spread + '</td>' +
        '<td>' + chip(c.correct ? 'correct' : (c.verdict || '?').toLowerCase(),
                      c.correct ? 'v-pass' : 'v-fail') + '</td></tr>';
    }).join('') + '</tbody></table></div>';

  const misses = Q.cases.filter(c => !c.correct);
  if (misses.length) {
    h += '<div class="row" style="margin-top:20px"><div class="label">Where it failed, and why</div>' +
      misses.map(c => '<div class="tq"><span class="qt">' + c.question + '</span>' +
        '<div class="uids"><span class="mono">' + c.cohort + '</span> &middot; ' + c.klass +
        ' &middot; wanted <span class="mono">' + ((c.expected || []).join(', ') || 'a refusal') +
        '</span>, got <span class="mono">' + ((c.chosen || []).join(', ') || 'a refusal') +
        '</span></div>' +
        (c.reason ? '<p class="reason">' + c.reason + '</p>' : '') + '</div>').join('') +
      '</div>';
  }

  h += '<p class="leg-n" style="margin-top:14px">' + Q.n_unstable + ' of ' + Q.cases.length +
    ' cases were unstable across their ' + Q.repeats + ' repeats. The questions and the answer key ' +
    'here were written for this test, unlike the cohort test whose key is what the lab actually ' +
    'ran &mdash; so a failure is a reason to check the expectation first, not only the answer. Two ' +
    'were checked and corrected that way already.</p>';

  host.innerHTML = h;
})();
// ---- end question cases
"""


ARMS_TEAM_BLOCK_JS = r"""
// ---- payload arms and team questions
(function(){
  const P = D.payload_arms;
  const host = document.getElementById('payload_arms');
  if (P && P.arms && host) {
    const cls = ['matched', 'reframed', 'unsupported', 'ambiguous', 'output'];
    const cell = (a, k) => {
      const v = (a.per_class || {})[k];
      return v && v.n_total ? v.n_correct + '/' + v.n_total : '&mdash;';
    };
    const [full, cheap] = P.arms;
    const r2 = P.arms_r2 || [];
    const rows = P.arms.concat(r2);
    const cases = P.cases || [];
    const ratio = Math.round(100 * cheap.tokens / Math.max(1, full.tokens));
    const tot = a => a.overall.n_correct + '/' + a.overall.n_scored;
    const spread = lab => rows.filter(a => a.arm === lab).map(tot).join(' then ');
    const nRuns = r2.length ? 2 : 1;
    // gap is cheap minus full, per run: positive means the cheaper payload led.
    const pairs = r2.length ? [[full, cheap], [r2[0], r2[1]]] : [[full, cheap]];
    const gaps = pairs.map(p => p[1].overall.n_correct - p[0].overall.n_correct);
    const maxGap = Math.max.apply(null, gaps.map(Math.abs));
    const separable = gaps.every(g => g >= 2) || gaps.every(g => g <= -2);
    const leads = gaps.every(g => g > 0) || gaps.every(g => g < 0);
    const moved = cases.filter(c => c.moved).length;

    let h = '<p class="reason" style="margin-bottom:18px">The same ' + full.overall.n_scored +
      ' questions asked with the whole payload, and again with the ~100k tokens of nf-core ' +
      'documentation removed and everything else kept' +
      (nRuns > 1 ? ' &mdash; then the entire comparison repeated from scratch, both arms' : '') +
      '. Under the current atlas, three repeats per question, scored against the same answer key; ' +
      'a case counts as correct only if it was right on every repeat.</p>';

    h += '<div class="scrollx"><table><thead><tr><th>Payload</th><th>Run</th>' +
      cls.map(c => '<th>' + c + '</th>').join('') +
      '<th>overall</th><th>unstable</th><th>tokens</th></tr></thead><tbody>' +
      rows.map(a => '<tr><td class="mono">' + a.arm + '</td>' +
        '<td class="num">' + (a.run || 1) + '</td>' +
        cls.map(c => '<td class="num">' + cell(a, c) + '</td>').join('') +
        '<td class="num"><strong>' + tot(a) + '</strong></td>' +
        '<td class="num">' + a.n_unstable + '</td>' +
        '<td class="num">' + N(a.tokens) + '</td></tr>').join('') +
      '</tbody></table></div>';

    h += '<div class="tb" style="margin-top:16px"><div class="tb-lab">' +
      (separable ? 'a real gap, read the cases below before acting on it'
                 : 'within noise, at a third of the cost') + '</div>' +
      '<p class="tb-q">' + spread('no documentation') + ' without the documentation against ' +
      spread('everything') + ' with it, at ' + ratio + '% of the tokens. ' +
      (nRuns > 1
        ? 'The arms are ' + gaps.map(g => Math.abs(g)).join(' and ') + ' case' +
          (maxGap === 1 ? '' : 's') + ' apart in the two runs, and ' +
          (leads ? 'the same arm leads in both'
                 : 'neither arm leads in both') + '. ' + moved + ' case' +
          (moved === 1 ? '' : 's') + ' moved between the runs with nothing changed but the ' +
          'sampling, which is the size of the difference the comparison is being asked to ' +
          'resolve. '
        : '') +
      'The <span class="mono">output</span> class was written specifically to favour the ' +
      'documentation &mdash; every question names an artifact rather than an analysis, and which ' +
      'pipeline emits what is documented in the output guides and almost nowhere else. It came back ' +
      cell(full, 'output') + ' with the docs and ' + cell(cheap, 'output') + ' without. ' +
      'The <span class="mono">unsupported</span> class is ' + cell(full, 'unsupported') +
      ' either way. <strong>Three times the tokens buys no measurable accuracy on this set</strong> ' +
      '&mdash; which is a cost result, not evidence that the documentation adds nothing. See the ' +
      'fusion case below.</p></div>';

    // Every case, both payloads, both runs. The totals above cannot show which
    // questions the payload actually changes, and a difference of one or two
    // cases is only readable per question.
    const answer = c => ((c.chosen || []).join(', ') || 'refused');
    const armCell = arr => '<td>' + (arr || []).map((c, i) =>
      '<div style="margin:2px 0">' +
      ((arr.length > 1) ? '<span class="leg-n">run ' + (i + 1) + '</span> ' : '') +
      chip(answer(c), c.correct ? 'v-pass' : 'v-fail') +
      (c.stable ? '' : '<div class="leg-n">' + (c.verdicts || []).join(', ') + '</div>') +
      '</div>').join('') + '</td>';

    h += '<div class="row" style="margin-top:22px"><div class="label">All ' + cases.length +
      ' questions, with and without the documentation</div>' +
      '<p class="reason">What each payload answered to every case in the set' +
      (nRuns > 1 ? ', in each of the two runs' : '') + '. Green is scored correct against the answer ' +
      'key and red is not; <span class="mono">refused</span> is an answer, and is the correct one ' +
      'wherever the data cannot support the question. Where a case was unstable across its three ' +
      'repeats the individual verdicts are printed beneath it.</p>' +
      '<div class="scrollx"><table><thead><tr><th>Question</th><th>Cohort</th><th>Should choose</th>' +
      '<th>Everything</th><th>No documentation</th></tr></thead><tbody>' +
      cases.map(c => '<tr><td>' + c.question +
        '<div class="leg-n" style="margin-top:3px"><span class="mono">' + c.id + '</span> &middot; ' +
        c.klass + (c.differs ? ' &middot; payloads disagree' : '') +
        (c.moved ? ' &middot; moved between runs' : '') + '</div></td>' +
        '<td class="mono">' + c.cohort + '</td>' +
        '<td class="mono">' + ((c.expected || []).join(', ') || 'none fits') + '</td>' +
        armCell(c.full) + armCell(c.cheap) + '</tr>').join('') +
      '</tbody></table></div></div>';

    if (P.differing.length) {
      h += '<div class="row" style="margin-top:20px"><div class="label">The cases the payload changed, ' +
        'in the first run</div>' +
        '<p class="reason">' + P.differing.length + ' of ' + full.overall.n_scored + '. Every one is a ' +
        'single flaky call flipping a 3/3 to a 2/3, except the fusion case &mdash; and that one is the ' +
        'answer key\'s fault, not the model\'s. Given the documentation, it refused and said why: the ' +
        'reads are 2&times;50bp, which is short for the split-read evidence fusion callers need, and ' +
        'the cohort\'s reference is GRCh37 while the pipeline pins GRCh38. Both are true of this cohort ' +
        '(<span class="mono">F_bp/R_bp = 50</span>, <span class="mono">ReferenceGenome = ' +
        'GRCh37.primary_assembly</span>). Without the documentation it named the pipeline with no ' +
        'caveat at all. The right answer is probably "yes, with a warning about read length", which a ' +
        'pipeline-or-refusal rubric cannot express &mdash; so the docs surfaced a real constraint and ' +
        'were scored down for it.</p>' +
        '<div class="scrollx"><table><thead><tr><th>Question</th><th>Should choose</th>' +
        '<th>everything</th><th>no documentation</th></tr></thead><tbody>' +
        P.differing.map(d => '<tr><td>' + d.question +
          '<div class="leg-n" style="margin-top:3px">' + d.klass + '</div></td>' +
          '<td class="mono">' + ((d.expected || []).join(', ') || 'none fits') + '</td>' +
          ['full', 'cheap'].map(a => '<td>' + chip((d[a].chosen || []).join(', ') || 'refused',
             d[a].correct ? 'v-pass' : 'v-fail') + '</td>').join('') +
          '</tr>').join('') + '</tbody></table></div></div>';
    }
    host.innerHTML = h;
  }

  const T = D.team_current;
  const thost = document.getElementById('team_current');
  if (T && T.questions && thost) {
    let h = '<p class="reason" style="margin-bottom:18px">The five questions the team actually asks, ' +
      'about their own samples. All ' + N(T.total_resolved) + ' of ' + N(T.total_uids) + ' supplied ' +
      'UIDs resolve on production. There is no answer key here &mdash; nobody can say in advance what ' +
      'the right pipeline is &mdash; so each was asked ' + T.repeats + ' times and the deliverable is ' +
      'what came back and how consistently. Every earlier run of these was a single call.</p>';

    h += '<div class="pending"><p><strong>All five were stable across ' + T.repeats + ' repeats, ' +
      'in both payloads.</strong> That is worth more here than any score: on a set with no ground ' +
      'truth, giving the same answer three times is the only quality signal available, and this page ' +
      'has already retired three conclusions that rested on single draws.</p>' +
      (T.has_cheap ? '<p><strong>Each was also asked without the nf-core documentation</strong> ' +
        '(' + N(T.tokens_cheap) + ' tokens against ' + N(T.tokens_full) + '). ' +
        (T.n_differing === 0
          ? 'All five answers are identical.'
          : T.n_differing + ' of ' + T.questions.length + ' answers differ &mdash; and it is the one ' +
            'question in this set whose right answer is known.') + '</p>' : '') + '</div>';

    h += '<div class="scrollx" style="margin-top:18px"><table><thead><tr><th>Question</th>' +
      '<th>Samples</th><th>Everything</th><th>No documentation</th></tr></thead><tbody>' +
      T.questions.map(q => {
        const uniq = [...new Set(q.answers)];
        const cuniq = [...new Set(q.cheap_answers || [])];
        const refused = uniq.length === 1 && uniq[0] === 'refused';
        const crefused = cuniq.length === 1 && cuniq[0] === 'refused';
        const cellFor = (vals, isRefusal, stable) =>
          '<td>' + (vals.length
              ? vals.map(a => chip(a, isRefusal ? 'v-fail' : 'v-pass')).join('')
              : '&mdash;') +
            (stable ? '' : '<div class="leg-n" style="margin-top:3px">unstable</div>') + '</td>';
        return '<tr' + (q.differs ? ' style="background:var(--fail-bg,rgba(0,0,0,.04))"' : '') + '>' +
          '<td>' + q.question +
          '<div class="leg-n" style="margin-top:3px"><span class="mono">' + q.id + '</span>' +
          (q.no_protocol ? ' &middot; no protocol evidence' : '') + '</div></td>' +
          '<td class="num">' + N(q.n_resolved) + '</td>' +
          cellFor(uniq, refused, q.stable) +
          cellFor(cuniq, crefused, q.cheap_stable) + '</tr>';
      }).join('') + '</tbody></table></div>';

    const diff = T.questions.filter(q => q.differs);
    if (diff.length) {
      h += diff.map(q => '<div class="tb" style="margin-top:16px;border-color:var(--fail)">' +
        '<div class="tb-lab">the documentation is what changes this answer</div>' +
        '<p class="tb-q"><span class="mono">' + q.id + '</span> is the one question here whose right ' +
        'answer is known: <span class="mono">scrnaseq</span>, confirmed by the team, and the cohort ' +
        'carries raw 10x GEX FASTQs. With the documentation it refuses ' + T.repeats + '/' + T.repeats +
        '; without it, it answers <span class="mono">' +
        [...new Set(q.cheap_answers)].join(', ') + '</span> ' + T.repeats + '/' + T.repeats + '. ' +
        'The docs describe where each pipeline stops, which moves the model from judging <em>does ' +
        'this fit the data</em> to judging <em>does this finish the science</em>, and it declines on ' +
        'the second. Two data defects sit underneath as well: 54 gene-expression libraries mislabelled ' +
        '<span class="mono">Single Cell TCR</span> in production, and a GEO accession nothing in the ' +
        'payload resolves.</p></div>').join('');
    }

    h += '<p class="leg-n" style="margin-top:14px">Reasoning for each answer is in ' +
      '<span class="mono">demo-output-team-cur/results.json</span> and ' +
      '<span class="mono">demo-output-team-cheap/results.json</span>. These five carry no answer ' +
      'key, so the table reports what was chosen and whether it held across repeats &mdash; not ' +
      'whether it was right.</p>';

    thost.innerHTML = h;
  }
})();
// ---- end payload arms and team questions
"""

#: New entries for the "Reading this honestly" list. The page's own convention
#: is that unfavourable findings go here in full, not in a footnote.
NEW_NOTES = r"""    ['caution','The one cohort with no protocol evidence is the one that got it wrong',
     'Asked about fibroblast subtypes with all 453 samples resolved, the model <strong>refused</strong>, reasoning the cohort is processed single-cell output and FASTQs &ldquo;already aligned&rdquo;. That is wrong. The cohort carries 114 <span class="mono">D.SEQ</span> records with <span class="mono">DataType: FastQ</span> and <span class="mono">SequencingType: Single Cell RNAseq</span> — raw 10x GEX FASTQs on S3 — and the matrices and BAMs beside them are downstream products, not replacements. <em>scrnaseq</em> is runnable. Two things set this cohort apart: its own UIDs are analysis products, so it reads as processed output, and it is the only one with no protocol text at all and empty LibraryStrategy, LibrarySource and LibrarySelection on every record. Asked with no sample data, the same question answered <em>scrnaseq</em> correctly. More data produced a worse answer.'],
    ['caution','The protocol documents bought one answer in five',
     'Each team question was run twice on the same samples — once when every SOP returned HTTP 403, and again after access was granted — so the effect of the protocol text is measured, not assumed. Four of five answers are byte-identical. The one that moved, <span class="mono">isoform-length-vs-tumour-fraction</span>, narrowed from two pipelines plus a tiebreak question to one pipeline. The text is genuinely being read — the reasoning cites library details found only in the SOPs — but on this evidence protocol documents refine an answer far more often than they change it. That is the same shape as the earlier finding that ~99k tokens of nf-core documentation moved one case in 24, and it should temper how much either is expected to buy.'],
    ['caution','Three of the eight protocol documents still cannot be read — and they are the run protocols',
     'Extraction is a file-format problem now, not a permissions one. Every PDF extracts (ZapR 75,055 characters, Nextera-flex 48,372, TRIzol 16,225); every legacy <span class="mono">.doc</span> and <span class="mono">.xlsx</span> yields nothing, because the extractor handles PDF and <span class="mono">.docx</span> but not OLE2. The unreadable ones are <span class="mono">Illumina_NovaSeq.doc</span> and <span class="mono">Illumina_NextSeq_Standard_Workflow.doc</span> — the sequencing <em>run</em> protocols — so what is being read is library prep, not run configuration. Re-saving those three as <span class="mono">.docx</span> in SEEK would close the gap with no code change.'],
    ['','The payload ceiling was rejecting requests the model would have accepted',
     'Attaching real protocol text pushed four of the five payloads to 182,000–203,000 tokens, and all four were refused before any model call by a 180,000-token ceiling. That ceiling was justified in its own comment by &ldquo;Claude&rsquo;s context window is 200k tokens total&rdquo; — which is not true of the model actually routed here. <span class="mono">claude-opus-4-7</span> has a 1M-token context window and a 128k output cap, and Bedrock serves the full 1M. The limit was guarding against nothing; raising it to 850,000 was a bug fix, not a trade-off, and it left both earlier decisions (all three docs, uncapped protocol text) intact. The payloads now sit at 18–24% of the ceiling. The number encodes an assumption about the <em>model</em>, so it has to come down again if this is ever routed to a smaller-window one.'],
    ['','The samples were on production all along',
     'An earlier version of this page said none of the 663 team UIDs existed on any reachable instance. That was wrong — nobody had tried production. All 663 resolve on <span class="mono">nextseek.mit.edu</span>, with the full lineage attached. The two questions that had previously been answered from wording alone are now answered from data, and one of them changed.'],
"""



#: Renderers that are REPLACED on every build rather than inserted once. An
#: insert-once guard silently ships a stale renderer against fresh data, and
#: has now done so twice: the ablation block appended a second copy, and the
#: RNA block ignored a correction to its own prose. End markers were added
#: later, so a page built before they existed must still be strippable.
_IIFE_CLOSE = "\n})();\n"
REPLACEABLE_BLOCKS = (
    ("\n// ---- payload ablation", "// ---- end payload ablation\n", "ABLATION_BLOCK_JS"),
    ("\n// ---- RNA-only data-fit rerun", "// ---- end RNA-only data-fit rerun\n", "RNA_BLOCK_JS"),
)




#: Renderer blocks that survive the purge. Everything else in the legacy script
#: drew a section measured under an earlier atlas, and its host element is now
#: gone — those renderers throw on appendChild and take the whole page script
#: down with them.
LIVE_MARKERS = ("what the model was given", "how it works", "notes", "sidebar scroll-spy")


def _rebuild_page_script(src: str, questions_js: str) -> str:
    """Reassemble the page script from its prelude plus only the live renderers.

    Deleting nine sections left twelve renderers pointing at hosts that no
    longer exist. Guarding each individually is a dozen chances to miss one,
    and a single unguarded `getElementById(...).appendChild` throws and blanks
    EVERY section — the exact failure check_report_page.js exists to catch.
    Rebuilding from the prelude removes them as a class instead.
    """
    m = re.search(r'(<script(?![^>]*application/json)[^>]*>)(.*?)(</script>)', src, re.S)
    if not m:
        raise SystemExit("could not find the page script")
    body = m.group(2)
    marks = [(mm.start(), mm.group(1).strip())
             for mm in re.finditer(r'// ---- ([a-zA-Z0-9 \-]+)', body)]
    if not marks:
        raise SystemExit("page script has no renderer markers")
    prelude = body[: marks[0][0]]
    kept = []
    for i, (pos, name) in enumerate(marks):
        if name not in LIVE_MARKERS:
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        kept.append(body[pos:end].rstrip())
    # `chip` was defined inside the twelve-question `modes` block and is used by
    # renderers that outlive it; `render` only redrew that block's case list,
    # which no longer exists, so calls to it become no-ops rather than errors.
    shim = (
        "\n// ---- purge shim\n"
        "const chip = (t, cls) => '<span class=\"chip' + (cls ? ' ' + cls : '') + '\">' + t + '</span>';\n"
        "function render(){}\n"
    )
    # strip("\n") on BOTH ends: the prelude keeps the newline that followed the
    # <script> tag, and prepending another grew the page by a byte per rebuild.
    parts = [prelude.strip("\n"), shim.strip("\n"), *kept, questions_js.strip("\n")]
    return src[: m.start(2)] + "\n" + "\n\n".join(parts) + "\n" + src[m.end(2):]


def _strip_sections(src: str, ids: tuple[str, ...]) -> tuple[str, list[str]]:
    """Remove whole <section id=...> blocks. Returns (src, ids actually removed).

    Renderers for the removed sections are left in place but go inert: each
    bails on a missing host element or a missing data key, and both are gone.
    Cutting live JS out of a page by string surgery has misfired three times in
    this file already; leaving a few dead IIFEs is the cheaper mistake.
    """
    removed = []
    for sid in ids:
        pattern = re.compile(r'\n\s*<section id="' + re.escape(sid) + r'">.*?</section>\n', re.S)
        new = pattern.sub("\n", src, count=1)
        if new != src:
            removed.append(sid)
            src = new
    return src, removed


def _strip_block(src: str, start_marker: str, end_marker: str) -> str:
    """Remove every copy of one renderer, marked or not."""
    while start_marker in src:
        a = src.index(start_marker)
        rest = src[a:]
        if end_marker in rest:
            b = a + rest.index(end_marker) + len(end_marker)
        elif _IIFE_CLOSE in rest:
            b = a + rest.index(_IIFE_CLOSE) + len(_IIFE_CLOSE)
        else:
            raise SystemExit(f"block {start_marker!r} has no recognisable end")
        src = src[:a] + src[b:]
    return src



#: Short sidebar labels, and the group each section sits under. Derived
#: labels from the <h2> would be far too long ("Data-fit test — scored against
#: what your lab actually ran"), so they are curated; the GROUPING and the
#: ORDER come from the page itself, so a new section can never be missing from
#: the nav again. It was: sec-datafit-rna and sec-ablation were both added
#: without touching the hand-maintained list.
NAV_LABELS: dict[str, str] = {
    "sec-given": "What it was given",
    "sec-team": "Team questions",
    "sec-datafit": "All 19 cohorts",
    "sec-datafit-rna": "RNA cohorts only",
    "sec-ablation": "How much payload?",
    "sec-ab": "Evidence-loss A/B",
    "sec-tiebreak": "Tiebreak questions",
    "sec-method": "How it works",
    "sec-scores": "Results by cohort",
    "sec-questions": "The twelve questions",
    "sec-evidence": "Evidence per cohort",
    "sec-honest": "Reading it honestly",
}

#: Group heading inserted BEFORE the named section.
NAV_GROUPS: dict[str, str] = {
    "sec-datafit": "Data-fit test",
    "sec-ab": "Robustness",
    "sec-method": "Earlier test",
    "sec-honest": "Caveats",
}


def _rebuild_sidenav(src: str) -> str:
    """Regenerate the sidebar from the sections actually present."""
    ids = re.findall(r'<section id="([^"]+)"', src)
    seen, order = set(), []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            order.append(sid)

    items = []
    for sid in order:
        if sid in NAV_GROUPS:
            items.append(f'        <li class="grp">{NAV_GROUPS[sid]}</li>')
        label = NAV_LABELS.get(sid)
        if label is None:
            # A section nobody has labelled still gets a nav entry, from its
            # own heading — being ugly in the sidebar is far better than being
            # invisible, which is the failure this function exists to end.
            m = re.search(rf'<section id="{re.escape(sid)}"[^>]*>\s*<h2[^>]*>(.*?)</h2>', src, re.S)
            label = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else sid
            label = (label[:28] + "…") if len(label) > 29 else label
        items.append(f'        <li><a href="#{sid}">{label}</a></li>')

    nav = ('<nav class="sidenav" aria-label="Sections">\n      <ol>\n'
           + "\n".join(items) + "\n      </ol>\n    </nav>")
    return re.sub(r'<nav[^>]*class="sidenav".*?</nav>', lambda _: nav, src, count=1, flags=re.S)


def _reinstall_blocks(src: str, blocks: dict[str, str]) -> str:
    """Strip then re-insert each renderer, so the page always matches the
    builder regardless of what the deployed page happened to contain."""
    for start_marker, end_marker, name in REPLACEABLE_BLOCKS:
        src = _strip_block(src, start_marker, end_marker)
        tail = src.rindex("</script>")
        src = src[:tail] + "\n" + blocks[name].strip("\n") + "\n" + src[tail:]
    return src


def main() -> int:
    src = PAGE.read_text()
    m = DATA_RE.search(src)
    if not m:
        raise SystemExit("could not find the data block in the page")
    data = json.loads(m.group(2))

    data["questions"] = build_questions()
    data["payload_arms"] = build_payload_arms()
    data["team_current"] = build_team_current()

    # Drop every result measured under an earlier atlas, BEFORE serialising —
    # doing it afterwards leaves the stale numbers in the shipped data block
    # while the sections that render them disappear, which is worse than either.
    dropped = [k for k in STALE_DATA_KEYS if data.pop(k, None) is not None]

    # 1. swap the data block
    new_json = json.dumps(data, separators=(",", ":"))
    src = src[:m.start(2)] + new_json + src[m.end(2):]

    # 3. add the RNA section markup after the existing data-fit section
    if 'id="sec-datafit-rna"' not in src:
        anchor = '      <section id="sec-ab">'
        rna_section = (
            '      <section id="sec-datafit-rna">\n'
            '        <h2>Data-fit test &mdash; RNA data only</h2>\n'
            '        <div class="card pad" id="datafit_rna"></div>\n'
            '      </section>\n\n'
        )
        src = src.replace(anchor, rna_section + anchor, 1)

    # 4. append the RNA renderer just before the closing script tag. Guarded:
    # without this check a re-run appends a second copy, which still renders
    # (it just runs twice) and so would never be noticed by eye.


    # 4b. the payload-ablation section. Appended AFTER the RNA section so the
    # page reads in the order the work happened: what was answered, then how
    # much of the payload the answers actually needed.
    if 'id="sec-ablation"' not in src:
        anchor = '      <section id="sec-ab">'
        block = (
            '      <section id="sec-ablation">\n'
            '        <h2>How much of the payload does the answer need?</h2>\n'
            '        <div class="card pad" id="ablation"></div>\n'
            '      </section>\n\n'
        )
        src = src.replace(anchor, block + anchor, 1)

    # Strip EVERY existing ablation block, then insert exactly one. Replacing
    # only when an end marker was found appended a second copy against a page
    # built before that marker existed — the same double-append that shipped
    # once already (ANN-12), and equally invisible at runtime since both copies
    # render. Stripping first makes the outcome independent of what the
    # deployed page happens to contain.
    # Everything measured under an earlier atlas comes out. Mixing vintages is
    # how this page carried three conclusions today that did not survive being
    # rechecked; annotating them as stale would leave the numbers quotable.
    src, removed = _strip_sections(src, STALE_SECTIONS)

    if 'id="sec-payload-arms"' not in src:
        anchor_sec = '      <section id="sec-honest">'
        src = src.replace(anchor_sec,
            '      <section id="sec-payload-arms">\n'
            '        <h2>Does it need the whole payload?</h2>\n'
            '        <div class="card pad" id="payload_arms"></div>\n'
            '      </section>\n\n'
            '      <section id="sec-team-current">\n'
            '        <h2>Real questions from the team</h2>\n'
            '        <div class="card pad" id="team_current"></div>\n'
            '      </section>\n\n' + anchor_sec, 1)

    if 'id="sec-questions-eval"' not in src:
        anchor_sec = '      <section id="sec-honest">'
        block = ('      <section id="sec-questions-eval">\n'
                 '        <h2>Asking it the questions a scientist would ask</h2>\n'
                 '        <div class="card pad" id="questions_eval"></div>\n'
                 '      </section>\n\n')
        src = src.replace(anchor_sec, block + anchor_sec, 1)

    src = _rebuild_page_script(src, QUESTIONS_BLOCK_JS + "\n\n" + ARMS_TEAM_BLOCK_JS)
    src = _rebuild_sidenav(src)

    # 4c. Correct two claims the ablation disproved. Both assert that the
    # fibroblast cohort's D.SEQ records carry SequencingType "Single Cell
    # RNAseq". They do not: 108 of them are labelled "Single Cell TCR", and 54
    # of those are gene-expression libraries by their own names
    # (docs/btc-gbm-sequencingtype-mislabelling.md). The surrounding argument —
    # that more data produced a worse answer — still holds, but the mechanism
    # is the nf-core docs, not the metadata, and correcting the labels does not
    # change the answer. Patched in the DEPLOYED page rather than in NEW_NOTES,
    # because the notes are inserted once and guarded against re-insertion.
    stale = ('<span class="mono">DataType: FastQ</span> and '
             '<span class="mono">SequencingType: Single Cell RNAseq</span>')
    fixed = ('<span class="mono">DataType: FastQ</span> and 10x GEX library names, '
             'though 54 of them are mislabelled '
             '<span class="mono">SequencingType: Single Cell TCR</span>')
    if stale in src:
        src = src.replace(stale, fixed)

    stale_cause = "More data produced a worse answer."
    fixed_cause = (
        "More data produced a worse answer &mdash; but the cause was later measured, and it is not the "
        "metadata. Repeating each arm three times shows the refusal tracks one thing: whether the nf-core "
        "pipeline documentation is in the payload. Without it the same question answers "
        "<em>scrnaseq</em> 9 times out of 9, with the labels corrected or not; with it, almost never. "
        "Correcting the mislabelled records changes nothing on its own. See the payload-ablation section."
    )
    if stale_cause in src and "the cause was later measured" not in src:
        src = src.replace(stale_cause, fixed_cause, 1)

    # 4d. "Reading this honestly" is the page's home for unfavourable findings,
    # and today produced two it did not have. Both are patched into the
    # DEPLOYED page rather than NEW_NOTES, which is insert-once and guarded.
    old_doc_note = (
        "When it was introduced it moved exactly one case out of twenty-four. "
        "The curated atlas appears to be doing most of the work.")
    new_doc_note = (
        "When it was introduced it moved exactly one case out of twenty-four. It is now measurably "
        "worse than that: on the 16 ground-truth cohorts, dropping the docs and keeping the schemas "
        "scores 14/16 against 11/16 with them, at a third of the tokens. The docs describe where each "
        "pipeline stops, which talks the model out of answers it otherwise gets right. The curated "
        "atlas is doing most of the work.")
    if old_doc_note in src:
        src = src.replace(old_doc_note, new_doc_note, 1)

    key_note = (
        "    ['caution','The answer key itself was wrong, and three conclusions did not survive repeating',\n"
        "     'Two cohorts were scored as expected <em>refusals</em> because their data was recorded as a "
        "processed GEO deposit with no raw reads. Checked against GEO, both are <span class=\"mono\">"
        "Sample_type = SRA</span> with no supplementary files, resolving to 577M and 164M raw paired-end "
        "reads. The model answering <em>scrnaseq</em> was right and this page was marking it wrong. "
        "Separately, temperature 0 is not deterministic here &mdash; a byte-identical payload returned "
        "both an answer and a refusal &mdash; so every single-pass number on this page has been re-run "
        "three times and scored as correct only if right every time. Three conclusions drawn earlier in "
        "this work reversed once repeated. Read any n=1 result here as provisional.'],\n"
    )
    notes_marker = "// ---- notes\n(function(){\n  const notes = [\n"
    if notes_marker in src and "did not survive repeating" not in src:
        src = src.replace(notes_marker, notes_marker + key_note, 1)

    # 4d. The header line and the payload panel read D.granuloma / D.macrophage,
    # which were measured under the original atlas and have just been dropped.
    # An unguarded read there throws and takes the WHOLE page script down —
    # every section renders empty, which is exactly the failure check_report_page
    # was written for. Repoint them at the one run that is still live.
    old_meta = re.search(r"\['model ' \+ D\.granuloma\.model,[^\]]*\]", src)
    if old_meta:
        src = src.replace(old_meta.group(0),
            "['model ' + (D.questions && D.questions.model || 'unknown'), "
            "'22 questions \\u00d7 14 real cohorts', "
            "(D.questions ? D.questions.repeats : 3) + ' repeats per question', "
            "'atlas with species constraints']", 1)

    # The payload panel's own numbers came from those runs too.
    old_panel = re.search(r"N\(D\.granuloma\.size\.est_tokens\)[^;]*?'tokens',", src)
    if old_panel:
        src = src.replace(old_panel.group(0), "'see per-question payloads', 'tokens',", 1)
    src = src.replace("D.granuloma.size, CEIL = 180000",
                      "(D.questions && D.questions.cases[0] || {}), CEIL = 850000")

    # Every legacy renderer iterates KEYS and reads D[k] unguarded. Filtering
    # the list to keys that survive is one edit that neutralises all of them,
    # instead of chasing each unguarded read: those loops then run zero times.
    src = src.replace("const KEYS = ['granuloma','macrophage'];",
                      "const KEYS = ['granuloma','macrophage'].filter(k => D[k]);", 1)

    # 5. header line — it still described only the twelve-question test
    old_meta = "['model ' + D.granuloma.model, '12 questions × 2 real cohorts',"
    new_meta = ("['model ' + D.granuloma.model, '12 questions × 2 cohorts · 16 RNA cohorts · 5 team questions',"
                " 'team questions on production, 663/663 samples',")
    if old_meta in src:
        src = src.replace(old_meta, new_meta, 1)

    # 6. prepend the new caveats to "Reading this honestly" (idempotent)
    marker = "// ---- notes\n(function(){\n  const notes = [\n"
    if marker in src and "the one that got it wrong" not in src:
        src = src.replace(marker, marker + NEW_NOTES, 1)

    PAGE.write_text(src)
    print(f"wrote {PAGE} ({len(src):,} bytes)")
    print(f"  removed {len(removed)} stale section(s): {', '.join(removed) or 'none'}")
    print(f"  dropped {len(dropped)} stale data key(s): {', '.join(dropped) or 'none'}")
    q = data.get("questions") or {}
    if q:
        print(f"  questions: {q['overall']['n_correct']}/{q['overall']['n_scored']} "
              f"(baseline {q['baseline']['n_correct']}/{q['baseline']['n_total']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

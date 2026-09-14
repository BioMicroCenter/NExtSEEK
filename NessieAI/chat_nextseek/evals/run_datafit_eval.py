#!/usr/bin/env python3
"""The data-fit eval: given real samples, does the model pick the pipeline the
lab actually ran — or refuse when nothing in the atlas fits?

`run_rna_selection.py` asks twelve invented scenarios ("mice", "tumour",
"beetle"...) against real cohorts whose actual biology frequently contradicts
the scenario. It measures whether the model can map a *described scenario* to
a pipeline. It cannot express "no pipeline fits", so a wrong recommendation on
unsuitable data can score as a pass.

This eval asks one neutral question — no modality hint, refusal explicitly
allowed — against 19 real cohorts pulled from `groundtruth_cohorts.json`, each
a group of `D.SEQ` samples from one study sharing the same recorded analysis
product (what the lab actually ran). Three of the nineteen are DNA (duplex
targeted capture) or Hi-C — outside the RNA atlas entirely — and the correct
answer for those is refusal. Naming any pipeline for them is the single most
important failure this eval can surface.

## `--rna-only`

The pipelines under development are RNA-only, so `--rna-only` restricts the
run to the 16 cohorts whose data are RNA, dropping the three DNA/Hi-C ones.
That would remove every refusal case, which is what makes a data-fit test
distinguishable from question pattern-matching — so the mode also relabels
the two `220720BRY` cohorts as expected refusals on their own evidence: their
`File_PrimaryData` is `GSM5937205` / `GSM5937206`, a GEO deposit of a
processed Seurat RDS object and a pre-computed expression matrix, with no raw
reads. Every nf-core RNA pipeline requires FASTQ, so nothing can be run on
them regardless of their `A.SCXP` label.

That relabel is deliberately narrower than "primary data is an accession".
A survey of `File_PrimaryData` across all 19 cohorts (written to
`primary_data_survey.json` by `survey_primary_data.py`) found SRA run and
experiment accessions — `SRR…` / `SRX…`, from which raw reads ARE
fetchable — on five RNA cohorts that answered normally: 250409KAM, 240709KAM,
220917SHA, 240422SHA and 220823SHA. The line that matters is processed
deposit versus raw reads, not accession versus filename.

One cohort, `241112SAS`, is genuinely ambiguous: it is labelled `A.GEX`
(bulk) but every sample's `SequencingType` says Single Cell RNA Sequencing —
the label and the data disagree. It is scored as its own `ambiguous` class:
either `rnaseq` or `scrnaseq` counts as CORRECT, and it is reported as a
separate line rather than folded into bulk or single-cell.

One cohort, `240910LAU` (176 samples, the largest here), carries no protocol
reference on its `D.SEQ` records at all — every other cohort's digest carries
some protocol text. This eval does not treat a poor result there as a defect;
it records each cohort's protocol count and extracted-character count
alongside the verdict so the correlation between "had protocol text" and
"got it right" can be read directly off the results.

Must run inside the nextseek container, where API + model credentials live:

    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/run_datafit_eval.py
    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/run_datafit_eval.py --rna-only

Writes to demo-output-datafit/ (demo-output-datafit-rna/ under --rna-only, so
a mode never overwrites the other's results): one <cohort>-size-report.json per cohort,
results.json (full per-cohort records, including n_protocols/protocol_chars),
and summary.json (per-class + overall accuracy, and the always-answer-
scrnaseq baseline).

This eval does not tune the question, the atlas, or the expectations to
improve the score. A poor result — including a named pipeline on the Hi-C or
duplex-capture cohorts — is the finding, not a bug to fix here.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demo_cohort_real import build_real_digest  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent
GROUNDTRUTH_PATH = EVALS_DIR / "groundtruth_cohorts.json"
DEFAULT_OUTPUT_DIR = EVALS_DIR / "demo-output-datafit"
DEFAULT_OUTPUT_DIR_RNA = EVALS_DIR / "demo-output-datafit-rna"

#: Max samples per cohort, taken as the first N of the cohort's UIDs sorted
#: lexicographically — deterministic, keeps the digest under the payload
#: ceiling even for the 111-sample cohort. Task constraint: never lower this
#: to dodge a PayloadTooLargeError without saying so.
SAMPLE_CAP = 10

#: Ground truth: the analysis product each study's D.SEQ samples actually fed
#: (see groundtruth_cohorts.json's `analysis` field), translated into the
#: atlas pipeline key(s) the lab's own choice corresponds to. Keyed by
#: (study, analysis) rather than study alone because one study — 220720BRY —
#: has two distinct cohorts (A.SCXP and A.SCCL+A.SCXP) sharing the same study
#: code with different sample sets.
#:
#: `acceptable` is the set of atlas pipeline keys that score CORRECT when
#: chosen alone. An empty set means the correct answer is refusal — nothing
#: in the RNA atlas fits this modality. This mapping is given, not
#: re-derived: A.SCXP -> scrnaseq, A.GEX -> rnaseq, A.ALN (duplex-sequenced
#: targeted DNA capture) and A.CHRM (Hi-C) -> neither is an RNA product, so
#: no atlas pipeline fits.
#:
#: 241112SAS is the one exception: labelled A.GEX (bulk) but every sample's
#: SequencingType says Single Cell RNA Sequencing. The label and the data
#: disagree — possibly pseudobulk quantified from single-cell libraries,
#: possibly a wrong field. It is its own `ambiguous` class: either rnaseq or
#: scrnaseq scores CORRECT, and it is reported as a separate line rather than
#: folded into bulk or single-cell.
EXPECTED_BY_COHORT: dict[tuple[str, str], dict[str, Any]] = {
    # --- refusal: outside the RNA atlas entirely ---
    "230306ESS::A.ALN": {"class": "refusal", "acceptable": frozenset()},
    "230303ESS::A.ALN": {"class": "refusal", "acceptable": frozenset()},
    "250422BAC::A.CHRM": {"class": "refusal", "acceptable": frozenset()},
    # --- bulk RNA ---
    "240910LAU::A.GEX": {"class": "bulk", "acceptable": frozenset({"rnaseq"})},
    "231101GRI::A.GEX": {"class": "bulk", "acceptable": frozenset({"rnaseq"})},
    "250409KAM::A.GEX": {"class": "bulk", "acceptable": frozenset({"rnaseq"})},
    "250605SHO::A.GEX": {"class": "bulk", "acceptable": frozenset({"rnaseq"})},
    "241219BRY::A.GEX": {"class": "bulk", "acceptable": frozenset({"rnaseq"})},
    "240709KAM::A.GEX": {"class": "bulk", "acceptable": frozenset({"rnaseq"})},
    # --- ambiguous: A.GEX label, single-cell SequencingType ---
    "241112SAS::A.GEX": {"class": "ambiguous", "acceptable": frozenset({"rnaseq", "scrnaseq"})},
    # --- single-cell ---
    "221031SHA::A.SCXP": {"class": "single-cell", "acceptable": frozenset({"scrnaseq"})},
    "220917SHA::A.SCXP": {"class": "single-cell", "acceptable": frozenset({"scrnaseq"})},
    "240422SHA::A.SCXP": {"class": "single-cell", "acceptable": frozenset({"scrnaseq"})},
    "220823SHA::A.SCXP": {"class": "single-cell", "acceptable": frozenset({"scrnaseq"})},
    "230328SAS::A.SCXP": {"class": "single-cell", "acceptable": frozenset({"scrnaseq"})},
    "231215SHA::A.SCXP": {"class": "single-cell", "acceptable": frozenset({"scrnaseq"})},
    "220720BRY::A.SCXP": {"class": "single-cell", "acceptable": frozenset({"scrnaseq"})},
    "230126SHA::A.SCXP": {"class": "single-cell", "acceptable": frozenset({"scrnaseq"})},
    "220720BRY::A.SCCL+A.SCXP": {"class": "single-cell", "acceptable": frozenset({"scrnaseq"})},
}

#: --rna-only: the cohorts whose data are not RNA at all. Dropped from the run
#: rather than scored, because the pipelines under development are RNA-only and
#: a DNA/Hi-C refusal is not a question those pipelines are being asked.
NON_RNA_COHORTS: frozenset[str] = frozenset({
    "230306ESS::A.ALN",    # duplex-sequenced targeted DNA capture
    "230303ESS::A.ALN",    # duplex-sequenced targeted DNA capture
    "250422BAC::A.CHRM",   # Hi-C chromatin conformation
})

#: --rna-only: cohorts whose expectation changes once the run is RNA-only.
#:
#: WITHDRAWN. This used to relabel both 220720BRY cohorts as expected refusals,
#: on the stated grounds that their `File_PrimaryData` (GSM5937205 /
#: GSM5937206) was "a GEO deposit of a processed Seurat RDS object and a
#: pre-computed expression matrix, with no raw reads anywhere".
#:
#: That was checked against GEO and is false. Both records are
#: `Sample_type = SRA` with `Sample_supplementary_file_1 = NONE` -- there are
#: no processed files at all -- and they resolve to raw paired-end RNA-Seq:
#:
#:     GSM5937205 -> SRX14400861 -> SRR18260176   577,490,040 spots, 48.5 Gbases
#:     GSM5937206 -> SRX14400862 -> SRR18260175   164,233,784 spots, 13.8 Gbases
#:
#: Both are public and fetchable, and agent_tools already routes SRR/GSM
#: accessions through nf-core/fetchngs on-cluster. So these cohorts ARE
#: runnable, their original single-cell / scrnaseq expectation stands, and the
#: model answering `scrnaseq` on them was correct while this table marked it
#: wrong.
#:
#: The consequence is deliberate and must not be quietly papered over:
#: `--rna-only` now has NO refusal cohorts. The genuine ones are the three
#: DNA/Hi-C studies, which exist only in the all-19 mode. An `--rna-only` score
#: therefore measures discrimination among RNA pipelines and says nothing about
#: whether the model can decline. The runner prints that.
RNA_ONLY_OVERRIDES: dict[str, dict[str, Any]] = {}


def _cohort_lookup_key(study: str, analysis: str) -> str:
    return f"{study}::{analysis}"


def _expectations(rna_only: bool) -> dict[str, dict[str, Any]]:
    """The (study, analysis) -> expectation table for this run's mode."""
    if not rna_only:
        return EXPECTED_BY_COHORT
    table = {k: v for k, v in EXPECTED_BY_COHORT.items() if k not in NON_RNA_COHORTS}
    table.update(RNA_ONLY_OVERRIDES)
    return table


def _expected_display(expected: dict[str, Any]) -> str:
    """Human-readable expected answer for the per-cohort table: a single
    pipeline key, 'rnaseq|scrnaseq' for the ambiguous class, or 'REFUSAL'."""
    acceptable = expected["acceptable"]
    if not acceptable:
        return "REFUSAL"
    return "|".join(sorted(acceptable))

QUESTION = (
    "Here is a set of samples from our database. Which nf-core pipeline "
    "should we run on them? If none of the pipelines available to you fit "
    "these samples, say so instead of choosing one."
)

SYSTEM_PROMPT = """You are the pipeline-selection judgement for NExtSEEK's Nessie assistant.

You will be given four sections of context, in this order:
  1. PIPELINE ATLAS — curated notes on nf-core RNA pipelines: what each one
     answers, what it assumes about the library prep, and how to tell it
     apart from pipelines that accept the same FASTQ files but answer a
     different question.
  2. SAMPLE DIGEST — what is actually known about THIS cohort's samples and
     protocols (metadata fields, grouping candidates, full protocol text).
  3. NF-CORE PIPELINE DOCS — each rich pipeline's own README, usage guide,
     and output guide.
  4. NF-CORE SCHEMAS — the live parameter schemas for the pipelines with a
     full schema fetched. Some pipelines only have atlas prose and no
     schema; that does not rule them out, it just means you have less to
     check.

Then you will be given a scientist's QUESTION. Decide which nf-core
pipeline(s), by their atlas key (e.g. "rnaseq", "rnasplice", "scrnaseq"),
genuinely fit the samples described in the digest.

Some sample sets will not match any pipeline in your atlas at all — the
digest may describe a library strategy, sequencing type, or assay that none
of your RNA pipelines are built for. When that is true, say so and return an
empty pipelines list. Refusing is a correct, expected answer when nothing
fits — it is not a hedge, and it is not something to avoid. Never force a
pipeline onto samples it does not match just to give a non-empty answer.

Respond with ONLY a single JSON object, no markdown code fence, no text
before or after it, in exactly this shape:

{"pipelines": ["<atlas key>", ...], "reason": "<one sentence>"}

`pipelines` must be a list of atlas pipeline keys, and MAY be empty if no
pipeline fits. `reason` must be one sentence explaining the choice (or the
refusal)."""

USER_TEMPLATE = """{payload}

## QUESTION

{question}

Respond with ONLY the JSON object described in your instructions."""


def _load_groundtruth(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def _extract_json_object(text: str) -> dict[str, Any]:
    """Pull the first balanced JSON object out of a model response, tolerating
    a leading/trailing markdown code fence or stray prose around it."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in model response: {text!r}")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(cleaned, start)
    if not isinstance(obj, dict):
        raise ValueError(f"Top-level JSON is not an object: {obj!r}")
    return obj


def query_model(client, model: str, budget: int | None, payload: str, question: str) -> dict[str, Any]:
    """Ask the model which pipeline(s), if any, fit `question` given `payload`.
    Returns {"chosen": [...], "reason": str, "raw_content": str, "parse_error": str|None}.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(payload=payload, question=question)},
    ]
    resp = client.chat(model=model, temperature=0, messages=messages, thinking_budget=budget)
    raw_content = resp.content or ""

    try:
        parsed = _extract_json_object(raw_content)
        chosen = parsed.get("pipelines")
        if not isinstance(chosen, list) or not all(isinstance(p, str) for p in chosen):
            raise ValueError(f"'pipelines' is not a list of strings: {chosen!r}")
        reason = parsed.get("reason", "")
        return {"chosen": chosen, "reason": reason, "raw_content": raw_content, "parse_error": None}
    except (ValueError, json.JSONDecodeError) as exc:
        return {"chosen": [], "reason": "", "raw_content": raw_content, "parse_error": str(exc)}


def grade(chosen: list[str], acceptable: frozenset[str]) -> tuple[str, bool]:
    """Return (verdict, correct).

    verdict is one of CORRECT, PARTIAL, WRONG, REFUSED — the literal shape of
    what happened, so a reader can see refusal behavior directly in the
    per-cohort table.

    `acceptable` is the set of pipeline keys that count as a correct answer.
    An empty set means refusal is the correct answer (the refusal classes).
    A set with more than one member (the ambiguous class) means naming any
    ONE of them alone is correct — this is the same "exact singleton match"
    rule the original single-pipeline grading used, just generalized to more
    than one acceptable singleton.

    correct folds REFUSED into the accuracy stats per the spec: a refusal
    scores correct exactly when a refusal was expected, and wrong otherwise.
    A raw REFUSED verdict can therefore be either accuracy outcome; PARTIAL
    is never counted as correct.
    """
    chosen_set = set(chosen)

    if not chosen_set:
        return "REFUSED", not acceptable

    if not acceptable:
        # A refusal was correct here; naming anything is wrong, no matter what.
        return "WRONG", False

    if len(chosen_set) == 1 and chosen_set <= acceptable:
        return "CORRECT", True
    if chosen_set & acceptable:
        return "PARTIAL", False
    return "WRONG", False


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"




def _accuracy(rs: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rs if r["verdict"] != "ERROR"]
    n = len(scored)
    return {
        "n_total": len(rs),
        "n_scored": n,
        "n_errored": len(rs) - n,
        "n_correct": sum(1 for r in scored if r["correct"]),
        "accuracy": (sum(1 for r in scored if r["correct"]) / n) if n else None,
    }


#: ambiguous is its own line, per spec, never folded into bulk or single-cell.
CLASSES = ["single-cell", "bulk", "refusal", "ambiguous"]


def build_summary(results: list[dict[str, Any]], agent: str, model: str,
                  rna_only: bool) -> dict[str, Any]:
    """The summary.json body. Shared by a live run and by --regrade, so a
    re-judged results.json can never sit beside a summary computed under the
    old answer key."""
    per_class = {c: _accuracy([r for r in results if r["expected_class"] == c]) for c in CLASSES}
    overall = _accuracy(results)
    # "How many cohorts expect scrnaseq" — the single-cell class only. The
    # ambiguous cohort is excluded even though scrnaseq is one of its two
    # acceptable answers, because the question is "what fraction is
    # unambiguously single-cell", not "what would a scrnaseq-always guess hit".
    n_scrnaseq = sum(1 for r in results if r["expected_class"] == "single-cell")
    baseline = (n_scrnaseq / len(results)) if results else None
    n_refusal = sum(1 for r in results if r["expected_class"] == "refusal")

    note = (
        f"Overall accuracy alone is misleading: {n_scrnaseq} of {len(results)} cohorts expect "
        "'scrnaseq' (the single-cell class), so always answering 'scrnaseq' scores at the baseline "
        "above without looking at the data at all. The 241112SAS cohort is scored separately as "
        "'ambiguous' (A.GEX label, single-cell SequencingType) and is not counted in this baseline."
    )
    if rna_only:
        note += (
            " RNA-ONLY MODE: not comparable to the all-19 run — the 3 DNA/Hi-C cohorts are dropped, "
            "and all three were answered correctly there, so removing them can only lower the "
            "headline."
        )
        if not n_refusal:
            note += (
                " This mode contains NO refusal cohorts, so the score measures discrimination among "
                "RNA pipelines and says nothing about whether the model can decline. The two "
                "220720BRY cohorts were previously relabelled as refusals on the grounds that their "
                "GEO deposits held only processed output; that was checked against GEO and is false "
                "(GSM5937205/GSM5937206 are Sample_type=SRA with no supplementary files, resolving "
                "to SRR18260176 / SRR18260175), so they are single-cell cohorts again."
            )
    return {
        "agent": agent,
        "model": model,
        "rna_only": rna_only,
        "per_class_accuracy": per_class,
        "overall_accuracy": overall,
        "baseline_always_answer_scrnaseq": {
            "n_scrnaseq_expected": n_scrnaseq,
            "n_total": len(results),
            "accuracy": baseline,
        },
        "note": note,
    }


def regrade(run_dir: Path, expectations: dict[str, dict[str, Any]]) -> int:
    """Re-judge a completed run against the current expectations, in place.

    Rewrites every record's per-repeat verdicts, the cohort-level `correct`
    (still all-repeats-correct), and summary.json. The model's stored answers
    are never touched — only the judgement of them.
    """
    res_path, sum_path = run_dir / "results.json", run_dir / "summary.json"
    if not res_path.exists():
        print(f"[regrade] no results.json in {run_dir}")
        return 1
    payload = json.loads(res_path.read_text())
    records = payload["results"]

    changed = []
    for r in records:
        key = _cohort_lookup_key(r["study"], r["analysis"])
        exp = expectations.get(key)
        if exp is None:
            continue
        acceptable = exp["acceptable"]
        was_correct = bool(r.get("correct"))
        runs = r.get("runs") or []
        if not runs:
            # A run from before --repeats existed: one answer, stored at the
            # top level. It is still re-judgeable, and skipping it would leave
            # the oldest results — the ones most likely to be quoted — scored
            # against a key nobody uses any more.
            if r.get("chosen") is None:
                continue
            runs = [{"chosen": r["chosen"], "reason": r.get("reason", ""),
                     "raw_content": r.get("raw_content", ""),
                     "parse_error": r.get("parse_error"),
                     "verdict": r.get("verdict"), "correct": r.get("correct")}]
            r["runs"] = runs
            r["n_repeats"] = 1
        for run in runs:
            if run.get("parse_error"):
                run["verdict"], run["correct"] = "ERROR", False
                continue
            run["verdict"], run["correct"] = grade(run["chosen"], acceptable)
        r["verdicts"] = [x["verdict"] for x in runs]
        r["stable"] = len(set(r["verdicts"])) == 1
        r["correct"] = all(x["correct"] for x in runs)
        r["verdict"] = collections.Counter(r["verdicts"]).most_common(1)[0][0]
        r["expected_class"] = exp["class"]
        r["expected_display"] = _expected_display(exp)
        r["expected_refusal_reason"] = exp.get("refusal_reason")
        if bool(r["correct"]) != was_correct:
            changed.append((key, was_correct, bool(r["correct"])))

    payload["regraded"] = True
    res_path.write_text(json.dumps(payload, indent=2) + "\n")

    meta = json.loads(sum_path.read_text()) if sum_path.exists() else {}
    sum_path.write_text(json.dumps(build_summary(
        records, meta.get("agent", "pipeline_agent"), meta.get("model", "unknown"),
        bool(meta.get("rna_only", True))), indent=2) + "\n")

    n_ok = sum(1 for r in records if r.get("correct"))
    print(f"[regrade] {run_dir.name}: {n_ok}/{len(records)} correct after re-judging")
    for key, old, new in changed:
        print(f"    {key}: {'correct' if old else 'wrong'} -> {'correct' if new else 'wrong'}")
    if not changed:
        print("    no cohort changed verdict")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--groundtruth", type=Path, default=GROUNDTRUTH_PATH, help="Path to groundtruth_cohorts.json")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Directory for results (default: demo-output-datafit, "
                             "or demo-output-datafit-rna under --rna-only)")
    parser.add_argument("--agent", default="pipeline_agent", help="Registered agent name to pull model/client from")
    parser.add_argument(
        "--rna-only", action="store_true",
        help="Restrict to the 16 RNA cohorts, dropping the 3 DNA/Hi-C ones, and expect refusal on "
             "the two 220720BRY cohorts whose primary data is a processed GEO deposit with no raw reads.",
    )
    parser.add_argument(
        "--regrade", type=Path, default=None, metavar="DIR",
        help="Re-score an existing run's results.json against the CURRENT expectations and "
             "rewrite its summary.json. Makes no model calls: a model's answer does not depend "
             "on the answer key, so when the key is corrected the recorded answers can simply be "
             "re-judged. Exists because the key WAS wrong (see RNA_ONLY_OVERRIDES) and re-running "
             "would have cost ~10M tokens to reproduce answers already on disk.",
    )
    parser.add_argument(
        "--sections", default=None, metavar="A,B",
        help="Comma-separated payload sections to send, from: " + ",".join(SECTION_NAMES) + ". "
             "Default: all four, which is what production sends. Use this to check whether a "
             "cheaper payload holds up on the ground-truth cohorts before cutting anything.",
    )
    parser.add_argument(
        "--repeats", type=int, default=1, metavar="N",
        help="Ask each cohort N times. Temperature 0 is not deterministic on this model, so N=1 "
             "cannot tell a genuine miss from an unlucky draw. A cohort counts as correct only if "
             "it was correct on EVERY repeat.",
    )
    parser.add_argument(
        "--study", action="append", default=None,
        help="Limit the run to one or more studies (repeatable), e.g. --study 250422BAC. Default: all 19. "
             "Note: 220720BRY names two distinct cohorts (A.SCXP and A.SCCL+A.SCXP) — both run.",
    )
    args = parser.parse_args()

    sections = None
    if args.sections:
        sections = [x.strip() for x in args.sections.split(",") if x.strip()]
        unknown = [x for x in sections if x not in SECTION_NAMES]
        if unknown:
            parser.error(f"--sections: unknown section(s): {', '.join(unknown)}; "
                         f"expected any of {', '.join(SECTION_NAMES)}")

    expectations = _expectations(args.rna_only)

    if args.regrade:
        return regrade(args.regrade, expectations)

    out_dir: Path = args.output_dir or (DEFAULT_OUTPUT_DIR_RNA if args.rna_only else DEFAULT_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    cohorts = _load_groundtruth(args.groundtruth)
    if args.rna_only:
        dropped = [c for c in cohorts if _cohort_lookup_key(c["study"], c["analysis"]) in NON_RNA_COHORTS]
        cohorts = [c for c in cohorts if _cohort_lookup_key(c["study"], c["analysis"]) not in NON_RNA_COHORTS]
        print("[run_datafit_eval] --rna-only: dropped "
              f"{len(dropped)} non-RNA cohort(s): "
              + ", ".join(f"{c['study']} ({c['seqtype']})" for c in dropped))
        n_refusal = sum(1 for e in expectations.values() if not e["acceptable"])
        if n_refusal:
            print(f"[run_datafit_eval] --rna-only: {n_refusal} refusal cohort(s) expected\n")
        else:
            print("[run_datafit_eval] --rna-only: NO refusal cohorts in this mode. The score "
                  "measures discrimination among RNA pipelines only, and says nothing about "
                  "whether the model can decline -- the genuine refusal cohorts are the three "
                  "DNA/Hi-C studies, which exist only in the all-19 mode.\n")
    if args.study:
        wanted = set(args.study)
        cohorts = [c for c in cohorts if c["study"] in wanted]
        missing = wanted - {c["study"] for c in cohorts}
        if missing:
            print(f"[run_datafit_eval] WARNING: unknown --study value(s), ignored: {sorted(missing)}")

    # study alone is not a unique key — 220720BRY has two cohorts sharing it —
    # so filenames/output keys disambiguate only when a collision exists,
    # leaving every other cohort's output filename unchanged from prior runs.
    study_counts: dict[str, int] = {}
    for c in cohorts:
        study_counts[c["study"]] = study_counts.get(c["study"], 0) + 1

    config = ChatConfig()
    client, model, budget = config.get_agent_model(args.agent)
    print(f"[run_datafit_eval] Using agent '{args.agent}' -> provider={getattr(client, 'provider', '?')} model={model}")
    print(f"[run_datafit_eval] {len(cohorts)} cohort(s) to run, sample cap={SAMPLE_CAP}\n")

    results: list[dict[str, Any]] = []

    for cohort in cohorts:
        study = cohort["study"]
        analysis = cohort["analysis"]
        lookup_key = _cohort_lookup_key(study, analysis)
        expected = expectations.get(lookup_key)
        if expected is None:
            print(f"  [SKIP] {lookup_key}: no entry in EXPECTED_BY_COHORT — "
                  f"groundtruth_cohorts.json has an unrecognized (study, analysis) pair")
            continue
        acceptable = expected["acceptable"]
        expected_class = expected["class"]
        expected_display = _expected_display(expected)

        # Unique only when the study name collides across cohorts (220720BRY).
        cohort_key = study if study_counts[study] == 1 else f"{study}-{analysis.replace('+', '_')}"

        all_uids = cohort["uids"]
        uids_used = sorted(all_uids)[:SAMPLE_CAP]
        n_available = cohort["n"]
        n_used = len(uids_used)

        record: dict[str, Any] = {
            "study": study,
            "cohort_key": cohort_key,
            "analysis": analysis,
            "seqtype": cohort["seqtype"],
            "libstrat": cohort["libstrat"],
            "expected_class": expected_class,
            "expected_acceptable": sorted(acceptable),
            "expected_display": expected_display,
            "expected_refusal_reason": expected.get("refusal_reason"),
            "n_available": n_available,
            "n_used": n_used,
            "uids_used": uids_used,
            "question": QUESTION,
        }

        print(f"[run_datafit_eval] {cohort_key} ({expected_class}, expected={expected_display}, "
              f"{n_used}/{n_available} samples)...")

        try:
            digest = build_real_digest(config, uids=uids_used)
        except DigestError as exc:
            record["error"] = f"DigestError: {exc}"
            record["verdict"] = "ERROR"
            record["correct"] = False
            results.append(record)
            print(f"  [ERROR] {cohort_key}: digest build failed — {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - live network call, record and continue
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["verdict"] = "ERROR"
            record["correct"] = False
            results.append(record)
            print(f"  [ERROR] {cohort_key}: unexpected error building digest — {type(exc).__name__}: {exc}")
            continue

        # Protocol coverage: how many protocol records were discovered for
        # this cohort's samples, and how many characters of protocol text
        # were actually extracted from their attachments. 240910LAU is
        # expected to show 0/0 here — it carries no protocol reference on any
        # of its D.SEQ records — while every other cohort should show at
        # least some protocol text, letting the correlation between "had
        # protocol text" and "got it right" be read off the results directly.
        protocols = digest.get("protocols") or {}
        n_protocols = len(protocols)
        protocol_chars = sum(
            len(att.get("text") or "")
            for p in protocols.values()
            for att in (p.get("attachments") or [])
        )
        record["n_protocols"] = n_protocols
        record["protocol_chars"] = protocol_chars

        # Build the selection context without the ceiling so we always get a
        # size report, even for a cohort that would exceed it; we then apply
        # the real ceiling ourselves before deciding whether to call the model.
        ctx = build_selection_context(config=None, uids=uids_used, digest=digest, max_tokens=10**12)
        size_report = ctx.size_report(sections)
        (out_dir / f"{cohort_key}-size-report.json").write_text(json.dumps(size_report, indent=2) + "\n")
        record["payload_size_report"] = size_report

        if size_report["est_tokens"] > DEFAULT_MAX_TOKENS:
            record["error"] = (
                f"PayloadTooLargeError: {size_report['est_tokens']:,} est. tokens "
                f"exceeds ceiling of {DEFAULT_MAX_TOKENS:,}"
            )
            record["verdict"] = "ERROR"
            record["correct"] = False
            results.append(record)
            print(f"  [ERROR] {cohort_key}: payload too large — {size_report['est_tokens']:,} tokens "
                  f"(ceiling {DEFAULT_MAX_TOKENS:,}), protocols={n_protocols} chars={protocol_chars:,}")
            continue

        payload = ctx.to_prompt_text(sections)
        runs = []
        for _ in range(args.repeats):
            outcome = query_model(client, model, budget, payload, QUESTION)
            if outcome["parse_error"]:
                runs.append({"chosen": outcome["chosen"], "reason": outcome["reason"],
                             "raw_content": outcome["raw_content"],
                             "parse_error": outcome["parse_error"],
                             "verdict": "ERROR", "correct": False})
                continue
            verdict, correct = grade(outcome["chosen"], acceptable)
            runs.append({"chosen": outcome["chosen"], "reason": outcome["reason"],
                         "raw_content": outcome["raw_content"], "parse_error": None,
                         "verdict": verdict, "correct": correct})

        record["runs"] = runs
        record["verdicts"] = [r["verdict"] for r in runs]
        record["n_repeats"] = len(runs)
        record["stable"] = len(set(record["verdicts"])) == 1
        # A cohort counts as correct only when it was correct on EVERY repeat.
        # Scoring the majority would let a cohort that flips half the time
        # count the same as one that is reliably right, which is the specific
        # mistake --repeats exists to prevent.
        record["correct"] = all(r["correct"] for r in runs)
        record["verdict"] = collections.Counter(record["verdicts"]).most_common(1)[0][0]
        first = runs[0]
        record["chosen"] = first["chosen"]
        record["reason"] = first["reason"]
        record["raw_content"] = first["raw_content"]
        record["parse_error"] = first["parse_error"]
        results.append(record)
        answers = " | ".join(sorted({", ".join(r["chosen"]) or "(empty)" for r in runs}))
        spread = "" if record["stable"] else "  spread=" + ",".join(record["verdicts"])
        print(f"  [{record['verdict']:<8}] {cohort_key}: chosen={answers} "
              f"(protocols={n_protocols} chars={protocol_chars:,}){spread}")

    # ------------------------------------------------------------------
    # Per-cohort table
    # ------------------------------------------------------------------
    print("\n" + "=" * 130)
    print(f"{'cohort':<20} {'class':<12} {'expected':<16} {'verdict':<9} {'protocols':<10} {'proto_chars':<12} {'chosen'}")
    print("-" * 130)
    for r in results:
        chosen_display = ", ".join(r.get("chosen") or []) or ("(error)" if r.get("error") else "(empty)")
        proto_n = r.get("n_protocols", "-")
        proto_chars = f"{r['protocol_chars']:,}" if "protocol_chars" in r else "-"
        print(f"{r.get('cohort_key', r['study']):<20} {r['expected_class']:<12} {r.get('expected_display', ''):<16} "
              f"{r['verdict']:<9} {str(proto_n):<10} {proto_chars:<12} {chosen_display}")
    print("=" * 130)

    # ------------------------------------------------------------------
    # Per-class + overall accuracy
    # ------------------------------------------------------------------
    def _accuracy(rs: list[dict[str, Any]]) -> dict[str, Any]:
        scored = [r for r in rs if r["verdict"] != "ERROR"]
        n = len(scored)
        n_correct = sum(1 for r in scored if r["correct"])
        return {
            "n_total": len(rs),
            "n_scored": n,
            "n_errored": len(rs) - n,
            "n_correct": n_correct,
            "accuracy": (n_correct / n) if n else None,
        }

    # ambiguous is its own line, per spec, never folded into bulk or single-cell.
    classes = ["single-cell", "bulk", "refusal", "ambiguous"]
    per_class = {cls: _accuracy([r for r in results if r["expected_class"] == cls]) for cls in classes}
    overall = _accuracy(results)

    # Baseline is literally "how many cohorts expect scrnaseq" (the single-cell
    # class only) — the ambiguous cohort is deliberately excluded from this
    # count even though scrnaseq happens to also be an acceptable answer
    # there, because the baseline question is "what fraction of cohorts are
    # unambiguously single-cell", not "what fraction would a scrnaseq-always
    # guess happen to satisfy."
    n_scrnaseq_expected = sum(1 for r in results if r["expected_class"] == "single-cell")
    baseline_always_scrnaseq = (n_scrnaseq_expected / len(results)) if results else None

    print("\nPER-CLASS ACCURACY:")
    for cls in classes:
        acc = per_class[cls]
        pct = f"{acc['accuracy']*100:.0f}%" if acc["accuracy"] is not None else "n/a"
        print(f"  {cls:<12} {acc['n_correct']}/{acc['n_scored']} correct ({pct}), "
              f"{acc['n_errored']} errored of {acc['n_total']} total")
    overall_pct = f"{overall['accuracy']*100:.0f}%" if overall["accuracy"] is not None else "n/a"
    print(f"  {'OVERALL':<12} {overall['n_correct']}/{overall['n_scored']} correct ({overall_pct}), "
          f"{overall['n_errored']} errored of {overall['n_total']} total")
    baseline_pct = f"{baseline_always_scrnaseq*100:.0f}%" if baseline_always_scrnaseq is not None else "n/a"
    print(f"\n  BASELINE (always answer 'scrnaseq'): {n_scrnaseq_expected}/{len(results)} = {baseline_pct}")
    print("  The overall number alone is misleading: read it against this baseline, not against 100%.")
    if args.rna_only:
        print("  RNA-ONLY MODE: not comparable to the all-19 run. The 3 DNA/Hi-C cohorts — every one of "
              "which was answered correctly there — are gone, and the 2 220720BRY cohorts moved from "
              "single-cell to refusal.")

    refusal_results = [r for r in results if r["expected_class"] == "refusal"]
    print("\nREFUSAL COHORTS — the headline finding:")
    for r in refusal_results:
        if r.get("error"):
            print(f"  - {r.get('cohort_key', r['study'])}: ERRORED — {r['error']}")
        else:
            chosen_display = ", ".join(r.get("chosen") or []) or "(empty — correctly refused)"
            print(f"  - {r.get('cohort_key', r['study'])}: verdict={r['verdict']} chosen={chosen_display}")
            if r.get("expected_refusal_reason"):
                print(f"      why refusal is correct: {r['expected_refusal_reason']}")
            print(f"      reason: {r.get('reason', '')!r}")

    ambiguous_results = [r for r in results if r["expected_class"] == "ambiguous"]
    if ambiguous_results:
        print("\nAMBIGUOUS COHORT(S) — labelled A.GEX (bulk) but SequencingType says single-cell; "
              "either rnaseq or scrnaseq scores CORRECT:")
        for r in ambiguous_results:
            if r.get("error"):
                print(f"  - {r.get('cohort_key', r['study'])}: ERRORED — {r['error']}")
            else:
                chosen_display = ", ".join(r.get("chosen") or []) or "(empty)"
                print(f"  - {r.get('cohort_key', r['study'])}: verdict={r['verdict']} chosen={chosen_display}")
                print(f"      reason: {r.get('reason', '')!r}")

    # Protocol coverage vs. verdict, for the 240910LAU (no-protocol) question —
    # printed for every non-errored cohort so the correlation is visible
    # without cross-referencing results.json.
    print("\nPROTOCOL COVERAGE (n_protocols / extracted chars) BY COHORT:")
    for r in results:
        if "n_protocols" not in r:
            continue
        print(f"  - {r.get('cohort_key', r['study']):<20} protocols={r['n_protocols']:<3} "
              f"chars={r['protocol_chars']:<8,} verdict={r['verdict']}")

    # ------------------------------------------------------------------
    # Write outputs
    # ------------------------------------------------------------------
    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps({
        "agent": args.agent,
        "model": model,
        "question": QUESTION,
        "sample_cap": SAMPLE_CAP,
        "rna_only": args.rna_only,
        "results": results,
    }, indent=2) + "\n")

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(
        build_summary(results, args.agent, model, args.rna_only), indent=2) + "\n")

    print(f"\nFull results written to: {results_path}")
    print(f"Summary written to: {summary_path}")
    print(f"Per-cohort size reports written to: {out_dir}/<cohort_key>-size-report.json")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

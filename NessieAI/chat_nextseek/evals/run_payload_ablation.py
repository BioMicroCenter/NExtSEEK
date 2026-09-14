#!/usr/bin/env python3
"""Ask the five team questions at four levels of payload, to find the least
information that still produces the full-information answer.

## The arms

The selection payload has four sections (selection_context.SECTION_NAMES). The
atlas and the digest are the irreducible pair — the atlas is the menu of
pipelines and the digest is the evidence about these samples, and no selection
is possible without both. So they are held fixed and the two bulky reference
sections are varied:

    atlas+digest              the floor
    atlas+digest+docs         + each pipeline's README / usage / output prose
    atlas+digest+schemas      + each pipeline's parameter schema
    all                       everything (what production sends today)

Sections are OMITTED in the short arms, never blanked — an empty docs body
reads as "the docs were fetched and are empty", which is a different claim.

Each question's context is built ONCE and sliced four ways, so the arms differ
in nothing but which sections were included, and the docs/schemas are fetched
from nf-core once rather than four times.

## What "successful" means here, precisely

There is no external ground truth for these five questions — they are the
team's real questions, not graded cases. What there IS:

  - Four of them have been answered identically by every full-payload run to
    date (with protocols and without): rnaseq, rnasplice, rnasplice,
    hlatyping.
  - fibroblast-subtypes is the exception. Its expected answer, scrnaseq, was
    confirmed by the user and reproduced once the SequencingType mislabelling
    was corrected (see docs/btc-gbm-sequencingtype-mislabelling.md).

So this measures REPRODUCTION OF THE FULL-INFORMATION ANSWER, not correctness
against an independent standard. An arm that matches has shown it needs no
more than those sections to reach the same conclusion; it has not been shown
to be right. Quote it that way.

## Which digest

Defaults to prod_digests_seqtype_counterfactual.json, NOT prod_digests.json.
54 of the fibroblast cohort's gene-expression libraries are labelled
"Single Cell TCR" in production; with those labels, that question cannot reach
its expected answer in ANY arm, and would contribute nothing but a constant
failure. The counterfactual corrects only those counts. Every other question's
digest is byte-identical between the two files.

Pass --digests explicitly to run against production metadata as it stands.

Must run inside the nextseek container, where the model credentials live:

    docker compose exec -T nextseek uv run python \\
        /app/chat_nextseek/evals/run_payload_ablation.py
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

from chat_nextseek.config import ChatConfig
from chat_nextseek.pipeline.selection_context import (
    DEFAULT_MAX_TOKENS,
    build_selection_context,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_team_questions import protocol_report, query_model  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = EVALS_DIR / "team_questions.json"
DEFAULT_DIGESTS = EVALS_DIR / "prod_digests_seqtype_counterfactual.json"
DEFAULT_OUTPUT_DIR = EVALS_DIR / "demo-output-ablation"

#: (label, sections). None means every section.
ARMS: tuple[tuple[str, list[str] | None], ...] = (
    ("atlas+digest", ["atlas", "digest"]),
    ("atlas+digest+docs", ["atlas", "digest", "docs"]),
    ("atlas+digest+schemas", ["atlas", "digest", "schemas"]),
    ("all", None),
)

#: The full-payload answer for each question — the target an arm has to
#: reproduce. NOT independent ground truth; see this module's docstring.
EXPECTED: dict[str, list[str]] = {
    "unsupervised-relationship": ["rnaseq"],
    "retained-introns": ["rnasplice"],
    "fibroblast-subtypes": ["scrnaseq"],
    "isoform-length-vs-tumour-fraction": ["rnasplice"],
    "hla-types-gbm": ["hlatyping"],
}


def summarise_cell(verdicts: list[str]) -> str:
    """One cell's repeats as "3/3 EXACT" or "2/3 EXACT +REFUSED".

    A split cell is never collapsed to its majority silently — the whole reason
    repeats exist here is that a single sample cannot tell a weak arm from an
    unlucky draw, and hiding the split would put that mistake back.
    """
    counts = collections.Counter(verdicts)
    (top, n_top), = counts.most_common(1)
    label = f"{n_top}/{len(verdicts)} {top}"
    others = sorted(v for v in counts if v != top)
    return label + (" +" + ",".join(others) if others else "")


def grade(chosen: list[str], expected: list[str]) -> str:
    """EXACT / PARTIAL / MISS / REFUSED.

    PARTIAL is kept distinct from EXACT rather than folded into it: naming the
    right pipeline alongside two others is a weaker result than committing to
    it, and collapsing the two would let an arm that hedges everything score as
    well as one that decides.
    """
    if not chosen:
        return "REFUSED"
    if set(chosen) == set(expected):
        return "EXACT"
    if set(expected) <= set(chosen):
        return "PARTIAL"
    return "MISS"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    parser.add_argument("--digests", type=Path, default=DEFAULT_DIGESTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--agent", default="pipeline_agent")
    parser.add_argument("--only", action="append", metavar="ID", default=None,
                        help="Run only this question id (repeatable).")
    parser.add_argument("--arm", action="append", metavar="LABEL", default=None,
                        help="Run only this arm (repeatable), e.g. --arm atlas+digest.")
    parser.add_argument("--repeats", type=int, default=1, metavar="N",
                        help="Ask each question/arm N times. Temperature 0 is NOT deterministic "
                             "on this model -- a byte-identical 628,662-char payload has been "
                             "observed returning both scrnaseq and a refusal -- so N=1 cannot "
                             "distinguish a weak arm from an unlucky draw. Costs N x the tokens.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Assemble every payload and print the size matrix, but make no "
                             "model calls. Use this to see the token cost before paying it.")
    args = parser.parse_args()

    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    config = ChatConfig()
    client, model, budget = config.get_agent_model(args.agent)

    questions = json.loads(args.questions.read_text())
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {q["id"] for q in questions}
        if unknown:
            parser.error(f"--only: no such question id: {', '.join(sorted(unknown))}")
        questions = [q for q in questions if q["id"] in wanted]

    arms = ARMS
    if args.arm:
        wanted = set(args.arm)
        unknown = wanted - {label for label, _ in ARMS}
        if unknown:
            parser.error(f"--arm: no such arm: {', '.join(sorted(unknown))}")
        arms = tuple(a for a in ARMS if a[0] in wanted)

    digests_by_id = {e["id"]: e for e in json.loads(args.digests.read_text())}

    print(f"[ablation] agent={args.agent} model={model}")
    print(f"[ablation] digests: {args.digests.name}")
    if args.digests == DEFAULT_DIGESTS:
        print("[ablation] NOTE: this is the SequencingType counterfactual, not production "
              "metadata. Only fibroblast-subtypes' inventory counts differ.")
    print(f"[ablation] {len(questions)} question(s) x {len(arms)} arm(s) = "
          f"{len(questions) * len(arms) * args.repeats} model call(s)\n")

    results: list[dict[str, Any]] = []
    total_tokens = 0

    for q in questions:
        qid, question_text = q["id"], q["question"]
        entry = digests_by_id.get(qid) or {}
        digest = entry.get("digest")
        if not digest:
            print(f"[ablation] {qid}: no pre-built digest, skipping")
            continue

        # Built once, sliced per arm: the arms must differ in nothing but their
        # sections, and nf-core gets one fetch rather than four.
        ctx = build_selection_context(config=config, uids=[], digest=digest,
                                      max_tokens=10**12)
        expected = EXPECTED.get(qid, [])
        print(f"[ablation] {qid}  (expects {', '.join(expected) or 'n/a'})")
        print(f"    {protocol_report(digest)['state']}")

        for arm_label, sections in arms:
            size = ctx.size_report(sections)
            # Per CALL, not per cell: with --repeats N the same payload is sent
            # N times, and a total that counts it once understates the bill by
            # a factor of N -- which is exactly the number someone reads to
            # decide whether to run this again.
            total_tokens += size["est_tokens"] * (1 if args.dry_run else args.repeats)
            record: dict[str, Any] = {
                "id": qid, "question": question_text, "arm": arm_label,
                "sections": size["sections"], "expected": expected,
                "payload_size_report": size,
            }

            if args.dry_run:
                print(f"    {arm_label:<22} {size['est_tokens']:>8,} tokens  (dry run)")
                results.append(record)
                continue

            if size["est_tokens"] > DEFAULT_MAX_TOKENS:
                record["error"] = (f"PayloadTooLargeError: {size['est_tokens']:,} est. tokens "
                                   f"exceeds ceiling of {DEFAULT_MAX_TOKENS:,}")
                print(f"    {arm_label:<22} [ERROR] payload too large")
                results.append(record)
                continue

            payload_text = ctx.to_prompt_text(sections)
            runs = []
            for _ in range(args.repeats):
                outcome = query_model(client, model, budget, payload_text, question_text)
                runs.append({
                    "chosen": outcome["chosen"], "reason": outcome["reason"],
                    "raw_content": outcome["raw_content"],
                    "parse_error": outcome["parse_error"],
                    "verdict": grade(outcome["chosen"], expected),
                })
            verdicts = [r["verdict"] for r in runs]
            record["runs"] = runs
            record["verdicts"] = verdicts
            record["cell"] = summarise_cell(verdicts)
            record["stable"] = len(set(verdicts)) == 1
            # Kept so a single-repeat run reads exactly like it did before.
            record["verdict"] = collections.Counter(verdicts).most_common(1)[0][0]
            record["chosen"] = runs[0]["chosen"]
            record["reason"] = runs[0]["reason"]
            results.append(record)
            answers = " | ".join(
                sorted({", ".join(r["chosen"]) or "(refused)" for r in runs})
            )
            print(f"    {arm_label:<22} {size['est_tokens']:>8,} tok  "
                  f"{record['cell']:<20} {answers}")
        print()

    # ---- matrix -----------------------------------------------------------
    arm_labels = [label for label, _ in arms]
    qids = [q["id"] for q in questions if digests_by_id.get(q["id"], {}).get("digest")]
    width = max((len(q) for q in qids), default=10) + 2

    print("=" * (width + len(arm_labels) * 24))
    print(f"{'question':<{width}}" + "".join(f"{a:<24}" for a in arm_labels))
    print("-" * (width + len(arm_labels) * 24))
    by_key = {(r["id"], r["arm"]): r for r in results}
    for qid in qids:
        cells = []
        for arm in arm_labels:
            r = by_key.get((qid, arm), {})
            cell = r.get("cell", "dry-run" if args.dry_run else "?")
            chosen = ", ".join(r.get("chosen") or []) or "-"
            marker = "" if r.get("stable", True) else " *"
            cells.append(f"{cell} {chosen}{marker}"[:23].ljust(24))
        print(f"{qid:<{width}}" + "".join(cells))
    print("-" * (width + len(arm_labels) * 24))

    if not args.dry_run:
        totals = []
        for arm in arm_labels:
            rows = [r for r in results if r["arm"] == arm and "verdicts" in r]
            n_always = sum(1 for r in rows if set(r["verdicts"]) == {"EXACT"})
            n_ever = sum(1 for r in rows if "EXACT" in r["verdicts"])
            cell = f"{n_always}/{len(rows)} exact"
            if n_ever != n_always:
                cell += f" ({n_ever} sometimes)"
            totals.append(cell.ljust(24))
        print(f"{'EXACT':<{width}}" + "".join(totals))
    tokens = []
    for arm in arm_labels:
        rows = [r for r in results if r["arm"] == arm]
        avg = sum(r["payload_size_report"]["est_tokens"] for r in rows) // max(1, len(rows))
        tokens.append(f"~{avg:,} tok/question".ljust(24))
    print(f"{'PAYLOAD':<{width}}" + "".join(tokens))
    print("=" * (width + len(arm_labels) * 24))
    print(f"\ntotal payload across all calls: ~{total_tokens:,} est. tokens")

    payload = {
        "agent": args.agent, "model": model, "repeats": args.repeats,
        "digests_source": str(args.digests),
        "digests_are_counterfactual": args.digests == DEFAULT_DIGESTS,
        "arms": {label: (sections or list(arm_labels and ["atlas", "digest", "docs", "schemas"]))
                 for label, sections in ARMS},
        "expected_is_full_payload_answer_not_ground_truth": True,
        "results": results,
    }
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nwritten: {out_dir / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

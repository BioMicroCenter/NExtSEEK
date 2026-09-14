#!/usr/bin/env python3
"""Build a two-real-cohort comparison for the talk: same question, same
atlas, same evaluator, two different REAL payloads pulled live from
production NExtSEEK — do the model's answer and reasoning change to match
the data?

Both payloads compared here carry the same four sections (atlas, digest,
docs, schemas) built by the current `selection_context.py` — an *identical*
atlas, identical nf-core docs, and identical schemas (the same six "rich"
pipelines, fetched at the same pinned GitHub revisions) on both sides. The
only thing that varies is the sample digest: each cohort's actual metadata
and protocol text, pulled live and read-only from NExtSEEK. Both cohorts are
real lab data — there is no fabricated cohort anywhere in this comparison.

Reads the two eval result files already written by run_rna_selection.py
--cohort <key> for each of the two cohorts being compared, and writes a
side-by-side markdown comparison. Does not call the model or the network
itself — it only reads JSON that's already on disk, so it can run on the
host:

    uv run python evals/cohort_comparison.py

Requires both cohorts' results already on disk (default: granuloma vs.
macrophage — see demo_cohort_real.COHORTS for the full registry):
    demo-output-granuloma/06-eval-results.json  (... run_rna_selection.py --cohort granuloma)
    demo-output-macrophage/06-eval-results.json (... run_rna_selection.py --cohort macrophage)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(EVALS_DIR))
from demo_cohort_real import COHORTS  # noqa: E402

DEFAULT_COHORT_A = "granuloma"
DEFAULT_COHORT_B = "macrophage"

#: The case this comparison leads with: intent-only question, no mention of
#: cohort or modality, that a single-cell cohort and a bulk cohort should
#: answer differently if the system is actually reading the sample evidence
#: rather than pattern-matching the question text.
HEADLINE_CASE_ID = "per-cell-expression"


def _results_path(cohort_key: str) -> Path:
    return EVALS_DIR / f"demo-output-{cohort_key}" / "06-eval-results.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(
            f"Missing {path}. Run `run_rna_selection.py --cohort <key>` for that cohort first — "
            "see this script's docstring."
        )
    return json.loads(path.read_text())


def _find_case(results: dict[str, Any], case_id: str) -> dict[str, Any]:
    for r in results.get("results", []):
        if r["id"] == case_id:
            return r
    raise SystemExit(f"Case {case_id!r} not found in {results.get('cohort', '?')} results.")


def _fmt_case(label: str, run: dict[str, Any], case: dict[str, Any]) -> str:
    chosen = ", ".join(case["chosen"]) or "(empty)"
    return f"""### {label}

- **Model:** `{run.get('model', '?')}`
- **Chosen pipeline(s):** `{chosen}`
- **Verdict:** {case['verdict']} (acceptable: {case.get('acceptable')}, must_not: {case.get('must_not')})
- **Reason (verbatim from the model):**

  > {case['reason']}
"""


def build_comparison_markdown(a: dict[str, Any], b: dict[str, Any], case_id: str) -> str:
    a_name = a.get("cohort_display_name") or a.get("cohort", "cohort A")
    b_name = b.get("cohort_display_name") or b.get("cohort", "cohort B")

    a_case = _find_case(a, case_id)
    b_case = _find_case(b, case_id)
    question = a_case["question"]
    assert question == b_case["question"], "Same case id must carry the same question in both runs"

    same_pipeline = set(a_case["chosen"]) == set(b_case["chosen"])
    verdict_line = (
        "The model gave a **different** answer to the identical question depending on which "
        "real cohort's evidence it was shown — it read the data rather than pattern-matching "
        "the question text."
        if not same_pipeline
        else "The model gave the **same** answer to this question on both cohorts — no "
        "differentiation was observed on this specific case; see the raw reasoning below to "
        "judge why, and the next section for where differentiation *does* show up."
    )

    a_summary = a["summary"]
    b_summary = b["summary"]
    a_size = a.get("payload_size_report") or {}
    b_size = b.get("payload_size_report") or {}

    #: Every other shared case where the two cohorts' answers diverged —
    #: computed from what actually came back, not curated. If the headline
    #: case didn't differentiate, this is where the honest evidence that the
    #: system reads per-cohort data (rather than just the question text)
    #: has to come from, if it exists at all.
    a_by_id = {r["id"]: r for r in a.get("results", [])}
    b_by_id = {r["id"]: r for r in b.get("results", [])}
    other_diffs = [
        cid for cid in a_by_id
        if cid != case_id and cid in b_by_id and set(a_by_id[cid]["chosen"]) != set(b_by_id[cid]["chosen"])
    ]

    if not same_pipeline:
        divergence_section = f"""## Why this is the demonstration, not a coincidence

The question itself never says "bulk" or "single-cell," never names a
cohort, and never names a pipeline. The only thing that changed between
these two runs is the sample evidence in `02-sample-digest.json` — each
cohort's own structured metadata and its own protocol text, pulled live from
NExtSEEK. The model's answer and reasoning tracked that change: that is the
system reading the data rather than keying off words in the question.
"""
    elif other_diffs:
        rows = "\n".join(
            f"| `{cid}` | {a_by_id[cid]['question']} | `{', '.join(a_by_id[cid]['chosen'])}` | "
            f"`{', '.join(b_by_id[cid]['chosen'])}` |"
            for cid in other_diffs
        )
        divergence_section = f"""## The headline case didn't differentiate — here's where it actually shows up

On `{case_id}`, both cohorts landed on the same pipeline: the question's own
wording ("cell by cell") is specific enough on its own to pin the answer to
scrnaseq, so {b_name} matched {a_name} even though {b_name}'s own digest
carries no single-cell evidence at all — bulk RNA-seq, no cell barcodes,
`SequencingType: RNA-Seq` throughout. That is worth stating plainly rather
than picking a friendlier headline case after the fact.

The differentiation this comparison is actually looking for shows up on
{len(other_diffs)} of the other {len(a_by_id) - 1} shared cases instead —
same evaluator, same atlas/docs/schemas, only the digest changed:

| Case | Question | {a_name} chose | {b_name} chose |
|---|---|---|---|
{rows}

Nothing about any of these questions names a cohort or a modality either;
the divergence traces back to `02-sample-digest.json` alone.
"""
    else:
        divergence_section = f"""## No differentiation observed, on this case or any other

Both cohorts produced the same chosen pipeline(s) on every one of the
{len(a_by_id)} shared cases, including the headline case above. On this run,
the sample digest did not change the model's answer on any question in the
bank. That is a genuine result, not a gap in this report — it means either
the case bank's questions are specific enough on their own to fully
determine the answer regardless of cohort evidence, or the model is not
using the digest as much as intended. Worth investigating; not something to
paper over by relabeling the headline case.
"""

    return f"""# Two real cohorts — same question, same evaluator, same payload shape, different data

This is the comparison to lead with: the exact same intent-only question was
sent to the exact same model, using the exact same eval harness
(`run_rna_selection.py`), against two different REAL payloads pulled live
and read-only from production NExtSEEK — **{a_name}** and **{b_name}**. Both
cohorts are real lab data; neither side of this comparison is synthetic.

**What was held constant:** both payloads are built by the same
`selection_context.py` and carry the same four sections in the same order —
atlas, digest, docs, schemas — with an *identical* atlas, docs, and schemas
(the same six "rich" pipelines, fetched at the same pinned GitHub revisions:
{a_size.get('n_docs_fetched', '?')} docs and {a_size.get('n_schemas_fetched', '?')} schemas fetched on each side). The system prompt, the eval
harness, the model, and the question text are also identical between runs.

**What varied:** only the sample digest — each cohort's actual metadata and
protocol text (`02-sample-digest.json`), pulled fresh, live, and read-only
from NExtSEEK per cohort. {a_name} digest: {a_size.get('digest', '?'):,} chars.
{b_name} digest: {b_size.get('digest', '?'):,} chars. Nothing about the
question ever names a modality ("single-cell", "bulk") or a cohort; if the
model's answer tracks the cohort anyway, that is the system reading the
sample evidence rather than pattern-matching the question text.

## Overall scores

| Cohort | PASS | PARTIAL | FAIL | Total |
|---|---|---|---|---|
| {a_name} | {a_summary['pass']} | {a_summary['partial']} | {a_summary['fail']} | {a_summary['total']} |
| {b_name} | {b_summary['pass']} | {b_summary['partial']} | {b_summary['fail']} | {b_summary['total']} |

## The headline case: `{case_id}`

**Question sent to the model, verbatim, unchanged between runs:**

> {question}

{verdict_line}

{_fmt_case(a_name, a, a_case)}

{_fmt_case(b_name, b, b_case)}

{divergence_section}
---

Generated by `chat_nextseek/evals/cohort_comparison.py` from:
- `{_results_path(a.get('cohort', DEFAULT_COHORT_A)).relative_to(EVALS_DIR)}`
- `{_results_path(b.get('cohort', DEFAULT_COHORT_B)).relative_to(EVALS_DIR)}`
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-a", default=DEFAULT_COHORT_A, choices=sorted(COHORTS))
    parser.add_argument("--cohort-b", default=DEFAULT_COHORT_B, choices=sorted(COHORTS))
    parser.add_argument("--a-results", type=Path, default=None, help="Override path to cohort A's results JSON")
    parser.add_argument("--b-results", type=Path, default=None, help="Override path to cohort B's results JSON")
    parser.add_argument("--case-id", default=HEADLINE_CASE_ID)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    a_path = args.a_results or _results_path(args.cohort_a)
    b_path = args.b_results or _results_path(args.cohort_b)
    out_path = args.out or (EVALS_DIR / f"demo-output-{args.cohort_b}" / "07-cohort-comparison.md")

    a = _load(a_path)
    b = _load(b_path)

    md = build_comparison_markdown(a, b, args.case_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)
    print(f"Wrote comparison to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

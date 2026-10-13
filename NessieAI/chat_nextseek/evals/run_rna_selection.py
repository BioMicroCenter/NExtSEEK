#!/usr/bin/env python3
"""The RNA-pipeline selection eval: the only test that measures whether the
atlas + digest + schema payload actually helps a model choose the right
nf-core pipeline (design spec section 9.1).

For each case in rna_selection_cases.json, sends the model the full
selection payload (atlas + sample digest + nf-core schemas, built ONCE and
reused across all 12 cases — it does not depend on the question) plus that
case's question, and asks which pipeline(s) fit. Grades the answer against
the case's `acceptable` sets and `must_not` list:

    FAIL    — the answer includes any pipeline in `must_not`.
    PASS    — no must_not violation AND the answer set exactly equals one
              of the `acceptable` sets.
    PARTIAL — no must_not violation, but the answer matches no `acceptable`
              set exactly (e.g. a superset, a subset, or a different tie).

A must-not violation is the real failure — the model recommended a pipeline
that cannot answer the question at all. A cautious multi-pipeline fork that
doesn't exactly match the curated acceptable set is a partial credit, not a
failure: the atlas's own guidance says naming two or three candidates is a
legitimate answer.

Scores the 12 cases against a REAL cohort's payload (see
`demo_cohort_real.COHORTS`) — pick one with `--cohort`. Must run inside the
nextseek container, where model credentials live:

    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/run_rna_selection.py --cohort granuloma
    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/run_rna_selection.py --cohort macrophage

Writes results to demo-output-<cohort>/06-eval-results.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from chat_nextseek.config import ChatConfig
from chat_nextseek.pipeline.selection_context import DEFAULT_MAX_TOKENS, build_selection_context

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demo_cohort_real import COHORTS, build_real_digest  # noqa: E402

CASES_PATH = Path(__file__).resolve().parent / "rna_selection_cases.json"
EVALS_DIR = Path(__file__).resolve().parent


def _default_output_dir(cohort_key: str) -> Path:
    return EVALS_DIR / f"demo-output-{cohort_key}"


SYSTEM_PROMPT = """You are the pipeline-selection judgement for NExtSEEK's Nessie assistant.

You will be given three sections of context, in this order:
  1. PIPELINE ATLAS — curated notes on nf-core RNA pipelines: what each one
     answers, what it assumes about the library prep, and how to tell it
     apart from pipelines that accept the same FASTQ files but answer a
     different question.
  2. SAMPLE DIGEST — what is actually known about THIS cohort's samples and
     protocols (metadata fields, grouping candidates, full protocol text).
  3. NF-CORE SCHEMAS — the live parameter schemas for the pipelines with a
     full schema fetched. Some pipelines only have atlas prose and no
     schema; that does not rule them out, it just means you have less to
     check.

Then you will be given a scientist's QUESTION. Decide which nf-core
pipeline(s), by their atlas key (e.g. "rnaseq", "rnasplice", "scrnaseq"),
best fit that question given the atlas and this cohort's evidence.

Naming two or three candidate pipelines is a legitimate answer, not a
hedge — the atlas's own guidance says a confident wrong answer is worse
than an honest fork. Use the `versus` entries and `data_signals` in the
atlas, plus the sample digest's grouping candidates and protocol text, to
narrow the field as far as the evidence genuinely supports and no further.
Never include a pipeline that the atlas or the evidence clearly rules out.

Respond with ONLY a single JSON object, no markdown code fence, no text
before or after it, in exactly this shape:

{"pipelines": ["<atlas key>", ...], "reason": "<one sentence>"}

`pipelines` must be a non-empty list of atlas pipeline keys. `reason` must
be one sentence explaining the choice (or the fork, if you named more than
one)."""

USER_TEMPLATE = """{payload}

## QUESTION

{question}

Respond with ONLY the JSON object described in your instructions."""


def _load_cases(path: Path) -> list[dict[str, Any]]:
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
    """Ask the model which pipeline(s) fit `question` given `payload`.
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


def grade(chosen: list[str], case: dict[str, Any]) -> str:
    """FAIL on any must_not violation. PASS if the chosen set exactly equals
    one of the acceptable sets. PARTIAL otherwise (no violation, no exact
    match)."""
    chosen_set = set(chosen)
    must_not = set(case.get("must_not") or [])
    if chosen_set & must_not:
        return "FAIL"
    acceptable_sets = [set(s) for s in case.get("acceptable") or []]
    if chosen_set in acceptable_sets:
        return "PASS"
    return "PARTIAL"


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASES_PATH, help="Path to rna_selection_cases.json")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for results JSON")
    parser.add_argument("--agent", default="pipeline_agent", help="Registered agent name to pull model/client from")
    parser.add_argument(
        "--cohort",
        required=True,
        choices=sorted(COHORTS),
        help="Which registered real cohort to score against (see demo_cohort_real.COHORTS).",
    )
    args = parser.parse_args()

    cohort = COHORTS[args.cohort]
    out_dir: Path = args.output_dir or _default_output_dir(cohort.key)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[run_rna_selection] Building the selection payload once (atlas + '{cohort.key}' digest, "
          f"live NExtSEEK query, + live nf-core schemas)...")
    digest = build_real_digest(uids=list(cohort.uids))
    uids = list(cohort.uids)
    ctx = build_selection_context(config=None, uids=uids, digest=digest)
    payload = ctx.to_prompt_text()
    size = ctx.size_report()
    print(f"[run_rna_selection] Payload built: ~{size['est_tokens']:,} tokens "
          f"({size['n_docs_fetched']} docs fetched, {size['n_docs_failed']} failed; "
          f"{size['n_schemas_fetched']} schemas fetched, {size['n_schemas_failed']} failed).")
    _used = size["est_tokens"]
    _pct_used = (_used / DEFAULT_MAX_TOKENS * 100) if DEFAULT_MAX_TOKENS else 0.0
    _warning = "  *** WARNING: over 90% of the ceiling ***" if _pct_used > 90 else ""
    print(f"[run_rna_selection] headroom: {_used:,} / {DEFAULT_MAX_TOKENS:,} est. tokens used "
          f"({_pct_used:.1f}%), {DEFAULT_MAX_TOKENS - _used:,} tokens remaining{_warning}")

    config = ChatConfig()
    client, model, budget = config.get_agent_model(args.agent)
    print(f"[run_rna_selection] Using agent '{args.agent}' -> provider={getattr(client, 'provider', '?')} model={model}")

    cases = _load_cases(args.cases)
    print(f"[run_rna_selection] Running {len(cases)} cases...\n")

    results: list[dict[str, Any]] = []
    for case in cases:
        outcome = query_model(client, model, budget, payload, case["question"])
        verdict = "FAIL" if outcome["parse_error"] else grade(outcome["chosen"], case)
        results.append({
            "id": case["id"],
            "question": case["question"],
            "acceptable": case.get("acceptable"),
            "must_not": case.get("must_not"),
            "chosen": outcome["chosen"],
            "reason": outcome["reason"],
            "verdict": verdict,
            "parse_error": outcome["parse_error"],
            "raw_content": outcome["raw_content"],
        })
        status = f"PARSE ERROR: {outcome['parse_error']}" if outcome["parse_error"] else ", ".join(outcome["chosen"]) or "(empty)"
        print(f"  [{verdict:<7}] {case['id']:<28} chosen={status}")

    # Per-case table
    print("\n" + "=" * 100)
    print(f"{'id':<28} {'verdict':<8} {'question':<45} {'chosen'}")
    print("-" * 100)
    for r in results:
        print(f"{r['id']:<28} {r['verdict']:<8} {_truncate(r['question'], 45):<45} {', '.join(r['chosen']) or '(empty)'}")
    print("=" * 100)

    n_pass = sum(1 for r in results if r["verdict"] == "PASS")
    n_partial = sum(1 for r in results if r["verdict"] == "PARTIAL")
    n_fail = sum(1 for r in results if r["verdict"] == "FAIL")
    print(f"\nSUMMARY: {n_pass} PASS, {n_partial} PARTIAL, {n_fail} FAIL  (of {len(results)} cases)")
    if n_partial or n_fail:
        print("Not PASS:")
        for r in results:
            if r["verdict"] != "PASS":
                print(f"  - [{r['verdict']}] {r['id']}: chosen={r['chosen']} acceptable={r['acceptable']} must_not={r['must_not']}")

    out_path = out_dir / "06-eval-results.json"
    out_path.write_text(json.dumps({
        "cohort": cohort.key,
        "cohort_display_name": cohort.display_name,
        "agent": args.agent,
        "model": model,
        "payload_size_report": size,
        "summary": {"pass": n_pass, "partial": n_partial, "fail": n_fail, "total": len(results)},
        "results": results,
    }, indent=2))
    print(f"\nFull results written to: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

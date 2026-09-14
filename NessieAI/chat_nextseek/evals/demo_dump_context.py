#!/usr/bin/env python3
"""Build the RNA-pipeline selection context from a REAL cohort (see
`demo_cohort_real.py`'s `COHORTS` registry) and dump every section as a
separate, human-readable file, for review before a talk (or by anyone who
wants to see exactly what the model is handed without reading code).

Every cohort here is real lab data pulled live from the production NExtSEEK
instance — a real NExtSEEK API call and real protocol-blob downloads — so
this must run inside the nextseek container, where API + model credentials
live:

    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/demo_dump_context.py --cohort granuloma
    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/demo_dump_context.py --cohort macrophage

Usage:
    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/demo_dump_context.py --cohort granuloma
    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/demo_dump_context.py --cohort macrophage --output-dir /tmp/demo-output
    docker compose exec -T nextseek uv run python /app/chat_nextseek/evals/demo_dump_context.py --cohort granuloma --simulate-fetch-failure rnasplice
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from chat_nextseek.pipeline.selection_context import (
    DEFAULT_MAX_TOKENS,
    RICH_PIPELINES,
    SelectionContext,
    build_selection_context,
)
from chat_nextseek.seqera.nfcore_schema import SchemaFetchError, get_schema

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demo_cohort_real import COHORTS, Cohort, build_real_digest  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent


def _default_output_dir(cohort_key: str) -> Path:
    return EVALS_DIR / f"demo-output-{cohort_key}"


def _make_schema_getter(fail_pipeline: str | None) -> Callable[[str, str], dict]:
    """The real get_schema, except `fail_pipeline`'s fetch always raises
    SchemaFetchError — so the payload shows the real "judged without
    parameters" path without needing an actual network outage."""

    def _getter(pipeline: str, revision: str) -> dict:
        if fail_pipeline and pipeline == fail_pipeline:
            raise SchemaFetchError(
                f"{pipeline}@{revision}: simulated fetch failure "
                f"(--simulate-fetch-failure {fail_pipeline})"
            )
        return get_schema(pipeline, revision)

    return _getter


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


#: size_report() mixes per-section character counts (atlas, digest, docs,
#: schemas) with summary/count fields (n_docs_fetched, total_chars, ...). This
#: filters down to just the per-section counts, so the printed size table is
#: driven by whatever section keys size_report() actually returns rather than
#: a hardcoded list that a future section (or a renamed one) could fall out of
#: sync with silently.
_SIZE_REPORT_SUMMARY_KEYS = {"total_chars", "est_tokens"}


def _size_report_section_keys(report: dict[str, Any]) -> list[str]:
    return [k for k in report if k not in _SIZE_REPORT_SUMMARY_KEYS and not k.startswith("n_")]


_DOC_LABELS = {"readme": "README", "usage": "USAGE", "output": "OUTPUT"}


def _write_docs_text(path: Path, ctx: SelectionContext) -> None:
    """Human-readable dump of the pipeline-docs section: for each pipeline
    with a doc fetch, its README, usage guide, and output guide verbatim, in
    that order — the same three files nf-core's website renders as a
    pipeline's Introduction, Usage, and Output tabs."""
    lines: list[str] = [
        "NF-CORE PIPELINE DOCS",
        "",
        "README.md, docs/usage.md, docs/output.md fetched live from each "
        "pipeline's GitHub repository at its pinned revision (see "
        "01-atlas.json for the revision, 03-nfcore-schemas.json for the "
        "parameter schemas fetched from the same revision).",
    ]
    if ctx.docs_fetch_failed:
        lines.append("")
        lines.append("NOT FETCHED — these pipelines were judged without their documentation:")
        for pipeline, reason in sorted(ctx.docs_fetch_failed.items()):
            lines.append(f"  - {pipeline}: {reason}")

    for pipeline in sorted(ctx.docs.keys()):
        lines.append("")
        lines.append("=" * 80)
        lines.append(f"PIPELINE: {pipeline}")
        lines.append("=" * 80)
        docs_for_pipeline = ctx.docs[pipeline]
        for key, label in _DOC_LABELS.items():
            if key not in docs_for_pipeline:
                continue
            lines.append("")
            lines.append(f"--- {label} ({pipeline}) ---")
            lines.append("")
            lines.append(docs_for_pipeline[key])

    path.write_text("\n".join(lines) + "\n")


def _write_readme(path: Path, ctx: SelectionContext, cohort: Cohort, fail_pipeline: str | None) -> None:
    failed = sorted(ctx.schema_fetch_failed.keys())
    docs_failed = sorted(ctx.docs_fetch_failed.keys())
    stub_pipelines = sorted(set(ctx.atlas["pipelines"].keys()) - set(RICH_PIPELINES))
    protocol_ids = sorted(ctx.digest.get("protocols", {}).keys())
    dseq_fields = (
        ctx.digest.get("grouping_candidates", {}).get("by_sample_type", {}).get("D.SEQ", {}).get("fields", {})
    )
    dseq_field_names = sorted(dseq_fields.keys())

    failure_note = ""
    if failed:
        lines = "\n".join(f"- **{p}** — {ctx.schema_fetch_failed[p]}" for p in failed)
        failure_note = f"""
### A schema fetch failed on purpose

This run was told to simulate a failed fetch for `{fail_pipeline}` (via
`--simulate-fetch-failure`), so you can see what happens when GitHub is slow,
down, or the pipeline's schema files move. The failure is **not hidden**:

{lines}
"""

    docs_failure_note = ""
    if docs_failed:
        lines = "\n".join(f"- **{p}** — {ctx.docs_fetch_failed[p]}" for p in docs_failed)
        docs_failure_note = f"""
### A docs fetch failed too

{lines}
"""

    text = f"""# What's in this folder — THIS IS REAL LAB DATA

**Everything in this folder was pulled from a live NExtSEEK instance.** The
samples, sample metadata, lineage, and protocol documents dumped here are
real records retrieved from production NExtSEEK, read-only, via the real
`fetch_reporter_metadata` and `fetch_protocols` API calls. Nothing here is a
fixture, and nothing in this repo's demo tooling presents fabricated data as
evidence.

## The cohort: {cohort.display_name}

{cohort.description}

{len(protocol_ids)} distinct protocol(s) were discovered across this lineage
(ids: {', '.join(protocol_ids) or '(none)'}) — see `02-sample-digest.json`'s
`protocols` section for the full extracted text of each.

## Grouping candidates found in this cohort

`filter_summary_for_deg` leaves **{', '.join(dseq_field_names) or '(none)'}**
as the D.SEQ-level grouping candidate(s) for this cohort. Whatever this is
(or isn't) is a true signal about this real data, not a gap in the tooling —
nothing in this dump manufactures a contrast variable that isn't actually
there.

## The blob-URL download workaround

Protocol attachment downloads 404 through the shipped code path inside this
container, because of a real bug (being filed as a separate GitHub issue):
SEEK returns blob links as `http://127.0.0.1:8000/sops/...`, and the
existing localhost-rewrite logic rewrites that loopback link using
`config.NEXTSEEK_BASE_URL`, which is *also* `http://127.0.0.1:8000` inside
this container — loopback rewritten to loopback. `demo_cohort_real.py`
works around this in the demo only (no shipped code was changed): it wraps
`fetch_protocols` and rewrites each payload's `source_base_url` to the real
public host before handing it to the unmodified download/extract step. See
the `REAL_BLOB_HOST` constant and `_fetch_protocols_with_fixed_blob_host` in
that file for exactly what it does and why it's safe to delete once the
underlying bug is fixed upstream.

## 01-atlas.json — the curated pipeline knowledge

A hand-written reference comparing {len(ctx.atlas['pipelines'])} nf-core RNA
pipelines that all accept the same kind of file (FASTQ sequencing reads) but
answer different scientific questions — "how much of each gene is
expressed" vs. "did splicing change between two groups" vs. "is there a gene
fusion" and so on. This file is identical across every registered cohort;
only the sample digest (file 02) changes between cohorts.

Pipelines with a full parameter schema fetched (see file 03) and prose docs
fetched (see file 03b): {', '.join(RICH_PIPELINES)}. The rest
({', '.join(stub_pipelines) if stub_pipelines else 'none'}) are prose-only
entries: enough for the assistant to explicitly rule them out rather than
silently ignore them.

## 02-sample-digest.json — what was learned about these REAL samples and their protocols

Built by the real, network-calling `build_sample_digest` (via
`demo_cohort_real.build_real_digest`) against this cohort's actual metadata
and actual protocol documents: a per-sample-type breakdown of which metadata
fields are populated and how much they vary (`metadata_summary`), the subset
of those fields that actually look like a scientifically meaningful contrast
(`grouping_candidates` — see above), and the full text of every lab protocol
referenced by these samples, extracted from whatever PDF or Word document
was attached to it.

## 03-nfcore-schemas.json — the live-fetched nf-core parameter schemas

The actual, current parameter list for each "rich" pipeline listed above,
fetched live from each pipeline's GitHub repository at its pinned version.
Identical across every registered cohort.
{failure_note}
## 03b-nfcore-docs.txt — the live-fetched nf-core prose documentation

Each "rich" pipeline's own README.md, docs/usage.md, and docs/output.md,
fetched live from GitHub at the same pinned revision as the schemas in file
03. This is what nf-core's own website renders as a pipeline's
Introduction, Usage, and Output tabs. In the actual payload
(`04-full-payload.txt`) this section sits right after the sample digest and
before the schemas; it's numbered 03b here only because 03 was already taken
by the schemas file. Identical across every registered cohort.
{docs_failure_note}
## 04-full-payload.txt — exactly what gets sent to the model

The concatenation of the four sections above (atlas, then digest, then
docs, then schemas), in the exact text form the model receives for this
cohort.

## 05-size-report.json — the size breakdown

Character/token counts per section (atlas, digest, docs, schemas), so you
can see where the payload's size actually comes from and confirm it stays
under the {DEFAULT_MAX_TOKENS:,}-token ceiling — see
`chat_nextseek/src/chat_nextseek/pipeline/selection_context.py`'s
`PayloadTooLargeError`.

---

Generated by `chat_nextseek/evals/demo_dump_context.py --cohort {cohort.key}`
from the real cohort registered as `{cohort.key}` in
`chat_nextseek/evals/demo_cohort_real.py`. Everything in
`02-sample-digest.json` came from a live, read-only NExtSEEK query against
production data; the nf-core schemas in file 03 and docs in file 03b are
live-fetched from GitHub, same as every other registered cohort.
"""
    path.write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cohort",
        required=True,
        choices=sorted(COHORTS),
        help="Which registered real cohort to dump (see demo_cohort_real.COHORTS).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write the dump into (default: demo-output-<cohort>/)",
    )
    parser.add_argument(
        "--simulate-fetch-failure",
        metavar="PIPELINE",
        default=None,
        help="Pretend this pipeline's nf-core schema fetch failed (e.g. rnasplice), "
        "to demonstrate the NOT-FETCHED notice in the payload.",
    )
    args = parser.parse_args()

    cohort = COHORTS[args.cohort]
    out_dir: Path = args.output_dir or _default_output_dir(cohort.key)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[demo_dump_context] Building sample digest from cohort '{cohort.key}' "
          f"({cohort.display_name}, {len(cohort.uids)} D.SEQ samples, live NExtSEEK query)...")
    digest = build_real_digest(uids=list(cohort.uids))
    uids = list(cohort.uids)

    print("[demo_dump_context] Building selection context (atlas + digest + live nf-core docs + schemas)...")
    if args.simulate_fetch_failure:
        print(f"[demo_dump_context] Simulating a fetch failure for: {args.simulate_fetch_failure}")
    schema_getter = _make_schema_getter(args.simulate_fetch_failure)

    ctx = build_selection_context(
        config=None,
        uids=uids,
        digest=digest,
        schema_getter=schema_getter,
    )

    _write_readme(out_dir / "00-README.md", ctx, cohort, args.simulate_fetch_failure)
    _write_json(out_dir / "01-atlas.json", ctx.atlas)
    _write_json(out_dir / "02-sample-digest.json", ctx.digest)
    _write_json(
        out_dir / "03-nfcore-schemas.json",
        {"fetched": ctx.schemas, "failed": ctx.schema_fetch_failed},
    )
    _write_docs_text(out_dir / "03b-nfcore-docs.txt", ctx)
    (out_dir / "04-full-payload.txt").write_text(ctx.to_prompt_text())
    report = ctx.size_report()
    _write_json(out_dir / "05-size-report.json", report)

    print(f"\nWrote demo dump to: {out_dir}\n")
    print(f"Docs fetched ({len(ctx.docs)}): {', '.join(sorted(ctx.docs)) or '(none)'}")
    if ctx.docs_fetch_failed:
        print(f"Docs FAILED ({len(ctx.docs_fetch_failed)}):")
        for pipeline, reason in sorted(ctx.docs_fetch_failed.items()):
            print(f"  - {pipeline}: {reason}")
    else:
        print("Docs FAILED: (none)")

    print(f"Schemas fetched ({len(ctx.schemas)}): {', '.join(sorted(ctx.schemas)) or '(none)'}")
    if ctx.schema_fetch_failed:
        print(f"Schemas FAILED ({len(ctx.schema_fetch_failed)}):")
        for pipeline, reason in sorted(ctx.schema_fetch_failed.items()):
            print(f"  - {pipeline}: {reason}")
    else:
        print("Schemas FAILED: (none)")

    print("\nSize report:")
    print(f"  {'section':<10} {'chars':>10} {'~tokens':>10}")
    for section in _size_report_section_keys(report):
        chars = report[section]
        print(f"  {section:<10} {chars:>10,} {chars // 4:>10,}")
    print(f"  {'TOTAL':<10} {report['total_chars']:>10,} {report['est_tokens']:>10,}")
    print(
        f"\n  n_docs_fetched={report['n_docs_fetched']}  n_docs_failed={report['n_docs_failed']}"
        f"  n_schemas_fetched={report['n_schemas_fetched']}  n_schemas_failed={report['n_schemas_failed']}"
    )

    used = report["est_tokens"]
    headroom = DEFAULT_MAX_TOKENS - used
    pct_used = (used / DEFAULT_MAX_TOKENS * 100) if DEFAULT_MAX_TOKENS else 0.0
    warning = "  *** WARNING: over 90% of the ceiling ***" if pct_used > 90 else ""
    print(
        f"\n  headroom: {used:,} / {DEFAULT_MAX_TOKENS:,} est. tokens used ({pct_used:.1f}%), "
        f"{headroom:,} tokens remaining before PayloadTooLargeError{warning}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

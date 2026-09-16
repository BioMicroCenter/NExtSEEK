"""Resolve each nf-core sample back to the D.SEQ record it came from.

Order: the launch record, then an exact fastq path match, then a basename
match. Two candidates is never resolved to one of them -- picking a parent by
coin flip writes a wrong lineage into a scientific database of record.

A launch record that lists the sample in its cohort but never got a UID for
it (`PipelineRun.knows_sample` True, `uid_for` None) is reported unresolved
directly, without trying the fastq fallback: the launch already established
there is nothing in NExtSEEK to match against, so retrying by path would only
manufacture a same-run coincidence, not a real answer. The fastq fallback is
tried only when the sample is absent from the cohort altogether -- or there is
no launch record for this run_dir at all, which happens for a run launched
outside Nessie.

A multi-run sample (several samplesheet rows sharing one `sample` value) is
marked MULTIRUN and left unresolved on purpose, regardless of anything above.
nf-core concatenates those reads before QC, so the single figure it reports
cannot honestly be attributed to any one contributing D.SEQ. This is a
measurement limit, not an identity one: analysis children can still cite
every contributing D.SEQ as `Parent`, but the QC backfill cannot pick one.
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Callable

from NessieAI.ns.reingest import manifest


def resolve(
    samplesheet_rows: list[dict],
    run_dir: str,
    lookup_by_fastq: Callable[[str], list[str]],
) -> list[tuple[str, str | None, str]]:
    """One (sample, uid_or_None, resolution) per samplesheet row, input order."""
    from nextseek_api.assistant.models_db import PipelineRun

    record = PipelineRun.objects.filter(run_dir=run_dir).first()
    multi = {s for s, n in Counter(
        str(r.get("sample") or "") for r in samplesheet_rows).items() if n > 1}

    out: list[tuple[str, str | None, str]] = []
    for row in samplesheet_rows:
        sample = str(row.get("sample") or "")
        if sample in multi:
            out.append((sample, None, manifest.RESOLUTION_MULTIRUN))
            continue
        if record is not None and record.knows_sample(sample):
            # The run's cohort has an entry for this sample: the launch
            # record is authoritative, resolved or not. Never fall through
            # to the fastq path here -- see module docstring.
            uid = record.uid_for(sample)
            if uid:
                out.append((sample, uid, manifest.RESOLUTION_LAUNCH_RECORD))
            else:
                out.append((sample, None, manifest.RESOLUTION_UNRESOLVED))
            continue
        fastq = str(row.get("fastq_1") or "")
        out.append((sample, *_by_path(fastq, lookup_by_fastq)))
    return out


def _by_path(fastq: str, lookup: Callable[[str], list[str]]) -> tuple[str | None, str]:
    if not fastq:
        return None, manifest.RESOLUTION_UNRESOLVED
    exact = lookup(fastq)
    if len(exact) == 1:
        return exact[0], manifest.RESOLUTION_FASTQ_EXACT
    if len(exact) > 1:
        return None, manifest.RESOLUTION_AMBIGUOUS
    base = lookup(os.path.basename(fastq))
    if len(base) == 1:
        return base[0], manifest.RESOLUTION_FASTQ_BASENAME
    if len(base) > 1:
        return None, manifest.RESOLUTION_AMBIGUOUS
    return None, manifest.RESOLUTION_UNRESOLVED
